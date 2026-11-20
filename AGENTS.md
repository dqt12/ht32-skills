# AGENTS.md — HT32 Skills 维护指南

> 供 AI 助手 / 人类维护者快速上手本仓库；**接手新任务前请读完本文**。
> 本仓库是 HT32 三仓库架构的**编排层**：上游 ht32-wiki（规格知识）与 ht32-code（代码知识）两个 MCP 服务，本仓库回答"什么时候调谁、怎么把本地工具链串起来"。
> 对外说明见 [README.md](README.md)，变更史见 [log.md](log.md)（本地，不入库）与 git log——**本文不写版本沿革**。

## 这是什么

一个宿主 Skill（`ht32`）+ **13 个**内部工作流 + 一份共享 runtime，让 Agent 用一条入口走完 HT32 全流程开发（选型 → 建工程 → 写码 → 编译 → 烧录 → 调试）。

- 宿主入口：`skills/ht32/SKILL.md`——唯一带 frontmatter 的 Skill（frontmatter 含 `version`）
- 内部工作流：`skills/ht32/workflows/*.md`——无 frontmatter，宿主不识别为独立 Skill
- 共享 runtime：`runtime/`——纯标准库；命令组由 `runtime/ht32_runtime/cli.py` 定义；Keil 为默认后端，J-Link 直连是未经真机验收的实验性备用通道
- 版本：根 `VERSION`（唯一事实源，首行 `vX.Y.Z`）→ 安装后落到 `<skills>/ht32/VERSION`，由 `ht32.py version` / `env check` 读取
- 模板：HT32_PROJECT.md 模板 v10（9 个固定 section、36 个占位符）+ AGENTS.md 薄引导
- 代码风格：`skills/ht32/references/code-style.md`（组织 / 命名 / 注释 / 结构）+ `skills/ht32/assets/clang-format`（排版）
- 配套服务：ht32-wiki MCP · ht32-code MCP（`https://mcp.s621.work/ht32-wiki` · `https://mcp.s621.work/ht32-code`）

### 三仓库分工

| 仓库 | 角色 | 边界 |
| --- | --- | --- |
| ht32-wiki | 规格知识：选型 / 参数 / 系列差异 / 原档检索 | 只回答知识，不碰硬件、不执行命令 |
| ht32-code | 代码知识：库 API / 例程定位 / 工程模板导出 | 只回答知识，不碰硬件、不执行命令 |
| **ht32-skills（本仓库）** | **编排**：意图路由、工作流、本地执行、结果契约 | **不存储知识**，也不复制上游内容 |

> **跨仓库不引用路径**：本仓库只以「服务」形式指代两个知识库（"查 wiki MCP 的 xxx"），**正文不得出现它们的仓库内文件路径**。

## 架构与边界

```
                  用户电脑 · AI 宿主侧
   AI 宿主 ──► 宿主 Skill：ht32（唯一入口）──► 内部工作流 ×13
                    │                              │
                    │                              └──► 本地能力自足：keil 内建；串口观测先问用户（用户串口 MCP / 串口 skill / HTTP 优先），内建 serial 兜底（J-Link 直连为已路由的实验性备用通道）
                    ▼
              共享 Runtime：_ht32-runtime/ht32.py
                    ▼
              本地工具链：Keil5 / HT32 DFP / 调试器（e-Link32 Pro · CMSIS-DAP · J-Link，均在 Keil 内配）/ 串口
                              │
                 MCP 工具调用（远程 HTTPS）取知识
                              ▼
             云端：ht32-wiki MCP（规格） · ht32-code MCP（代码）
```

三条不可动摇的边界：

1. **执行在本地** —— 编译 / 烧录 / 调试 / 读写寄存器全部由本机 runtime 驱动
2. **知识在云端** —— 两个 MCP 是远程服务，只回答知识，不改用户工程
3. **MCP 之间不直连** —— 先查规格还是先查代码由本仓库的 Skill 决定

## 设计说明（README 只留摘要，论证在这里）

