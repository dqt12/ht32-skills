import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ht32_runtime.backends import OperationContext
from ht32_runtime.models import DebuggerConfig, OperationResult
from ht32_runtime.paths import RuntimePaths
from ht32_runtime.process import ToolExecutionError
from ht32_runtime.serial import SerialError, TapReport
from ht32_runtime.workflow import ObservePlan, WorkflowStep, execute_workflow, load_project_state


class Backend:
    def __init__(self, statuses): self.statuses, self.calls = iter(statuses), []
    def _run(self, name, *args):
        self.calls.append(name); result = OperationResult.start(name, "jlink", "pid")
        result.log_path = f"global/{name}.log"; return result.finish(next(self.statuses))
    def build(self, ctx): return self._run("build")
    def flash(self, ctx, image=None): return self._run("flash")
    def debug(self, ctx): return self._run("debug")
    def rtt(self, ctx, elf=None, seconds=5): return self._run("rtt")


class FailingBackend(Backend):
    def __init__(self, log_path):
        super().__init__([])
        self.log_path = log_path

    def build(self, ctx):
        self.calls.append("build")
        raise ToolExecutionError("backend failed", log_path=self.log_path)


class ArtifactBackend(Backend):
    """build 产出 .axf，flash 记录收到的镜像（验证自动解析）。"""

    def __init__(self, statuses):
        super().__init__(statuses)
        self.images = []

    def build(self, ctx):
        result = super().build(ctx)
        result.artifacts = {".axf": ["out/demo.axf"], ".hex": []}
        return result

    def flash(self, ctx, image=None):
        self.images.append(image)
        return super().flash(ctx, image)


class WorkflowTest(unittest.TestCase):
    def setUp(self): self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name)
    def tearDown(self): self.temp.cleanup()
    def context(self, paths, backend):
        return OperationContext(self.root, paths.project_id, "chip", self.root/"p.uvprojx", None,
                                DebuggerConfig("jlink"), {}, paths.cache_dir, paths.logs_dir, backend)
    def test_warn_continues_and_state_is_global(self):
        with tempfile.TemporaryDirectory() as home, patch.dict(os.environ, {"USERPROFILE": home}, clear=False):
            paths = RuntimePaths.for_project(self.root); backend = Backend(["warn", "ok", "ok"])
            result = execute_workflow(self.context(paths, backend), [WorkflowStep("build", {}), WorkflowStep("flash", {}), WorkflowStep("debug", {})])
            self.assertEqual(backend.calls, ["build", "flash", "debug"]); self.assertEqual(result.status, "warn")
            self.assertEqual(load_project_state(paths)["status"], "warn"); self.assertFalse((self.root/".ht32-skills"/"state.json").exists())
    def test_state_write_failure_is_warning_not_crash(self):
        """全局状态目录不可写（宿主沙箱）时流程结果照常返回，只多一条 warning。"""
        with tempfile.TemporaryDirectory() as home:
            blocked = Path(home) / "blocked"
            blocked.write_text("x", encoding="utf-8")
            with patch.dict(os.environ, {"HT32_SKILLS_HOME": str(blocked)}, clear=False):
                paths = RuntimePaths.for_project(self.root)
                result = execute_workflow(self.context(paths, Backend(["ok"])), [WorkflowStep("build", {})])
        self.assertEqual((result.status, result.exit_code), ("ok", 0))
        self.assertTrue(any("流程状态未写入" in item for item in result.warnings))

    def test_flash_without_image_uses_build_artifact(self):
        with tempfile.TemporaryDirectory() as home, patch.dict(os.environ, {"USERPROFILE": home}, clear=False):
            paths = RuntimePaths.for_project(self.root)
            backend = ArtifactBackend(["ok", "ok"])
            execute_workflow(self.context(paths, backend),
                             [WorkflowStep("build", {}), WorkflowStep("flash", {})])
        self.assertEqual(backend.images, ["out/demo.axf"])

    def test_explicit_flash_image_wins(self):
        with tempfile.TemporaryDirectory() as home, patch.dict(os.environ, {"USERPROFILE": home}, clear=False):
            paths = RuntimePaths.for_project(self.root)
            backend = ArtifactBackend(["ok", "ok"])
            execute_workflow(self.context(paths, backend),
                             [WorkflowStep("build", {}), WorkflowStep("flash", {"image": "custom.bin"})])
        self.assertEqual(backend.images, ["custom.bin"])

    def test_blocked_and_error_short_circuit(self):
        for status in ("blocked", "error"):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as home, patch.dict(os.environ, {"USERPROFILE": home}, clear=False):
                paths = RuntimePaths.for_project(self.root); backend = Backend([status])
                result = execute_workflow(self.context(paths, backend), [WorkflowStep("build", {}), WorkflowStep("flash", {})])
                self.assertEqual(result.status, status); self.assertEqual(backend.calls, ["build"])
    def test_tool_execution_error_becomes_failed_step_and_saved_state(self):
        with tempfile.TemporaryDirectory() as home, patch.dict(os.environ, {"USERPROFILE": home}, clear=False):
            paths = RuntimePaths.for_project(self.root)
            log_path = paths.logs_dir / "build.log"
            backend = FailingBackend(log_path)

            result = execute_workflow(self.context(paths, backend), [WorkflowStep("build", {}), WorkflowStep("flash", {})])

            self.assertEqual(backend.calls, ["build"])
            self.assertEqual(result.status, "error")
            self.assertEqual(result.observations["steps"], [{
                "operation": "build", "status": "error", "log_path": str(log_path),
                "error": {"message": "backend failed"},
            }])
            state = load_project_state(paths)
            self.assertEqual(state["status"], "error")
            self.assertEqual(state["observations"]["steps"], result.observations["steps"])
    def test_non_tool_exception_is_not_converted(self):
        class BrokenBackend(Backend):
            def build(self, ctx):
                raise RuntimeError("runtime bug")

        with tempfile.TemporaryDirectory() as home, patch.dict(os.environ, {"USERPROFILE": home}, clear=False):
            paths = RuntimePaths.for_project(self.root)
            with self.assertRaisesRegex(RuntimeError, "runtime bug"):
                execute_workflow(self.context(paths, BrokenBackend([])), [WorkflowStep("build", {})])
            self.assertFalse(paths.state_file.exists())


