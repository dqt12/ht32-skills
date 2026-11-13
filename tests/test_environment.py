import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ht32_runtime.environment import (_decode_console_bytes, _registry_serial_descriptions,
                                     _serial_port_descriptions, check_environment, dfp_requirement_status,
                                     enumerate_cmsis_dap, enumerate_serial_port_details,
                                     parse_dfp_pack, read_dfp_requirement)


class FakeRegistry:
    HKEY_LOCAL_MACHINE = object()

    def __init__(self, tree):
        self.tree = tree

    def OpenKey(self, parent, name):
        try:
            if parent is self.HKEY_LOCAL_MACHINE:
                node = self.tree
                for part in name.split("\\"):
                    node = node[part]
                return node
            return parent["subkeys"][name]
        except KeyError as exc:
            raise FileNotFoundError(name) from exc

    def QueryInfoKey(self, key):
        return len(key.get("subkeys", {})), len(key.get("values", {})), 0

    def EnumKey(self, key, index):
        return list(key.get("subkeys", {}))[index]

    def EnumValue(self, key, index):
        name, value = list(key.get("values", {}).items())[index]
        return name, value, 1

    def QueryValueEx(self, key, name):
        try:
            return key["values"][name], 1
        except KeyError as exc:
            raise FileNotFoundError(name) from exc


def node(*, subkeys=None, values=None):
    return {"subkeys": subkeys or {}, "values": values or {}}


