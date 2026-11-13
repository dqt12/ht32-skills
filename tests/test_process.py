import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from ht32_runtime.paths import RuntimePaths
from ht32_runtime.process import BackgroundProcess, ToolExecutionError, run_process, start_process


class ProcessTest(unittest.TestCase):
    def setUp(self):
        self.fake_tool = Path(__file__).parent / "fixtures" / "tools" / "fake_tool.py"
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def run_fake(self, *args, timeout_seconds=5):
        log_path = self.root / "logs" / "process.log"
        return run_process(
            [sys.executable, str(self.fake_tool), *args],
            cwd=self.root,
            timeout_seconds=timeout_seconds,
            log_path=log_path,
        )

    def test_captures_stdout_stderr_and_nonzero_exit(self):
        result = self.run_fake("--stdout", "hello", "--stderr", "problem", "--exit-code", "7")
        self.assertEqual(result.returncode, 7)
        self.assertEqual(result.stdout.strip(), "hello")
        self.assertEqual(result.stderr.strip(), "problem")
        self.assertFalse(result.timed_out)
        self.assertTrue(result.log_path.is_absolute())
        log = result.log_path.read_text(encoding="utf-8")
        self.assertIn("[stdout]\nhello", log)
        self.assertIn("[stderr]\nproblem", log)

    def test_timeout_returns_partial_output(self):
        result = self.run_fake("--stdout", "started", "--sleep", "2", timeout_seconds=1)
        self.assertTrue(result.timed_out)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("started", result.stdout)

    def test_log_command_redacts_password_and_token_values(self):
        secret_password = "password-secret"
        secret_token = "token-secret"
        result = self.run_fake("--password", secret_password, "--token", secret_token)
        log = result.log_path.read_text(encoding="utf-8")
        self.assertNotIn(secret_password, log)
        self.assertNotIn(secret_token, log)
        self.assertIn("--password ***", log)
        self.assertIn("--token ***", log)
        self.assertIn(secret_password, result.argv)
        self.assertIn(secret_token, result.argv)

    def test_log_path_none_still_runs_and_returns_no_log(self):
        """日志目录不可写时的降级路径：命令照跑，不返回日志路径。"""
        result = run_process(
            [sys.executable, str(self.fake_tool), "--stdout", "hi"],
            cwd=self.root,
            timeout_seconds=5,
            log_path=None,
        )
        self.assertIsNone(result.log_path)
        self.assertEqual(result.stdout.strip(), "hi")

    @patch("ht32_runtime.process.subprocess.run", side_effect=PermissionError("denied"))
    def test_process_execution_failure_is_wrapped(self, _run):
        with self.assertRaises(ToolExecutionError):
            run_process(["tool"], cwd=self.root, timeout_seconds=1, log_path=self.root / "run.log")

    @patch("ht32_runtime.process.subprocess.Popen", side_effect=PermissionError("denied"))
    def test_process_start_failure_is_wrapped(self, _popen):
        with self.assertRaises(ToolExecutionError):
            start_process(["tool"], cwd=self.root, log_path=self.root / "start.log")

    def test_missing_executable_remains_distinguishable(self):
        missing = self.root / "missing-tool.exe"
        with self.assertRaises(FileNotFoundError):
            run_process([missing], cwd=self.root, timeout_seconds=1, log_path=self.root / "run.log")

    def test_runtime_log_path_is_in_global_project_directory(self):
        with tempfile.TemporaryDirectory() as home:
            with patch.dict(os.environ, {"USERPROFILE": home}, clear=False):
                paths = RuntimePaths.for_project(self.root)
                paths.ensure_global_dirs()
                result = run_process(
                    [sys.executable, str(self.fake_tool)],
                    cwd=self.root,
                    timeout_seconds=5,
                    log_path=paths.logs_dir / "operation.log",
                )
        self.assertTrue(result.log_path.is_relative_to(paths.logs_dir.resolve()))
        self.assertFalse((self.root / "logs").exists())
        self.assertFalse((self.root / "state").exists())
        self.assertFalse((self.root / "cache").exists())

    def test_background_stop_closes_log_when_poll_fails(self):
        process = Mock()
        process.poll.side_effect = OSError("poll failed")
        handle = Mock()

        with self.assertRaisesRegex(OSError, "poll failed"):
            BackgroundProcess(process, self.root / "bg.log", handle).stop()

        handle.close.assert_called_once_with()

    def test_background_stop_kills_and_closes_log_when_terminate_fails(self):
        process = Mock()
        process.poll.return_value = None
        process.terminate.side_effect = OSError("terminate failed")
        handle = Mock()

        BackgroundProcess(process, self.root / "bg.log", handle).stop()

        process.kill.assert_called_once_with()
        process.wait.assert_called_once_with(timeout=5)
        handle.close.assert_called_once_with()

    def test_background_stop_kills_after_wait_failure(self):
        process = Mock()
        process.poll.return_value = None
        process.wait.side_effect = [OSError("wait failed"), None]
        handle = Mock()

        BackgroundProcess(process, self.root / "bg.log", handle).stop(timeout_seconds=2)

        process.terminate.assert_called_once_with()
        process.kill.assert_called_once_with()
        self.assertEqual(process.wait.call_count, 2)
        process.wait.assert_called_with(timeout=2)
        handle.close.assert_called_once_with()

    def test_background_stop_reports_failure_after_kill_and_final_wait_fail(self):
        process = Mock()
        process.poll.return_value = None
        process.wait.side_effect = [subprocess.TimeoutExpired("tool", 1), OSError("final wait failed")]
        process.kill.side_effect = OSError("kill failed")
        handle = Mock()
        background = BackgroundProcess(process, self.root / "bg.log", handle)

        with self.assertRaisesRegex(ToolExecutionError, "无法停止后台进程") as raised:
            background.stop(timeout_seconds=1)

        self.assertEqual(raised.exception.log_path, background.log_path)
        process.kill.assert_called_once_with()
        self.assertEqual(process.wait.call_count, 2)
        handle.close.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
