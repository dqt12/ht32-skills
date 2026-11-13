import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from ht32_runtime import keil_config
from ht32_runtime.backends import OperationContext
from ht32_runtime.backends.keil import KeilBackend
from ht32_runtime.models import DebuggerConfig

CMSIS_NAME = ('-X"Any" -UAny -O206 -S0 -C0 -P00 -N00("ARM CoreSight SW-DP") -D00(0BC11477) -L00(0) '
              '-TO18 -TC1000000 -TP20 -TDS8007 -TDT0 -TDC1F -TIEFFFFFFFF -TIP8 -FO15 -FD20000000 '
              '-FC1000 -FN2 -FF0HT32F.FLM -FS00 -FL0100000 -FP0($$Device:HT32F52352$ARM\\Flash\\HT32F.FLM)')

UVPROJX = """<?xml version="1.0" encoding="UTF-8" standalone="no" ?>
<Project xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
<Targets><Target><TargetName>Demo</TargetName>
<ToolsetNumber>0x4</ToolsetNumber><ToolsetName>ARM-ADS</ToolsetName>
<TargetOption><TargetCommonOption><Device>HT32F52352</Device></TargetCommonOption>
<DebugOption><OPTHX><Host>1</Host></OPTHX>
<TargetDlls><Driver>BIN\\CMSIS_AGDI.dll</Driver></TargetDlls></DebugOption>
<Utilities><Flash1><UseTargetDll>1</UseTargetDll></Flash1>
<Flash2>BIN\\CMSIS_AGDI.dll</Flash2><Flash3></Flash3></Utilities>
</TargetOption></Target></Targets></Project>"""

UVOPTX = """<Target><TargetName>Demo</TargetName><TargetOption><DebugOpt>
<uSim>0</uSim><uTrg>1</uTrg><pMon>BIN\\CMSIS_AGDI.dll</pMon><nTsel>2</nTsel>
<TargetDriverDllRegistry>
<SetRegEntry><Number>0</Number><Key>CMSIS_AGDI</Key><Name>%s</Name></SetRegEntry>
<SetRegEntry><Number>1</Number><Key>UL2CM3</Key><Name>-UAny -O206 -S0 -C0 -P00</Name></SetRegEntry>
</TargetDriverDllRegistry>
</DebugOpt></TargetOption></Target>""" % CMSIS_NAME


class KeilConfigTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name)
        self.uvprojx = self.root / "demo.uvprojx"; self.uvoptx = self.root / "demo.uvoptx"
        self.write(UVPROJX, UVOPTX)

    def tearDown(self): self.temp.cleanup()

    def write(self, project, option=None, bom=True):
        self.uvprojx.write_text(project, encoding="utf-8")
        if option is not None:
            self.uvoptx.write_text(option, encoding="utf-8-sig" if bom else "utf-8")

    def read(self, path):
        return path.read_text(encoding="utf-8-sig")

    def test_read_config_reports_current_debugger(self):
        info = keil_config.read_config(self.uvprojx)
        self.assertEqual(info["pmon"], r"BIN\CMSIS_AGDI.dll")
        self.assertEqual(info["driver"], r"BIN\CMSIS_AGDI.dll")
        self.assertEqual(info["flash_driver"], r"BIN\CMSIS_AGDI.dll")
        self.assertEqual(info["registry_keys"], ["CMSIS_AGDI", "UL2CM3"])
        self.assertEqual(info["speed_khz"], 1000)
        self.assertIsNone(info["probe_serial"])

    def test_switch_to_jlink_rewrites_both_files(self):
        report = keil_config.switch(self.uvprojx, "jlink", speed_khz=500, probe_serial="20080643")
        self.assertEqual(report["debugger"], "jlink")
        self.assertEqual(len(report["changed"]), 4)
        project, option = self.read(self.uvprojx), self.read(self.uvoptx)

        self.assertIn(r"<Driver>BIN\JL2CM3.dll</Driver>", project)
        self.assertIn(r"<Flash2>BIN\JL2CM3.dll</Flash2>", project)
        self.assertIn(r"<pMon>BIN\JL2CM3.dll</pMon>", option)
        name = keil_config._registry_name(option, "JL2CM3")
        self.assertTrue(name.startswith("-U20080643 -O78"), name)  # 序列号在最前，驱动前缀紧跟
        self.assertNotIn("-O206", name)                     # 不把 CMSIS 前缀拼进来
        self.assertIn("-N00(\"ARM CoreSight SW-DP\")", name)  # 目标/Flash 算法沿用原参数
        self.assertIn("-TC500000", name)
        self.assertIn("-U20080643", name)
        # uvoptx 里的 CMSIS 条目原样保留，便于切回
        self.assertEqual(keil_config._registry_name(option, "CMSIS_AGDI"), CMSIS_NAME)
        for path in (self.uvprojx, self.uvoptx):
            ET.parse(path)
            self.assertTrue(path.read_bytes().startswith(b"\xef\xbb\xbf") if path is self.uvoptx else True)
        self.assertEqual(report["after"]["pmon"], r"BIN\JL2CM3.dll")
        self.assertEqual(report["after"]["driver"], r"BIN\JL2CM3.dll")

    def test_switch_back_to_cmsis_is_reversible(self):
        keil_config.switch(self.uvprojx, "jlink", speed_khz=1000)
        keil_config.switch(self.uvprojx, "cmsis-dap", speed_khz=1000)
        self.assertEqual(keil_config._registry_name(self.read(self.uvoptx), "CMSIS_AGDI"), CMSIS_NAME)
        info = keil_config.read_config(self.uvprojx)
        self.assertEqual(info["pmon"], r"BIN\CMSIS_AGDI.dll")
        self.assertEqual(info["driver"], r"BIN\CMSIS_AGDI.dll")
        self.assertEqual(info["flash_driver"], r"BIN\CMSIS_AGDI.dll")

    def test_elink32_alias_maps_to_cmsis(self):
        self.assertEqual(keil_config.normalize("e-Link32 Pro"), "cmsis-dap")
        self.assertEqual(keil_config.normalize("cmsis"), "cmsis-dap")
        self.assertEqual(keil_config.normalize("J-Link"), "jlink")

    def test_dry_run_does_not_touch_files(self):
        before = (self.read(self.uvprojx), self.read(self.uvoptx))
        report = keil_config.switch(self.uvprojx, "jlink", dry_run=True)
        self.assertTrue(report["dry_run"]); self.assertTrue(report["changed"])
        self.assertIsNone(report["backup"])
        self.assertEqual((self.read(self.uvprojx), self.read(self.uvoptx)), before)
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), ["demo.uvoptx", "demo.uvprojx"])
        self.assertEqual(report["after"]["pmon"], r"BIN\JL2CM3.dll")

    def test_switch_backs_up_before_writing(self):
        report = keil_config.switch(self.uvprojx, "jlink")
        backups = sorted(self.root.glob("demo.*.bak-*"))
        self.assertEqual(len(backups), 2, backups)
        self.assertTrue(report["backup"].startswith(str(self.uvoptx)), report["backup"])
        self.assertIn(".bak-", report["backup"])
        for backup in backups:
            ET.fromstring(backup.read_text(encoding="utf-8-sig"))

    def test_missing_uvoptx_asks_for_a_keil_save(self):
        self.uvoptx.unlink()
        with self.assertRaises(keil_config.KeilConfigError) as caught:
            keil_config.switch(self.uvprojx, "jlink")
        self.assertIn("uvoptx", str(caught.exception))

    def test_unknown_debugger_is_rejected(self):
        with self.assertRaises(keil_config.KeilConfigError):
            keil_config.switch(self.uvprojx, "stlink")

    def test_registry_entry_is_inserted_when_absent(self):
        self.write(UVPROJX, UVOPTX.replace("<SetRegEntry><Number>1</Number><Key>UL2CM3</Key>"
                                           "<Name>-UAny -O206 -S0 -C0 -P00</Name></SetRegEntry>", ""))
        keil_config.switch(self.uvprojx, "jlink")
        option = self.read(self.uvoptx)
        ET.fromstring(option)
        self.assertEqual(keil_config.read_config(self.uvprojx)["registry_keys"], ["CMSIS_AGDI", "JL2CM3"])
        self.assertEqual(len(ET.fromstring(option).findall(".//SetRegEntry")), 2)


class KeilConfigureBackendTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name)
        self.uvprojx = self.root / "demo.uvprojx"
        self.uvprojx.write_text(UVPROJX, encoding="utf-8")
        (self.root / "demo.uvoptx").write_text(UVOPTX, encoding="utf-8-sig")
        self.keil = self.root / "Keil_v5"
        (self.keil / "ARM" / "BIN").mkdir(parents=True)
        (self.keil / "ARM" / "BIN" / "CMSIS_AGDI.dll").write_bytes(b"")
        (self.keil / "ARM" / "Segger").mkdir(parents=True)
        (self.keil / "ARM" / "Segger" / "JL2CM3.dll").write_bytes(b"")

    def tearDown(self): self.temp.cleanup()

    def ctx(self):
        return OperationContext(self.root, "pid", "HT32F52352", self.uvprojx, "Demo", DebuggerConfig(),
                                {"uv4_exe": str(self.keil / "UV4" / "UV4.exe")}, self.root / "cache", self.root / "logs")

    def test_configure_without_debugger_only_reports(self):
        result = KeilBackend(None).configure(self.ctx())
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.observations["current"]["pmon"], r"BIN\CMSIS_AGDI.dll")
        self.assertTrue(result.observations["available"]["jlink"]["installed"])
        self.assertEqual(result.artifacts, {})

    def test_configure_switches_and_reports_next_steps(self):
        result = KeilBackend(None).configure(self.ctx(), debugger="jlink", speed_khz=1000)
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.observations["after"]["driver"], r"BIN\JL2CM3.dll")
        self.assertIn("uvoptx", result.artifacts)
        self.assertIn("Max Clock", " ".join(result.observations["next_steps"]))
        self.assertEqual(result.warnings, [])

    def test_configure_warns_when_driver_dll_is_missing(self):
        (self.keil / "ARM" / "Segger" / "JL2CM3.dll").unlink()
        result = KeilBackend(None).configure(self.ctx(), debugger="jlink")
        self.assertEqual(result.status, "warn")
        self.assertIn("JL2CM3.dll", " ".join(result.warnings))

    def test_configure_blocks_without_uvoptx(self):
        (self.root / "demo.uvoptx").unlink()
        result = KeilBackend(None).configure(self.ctx(), debugger="jlink")
        self.assertEqual(result.status, "blocked")
        self.assertIn("uvoptx", result.error["message"])

    def test_available_reports_unknown_without_uv4(self):
        report = keil_config.available(None)
        self.assertIsNone(report["jlink"]["installed"])
        self.assertIn("uv4_exe", report["jlink"]["hint"])

    def test_configure_without_uv4_still_switches_but_warns(self):
        ctx = OperationContext(self.root, "pid", "HT32F52352", self.uvprojx, "Demo", DebuggerConfig(),
                               {}, self.root / "cache", self.root / "logs")
        result = KeilBackend(None).configure(ctx, debugger="jlink")
        self.assertEqual(result.status, "warn")
        self.assertIn("uv4_exe", " ".join(result.warnings))
        self.assertEqual(result.observations["after"]["driver"], r"BIN\JL2CM3.dll")


if __name__ == "__main__":
    unittest.main()