class EnvironmentTest(unittest.TestCase):
    def test_prefers_mockable_pnp_instances_and_does_not_match_arbitrary_usb(self):
        class RegistryMustNotBeUsed:
            HKEY_LOCAL_MACHINE = object()
            def OpenKey(self, parent, name):
                raise AssertionError("registry fallback should not run")

        instances = [
            {"Name": "CMSIS-DAP v2", "FriendlyName": None, "PNPDeviceID": r"USB\VID_9999&PID_0001\A"},
            {"Name": "USB Composite Device", "FriendlyName": "DAP Adapter", "PNPDeviceID": r"USB\VID_9999&PID_0002\B"},
            {"Name": "USB Input Device", "FriendlyName": "Ordinary USB", "PNPDeviceID": r"USB\VID_9999&PID_0003\C"},
        ]

        found = enumerate_cmsis_dap(
            pnp_provider=lambda: instances,
            registry=RegistryMustNotBeUsed(),
            platform="nt",
        )

        self.assertEqual(len(found["confirmed"]), 1)
        self.assertEqual(len(found["suspected"]), 1)
        self.assertNotIn("Ordinary USB", " ".join(found["confirmed"] + found["suspected"]))

    def test_recursively_detects_common_cmsis_dap_names_and_vids(self):
        instances = {
            "VID_0D28&PID_0204": node(subkeys={"A": node(values={"FriendlyName": "USB Debug Probe"})}),
            "VID_C251&PID_F001": node(subkeys={"B": node(values={"DeviceDesc": "Vendor probe"})}),
            "VID_1241&PID_0007": node(subkeys={"C": node(values={"DeviceDesc": "Vendor probe"})}),
            "VID_9999&PID_0001": node(subkeys={
                "D": node(values={"FriendlyName": "CMSIS-DAP v2"}),
                "E": node(values={"DeviceDesc": "DAPLink Interface"}),
                "F": node(values={"FriendlyName": "E-Link32 Debugger"}),
                "G": node(values={"FriendlyName": "ULINKpro"}),
                "H": node(values={"FriendlyName": "Unrelated USB Device"}),
            }),
        }
        registry = FakeRegistry({"SYSTEM": {"CurrentControlSet": {"Enum": {"USB": node(subkeys=instances)}}}})

        found = enumerate_cmsis_dap(registry=registry, platform="nt")

        self.assertEqual(len(found["confirmed"]), 7)
        self.assertEqual(found["suspected"], [])
        labels = " ".join(found["confirmed"]).lower()
        for marker in ("cmsis-dap", "daplink", "e-link", "ulink", "vid_0d28", "vid_c251", "vid_1241"):
            self.assertIn(marker, labels)

    def test_generic_dap_text_is_reported_as_suspected(self):
        instances = {"VID_9999&PID_0001": node(subkeys={"A": node(values={"FriendlyName": "Acme DAP Adapter"})})}
        registry = FakeRegistry({"SYSTEM": {"CurrentControlSet": {"Enum": {"USB": node(subkeys=instances)}}}})

        found = enumerate_cmsis_dap(registry=registry, platform="nt")

        self.assertEqual(found["confirmed"], [])
        self.assertEqual(len(found["suspected"]), 1)

    def test_holtek_probe_vid_pid_is_confirmed(self):
        """合泰自家调试器（e-Link32 / e-Link）在 HID 固件模式下接口名是通用的
        "USB 输入设备 / USB 串行设备"，只能靠 VID_04D9&PID_802F / 807C 识别；
        同 VID 的合泰键鼠（PID 不在白名单）不得误判。"""
        instances = [
            {"Name": "USB 串行设备 (COM13)", "FriendlyName": None,
             "PNPDeviceID": r"USB\VID_04D9&PID_802F&MI_01\9&56FFFBB&1&0001"},
            {"Name": "USB Input Device", "FriendlyName": None,
             "PNPDeviceID": r"USB\VID_04D9&PID_802F&MI_00\9&56FFFBB&1&0000"},
            {"Name": "USB Input Device", "FriendlyName": None,
             "PNPDeviceID": r"USB\VID_04D9&PID_8071\6&32094969&0&2"},
        ]

        found = enumerate_cmsis_dap(pnp_provider=lambda: instances, platform="nt")

        self.assertEqual(len(found["confirmed"]), 2)
        self.assertEqual(found["suspected"], [])
        self.assertTrue(all("802f" in item.lower() for item in found["confirmed"]))

    def test_serial_port_details_merge_registry_names_with_pnp_descriptions(self):
        """串口清单 = 注册表端口名 + PnP 设备友好名；查不到描述时回落端口名。"""
        instances = [
            {"Name": "USB 串行设备 (COM13)", "FriendlyName": None, "PNPDeviceID": r"USB\VID_04D9&PID_802F&MI_01\9&1"},
            {"Name": "USB Input Device", "FriendlyName": None, "PNPDeviceID": r"USB\VID_9999&PID_0001\A"},
        ]
        with patch("ht32_runtime.environment._powershell_pnp_instances", return_value=instances), \
             patch("ht32_runtime.environment.enumerate_serial_ports", return_value=["COM13", "COM7"]):
            details = enumerate_serial_port_details()
        self.assertEqual(details, [
            {"port": "COM13", "name": "USB 串行设备 (COM13)"},
            {"port": "COM7", "name": "COM7"},
        ])

    def test_serial_port_details_survive_pnp_failure(self):
        with patch("ht32_runtime.environment._powershell_pnp_instances", side_effect=OSError("blocked")), \
             patch("ht32_runtime.environment._registry_serial_descriptions", return_value={}), \
             patch("ht32_runtime.environment.enumerate_serial_ports", return_value=["COM13"]):
            self.assertEqual(enumerate_serial_port_details(), [{"port": "COM13", "name": "COM13"}])

    def test_decode_console_bytes_handles_utf8_and_gbk_without_replacement(self):
        """控制台输出按字节收：UTF-8（本机实测）与 GBK（旧环境）都要还原成中文。"""
        text = "USB 串行设备 (COM13)"
        self.assertEqual(_decode_console_bytes(text.encode("utf-8")), text)
        self.assertEqual(_decode_console_bytes(text.encode("mbcs")), text)
        # 两种编码都解不开时只替换、不抛错（设备名是展示信息，枚举不能因此失败）
        self.assertIn("\ufffd", _decode_console_bytes(b"\xff\xfe\xff"))

    def test_registry_serial_names_are_unicode_and_skip_placeholder_desc(self):
        tree = {"SYSTEM": {"CurrentControlSet": {"Enum": {"USB": node(subkeys={
            "VID_1": node(subkeys={
                "I1": node(values={"FriendlyName": "USB 串行设备 (COM13)"},
                           subkeys={"Device Parameters": node(values={"PortName": "COM13"})}),
                "I2": node(values={"DeviceDesc": "@usbser.inf,%usbserial.devicedesc%;USB 串行设备"},
                           subkeys={"Device Parameters": node(values={"PortName": "COM99"})}),
                "I3": node(values={"FriendlyName": "no port"}),
            })})
        }}}}
        names = _registry_serial_descriptions(FakeRegistry(tree))
        self.assertEqual(names, {"COM13": "USB 串行设备 (COM13)", "COM99": "USB 串行设备"})

    def test_serial_port_descriptions_prefer_registry_unicode_names_over_pnp(self):
        """注册表名（Unicode 原生）优先；PnP 只补注册表没有的端口（如蓝牙串口）。"""
        with patch("ht32_runtime.environment._registry_serial_descriptions",
                   return_value={"COM13": "USB 串行设备 (COM13)"}), \
             patch("ht32_runtime.environment._powershell_pnp_instances",
                   return_value=[{"Name": "USB 涓茶尪 (COM13)"},
                                 {"Name": "标准串行 (COM7)"}]):
            descriptions = _serial_port_descriptions()
        self.assertEqual(descriptions["COM13"], "USB 串行设备 (COM13)")
        self.assertEqual(descriptions["COM7"], "标准串行 (COM7)")

    @patch("ht32_runtime.environment.enumerate_serial_ports", return_value=[])
    @patch("ht32_runtime.environment.enumerate_jlink_probes", return_value=[])
    @patch("ht32_runtime.environment.enumerate_cmsis_dap")
    @patch("ht32_runtime.environment.find_ht32_dfp", return_value=[])
    @patch("ht32_runtime.environment.find_jlink_tools", return_value={"commander": None, "gdb_server": None, "rtt_client": None})
    @patch("ht32_runtime.environment.find_uv4", return_value=None)
    def test_suspected_dap_does_not_make_environment_available(
        self, _find_uv4, _find_jlink, _find_dfp, enumerate_dap, _enumerate_jlink, _enumerate_serial
    ):
        enumerate_dap.return_value = {"confirmed": [], "suspected": ["FriendlyName=Acme DAP Adapter"]}

        cmsis = check_environment({})["cmsis_dap"]

        self.assertFalse(cmsis["available"])
        self.assertEqual(cmsis["items"], [])
        self.assertEqual(cmsis["suspected"], ["FriendlyName=Acme DAP Adapter"])