- **为什么要 runtime**：确定性（参数校验固化成子命令）、统一契约（一套 JSON + 退出码）、统一配置解析（单次覆盖 > 项目 > 机器 > 默认，覆盖不写回）、统一数据边界（全局目录；不可写时降级）、可测（纯标准库 + mock）
- **为什么独立目录**：主入口与多个工作流共享，只需一份；安装成 skills 同级的 `_ht32-runtime/`
- **8 组命令定位**：`config` 配置 / `env` 检测 / `keil`·`jlink` 执行 / `serial` 观测 / `workflow` 闭环 / `fetch` 下载 / `version` 追溯；参数细节以 `--help` 为准
- **易踩点**：`fetch` 只走 HTTPS + 证书校验，证书链不可信时用 `--sha256` 退化为按哈希校验，**不跳过校验**；日志 / 状态 / 缓存写 `%USERPROFILE%/.ht32-skills`，宿主沙箱拦截时降级并告警，可用 `HT32_SKILLS_HOME` 重定向

## 目录结构

```
ht32-skills/
├── AGENTS.md / README.md / VERSION / LICENSE   ← 本文 / 对外说明 / 版本事实源 / MIT
├── log.md                      ← 操作日志（本地文件，不入库）
├── skills/ht32/
│   ├── SKILL.md                ← 唯一入口：意图路由 + 后端决策 + 安全边界
│   ├── workflows/              ← 13 个内部工作流（无 frontmatter）
│   │   ├── select.md  project-create.md  dev-assist.md  migrate.md
│   │   ├── env-check.md  project-context.md
│   │   ├── flash.md  debug.md  workflow.md
│   │   ├── keil.md  jlink.md  uart-print.md  troubleshoot.md
│   ├── references/             ← mcp-tools.md + code-style.md + clock-power.md + git.md
│   ├── assets/clang-format     ← 排版唯一事实源（安装后复制为用户工程根 .clang-format）
│   ├── scripts/check_local_tools.ps1
│   └── templates/              ← HT32_PROJECT.template.md + AGENTS.guide.md（与根 templates/ 字节一致）
├── runtime/                    ← 共享 runtime（安装后落到 _ht32-runtime/）
├── templates/                  ← 模板唯一事实源（两份，改这里）
├── tests/                      ← 契约 / runtime / 后端 / 串口回归 + fixtures
└── scripts/                    ← validate_template.ps1 / analyze_feedback.py / test_analyze_feedback.py
```

## 核心规则（不可违反）

1. **单入口不变式** —— `skills/ht32/SKILL.md` 是唯一带 frontmatter 的 Skill。新增能力先落 `workflows/*.md`，再在主入口路由表登记；不得新增顶层 `ht32-*` Skill 目录
2. **工作流不是 Skill** —— `workflows/*.md` 不得带 YAML frontmatter、不得出现 `/ht32-*` 子命令、不得出现加粗的旧 Skill 名、不得引用兄弟 Skill 目录（负向测试守着）
3. **模板单一事实源** —— 只改根 `templates/` 两份；`skills/ht32/templates/` 与其**字节一致**（测试校验 SHA256）；改模板必须同步 bump 版本标记
4. **模板版本标记不可删** —— `<!-- ht32-project:template-version:N -->` 是 `validate_template.ps1` 的正则锚点，删标记校验直接失败
5. **MCP-only 取证** —— 规格 / 引脚 / 寄存器只来自 wiki MCP，库 API / 例程 / 模板只来自 code MCP；MCP 答不出 → 记知识缺口 + `submit_feedback`，**不得绕过 MCP、不得凭记忆编造**
   - 用户态唯一合法取证路径是 MCP 工具（终端机器上没有两个知识库的仓库副本）
   - 本 Skill 自身缺陷走 code MCP `submit_feedback(issue_type="skill_defect")`，`skill_version` 传实测版本（结构化字段），`details` 写现象 + 日志路径
