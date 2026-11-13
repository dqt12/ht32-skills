import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENTRY = ROOT / "runtime" / "ht32.py"


class CliTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "demo.uvprojx").write_text("<Project><TargetName>Demo</TargetName></Project>", encoding="utf-8")
        (self.root / "HT32_PROJECT.md").write_text("# HT32_PROJECT.md — Demo\n", encoding="utf-8")
        self.home = tempfile.TemporaryDirectory()
        self.env = dict(os.environ, USERPROFILE=self.home.name, HOME=self.home.name, PYTHONPATH=str(ROOT / "runtime"))

    def tearDown(self):
        self.home.cleanup()
        self.temp.cleanup()

    def run_cli(self, *args):
        completed = subprocess.run(
            [sys.executable, str(ENTRY), *map(str, args)],
            cwd=ROOT,
            env=self.env,
            capture_output=True,
            text=True,
            # env check 会探测 PowerShell/CIM/调试器，冷启动在重负载下可超过 10s
            timeout=40,
        )
        self.assertEqual(completed.stderr, "")
        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            self.fail(f"stdout is not pure JSON: {completed.stdout!r}: {exc}")
        return completed.returncode, payload

    def init(self, *extra):
        return self.run_cli("config", "init", "--project", self.root, "--chip", "HT32F52352", "--uvprojx", "demo.uvprojx", *extra)

    def test_config_tree_show_validate_and_transient_override(self):
        code, result = self.init()
        self.assertEqual((code, result["status"]), (0, "ok"))
        code, shown = self.run_cli("config", "show", "--project", self.root, "--speed-khz", "2000")
        self.assertEqual(code, 0)
        self.assertEqual(shown["observations"]["config"]["debugger"]["speed_khz"], 2000)
        _, persisted = self.run_cli("config", "show", "--project", self.root)
        self.assertEqual(persisted["observations"]["config"]["debugger"]["speed_khz"], 1000)
        code, validated = self.run_cli("config", "validate", "--project", self.root)
        self.assertEqual((code, validated["status"]), (0, "ok"))

    def test_config_init_echoes_resolved_values(self):
        """config init 必须回显落盘后的解析值（uvprojx 已归一化、debugger 已套默认），不是入参回声。"""
        code, result = self.init()
        self.assertEqual((code, result["status"]), (0, "ok"))
        observations = result["observations"]
        self.assertEqual(observations["project"]["chip"], "HT32F52352")
        self.assertEqual(Path(observations["project"]["uvprojx"]), (self.root / "demo.uvprojx").resolve())
        self.assertEqual(observations["debugger"]["backend"], "keil")
        self.assertEqual(observations["debugger"]["speed_khz"], 1000)
        self.assertEqual(observations["config_file"], result["artifacts"]["config"])
        self.assertTrue(Path(observations["config_file"]).is_file())

    def test_config_init_warns_when_context_missing(self):
        """HT32_PROJECT.md 缺失只提示（warn），不阻断 config init。"""
        (self.root / "HT32_PROJECT.md").unlink()
        code, result = self.init()
        self.assertEqual(code, 0)
        self.assertEqual(result["status"], "warn")
        self.assertEqual(result["observations"]["project_context"]["ht32_project"], "missing")
        self.assertTrue(any("HT32_PROJECT.md 缺失" in w for w in result["warnings"]))

    def test_env_check_without_project_reports_null_project_id(self):
        """裸机 env check 没有工程上下文：project_id 留 null，不拿 cwd 硬算。"""
        code, payload = self.run_cli("env", "check")
        self.assertIn(code, {0, 2})
        self.assertIsNone(payload["project_id"])
        self.assertEqual(payload["observations"]["project_source"], "none")

    def test_fetch_extract_flag_is_accepted(self):
        code, payload = self.run_cli("fetch", "--url", "http://mcp.example/bundle.zip",
                                     "--out", self.root / "bundle.zip", "--extract")
        self.assertEqual((code, payload["status"]), (2, "blocked"))
        self.assertIn("HTTPS", payload["error"]["message"])

    def test_version_reports_skill_version_without_project(self):
        """追溯入口：version 不需要工程上下文，读仓库/安装包 VERSION 文件。"""
        code, result = self.run_cli("version")
        self.assertEqual((code, result["status"]), (0, "ok"))
        version = result["observations"]["skill_version"]
        self.assertRegex(version, r"^v?\d+\.\d+\.\d+$")
        self.assertTrue(result["observations"]["source"])

    def test_version_update_check_cache_and_ttl(self):
        """更新检查：无缓存时 check_due；记录云端版本后 24h 内不再查；过期重新到期。"""
        code, fresh = self.run_cli("version")
        self.assertEqual(code, 0)
        self.assertTrue(fresh["observations"]["update"]["check_due"])
        self.assertFalse(fresh["observations"]["update"]["cache_valid"])

        code, recorded = self.run_cli("version", "--record-update", "v9.9.9")
        self.assertEqual(code, 0)
        update = recorded["observations"]["update"]
        self.assertEqual(update["latest"], "v9.9.9")
        self.assertEqual(recorded["artifacts"]["update_cache"], update["cache_file"])
        self.assertFalse(update["check_due"])       # 刚查过，TTL 内
        self.assertTrue(update["cache_valid"])
        self.assertEqual(update["relation"], "behind")
        self.assertTrue(update["behind"])           # 本机版本 < 云端最新
        self.assertFalse(update["latest_may_be_stale"])

        cache = Path(update["cache_file"])
        payload = json.loads(cache.read_text(encoding="utf-8"))
        payload["checked_at"] = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
        cache.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

        _, again = self.run_cli("version")
        self.assertTrue(again["observations"]["update"]["check_due"])
        self.assertTrue(again["observations"]["update"]["latest_may_be_stale"])
        self.assertGreater(again["observations"]["update"]["age_hours"], 24)

    def test_exact_command_tree_is_accepted(self):
        self.init()
        commands = [
            ("env", "check"),
            ("keil", "scan"), ("keil", "targets"), ("keil", "build"),
            ("keil", "rebuild"), ("keil", "clean"), ("keil", "flash"), ("keil", "debug"),
            ("jlink", "probes"), ("jlink", "connect"), ("jlink", "flash"), ("jlink", "reset"),
            ("jlink", "read-memory", "--address", "0x20000000", "--count", "1"),
            ("jlink", "write-memory", "--address", "0x20000000", "--values", "1", "--yes"),
            ("jlink", "registers"), ("jlink", "write-register", "--name", "r0", "--value", "1", "--yes"),
            ("jlink", "debug", "--elf", self.root / "fw.elf", "--actions", "break main,backtrace"),
            ("jlink", "rtt", "--elf", self.root / "fw.elf", "--seconds", "1"),
            ("workflow", "run", "--steps", "build,flash"),
            ("workflow", "run", "--steps", "build,flash", "--observe-port", "COM99",
             "--observe-baud", "115200", "--observe-seconds", "1"),
        ]
        for command in commands:
            with self.subTest(command=command):
                code, payload = self.run_cli(*command, "--project", self.root)
                self.assertIn(code, {0, 2, 3})
                self.assertIn(payload["status"], {"ok", "warn", "blocked", "error"})

    def test_workflow_observe_invalid_port_is_blocked(self):
        self.init()
        code, payload = self.run_cli("workflow", "run", "--steps", "build,flash",
                                     "--observe-port", "NOTAPORT", "--project", self.root)
        self.assertEqual((code, payload["status"]), (2, "blocked"))
        self.assertIn("不合法", payload["error"]["message"])

    def test_write_commands_require_yes(self):
        self.init()
        for command in [
            ("jlink", "write-memory", "--address", "0x20000000", "--values", "1"),
            ("jlink", "write-register", "--name", "r0", "--value", "1"),
        ]:
            code, result = self.run_cli(*command, "--project", self.root)
            self.assertEqual((code, result["status"]), (2, "blocked"))

    def test_external_tool_nonzero_is_single_error_json_exit_three(self):
        fake_tool = ROOT / "tests" / "fixtures" / "tools" / "fake_tool.py"
        launcher = self.root / "fake-tool.cmd"
        launcher.write_text(f'@"{sys.executable}" "{fake_tool}" --exit-code 7\n', encoding="utf-8")
        config_dir = Path(self.home.name) / ".ht32-skills"
        config_dir.mkdir(parents=True)
        (config_dir / "config.json").write_text(json.dumps({
            "schema_version": 1,
            "tools": {"uv4_exe": str(launcher)},
            "defaults": {"debugger": {"backend": "keil"}},
        }), encoding="utf-8")
        self.init()

        completed = subprocess.run(
            [sys.executable, str(ENTRY), "keil", "build", "--project", str(self.root)],
            cwd=ROOT, env=self.env, capture_output=True, text=True, timeout=10,
        )

        self.assertEqual(completed.stderr, "")
        self.assertEqual(completed.stdout.count("\n"), 1)
        payload = json.loads(completed.stdout)
        self.assertEqual((completed.returncode, payload["status"]), (3, "error"))

    def test_fetch_refuses_plain_http(self):
        code, result = self.run_cli("fetch", "--url", "http://example.com/x.zip",
                                    "--out", self.root / "x.zip")
        self.assertEqual((code, result["status"]), (2, "blocked"))
        self.assertIn("HTTPS", result["error"]["message"])

    def test_version_record_update_degrades_when_data_home_unwritable(self):
        blocked = self.root / "blocked"
        blocked.write_text("x", encoding="utf-8")
        env = dict(self.env, HT32_SKILLS_HOME=str(blocked))
        completed = subprocess.run(
            [sys.executable, str(ENTRY), "version", "--record-update", "v9.9.9"],
            cwd=ROOT, env=env, capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(completed.stderr, "")
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["status"], "warn")
        self.assertIn("缓存", payload["warnings"][0])

    def test_unknown_and_invalid_cli_return_json_exit_two(self):
        for command in [("unknown",), ("workflow", "run", "--project", self.root, "--steps", "build,erase")]:
            code, result = self.run_cli(*command)
            self.assertEqual((code, result["status"]), (2, "blocked"))

    def test_env_check_without_project_returns_json_not_argparse_error(self):
        """env check 不强制 --project：裸机环境也返回统一 JSON，不 argparse exit 2。"""
        completed = subprocess.run(
            [sys.executable, str(ENTRY), "env", "check"],
            cwd=self.root, env=self.env, capture_output=True, text=True, timeout=40,
        )
        self.assertEqual(completed.stderr, "")
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["operation"], "env-check")
        self.assertIn(payload["status"], {"ok", "warn", "blocked"})
        self.assertNotIn("required", completed.stdout)

    def test_piped_stdout_is_ascii_json(self):
        """管道（非 tty）下输出 ASCII JSON：宿主按 UTF-8 解码不会把 GBK 中文读成乱码。"""
        completed = subprocess.run(
            [sys.executable, str(ENTRY), "env", "check"],
            cwd=self.root, env=self.env, capture_output=True, timeout=40,
        )
        self.assertEqual(completed.stderr, b"")
        self.assertTrue(completed.stdout.isascii(), completed.stdout[:200])
        self.assertIn(b"\\u", completed.stdout)   # 中文按 \uXXXX 转义，而不是 GBK 字节
        payload = json.loads(completed.stdout.decode("ascii"))
        self.assertEqual(payload["operation"], "env-check")


if __name__ == "__main__":
    unittest.main()