class OrderedBackend(Backend):
    """把调用顺序记进共享列表，用于验证「观测先开、步骤后跑」。"""

    def __init__(self, statuses, order):
        super().__init__(statuses)
        self.order = order

    def build(self, ctx):
        self.order.append("build"); return super().build(ctx)

    def flash(self, ctx, image=None):
        self.order.append("flash"); return super().flash(ctx, image)


class FakeTap:
    """假观测采集器：记录 start/stop 顺序与窗口，返回指定字节。"""

    def __init__(self, order, *, data=b"", start_error=None, pre_flash_bytes=0,
                 reopen_events=None, chunks=None):
        self.order, self.data, self.start_error = order, data, start_error
        self.pre_flash_bytes = pre_flash_bytes
        self.reopen_events = reopen_events or []
        self.chunks = chunks or []
        self.port, self.baudrate, self.tail_seconds = "COM13", 115200, None

    def start(self):
        self.order.append("observe.start")
        if self.start_error is not None:
            raise self.start_error
        return self

    def bytes_so_far(self):
        return self.pre_flash_bytes

    def elapsed_ms(self):
        return 1000.0

    def stop(self, *, tail_seconds, silence_seconds=2.0, **_kwargs):
        self.order.append("observe.stop")
        self.tail_seconds = tail_seconds
        return TapReport(self.data, len(self.reopen_events), [], "timeout", silence_seconds,
                         list(self.chunks), list(self.reopen_events))