6. **跨仓库不引用路径** —— 见上文「三仓库分工」
7. **runtime 只依赖标准库** —— 引入第三方依赖即破坏"开箱可用"
8. **项目数据边界** —— 用户工程 `.ht32-skills/` 只允许 `config.json`；state / cache / logs 写全局数据目录；**不得在用户工程内开状态目录**，也不得把构建 / 烧录 / 调试委派给宿主其他 Skill。串口观测例外：用户已有串口能力时优先用，没有才用内建 `serial`。全局目录不可写时**降级不失败**（只给 warning）；需要落盘用 `HT32_SKILLS_HOME` 重定向
9. **统一结果契约** —— stdout 纯 JSON；`status` ∈ `ok` / `warn` / `blocked` / `error`，退出码 0 / 0 / 2 / 3，未预期异常 4；改契约必须同步测试与文档
10. **安装不设脚本** —— 不提供、不维护任何安装脚本。安装与升级由宿主 Agent 按 README「安装」章节、用它自己平台的机制完成；**不得在仓库内维护宿主路径表**。升级只由版本检测驱动，且必须先经用户同意
11. **公开仓库纪律** —— 不得提交内网地址 / 内部文档路径 / 部署细节 / 密钥令牌。对外地址 GitHub 与 Gitee 等价：`https://github.com/dqt12/ht32-skills.git` · `https://gitee.com/dqt12/ht32-skills.git`，文档写真实地址；内网地址用占位符
12. **提交规范** —— conventional commits；`feat/*` 分支 + no-ff 合并；每次变更追加 `log.md`（本地文件，不入库）
13. **版本可追溯** —— 根 `VERSION` 首行与 `SKILL.md` frontmatter `version` 必须一致；改 Skill / workflows / runtime / scripts / templates 必须同步 bump；**版本值只认实测**
14. **更新检查清单在 ht32-code 侧** —— 云端最新版只从 code MCP `check_skill_update` 取（数据源 = ht32-code 根 `skills-release.json`）；本地缓存只在 `ht32.py version`（全局目录，24h TTL）。检查必须非阻断；**升级必须先经用户同意**，下次会话生效
15. **代码风格单一事实源** —— 只有 `references/code-style.md`（分层判定 / 命名 / 注释 / 结构）与 `assets/clang-format`（排版）。命名分层两套（驱动 PascalCase、应用 snake_case），同一文件只许一套；类型统一 `xxx_t`、每模块一个实例、禁止全局变量。改任一处必须同步模板第 8 节、project-create 复制步骤、dev-assist 写码步骤，并 bump 版本。**不得在 workflows 里另立命名规则**
16. **VERSION 只写 3 行** —— 首行版本号 / `Date:` / `Skill: ht32`；每行单行、≤100 字符。禁止变更说明、多版本并列、run-on 长句。变更史只进 `log.md` 与 commit；新增字段先改本条
17. **Git 集成口径唯一** —— 只写在 `references/git.md`，workflows 只引用。仅限本地 `init` / `add` / `commit`：禁止 push、改 remote、重写历史、代设身份；已有仓库创建前有未提交改动须先确认；父仓库内的工程不初始化、不自动提交
18. **README 与 AGENTS.md 分工** —— README 是对外门面 + **Agent 安装手册**：必须保留可执行的完整安装链（获取仓库 → 安装 Skill 与 runtime → 配置两个 MCP → 验证），不得为精简删步骤或改成"详见某文件"；设计论证、参数细节、工具清单放本文与 `references/`

## 会话开始流程（Orient）— 必须执行

```powershell
# 1. 读 README.md 了解对外定位与能力面
# 2. 读 log.md 最近 20-30 条了解近期决策（本地文件）
# 3. 按需读 skills/ht32/SKILL.md（路由表）与目标 workflows/*.md
```

跳过 orient 会导致重复能力、路由表与工作流脱节、违反单入口不变式。大改动前先跑一遍测试电池，确认起点是绿的。

## 核心操作

### 1. 新增 / 修改内部工作流

