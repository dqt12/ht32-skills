import tempfile
import unittest
from pathlib import Path

from ht32_runtime.backends import OperationContext
from ht32_runtime.backends.keil import KeilBackend
from ht32_runtime.models import DebuggerConfig
from ht32_runtime.process import ProcessResult, ToolExecutionError


class Runner:
    def __init__(self, code=0): self.code, self.calls = code, []
    def __call__(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        return ProcessResult(tuple(argv), self.code, "", "", 1, False, kwargs["log_path"])


class ToolLogRunner:
    """模拟真实 UV4：stdout 基本为空，真实输出只写进 `-o` 指定的日志。"""

    def __init__(self, text, code=0):
        self.text, self.code, self.calls = text, code, []

    def __call__(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        tool_log = Path(argv[argv.index("-o") + 1])
        tool_log.parent.mkdir(parents=True, exist_ok=True)
        tool_log.write_text(self.text, encoding="utf-8")
        runtime_log = kwargs["log_path"]
        if runtime_log is not None:
            runtime_log.parent.mkdir(parents=True, exist_ok=True)
            runtime_log.write_text("[command] UV4\n[stdout]\n", encoding="utf-8")
        return ProcessResult(tuple(argv), self.code, "", "", 1, False, runtime_log)


class KeilBackendTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name)
        self.uvprojx = self.root / "demo.uvprojx"
        self.uvprojx.write_text("<Project><Target><TargetName>Demo</TargetName><TargetOption><DebugOpt><uSim>0</uSim><uTrg>1</uTrg><Driver>CMSIS-DAP</Driver></DebugOpt></TargetOption></Target></Project>", encoding="utf-8")
    def tearDown(self): self.temp.cleanup()
    def ctx(self, debugger=DebuggerConfig()):
        return OperationContext(self.root, "pid", "HT32F52352", self.uvprojx, "Demo", debugger,
            {"uv4_exe": r"C:\Keil_v5\UV4\UV4.exe"}, self.root/"cache", self.root/"logs")

    def test_build_rebuild_clean_and_flash_commands(self):
        runner = Runner(); backend = KeilBackend(runner)
        for action, flag in (("build", "-b"), ("rebuild", "-r"), ("clean", "-c"), ("flash", "-f")):
            getattr(backend, action)(self.ctx())
            argv = runner.calls[-1][0]
            self.assertEqual(argv[:2], [r"C:\Keil_v5\UV4\UV4.exe", flag])
            self.assertIn("-t", argv); self.assertIn("Demo", argv); self.assertIn("-o", argv)

    def test_return_codes_and_interactive_debug(self):
        self.assertEqual(KeilBackend(Runner(0)).build(self.ctx()).status, "ok")
        self.assertEqual(KeilBackend(Runner(1)).build(self.ctx()).status, "warn")
        with self.assertRaises(ToolExecutionError):
            KeilBackend(Runner(2)).build(self.ctx())
        result = KeilBackend(Runner()).debug(self.ctx())
        self.assertEqual(result.observations["session"], "interactive")

    def test_real_ht32_cmsis_agdi_project_allows_flash_and_debug(self):
        self.uvprojx.write_text(
            (Path(__file__).parent / "fixtures" / "project" / "cmsis_dap_ht32.uvprojx").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        backend = KeilBackend(Runner())

        self.assertNotEqual(backend.flash(self.ctx()).status, "blocked")
        self.assertNotEqual(backend.debug(self.ctx()).status, "blocked")

    def test_uvoptx_is_authoritative_for_debugger(self):
        """真实 Keil 工程把调试器选择写在 .uvoptx（<pMon>CMSIS_AGDI），
        .uvprojx 里只有 <Flash2> 烧录算法；只看 uvprojx 会漏判。"""
        self.uvprojx.write_text(
            "<Project><Target><TargetOption><Utilities>"
            "<Flash2>BIN\\HT32F52352_512.FLM</Flash2></Utilities></TargetOption></Target></Project>",
            encoding="utf-8",
        )
        backend = KeilBackend(Runner())
        self.assertEqual(backend.flash(self.ctx()).status, "blocked")   # 无 uvoptx → 未配置

        (self.root / "demo.uvoptx").write_text(
            "<Target><DebugOpt><pMon>BIN\\CMSIS_AGDI.dll</pMon></DebugOpt></Target>",
            encoding="utf-8",
        )
        self.assertNotEqual(backend.flash(self.ctx()).status, "blocked")
        self.assertNotEqual(backend.debug(self.ctx()).status, "blocked")

    def test_jlink_probe_selected_in_keil_is_allowed(self):
        """J-Link 探针同样由 Keil 驱动（<pMon>BIN\\JL2CM3.dll），不应被判"未配置调试器"。"""
        (self.root / "demo.uvoptx").write_text(
            "<Target><DebugOpt><pMon>BIN\\JL2CM3.dll</pMon></DebugOpt></Target>",
            encoding="utf-8",
        )
        backend = KeilBackend(Runner())
        self.assertNotEqual(backend.flash(self.ctx()).status, "blocked")
        self.assertNotEqual(backend.debug(self.ctx()).status, "blocked")

    def test_flash_precheck_reports_missing_programming_algorithm(self):
        """uvoptx 的 Flash Download 算法列表为空（-FN0 / 缺 -FF0）时，调 UV4 前就给出指向性诊断。

        实测现象：UV4 只输出 `Erase Failed! Flash Download failed - "Cortex-M4"`，
        CLI 只剩「退出码 2」，用户无从下手（真实根因是 Keil Utilities 里算法列表为空）。
        """
        self.uvprojx.write_text(
            "<Project><Target><TargetName>Demo</TargetName><TargetOption><DebugOpt>"
            "<Driver>CMSIS-DAP</Driver></DebugOpt></TargetOption></Target></Project>",
            encoding="utf-8")
        uvoptx = self.root / "demo.uvoptx"
        header = "<Target><DebugOpt><pMon>BIN\\CMSIS_AGDI.dll</pMon></DebugOpt>"
        uvoptx.write_text(
            header + "<TargetDriverDllRegistry><SetRegEntry><Number>0</Number><Key>CMSIS_AGDI</Key>"
            "<Name>-X\"Any\" -FO15 -FD20000000 -FC1000 -FN0 -FL010000</Name></SetRegEntry>"
            "</TargetDriverDllRegistry></Target>", encoding="utf-8")
        runner = Runner()
        result = KeilBackend(runner).flash(self.ctx())
        self.assertEqual(result.status, "blocked")
        self.assertIn("Programming Algorithm 未配置", result.error["message"])
        self.assertIn("Flash Download", result.error["message"])
        self.assertEqual(runner.calls, [])          # 没有真的去调 UV4

        # -FN1 但缺 -FF0（条目名丢失）同样是算法没配
        uvoptx.write_text(
            header + "<TargetDriverDllRegistry><SetRegEntry><Number>0</Number><Key>CMSIS_AGDI</Key>"
            "<Name>-X\"Any\" -FO15 -FD20000000 -FC1000 -FN1 -FL010000</Name></SetRegEntry>"
            "</TargetDriverDllRegistry></Target>", encoding="utf-8")
        self.assertEqual(KeilBackend(runner).flash(self.ctx()).status, "blocked")

        # 正常配置（-FN1 -FF0）不拦，行为与修复前一致
        uvoptx.write_text(
            header + "<TargetDriverDllRegistry><SetRegEntry><Number>0</Number><Key>CMSIS_AGDI</Key>"
            "<Name>-X\"Any\" -FO15 -FD20000000 -FC1000 -FN1 -FF0HT32F490x1_64 "
            "-FS08000000 -FL010000</Name></SetRegEntry>"
            "</TargetDriverDllRegistry></Target>", encoding="utf-8")
        self.assertNotEqual(KeilBackend(Runner()).flash(self.ctx()).status, "blocked")

    def test_unconfigured_debugger_reports_how_to_configure(self):
        self.uvprojx.write_text(
            "<Project><Target><TargetOption><Utilities>"
            "<Flash2>BIN\\HT32F52352_512.FLM</Flash2></Utilities></TargetOption></Target></Project>",
            encoding="utf-8",
        )
        result = KeilBackend(Runner()).flash(self.ctx())
        self.assertEqual(result.status, "blocked")
        message = result.error["message"]
        self.assertIn("CMSIS-DAP", message)   # 给出 Keil 侧配置步骤
        self.assertIn("uvoptx", message)

    def test_unrelated_vendor_driver_is_not_a_debugger(self):
        self.uvprojx.write_text(
            "<Project><Target><TargetOption><DebugOpt><Driver>BIN\\Vendor_AGDI.dll</Driver>"
            "</DebugOpt><Utilities><Flash2>BIN\\Vendor_AGDI.dll</Flash2></Utilities>"
            "</TargetOption></Target></Project>",
            encoding="utf-8",
        )

        self.assertEqual(KeilBackend(Runner()).flash(self.ctx()).status, "blocked")

    def test_missing_tool_and_invalid_debugger_are_blocked(self):
        ctx = self.ctx(DebuggerConfig("jlink", None, "swd", 1000))
        self.assertEqual(KeilBackend(Runner()).flash(ctx).status, "blocked")
        ctx = self.ctx(DebuggerConfig("keil", None, "jtag", 1000))
        self.assertEqual(KeilBackend(Runner()).flash(ctx).status, "blocked")
        ctx = self.ctx(); ctx.tools["uv4_exe"] = None
        self.assertEqual(KeilBackend(Runner()).build(ctx).status, "blocked")

    def test_unwritable_log_dir_does_not_block_build(self):
        """宿主沙箱拦全局日志目录时，构建照常执行（不传 -o），只在 JSON 里降级告警。"""
        runner = Runner()
        ctx = self.ctx()
        blocker = self.root / "blocked"
        blocker.write_text("x", encoding="utf-8")
        ctx.logs_dir = blocker / "logs"

        result = KeilBackend(runner).build(ctx)

        self.assertEqual(result.status, "warn")
        self.assertNotIn("-o", runner.calls[-1][0])
        self.assertIsNone(runner.calls[-1][1]["log_path"])
        self.assertIsNone(result.log_path)
        self.assertTrue(any("HT32_SKILLS_HOME" in item for item in result.warnings))

    def test_uv4_tool_log_becomes_primary_log_and_yields_summary(self):
        """UV4 的真实输出在 `-o` 日志里：log_path 指向它，并摘出编译统计。"""
        text = ("Build target 'Demo'\ncompiling main.c...\nProgram Size: Code=2464 RO-data=100 "
                "RW-data=16 ZI-data=512\n0 Error(s), 0 Warning(s).\n")
        runner = ToolLogRunner(text)
        result = KeilBackend(runner).build(self.ctx())

        tool_log = Path(runner.calls[-1][0][runner.calls[-1][0].index("-o") + 1])
        self.assertEqual(result.log_path, str(tool_log.resolve()))
        self.assertEqual(result.artifacts["tool_log"], str(tool_log.resolve()))
        self.assertIn("runtime_log", result.artifacts)
        self.assertEqual(result.observations["summary"], {
            "errors": 0, "warnings": 0, "code": 2464, "ro_data": 100, "rw_data": 16,
            "zi_data": 512, "source": "tool_log"})

    def test_ac6_build_is_warned_as_out_of_scope(self):
        """工程切到 ARM Compiler 6 时只告警（编译结果照常返回），提示切回 AC5。"""
        text = ("*** Using Compiler 'V6.19', folder: 'C:\\Keil_v5\\ARM\\ARMCLANG\\Bin'\n"
                "Program Size: Code=100 RO-data=0 RW-data=0 ZI-data=0\n0 Error(s), 0 Warning(s).\n")
        result = KeilBackend(ToolLogRunner(text)).build(self.ctx())

        self.assertEqual(result.status, "warn")
        self.assertEqual(result.observations["summary"]["compiler"], "V6.19")
        self.assertFalse(result.observations["summary"]["compiler_supported"])
        self.assertTrue(any("ARM Compiler 6" in item for item in result.warnings))

    def test_flash_summary_reports_stages_and_error_lines(self):
        runner = ToolLogRunner("Erase Done.\nProgramming Done.\nVerify OK.\nApplication running ...\n", code=0)
        result = KeilBackend(runner).flash(self.ctx())
        self.assertEqual(result.observations["summary"]["flash"], {
            "erase_done": True, "programming_done": True, "verify_ok": True, "application_running": True})

        runner = ToolLogRunner("Erase Done.\nError: Flash Download failed - Target DLL has been cancelled\n", code=2)
        with self.assertRaises(ToolExecutionError) as caught:
            KeilBackend(runner).flash(self.ctx())
        self.assertIn("退出码 2", str(caught.exception))
        self.assertTrue(Path(caught.exception.log_path).is_file())

    def test_build_skips_debugger_validation_while_flash_keeps_it(self):        # build/rebuild/clean 是纯编译动作，不接触调试器，debugger 语义不阻断
        ctx = self.ctx(DebuggerConfig("keil", None, "jtag", 1000))
        backend = KeilBackend(Runner())
        self.assertEqual(backend.build(ctx).status, "ok")
        self.assertEqual(backend.rebuild(ctx).status, "ok")
        self.assertEqual(backend.clean(ctx).status, "ok")
        self.assertEqual(backend.flash(ctx).status, "blocked")

        ctx = self.ctx(DebuggerConfig("jlink", None, "jtag", 1000))
        backend = KeilBackend(Runner())
        self.assertEqual(backend.build(ctx).status, "ok")
        self.assertEqual(backend.flash(ctx).status, "blocked")


if __name__ == "__main__": unittest.main()