class WorkflowObserveTest(unittest.TestCase):
    """烧录前预开串口观测（ObservePlan）的契约。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def context(self, paths, backend):
        return OperationContext(self.root, paths.project_id, "chip", self.root / "p.uvprojx", None,
                                DebuggerConfig("jlink"), {}, paths.cache_dir, paths.logs_dir, backend)

    def observe_run(self, order, statuses, tap):
        with tempfile.TemporaryDirectory() as home, patch.dict(os.environ, {"USERPROFILE": home}, clear=False):
            paths = RuntimePaths.for_project(self.root)
            backend = OrderedBackend(statuses, order)
            result = execute_workflow(self.context(paths, backend),
                                      [WorkflowStep("build", {}), WorkflowStep("flash", {})],
                                      observe=ObservePlan(tap=tap, tail_seconds=15))
            self.log_path = str(result.artifacts.get("serial_log") or "")
            self.log_bytes = Path(self.log_path).read_bytes() if self.log_path else b""
        return result, backend

    def test_observe_opens_before_steps_and_carries_boot_log(self):
        order = []
        result, backend = self.observe_run(order, ["ok", "ok"],
                                           FakeTap(order, data=b"HT32F5xxxx boot\r\nLED1 = ON\r\n"))
        self.assertEqual(order, ["observe.start", "build", "flash", "observe.stop"])
        self.assertEqual(backend.calls, ["build", "flash"])
        self.assertEqual(result.status, "ok")
        observe = result.observations["observe"]
        self.assertEqual(observe["port"], "COM13")
        self.assertEqual(observe["tail_seconds"], 15)
        self.assertEqual(observe["lines"], ["HT32F5xxxx boot", "LED1 = ON"])
        self.assertEqual(observe["reopens"], 0)
        self.assertIn(b"HT32F5xxxx boot", self.log_bytes)
        self.assertNotIn(str(self.root), self.log_path)  # 采集原文只落全局日志

    def test_empty_capture_warns_but_keeps_step_status(self):
        order = []
        result, _ = self.observe_run(order, ["ok", "ok"], FakeTap(order, data=b""))
        self.assertEqual(result.status, "warn")
        self.assertTrue(any("未从 COM13 收到任何数据" in item for item in result.warnings))
        self.assertEqual(result.observations["observe"]["bytes_received"], 0)

    def test_observe_start_failure_degrades_to_warning_and_keeps_steps(self):
        order = []
        tap = FakeTap(order, start_error=SerialError("串口 COM13 被占用或拒绝访问（Win32 错误 5）"))
        result, backend = self.observe_run(order, ["ok", "ok"], tap)
        self.assertEqual(order, ["observe.start", "build", "flash"])
        self.assertEqual(backend.calls, ["build", "flash"])
        self.assertEqual(result.status, "ok")
        self.assertNotIn("observe", result.observations)
        self.assertNotIn("serial_log", result.artifacts)
        self.assertTrue(any("串口观测未启动" in item for item in result.warnings))

    def test_flash_failure_still_collects_observed_output(self):
        order = []
        result, _ = self.observe_run(order, ["ok", "error"], FakeTap(order, data=b"banner\r\n"))
        self.assertEqual(order, ["observe.start", "build", "flash", "observe.stop"])
        self.assertEqual(result.status, "error")
        self.assertEqual(result.observations["observe"]["lines"], ["banner"])

    def test_pre_flash_output_is_marked(self):
        """先开后烧：旧固件输出与烧录后输出必须能区分。"""
        order = []
        result, _ = self.observe_run(order, ["ok", "ok"],
                                     FakeTap(order, data=b"OLD\r\nNEW\r\n", pre_flash_bytes=5))
        observe = result.observations["observe"]
        self.assertEqual(observe["post_flash_offset"], 5)
        self.assertEqual(observe["post_flash_offset_source"], "flash_command_return")
        self.assertIn("近似", observe["post_flash_offset_note"])
        self.assertEqual(observe["flash_return_offset"], 5)
        self.assertIn(observe["stop_reason"], {"idle", "timeout"})
        self.assertIsNotNone(observe["silence_seconds"])
        self.assertIn(b"flash command returned", self.log_bytes)
        self.assertNotIn(b"below is post-reset output", self.log_bytes)
        self.assertFalse(any("全部来自烧录前" in item for item in result.warnings))

    def test_usb_reenumeration_moves_boundary_to_reset_not_flash_return(self):
        """UV4 返回比复位早约 1 s：有 USB 重新枚举时，分界要用重开那一刻。"""
        order = []
        tap = FakeTap(order, data=b"OLD\r\nboot\r\n", pre_flash_bytes=5,
                      reopen_events=[{"ms": 1500.0, "offset": 5}])
        result, _ = self.observe_run(order, ["ok", "ok"], tap)
        observe = result.observations["observe"]
        self.assertEqual(observe["post_flash_offset"], 5)
        self.assertEqual(observe["post_flash_offset_source"], "usb_reenumerate")
        self.assertNotIn("post_flash_offset_note", observe)
        self.assertIn(b"target reset detected", self.log_bytes)

    def test_reopen_before_flash_return_is_ignored(self):
        order = []
        tap = FakeTap(order, data=b"OLD\r\nNEW\r\n", pre_flash_bytes=5,
                      reopen_events=[{"ms": 500.0, "offset": 0}])
        result, _ = self.observe_run(order, ["ok", "ok"], tap)
        observe = result.observations["observe"]
        self.assertEqual(observe["post_flash_offset_source"], "flash_command_return")

    def test_all_pre_flash_output_warns_about_stale_capture(self):
        order = []
        result, _ = self.observe_run(order, ["ok", "ok"],
                                     FakeTap(order, data=b"OLD\r\n", pre_flash_bytes=5))
        self.assertTrue(any("全部来自烧录前" in item for item in result.warnings), result.warnings)

    def test_step_summary_and_artifacts_are_carried_into_steps(self):
        class SummaryBackend(Backend):
            def build(self, ctx):
                result = super().build(ctx)
                result.observations["summary"] = {"errors": 0, "warnings": 0}
                result.artifacts["tool_log"] = "global/uv4.log"
                return result

        with tempfile.TemporaryDirectory() as home, patch.dict(os.environ, {"USERPROFILE": home}, clear=False):
            paths = RuntimePaths.for_project(self.root)
            result = execute_workflow(self.context(paths, SummaryBackend(["ok"])), [WorkflowStep("build", {})])
        self.assertEqual(result.observations["steps"][0]["summary"], {"errors": 0, "warnings": 0})
        self.assertEqual(result.observations["steps"][0]["artifacts"], {"tool_log": "global/uv4.log"})


if __name__ == "__main__": unittest.main()
