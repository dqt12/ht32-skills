import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from ht32_runtime.cli import build_parser


ROOT = Path(__file__).resolve().parents[1]
FIELDS = ("backend", "probe_serial", "interface", "speed_khz")
TEMPLATE = "templates/HT32_PROJECT.template.md"
TEMPLATE_MIRROR = "skills/ht32/templates/HT32_PROJECT.template.md"
GUIDE = "templates/AGENTS.guide.md"
GUIDE_MIRROR = "skills/ht32/templates/AGENTS.guide.md"
GUIDE_START = "<!-- ht32-project-guide:start -->"
GUIDE_END = "<!-- ht32-project-guide:end -->"


def powershell(script):
    # errors="replace"：PowerShell 的输出编码与控制台解码不一致时（新版 Python 的
    # locale 解码比 3.11 更严格）不要让断言消息本身炸掉——这里只断言 returncode
    return subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
                          capture_output=True, text=True, errors="replace")


class GlobalContractsTest(unittest.TestCase):
    def test_template_fields_and_byte_identical_mirror(self):
        source = (ROOT / TEMPLATE).read_bytes()
        self.assertEqual(source, (ROOT / TEMPLATE_MIRROR).read_bytes())
        text = source.decode("utf-8-sig")
        for field in FIELDS:
            self.assertIn("{{debugger_" + field + "}}", text)
        self.assertNotIn("{{debugger_serial}}", text)
        self.assertIn("<!-- ht32-project:template-version:10 -->", text)
        self.assertIn("{{code_conventions}}", text)
        self.assertIn("{{components}}", text)
        self.assertIn("{{component_decision}}", text)
        self.assertIn("# HT32_PROJECT.md — {{project_name}}", text)
        self.assertNotIn("# AGENTS.md —", text)
        self.assertFalse((ROOT / "templates/AGENTS.template.md").exists())
        self.assertFalse((ROOT / "skills/ht32/templates/AGENTS.template.md").exists())

    def test_agents_guide_is_fixed_minimal_and_mirrored(self):
        guide = (ROOT / GUIDE).read_text(encoding="utf-8-sig")
        self.assertEqual((ROOT / GUIDE).read_bytes(), (ROOT / GUIDE_MIRROR).read_bytes())
        self.assertEqual(guide.count(GUIDE_START), 1)
        self.assertEqual(guide.count(GUIDE_END), 1)
        self.assertIn("HT32_PROJECT.md", guide)
        self.assertNotIn("{{", guide)

    def test_powershell_scripts_keep_utf8_bom(self):
        """两个 .ps1 必须保留 UTF-8 BOM：丢了之后 PowerShell 5.1 按 ANSI 解码，中文直接语法错。

        编辑器/工具重写文件时很容易丢 BOM（本次改 check_local_tools.ps1 就踩过一次），
        用回归守住，比靠人眼可靠。
        """
        for relative in ("scripts/validate_template.ps1", "skills/ht32/scripts/check_local_tools.ps1"):
            with self.subTest(script=relative):
                self.assertEqual((ROOT / relative).read_bytes()[:3], b"\xef\xbb\xbf")

    def test_cmsis_dap_probe_has_registry_fallback(self):
        """宿主沙箱会拦 CIM/Get-PnpDevice：探测脚本必须能回退注册表，
        并把"探测受限"与"确实没有"区分开（否则会误报成"没接调试器"，逼用户重插）。"""
        script = (ROOT / "skills/ht32/scripts/check_local_tools.ps1").read_text(encoding="utf-8-sig")
        self.assertIn(r"Enum\USB", script)
        self.assertIn("probe_limited", script)
        self.assertIn("probe_source", script)
        self.assertIn("unknown", script)

    def test_validator_rejects_missing_fields_legacy_and_non_heading(self):
        original = (ROOT / TEMPLATE).read_text(encoding="utf-8-sig")
        for mutation in (*FIELDS, "legacy", "heading", "title", "anchor", "section8"):
            text = original
            if mutation in FIELDS:
                text = text.replace("{{debugger_" + mutation + "}}", "missing")
            elif mutation == "legacy":
                text += "\n{{debugger_serial}}\n"
            elif mutation == "heading":
                text = text.replace("## 7. 工具链与版本记录", "prefix ## 7. 工具链与版本记录")
            elif mutation == "title":
                text = text.replace("# HT32_PROJECT.md — {{project_name}}", "# AGENTS.md — {{project_name}}")
            elif mutation == "anchor":
                text = text.replace("<!-- ht32-project:template-version:10 -->", "<!-- no anchor -->")
            else:
                text = text.replace("## 8. 代码规范与约定", "## 8. 附则")
            with tempfile.TemporaryDirectory() as directory:
                template = Path(directory) / "template.md"
                template.write_text(text, encoding="utf-8")
                result = powershell(f"& '{ROOT / 'scripts/validate_template.ps1'}' -Template '{template}'")
                with self.subTest(mutation=mutation):
                    self.assertEqual(result.returncode, 1, result.stdout + result.stderr)

    def test_version_file_is_three_lines(self):
        """VERSION 固定 3 行（AGENTS.md 核心规则 16）：版本号 / Date / Skill。

        历史上这份文件长成了一整段 run-on 变更史（2.4 KB），既难读又会被安装包
        原样带到用户机器上；变更史只该进 log.md 与 git commit。
        """
        text = (ROOT / "VERSION").read_text(encoding="utf-8-sig")
        lines = [ln for ln in text.splitlines() if ln.strip()]
        self.assertLessEqual(len(lines), 3, f"VERSION 最多 3 行，实际 {len(lines)} 行：{lines}")
        self.assertLessEqual(len(text.encode("utf-8")), 300, "VERSION 体积超过 300 字节")
        self.assertRegex(lines[0], r"^v\d+\.\d+\.\d+$")
        self.assertTrue(lines[1].startswith("Date: "), lines[1])
        self.assertEqual(lines[2], "Skill: ht32")
        # 首行版本号与 SKILL.md frontmatter 的 version 必须一致（核心规则 13）
        skill = (ROOT / "skills" / "ht32" / "SKILL.md").read_text(encoding="utf-8-sig")
        match = re.search(r"(?m)^version:\s*([\w.-]+)$", skill)
        self.assertIsNotNone(match, "SKILL.md frontmatter 缺少 version")
        self.assertEqual(lines[0].lstrip("v"), match.group(1))

    def test_skill_build_routes_and_debugger_snapshot(self):
        for name in ("project-create", "dev-assist"):
            text = (ROOT / "skills" / "ht32" / "workflows" / f"{name}.md").read_text(encoding="utf-8-sig")
            self.assertNotRegex(text, r"宿主\s*\*\*keil\*\*|UV4路径")
        text = (ROOT / "skills/ht32/workflows/project-context.md").read_text(encoding="utf-8-sig")
        self.assertNotIn("{{debugger_serial}}", text)
        for field in FIELDS:
            self.assertIn("{{debugger_" + field + "}}", text)

    def test_readme_names_the_project_context_source_of_truth(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8-sig")
        self.assertIn("HT32_PROJECT.md", readme)
        self.assertIn("AGENTS.md", readme)
        self.assertIn("project-context", readme)
        self.assertNotIn("AGENTS.md 模板", readme)
        self.assertNotIn("`agents-doc`", readme)
        self.assertNotIn("AGENTS.template.md", readme)

    def test_documented_executable_commands_parse(self):
        parser = build_parser()
        commands = []
        for path in (ROOT / "skills" / "ht32").rglob("*.md"):
            for line in path.read_text(encoding="utf-8-sig").splitlines():
                line = line.strip()
                if not line.startswith('python "<global-skill-root>'):
                    continue
                tokens = re.findall(r'"[^"]*"|\S+', line)
                argv = [token.strip('"') for token in tokens[2:]]
                commands.append(argv)
                with self.subTest(doc=path.relative_to(ROOT / "skills"), argv=argv):
                    parser.parse_args(argv)
        self.assertGreaterEqual(len(commands), 16)

    def test_powershell_scripts_have_utf8_bom(self):
        for name in ("validate_template.ps1",):
            self.assertTrue((ROOT / "scripts" / name).read_bytes().startswith(b"\xef\xbb\xbf"))


if __name__ == "__main__":
    unittest.main()