1. **先定路由** —— 在主入口意图表里属于哪一行？是否与现有工作流重叠？
2. **写工作流** —— 落到 `workflows/<name>.md`，不带 frontmatter，开头一行定位说明
3. **登记路由** —— 主入口意图表加行，路径必须能被测试检测到
4. **保住能力** —— 原有的阻断条件、确认边界、MCP-only 规则一条都不能丢
5. **更新契约测试** —— `tests/test_skill_contracts.py` 的 `WORKFLOWS` 集合与路由矩阵
6. **追加 log.md** —— 说明为什么改

> 用户侧只感知一个入口：不要在 workflow 里写"请调用 xx skill"这类让用户做选择的引导。

### 2. 改完必跑验证

```powershell
$env:PYTHONPATH = Join-Path (Get-Location) 'runtime'
python -m unittest discover -s tests -p 'test_*.py' -v   # 全量回归
python scripts/test_analyze_feedback.py                   # 反馈分析自验
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/validate_template.ps1
```

| 改了什么 | 重点确认 |
| --- | --- |
| `workflows/*.md` | 无 frontmatter、无旧子命令、无跨 Skill 引用；路由矩阵完整 |
| `templates/` 两份 | 镜像字节一致；版本标记 / 标题 / debugger 四字段 / 第 8 节在；`validate_template.ps1` 通过 |
| `references/code-style.md`、`assets/clang-format` | 样例可格式化且幂等（`clang-format --dry-run --Werror` 无输出）；workflows 引用路径存在 |
| `runtime/**` | 全量回归 + 契约测试（退出码与 JSON 结构） |
| `VERSION` / `SKILL.md` version | 两处一致；`ht32.py version` 能读出；安装后 SHA256 一致 |
| `runtime/ht32_runtime/version.py` | TTL 用例通过；**发版时必须同步 ht32-code 的 `skills-release.json`** |
| `skills/ht32/scripts/check_local_tools.ps1` | 仍是 UTF-8 BOM；探针判定用例通过 |

### 3. 安装 / 升级

按 README「安装」章节执行（规则 10）：`skills/ht32/` → `<skills>/ht32/`（含 `VERSION`），`runtime/` → 同级 `_ht32-runtime/`，根 `templates/` 两份同步进 `skills/ht32/templates/`。升级 = 版本检测报 behind → 用户同意 → 同一机制重装；旧顶层 `ht32-*` 目录一并移除。

### 4. 脚本改动高危点

- **别碰 BOM**：`validate_template.ps1` 与 `check_local_tools.ps1` 必须保留 UTF-8 BOM
- **别用保留变量名**：`$host` / `$error` / `$args` 是只读自动变量
- **别只改一份模板**：漏改镜像会让 `test_global_contracts` 直接红
- **别扩大受管范围**：`legacySkillNames` 只列本仓库历史上发布过的顶层目录

## log.md 格式

本地文件，不入库；最新条目加在顶部：

```
## [YYYY-MM-DD] action | subject
- 详细说明
- 涉及文件列表
```

Actions：`docs` | `feat` | `fix` | `refactor` | `update` | `test` | `chore` | `release`。超过 500 条时重命名为 `log-YYYY.md`。

## 反馈与用量日志分析（维护者工具）

```bash
python scripts/analyze_feedback.py <反馈日志根目录> [--days 7] [--service code] [--json]
```

输出需求信号、调用分布与失败、反馈类型、耗时 p50/p95。日志可能含用户项目信息，只挂受控服务器、不要提交。

## 深入参考

| 想了解 | 去哪里 |
| --- | --- |
| 对外说明、安装步骤、宿主与工具链支持 | [README.md](README.md) |
| 主入口路由表与后端决策 | `skills/ht32/SKILL.md` |
| 单条流程细节 | `skills/ht32/workflows/*.md` |
| code MCP 14 个工具速查 | `skills/ht32/references/mcp-tools.md` |
| Git 初始化 / 提交口径 | `skills/ht32/references/git.md` |
| runtime CLI 与结果契约 | `python runtime/ht32.py --help` + `runtime/ht32_runtime/cli.py` |
| 模板结构契约 | `templates/` 两份 + `scripts/validate_template.ps1` |
| 安装 / 升级 | README「安装」+ 核心规则 10 |