class DfpRequirementTest(unittest.TestCase):
    """DFP 版本口径（用户决策 2026-10-18）：已装版本与工程要求"相同或更高"即满足。"""

    REQ = {"pack": "Holtek.HT32_DFP", "min_version": "1.0.78",
           "bundled_file": "utilities/pack/Holtek.HT32_DFP.1.0.78.pack"}

    def test_parse_dfp_pack_keil_layout(self):
        self.assertEqual(parse_dfp_pack(r"C:\Keil_v5\ARM\PACK\Holtek\HT32_DFP\1.0.78"),
                         {"pack": "HT32_DFP", "version": "1.0.78"})
        self.assertEqual(parse_dfp_pack("/root/Arm/Packs/Holtek/HT32F493x5_DFP/1.0.6"),
                         {"pack": "HT32F493x5_DFP", "version": "1.0.6"})

    def test_parse_dfp_pack_version_in_name_or_absent(self):
        self.assertEqual(parse_dfp_pack("/dl/Holtek.HT32F491x3_DFP.1.0.4"),
                         {"pack": "HT32F491x3_DFP", "version": "1.0.4"})
        self.assertEqual(parse_dfp_pack("/keil/Holtek/HT32_DFP"),
                         {"pack": "HT32_DFP", "version": ""})

    def test_parse_dfp_pack_reads_version_from_subdir_or_pdsc(self):
        """本机 Keil 形态：DFP 目录名不带版本，版本在子目录（1.0.78）里；再不行读 pdsc release。"""
        with tempfile.TemporaryDirectory() as root:
            pack = Path(root) / "HT32_DFP"
            (pack / "1.0.78").mkdir(parents=True)
            (pack / "1.0.102").mkdir()
            self.assertEqual(parse_dfp_pack(str(pack)), {"pack": "HT32_DFP", "version": "1.0.102"})
            legacy = Path(root) / "HT32F50241_DFP"
            legacy.mkdir()
            (legacy / "HT32_DFP.pdsc").write_text(
                '<package><releases><release version="2.3.4" date="2026-01-01"/></releases></package>',
                encoding="utf-8")
            self.assertEqual(parse_dfp_pack(str(legacy)), {"pack": "HT32F50241_DFP", "version": "2.3.4"})

    def test_requirement_ok_when_same_or_higher(self):
        for version in ("1.0.78", "1.0.80"):
            status = dfp_requirement_status([{"pack": "HT32_DFP", "version": version}], self.REQ)
            self.assertEqual(status["status"], "ok", version)
            self.assertEqual(status["installed"], version)

    def test_requirement_below_minimum_and_missing(self):
        below = dfp_requirement_status([{"pack": "HT32_DFP", "version": "1.0.77"}], self.REQ)
        self.assertEqual(below["status"], "below-minimum")
        missing = dfp_requirement_status([{"pack": "HT32F490x1_DFP", "version": "1.0.1"}], self.REQ)
        self.assertEqual(missing["status"], "missing")
        self.assertEqual(missing["installed"], "")
        self.assertIsNone(dfp_requirement_status([], None))

    def test_requirement_picks_highest_installed(self):
        status = dfp_requirement_status(
            [{"pack": "HT32_DFP", "version": "1.0.74"}, {"pack": "HT32_DFP", "version": "1.0.80"}],
            self.REQ)
        self.assertEqual(status["status"], "ok")
        self.assertEqual(status["installed"], "1.0.80")

    def test_requirement_matches_vendor_prefixed_versioned_pack_id(self):
        """工程 manifest 的 pack.id 带 vendor 前缀与版本（Holtek.HT32F490x1_DFP.1.0.1），
        Keil 安装目录却只有 HT32F490x1_DFP：不能直接拿全名比目录名，否则已装也报 missing。"""
        req = {"pack": "Holtek.HT32F490x1_DFP.1.0.1", "min_version": "1.0.1",
               "bundled_file": "utilities/pack/Holtek.HT32F490x1_DFP.1.0.1.pack"}
        status = dfp_requirement_status([{"pack": "HT32F490x1_DFP", "version": "1.0.1"}], req)
        self.assertEqual(status["status"], "ok")
        self.assertEqual(status["name"], "HT32F490x1_DFP")
        self.assertEqual(status["installed"], "1.0.1")

        upper = dfp_requirement_status([{"pack": "ht32f490x1_dfp", "version": "1.0.2"}], req)
        self.assertEqual(upper["status"], "ok")          # 大小写不敏感

        lower = dfp_requirement_status([{"pack": "HT32F490x1_DFP", "version": "1.0.0"}], req)
        self.assertEqual(lower["status"], "below-minimum")

    def test_read_dfp_requirement_from_project_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            mdir = Path(tmp) / "manifest"
            mdir.mkdir(parents=True)
            (mdir / "project-lock.json").write_text(json.dumps({
                "pack": {"id": "Holtek.HT32_DFP", "min_version": "1.0.78",
                         "file": "utilities/pack/Holtek.HT32_DFP.1.0.78.pack"},
                "requires": {"pack": "Holtek.HT32_DFP", "pack_min_version": "1.0.78"},
            }), encoding="utf-8")
            req = read_dfp_requirement(tmp)
        self.assertEqual(req["min_version"], "1.0.78")
        self.assertEqual(req["bundled_file"], "utilities/pack/Holtek.HT32_DFP.1.0.78.pack")
        self.assertIsNone(read_dfp_requirement(None))
        self.assertIsNone(read_dfp_requirement("no-such-project"))

    def test_check_environment_carries_pack_versions_and_requirement(self):
        with patch("ht32_runtime.environment.find_uv4", return_value=None), \
             patch("ht32_runtime.environment.find_jlink_tools",
                   return_value={"commander": None, "gdb_server": None, "rtt_client": None}), \
             patch("ht32_runtime.environment.find_ht32_dfp",
                   return_value=["/keil/Holtek/HT32_DFP/1.0.77"]), \
             patch("ht32_runtime.environment.enumerate_cmsis_dap",
                   return_value={"confirmed": [], "suspected": []}), \
             patch("ht32_runtime.environment.enumerate_jlink_probes", return_value=[]), \
             patch("ht32_runtime.environment.enumerate_serial_ports", return_value=[]):
            obs = check_environment({}, requirement=self.REQ)

        self.assertEqual(obs["dfp"]["items"], ["/keil/Holtek/HT32_DFP/1.0.77"])
        self.assertEqual(obs["dfp"]["packs"], [{"pack": "HT32_DFP", "version": "1.0.77"}])
        self.assertEqual(obs["dfp"]["requirement"]["status"], "below-minimum")


if __name__ == "__main__":
    unittest.main()
