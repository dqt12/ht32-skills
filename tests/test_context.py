"""工程上下文（第 9 节计划）、运行诊断与编码的回归测试。"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from ht32_runtime.backends.keil import _flash_diagnosis
from ht32_runtime.context import parse_plan, plan_warnings, project_context, read_plan
from ht32_runtime.environment import armcc_info, uv4_info
from ht32_runtime.process import console_encoding, run_process
from ht32_runtime.serial import render_timed_lines, timing_summary


PLAN = """# HT32_PROJECT.md — Demo

## 9. 本次执行计划与进度

### 任务：初始化工程（2026-10-28 起）

- [x] P7b 基线编译（0 Error 0 Warning）
- [ ] P8a 烧录（application_running=true）
- [!] P8b 串口观测（跳过：本次未要求）
"""


class ContextTest(unittest.TestCase):
    def test_parse_plan_three_states(self):
        steps = parse_plan(PLAN)
        self.assertEqual(steps["P7b"]["status"], "done")
        self.assertEqual(steps["P8a"]["status"], "pending")
        self.assertEqual(steps["P8b"]["status"], "failed")

    def test_project_context_detects_files_and_plan(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "HT32_PROJECT.md").write_text(PLAN, encoding="utf-8")
            (root / "AGENTS.md").write_text(
                "<!-- ht32-project-guide:start -->\n## HT32 项目上下文\n<!-- ht32-project-guide:end -->\n",
                encoding="utf-8")
            status = project_context(root)
            self.assertEqual(status, {"ht32_project": "present",
                                      "agents_guide": "present", "plan": "present"})
            self.assertEqual(read_plan(root)["P8a"]["status"], "pending")

    def test_project_context_reports_missing_and_partial_guide(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "AGENTS.md").write_text("<!-- ht32-project-guide:start -->\n", encoding="utf-8")
            status = project_context(root)
            self.assertEqual(status["ht32_project"], "missing")
            self.assertEqual(status["agents_guide"], "partial")
            self.assertEqual(status["plan"], "missing")

    def test_plan_warns_only_when_failed_step_marked_done(self):
        plan = parse_plan(PLAN)
        warnings = plan_warnings([{"operation": "build", "status": "error"}], plan)
        self.assertEqual(len(warnings), 1)
        self.assertIn("P7b", warnings[0])
        # build 成功但还没勾 -> 不告警
        self.assertEqual(plan_warnings([{"operation": "build", "status": "ok"}], plan), [])


class EncodingTest(unittest.TestCase):
    def test_console_encoding_is_platform_specific(self):
        self.assertEqual(console_encoding(), "mbcs" if os.name == "nt" else "utf-8")

    def test_run_process_decodes_non_utf8_bytes_with_utf8_mode(self):
        """PYTHONUTF8=1 下 GBK 字节不能让 stdout 变成 None。"""
        env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
        script = "import sys; sys.stdout.buffer.write(bytes([0xcf,0xd6,0xce,0xc4]))"
        with tempfile.TemporaryDirectory() as directory:
            result = run_process([sys.executable, "-c", script], cwd=Path(directory),
                                 timeout_seconds=20, log_path=None, env=env)
        self.assertIsNotNone(result.stdout)
        self.assertTrue(result.stdout.strip())


class DiagnosticsTest(unittest.TestCase):
    def test_flash_diagnosis_flags_missing_registry_and_capacity_mismatch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            uvprojx = root / "demo.uvprojx"
            uvprojx.write_text(
                "<Project><Targets><Target><TargetOption><IROM><Size>0x8000</Size></IROM>"
                "</TargetOption></Target></Targets></Project>", encoding="utf-8")
            uvprojx.with_suffix(".uvoptx").write_text(
                "<Project><Target><DebugOpt><pMon>BIN\\CMSIS_AGDI.dll</pMon></DebugOpt>"
                "<TargetDriverDllRegistry><SetRegEntry><Key>UL2CM3</Key>"
                "<Name>UL2CM3(-S0 -FC1000 -FL010000)</Name></SetRegEntry>"
                "</TargetDriverDllRegistry></Target></Project>", encoding="utf-8")
            diagnosis = _flash_diagnosis(SimpleNamespace(uvprojx=uvprojx))
            self.assertIn("CMSIS_AGDI", " ".join(diagnosis["issues"]))
            self.assertIn("IROM", " ".join(diagnosis["issues"]))
            self.assertEqual(diagnosis["params"]["fc"], 0x1000)
            self.assertEqual(diagnosis["params"]["fl0"], 0x10000)

    def test_env_version_helpers_degrade_with_reason(self):
        self.assertEqual(uv4_info(None)["version"], None)
        armcc = armcc_info(None)
        self.assertFalse(armcc["available"])
        self.assertTrue(armcc["reason"])


class SerialTimingTest(unittest.TestCase):
    def test_render_timed_lines_and_summary(self):
        data = b"AB\nCD\n"
        chunks = [{"offset": 0, "ms": 107.0, "bytes": 3},
                  {"offset": 3, "ms": 214.0, "bytes": 3}]
        self.assertEqual(render_timed_lines(data, chunks),
                         ["[107.0ms] AB", "[214.0ms] CD"])
        summary = timing_summary(chunks)
        self.assertEqual(summary["first_byte_ms"], 107.0)
        self.assertEqual(summary["last_byte_ms"], 214.0)
        self.assertEqual(summary["median_interval_ms"], 107.0)
        self.assertEqual(timing_summary([])["chunk_count"], 0)


if __name__ == "__main__":
    unittest.main()
