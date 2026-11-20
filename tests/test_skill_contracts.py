import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAIN_SKILL = "ht32"
WORKFLOWS = {"project-context", "debug", "dev-assist", "env-check", "flash", "jlink", "keil", "migrate", "project-create", "select", "uart-print", "workflow", "troubleshoot"}
LEGACY_ALT = r"agents-doc|debug|dev-assist|env-check|flash|jlink|keil|project-create|select|workflow"
# 与 wiki 知识页重名（ht32-jlink 等）的裸名引用合法；只禁止旧 Skill 的使用方式
BANNED_BARE = r"ht32-(?:agents-doc|debug|dev-assist|env-check|keil|project-create|select|workflow)\b"
SLASH_CMD = rf"/ht32-(?:{LEGACY_ALT})\b"
BOLD_NAME = rf"\*\*ht32-(?:{LEGACY_ALT})\*\*"
SKILL_MD_REF = rf"ht32-(?:{LEGACY_ALT})/SKILL\.md"
# 纪律（2026-10-16 起）：不得委派宿主 skill——禁止把执行外包给宿主其他 Skill 的表述。
# 注意 SKILL.md 的"不委派宿主 Skill"纪律本身是禁令而非委派，不匹配下列模式
#（"调用宿主其他 Skill""外包给宿主——"后面跟的都不是 keil/jlink/serial）。
HOST_SKILL_DELEGATION = (
    r"宿主\s*[Ss]kill\s*(?:机制|目录)"                                  # 跨 Skill 机制/目录表述
    r"|宿主\s*\*\*(?:keil|jlink|serial)\*\*"                             # 加粗宿主 skill 名
    r"|宿主\s+(?:keil|jlink|serial)\s+[Ss]kill"                          # 宿主 keil/jlink/serial skill
    r"|(?:调用|使用|借助|委派|委托|转交|转给|外包给?)\s*宿主\s*\**\s*(?:keil|jlink|serial)"  # 委托动作
)
# 承载 HT32 事实的工作流：必须显式提到唯一事实源 HT32_PROJECT.md
CONTEXT_WORKFLOWS = ("project-context", "project-create", "dev-assist", "migrate")


class SkillContractsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.main = (ROOT / "skills" / MAIN_SKILL / "SKILL.md").read_text(encoding="utf-8-sig")
        cls.docs = {name: (ROOT / "skills" / MAIN_SKILL / "workflows" / f"{name}.md").read_text(encoding="utf-8-sig") for name in WORKFLOWS}

    def test_single_host_entry_and_complete_workflows(self):
        self.assertEqual({path.name for path in (ROOT / "skills").iterdir() if path.is_dir()}, {MAIN_SKILL})
        self.assertRegex(self.main, rf"(?m)^name:\s*{MAIN_SKILL}\s*$")
        self.assertEqual({path.stem for path in (ROOT / "skills" / MAIN_SKILL / "workflows").glob("*.md")}, WORKFLOWS)

    def test_route_and_maintenance_inventories_match_filesystem(self):
        route_lines = [line for line in self.main.splitlines() if line.startswith("|") and "workflows/" in line]
        routed = {
            path
            for line in route_lines
            for path in re.findall(r"workflows/[\w-]+\.md", line)
        }
        actual = {f"workflows/{path.name}" for path in (ROOT / "skills" / MAIN_SKILL / "workflows").glob("*.md")}
        self.assertEqual(routed, actual)

        agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8-sig")
        inventory = agents.split("## 目录结构", 1)[1].split("\n## ", 1)[0]
        workflows_section = inventory.split("workflows/", 1)[1].split("references/", 1)[0]
        references_section = inventory.split("references/", 1)[1].split("assets/", 1)[0]
        documented_workflows = set(re.findall(r"[\w-]+\.md", workflows_section))
        documented_references = set(re.findall(r"[\w-]+\.md", references_section))
        expected_workflows = {Path(path).name for path in actual}
        expected_references = {
            path.name for path in (ROOT / "skills" / MAIN_SKILL / "references").glob("*.md")
        }
        self.assertEqual(documented_workflows, expected_workflows)
        self.assertEqual(documented_references, expected_references)

    def test_public_inventory_counts_match_repository_sources(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8-sig")
        agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8-sig")
        workflows = list((ROOT / "skills" / MAIN_SKILL / "workflows").glob("*.md"))
        cli = (ROOT / "runtime" / "ht32_runtime" / "cli.py").read_text(encoding="utf-8-sig")
        template = (ROOT / "templates" / "HT32_PROJECT.template.md").read_text(encoding="utf-8-sig")
        version = re.search(r"ht32-project:template-version:(\d+)", template).group(1)
        sections = re.findall(r"(?m)^## \d+\.", template)
        placeholders = set(re.findall(r"\{\{[\w-]+\}\}", template))
        command_groups = re.findall(r'groups\.add_parser\("([\w-]+)"', cli)
        version_line = (ROOT / "VERSION").read_text(encoding="utf-8-sig").splitlines()[0]
        skill_version = re.search(r"(?m)^version:\s*([\w.-]+)$", self.main).group(1)

        for document in (readme, agents):
            self.assertIn(f"**{len(workflows)} 个**", document)
        self.assertIn(f"模板 v{version}", readme)
        self.assertIn(f"{len(sections)} 个固定 section", readme)
        self.assertIn(f"{len(placeholders)} 个占位符", readme)
        self.assertIn(f"{len(command_groups)} 组命令", readme)
        for command in command_groups:
            self.assertIn(f"`{command}`", readme)
        self.assertEqual(version_line.removeprefix("v"), skill_version)

    def test_main_entry_keeps_serial_decisions_and_links_details(self):
        self.assertIn("用户自己的串口助手", self.main)
        self.assertIn("先问用户", self.main)
        self.assertIn("--observe-port", self.main)
        serial_section = self.main.split("## 执行后端", 1)[1].split("## 执行纪律", 1)[0]
        self.assertIn("workflows/uart-print.md", serial_section)
        for option in ("--stamp rel|abs|both|none", "--send DATA --send-at S", "--analyze intervals", "--reset", ".jsonl"):
            self.assertNotIn(option, serial_section)
        uart = self.docs["uart-print"]
        for option in ("--stamp", "--send-at", "--analyze intervals", "--reset", ".jsonl"):
            self.assertIn(option, uart)

    def test_internal_workflows_are_not_discoverable_skills(self):
        for name, text in self.docs.items():
            with self.subTest(workflow=name):
                self.assertFalse(text.lstrip().startswith("---"))
                self.assertNotRegex(text, r"(?m)^name:\s")
                for pattern in (BANNED_BARE, SLASH_CMD, BOLD_NAME, SKILL_MD_REF, HOST_SKILL_DELEGATION):
                    self.assertNotRegex(text, pattern)
        for pattern in (BANNED_BARE, SLASH_CMD, BOLD_NAME, SKILL_MD_REF, HOST_SKILL_DELEGATION):
            self.assertNotRegex(self.main, pattern)

    def test_no_host_skill_delegation_and_self_sufficiency_discipline(self):
        """自足化纪律：任何工作流与主入口都不得出现委派宿主 skill 的表述；
        同时 SKILL.md 必须写明该纪律，并把串口观测指向内建 serial 命令。"""
        for name, text in self.docs.items():
            with self.subTest(workflow=name):
                self.assertNotRegex(text, HOST_SKILL_DELEGATION)
        self.assertRegex(self.main, r"不委派宿主 Skill")
        self.assertIn("serial log", self.main)
        self.assertIn("uart-print", self.main)

    def test_context_workflows_read_the_single_source_of_truth(self):
        for name in CONTEXT_WORKFLOWS:
            with self.subTest(workflow=name):
                self.assertIn("HT32_PROJECT.md", self.docs[name])
        self.assertIn("HT32_PROJECT.md", self.main)
        self.assertIn("project-context", self.main)

    def test_route_keywords_cover_specialized_workflows(self):
        frontmatter = self.main.split("---", 2)[1]
        for target, phrases in (
            ("workflows/troubleshoot.md", ("死机", "跑飞", "HardFault", "复位环路", "外设不工作")),
            ("references/clock-power.md", ("时钟树", "低功耗", "唤醒源", "电流偏大")),
        ):
            route = next(line for line in self.main.splitlines() if line.startswith("|") and target in line)
            for phrase in phrases:
                with self.subTest(target=target, phrase=phrase):
                    self.assertIn(phrase, frontmatter)
                    self.assertIn(phrase, route)

    def test_jlink_direct_path_is_explicitly_experimental(self):
        self.assertIn("backend=keil", self.main)
        self.assertIn("J-Link 直连（实验性备用通道）", self.main)
        self.assertIn("未经真机验收", self.main)
        self.assertIn("只在用户明确要求", self.main)
        self.assertIn("实验性备用通道", self.docs["jlink"])
        self.assertIn("仅在用户明确要求", self.docs["jlink"])
        self.assertIn("未经真机验收", self.docs["jlink"])
        self.assertNotIn("主入口不路由", self.docs["jlink"])

    def test_interrupt_rules_cover_context_and_atomicity(self):
        code_style = (ROOT / "skills" / MAIN_SKILL / "references" / "code-style.md").read_text(encoding="utf-8-sig")
        for stale_rule in (
            "ISR 与主循环共享的变量必须 `volatile`",
            "临界区成对使用 `__disable_irq()` / `__enable_irq()`",
        ):
            self.assertNotIn(stale_rule, code_style)
        for phrase in ("volatile` 只保证可见性", "保存并恢复进入临界区前的 PRIMASK", "单字节标志", "复合状态", "上层中断适配器"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, code_style)

    def test_main_entry_routes_all_intents(self):
        # jlink.md is routed only as an experimental path; Keil remains default.
        for workflow in sorted(WORKFLOWS):
            with self.subTest(workflow=workflow):
                self.assertIn(f"workflows/{workflow}.md", self.main)
        for phrase in ("选型", "新建工程", "续开发", "迁移", "环境", "AGENTS.md", "HT32_PROJECT.md", "build", "烧录", "调试", "串口"):
            self.assertIn(phrase, self.main)
        self.assertRegex(self.main, r"_ht32-runtime[\\/]ht32\.py")
        self.assertIn("env-check", self.main)
        self.assertIn("backend=keil", self.main)
        self.assertIn("J-Link 直连（实验性备用通道）", self.main)
        self.assertIn("未经真机验收", self.main)
        self.assertIn("用户明确要求", self.main)
        self.assertIn("确认", self.main)
        for target, phrases in (
            ("workflows/troubleshoot.md", ("死机", "跑飞", "HardFault", "复位环路", "外设不工作")),
            ("references/clock-power.md", ("时钟树", "低功耗", "唤醒源", "电流偏大")),
        ):
            route = next(line for line in self.main.splitlines() if line.startswith("|") and target in line)
            for phrase in phrases:
                with self.subTest(target=target, phrase=phrase):
                    self.assertIn(phrase, self.main.split("---", 2)[1])
                    self.assertIn(phrase, route)

    def test_shared_runtime_and_project_storage_boundaries(self):
        for name, text in self.docs.items():
            with self.subTest(workflow=name):
                self.assertNotIn(".ht32-skills/state.json", text)
                self.assertNotIn(".ht32-skills/logs", text)
                self.assertRegex(text, r"项目 ``?\.ht32-skills/``? 只允许(?:保存)? ``?config\.json``?")
        for name in ("keil", "jlink", "workflow"):
            self.assertRegex(self.docs[name], r"_ht32-runtime[\\/]ht32\.py")

    def test_backend_workflow_capabilities(self):
        self.assertIn("backend=keil", self.docs["keil"])
        self.assertIn("CMSIS-DAP", self.docs["keil"])
        for operation in ("probes", "connect", "flash", "reset", "read-memory", "write-memory", "registers", "write-register", "debug", "rtt"):
            self.assertIn(operation, self.docs["jlink"])
        for step in ("build", "flash", "debug"):
            self.assertIn(step, self.docs["workflow"])

    def test_debugger_switch_is_first_class_command(self):
        """用户要的不只是"告诉你改哪里"：主入口与相关工作流都必须给出 keil configure，
        且 runtime CLI 真的注册了该子命令（文档不能比实现超前）。"""
        for name in ("keil", "flash", "debug", "env-check"):
            with self.subTest(workflow=name):
                self.assertIn("keil configure", self.docs[name])
        self.assertIn("keil configure", self.main)
        from ht32_runtime.cli import build_parser
        args = build_parser().parse_args(
            ["keil", "configure", "--project", ".", "--debugger", "jlink", "--dry-run", "--speed-khz", "500"])
        self.assertEqual((args.group, args.action), ("keil", "configure"))
        self.assertEqual((args.debugger, args.dry_run, args.speed_khz), ("jlink", True, 500))

    def test_scope_terms_stay_out_of_routed_docs(self):
        """HT32 路由文档只暴露当前支持面；实验性 J-Link 工作流显式排除在对外路由文档外。"""
        banned = ("ISP", "量产", "RTT", "SEGGER", "J-Flash", "Flash Commander", "e-Writer32")
        for name, text in self.docs.items():
            if name == "jlink":
                continue
            for term in banned:
                with self.subTest(workflow=name, term=term):
                    self.assertNotIn(term, text)
        for term in banned:
            with self.subTest(main=term):
                self.assertNotIn(term, self.main)

    def test_hardware_guidance_requires_model_specific_evidence(self):
        clock_power = (ROOT / "skills" / MAIN_SKILL / "references" / "clock-power.md").read_text(encoding="utf-8-sig")
        for phrase in ("概念分类框架", "不是 HT32 全系列规格", "先核对目标型号", "wiki MCP", "不确定时不推断"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, clock_power)
        self.assertIn("Sleep / 浅睡眠", clock_power)
        self.assertIn("DeepSleep / 深睡眠", clock_power)

    def test_troubleshooting_has_no_debugger_fallback(self):
        troubleshoot = self.docs["troubleshoot"]
        self.assertIn("没有调试器", troubleshoot)
        self.assertIn("GPIO", troubleshoot)
        self.assertIn("串口", troubleshoot)
        self.assertIn("不写入用户代码", troubleshoot)

    def test_firmware_archive_requires_confirmation_and_never_overwrites(self):
        flash = self.docs["flash"]
        for phrase in ("先询问用户是否归档", "不自动创建", "不得覆盖", "默认不提交 Git"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, flash)
        self.assertIn("用户确认后", flash)

    def test_cppcheck_is_optional_and_requires_toolchain_context(self):
        dev_assist = self.docs["dev-assist"]
        self.assertIn("cppcheck 保持可选", dev_assist)
        self.assertIn("AC5", dev_assist)
        self.assertIn("include 路径", dev_assist)
        self.assertIn("基线", dev_assist)
        self.assertIn("不作为交付门禁", dev_assist)
        self.assertIn("未做静态分析", dev_assist)

    def test_private_component_flow_is_mcp_only(self):
        """私有组件：新建工程先发现/确认再导出；续开发按工程锁定清单取证。"""
        create = self.docs["project-create"]
        for phrase in ("list_components", "get_component_info", "components-lock.json",
                       "components", "零个组件", "不凭记忆确认 tag"):
            with self.subTest(workflow="project-create", phrase=phrase):
                self.assertIn(phrase, create)
        self.assertNotIn("git clone", create)
        for phrase in ("component_id", "tag"):
            self.assertIn(phrase, create)

        dev = self.docs["dev-assist"]
        for phrase in ("manifest/components-lock.json", "search_component_code",
                       "read_component_file", "list_components", "不读本机 Git 历史"):
            with self.subTest(workflow="dev-assist", phrase=phrase):
                self.assertIn(phrase, dev)
        self.assertNotIn("git clone", dev)

        tools = (ROOT / "skills" / MAIN_SKILL / "references" / "mcp-tools.md").read_text(
            encoding="utf-8-sig")
        for tool in ("list_components", "get_component_info", "search_component_code",
                     "read_component_file", "get_knowledge_page", "get_file"):
            with self.subTest(tool=tool):
                self.assertIn(f"`{tool}`", tools)
        self.assertIn('"<component_id>@<tag>"', tools)
        self.assertIn("14 个工具", tools)
        # 旧库归档口径：工具页必须写明 legacy 参数与归档提示，否则旧工程会误用新库路径
        self.assertIn("legacy", tools)
        self.assertIn("raw/legacy/", tools)
        # AGENTS.md 的「深入参考」表同样写工具数，曾漂移两次（13 vs 14），一并锁住
        agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8-sig")
        self.assertIn("14 个工具", agents)
        self.assertNotIn("13 个工具", agents)
        self.assertIn("components-lock.json", tools)

        for phrase in ("list_components", "components-lock.json", "不回退 branch/HEAD"):
            with self.subTest(main=phrase):
                self.assertIn(phrase, self.main)

    def test_workflow_scenario_boundaries(self):
        expectations = {
            "env-check": ("get_wiki_version", "get_code_version", "env check"),
            "project-context": ("init", "update", "resolved debugger", "HT32_PROJECT.md", "ht32-project-guide"),
            "project-create": ("get_project_manifest", "export_project_bundle", "config init", "--steps build"),
            "dev-assist": ("HT32_PROJECT.md", "MCP", "--steps build", "风格基线", "沿用工程现有风格"),
            "migrate": ("同一固件库包", "list_libraries", "compare_chips", "阻断", "先给用户确认"),
            "flash": ("keil flash", "runtime", "排障"),
            "debug": ("keil debug", "排障", "不改代码"),
            "select": ("选型", "不在本工作流内生成工程/写代码"),
            "uart-print": ("RETARGET_Configuration", "exporter", "serial log", "L6200E", "printf.c", "默认路径", "手动配置", "--observe-port"),
        }
        for name, phrases in expectations.items():
            for phrase in phrases:
                with self.subTest(workflow=name, phrase=phrase):
                    self.assertIn(phrase, self.docs[name])


    def test_documented_template_versions_match_anchor(self):
        """文档里出现的「模板 vN」必须与模板锚点一致，防止改版后文档残留旧版本。"""
        template = (ROOT / "templates" / "HT32_PROJECT.template.md").read_text(encoding="utf-8-sig")
        anchor = re.search(r"ht32-project:template-version:(\d+)", template).group(1)
        documents = [ROOT / "README.md", ROOT / "AGENTS.md"]
        documents += list((ROOT / "skills" / MAIN_SKILL / "workflows").glob("*.md"))
        for document in documents:
            for found in re.findall(r"模板 v(\d+)", document.read_text(encoding="utf-8-sig")):
                with self.subTest(document=document.name, found=found):
                    self.assertEqual(found, anchor)

    def test_git_integration_is_consent_first_and_local_only(self):
        """Git 集成：无仓库先询问；初始提交一次；后续功能自动提交；只做本地操作。"""
        reference = (ROOT / "skills" / MAIN_SKILL / "references" / "git.md").read_text(
            encoding="utf-8-sig")
        for phrase in ("询问", "git init", "初始提交", "git add", ".gitignore", "父仓库",
                       "不 push", "重写历史", "user.name", "git status -s", "交付基线",
                       "创建前预检", "停止创建"):
            with self.subTest(reference=phrase):
                self.assertIn(phrase, reference)
        create = self.docs["project-create"]
        for phrase in ("references/git.md", "询问用户是否启用 Git", "一次初始提交",
                       "git status -s", "确认后", "创建前 Git 预检", "停止创建"):
            with self.subTest(workflow="project-create", phrase=phrase):
                self.assertIn(phrase, create)
        dev = self.docs["dev-assist"]
        for phrase in ("references/git.md", "功能提交", "只提交本轮改动"):
            with self.subTest(workflow="dev-assist", phrase=phrase):
                self.assertIn(phrase, dev)
        for phrase in ("先问用户", "先确认", "不 push", "不重写历史"):
            with self.subTest(main=phrase):
                self.assertIn(phrase, self.main)

    def test_documented_runtime_flags_exist_in_cli(self):
        """文档里写的 --long-flag 必须是 runtime CLI 真有的参数（ext 只放外部工具参数）。"""
        cli_source = (ROOT / "runtime" / "ht32_runtime" / "cli.py").read_text(encoding="utf-8-sig")
        options = set(re.findall(r"--[a-z][a-z0-9-]+", cli_source))
        external = {"--style", "--Werror", "--show-toplevel"}
        for document in (ROOT / "skills" / MAIN_SKILL).rglob("*.md"):
            for flag in re.findall(r"(?<![\w-])--[a-z][a-z0-9-]+", document.read_text(encoding="utf-8-sig")):
                if flag in external:
                    continue
                with self.subTest(document=document.name, flag=flag):
                    self.assertIn(flag, options)


if __name__ == "__main__":
    unittest.main()
