# HT32 新建工程（场景 a 主流程）

> 本文件是 `ht32` 主 Skill 的内部工作流（无 frontmatter，不是独立宿主 Skill），由主入口 `SKILL.md` 按意图路由进入。

从需求到可编译工程的一条龙流程。芯片选型必须有 wiki 依据，工程模板来自 code-mcp。

## 前置

1. 执行内部工作流 **env-check**（读取主包 `workflows/env-check.md` 按其流程执行）
2. 结论 `blocked` → 停止，先按其指引配置 MCP
3. 结论 `warn` 且 Keil 缺失 → 明确告知"无法基线编译"，询问用户是否仍要生成工程

## 执行流程

### 第 1 步 · 需求确认

- 必须明确：芯片型号（或选型需求）、工程名、放置目录、初始外设需求
- **工程名必须问用户**：工程名会落到工程文件名（`<工程名>.uvprojx`）、Keil Target 名与产物名
  （`.axf`/`.hex`），是本工程的对外标识。用户没给就**先问**，并给出建议（业务化命名，如
  `HT32F50230_UART_Printf`）；用户明确说"用型号就行/无所谓"时才以型号兜底，
  **不得静默使用型号默认值**
- 型号未定时：用 wiki-mcp `list_chips` → `get_chip_info` → `compare_chips` 辅助选型，
  把候选与差异摆给用户决定，**不代替用户拍板**
- 任何歧义（多候选、目录已存在同名工程等）必须询问用户，不猜测
- **创建前 Git 预检（写任何文件之前）**：目标目录已存在且是工程自身仓库时，按
  `references/git.md` 第 3.1 节执行 `git status -s`；有未提交改动 → **先问用户是否提交后再开始创建**：
  同意则先提交现有改动（`chore: 创建 <工程名> 前提交现有改动`）再继续；拒绝则**停止创建**，
  请用户先 commit / stash / 清理，不把新工程与旧改动混在一起

### 第 2 步 · 规格核对（MCP-only 取证）

**用户态唯一合法取证路径 = MCP 工具**。终端用户机器上只有 MCP 端点 + skill + 本地工具链，
没有 ht32-wiki / ht32-code 仓库副本——结论只能由 MCP 工具产出，**不得绕过 MCP 取证**。

1. 芯片规格：wiki-mcp `get_model`（型号 → 系列/文档映射）+ `get_chip_info`（系列页规格）
2. 核对并记录：内核/主频、封装/引脚数、Flash/SRAM、工作电压、需求外设是否支持
   （必要时 `get_peripheral_info`）
3. **引脚 / 复用功能类问题**：走 wiki MCP 的**引脚复用（pinmux）检索**，按序调用——
   ① `search_datasheets("<型号> pinmux")`：pinmux 行级数据在原档全文层（AF 族 54 型号已覆盖，
   F5 全子系列含 F50241 等）；
   ② 未命中再 `search_knowledge("<型号> <引脚号>", include_datasheets=True)`：
   该参数现追加**原档章节级**结果（「文档 › 章节 › 标题」定位），不再返回整页；
   ③ 要看引脚表上下文：`list_datasheet_sections(series)` 找章节 → `get_datasheet_section(series, "<章节>")`
   取单章正文（**不拉整篇**，避免截断与上下文浪费）；
   ④ 仍无 → 按第 7 条记知识缺口。**wiki 没有「按引脚查 AF」的独立工具**，
   `find_models_with_function` 只做功能→型号反查，不能替代按引脚查询；只搜精炼页搜不到 ≠ 未覆盖
   （覆盖范围随 wiki pinmux 索引进展更新，不写死"只有 F5"）
4. **库 API / 例程 / 模板类问题**：走 code MCP（`search_code_knowledge` / `find_example` / `get_file`）
5. 结论与来源（页面名）一并记录——它们要写入 HT32_PROJECT.md 第 2 节
6. 型号在 wiki 查不到 → **阻断**，请用户确认型号拼写或换型号
7. MCP 答不出（引脚/外设/寄存器/库 API 缺口）→ **记为知识缺口**，不得绕过 MCP 取证：
   - 用对应 MCP 的 `submit_feedback` 上报
     （规格 / 引脚 / 寄存器类 → wiki MCP；库 API / 例程 / 模板类 → code MCP）
   - 在 HT32_PROJECT.md 第 2 节标「待核对（MCP 未覆盖）」

### 第 3 步 · 上下文内容准备（不写盘）

- 执行内部工作流 **project-context** 的**字段收集部分**（init）：芯片核对结论、工程目标、
  外设需求、工具链快照来源；把结果暂存在本轮上下文里
- **不写文件**：目标工程根要到第 5/6 步导出落盘后才存在，提前写只会写到错误位置
- 依赖 `config init` 的 debugger 四值先留「待补」，第 6 步落盘后统一写

### 第 4 步 · 需求能力评估与私有组件选择（必做）

- code-mcp `get_project_manifest` 获取模板清单
- 按芯片系列/型号匹配；无精确匹配时列出可用模板请用户选择，不擅自替代
- **能力评估（必做，导出前每次都要做）**：code-mcp `list_components` 列出已登记组件，
  把本轮需求逐条对照组件的**能力**（`get_component_info` 的 `provides`）：
  即使结论是"不引入"，也必须留下评估结论并写入第 6 步交付说明与 HT32_PROJECT.md 第 4 节

  | 需求中出现的能力 | 优先组件 | 说明 |
  |---|---|---|
  | 毫秒时基 / 周期任务 / 非阻塞延时 | ht32_bsp（`BSP_USE_TIMEBASE`） | 用 `TimeSlice_20ms` / `TimeSlice_1s`，不为延时另写 SysTick/BFTM 轮询 |
  | 串口中断发送 / 在途帧不覆盖 | ht32_bsp（`URx_Tx` + `xxx_IntSend*`） | 轮询 printf 仍走官方 retarget |
  | Flash 参数存取 | ht32_bsp（`FLASH_Read/Write/Erase`） | |
  | 应用状态机 | lwfsm | |
  | GPIO / LED / 按键 | 无对应组件 → 用官方 `HT32_Board` | 组件不提供该能力，也不要重复实现 |

- `get_component_info` 里的「已验证型号」是**验证样本，不是适用白名单**（`list_components`
  已不展示验证样本，只给能力摘要）：未列出的型号按「适用系列」判断，同样可选，
  但须在交付说明与 HT32_PROJECT.md 第 4 节标注「该型号未在组件验证样本内」，并在本工程实测
- 选到候选后用 `get_component_info` 核对 `provides` / `not_provides` / `when_to_use` /
  `composability` / `docs`，读 `docs` 里的接入步骤，由用户确认 `"<component_id>@<tag>"`
  （tag 只从 MCP 返回的可用 tag 里选）
- **接入按组件 `docs` 逐条做**（如 BSP 的 `doc/HT32_BSP_Usage.md` §2）：除把组件源码加入工程外，
  时基/串口还要在工程 `it` 文件里合并 IRQ 入口——漏了入口会落 `Default_Handler`；
  不凭记忆拼接入步骤，文档缺失再报缺口
- **组合规则（默认不冲突）**：
  1. 组件是能力集合，可按需只用其中一部分（例如只要时基，不用其 UART 发送 API）
  2. 与官方 printf/retarget 共存的前提是**不重复实现** `fputc` / `_sys_exit` / `_ttywrch`，
     不是"不能引入"；`not_provides` 只表示组件不实现该能力
  3. 与官方板级 `HT32F_DVB_*`（LED/按键）零耦合，可同时使用
- **tag 只能选组件发布的版本**：不得凭记忆确认 tag、不得改用 branch/HEAD；MCP 未返回的
  组件视为不可用
- **不得**要求用户提供仓库地址、在本机 clone 仓库，或用本地 Git 历史核对版本
- 零组件是**允许的结果**，但必须写明理由（能力不匹配 / 用户明确不用），不得静默跳过

### 第 5 步 · 打包与下载

- code-mcp `export_project_bundle(config_id[, out_dir][, project_name][, components])`
  - 参数取值规则（`config_id` 格式、`board` 必填条件、旧 `chip` 弃用）见
    `references/mcp-tools.md`，本节不重复；导出后落盘核对见下表
  - `project_name`：第 1 步与用户确认的**工程名**，同时落到三处——工程文件名
    `<工程名>.uvprojx`、`<TargetName>`/`<OutputName>`（产物即同名 `.axf` / `.hex`）
  - `components`：第 4 步用户确认的选择列表，如 `["ht32_bsp@V2.0"]`；零个组件时不传该参数
  - 导出会把模板自用文件（`devices.json`、模板维护文档）剔出，并在工程根生成属于用户工程的
    `README.md`（含工程名/芯片/打开与编译方式）
- **导出后自检（逐项核对，发现残留/错误即重新 export 或按第 2 步 7 上报，不将就）**：

  | 项 | 期望 |
  |---|---|
  | 工程文件名 | `project/MDK_ARMv5/<工程名>.uvprojx`，**不得残留 `*-template.uvprojx`** |
  | `<TargetName>` / `<OutputName>` | 等于 `project_name`（业务名），不得残留模板默认名 |
  | `<Cpu>` 内存布局 | IROM/IRAM 随目标型号（与 DFP 定义一致），不得残留模板默认值 |
  | `<SFDFile>` | 指向目标型号的 SVD，不得残留模板型号 |
  | 组件（选了才有） | `components/<组件 id>/` 存在；`manifest/components-lock.json` 的 tag/commit/文件数与导出报告一致 |

  另核对工程根目录：`README.md` 首行是工程名（不是模板名）；**没有** `devices.json` / `TEMPLATE.md`
- **交付形态由服务端环境决定，无需判断**：报告首行会写明是「云端 zip 下载」还是「已导出」目录
- 云端模式：返回 token 下载 URL + 大小 + **SHA256** + 有效期 → 下载**只用本包 runtime 的 `fetch`**
  （Python TLS，不经 Windows schannel；大小与 SHA256 一并校验）：
  ```powershell
  python "<global-skill-root>\_ht32-runtime\ht32.py" fetch --url "<url>" --out "<目标.zip>" --sha256 "<报告里的 SHA256>" --size 123456 --extract "<目标工程目录>"
  ```
  - **不得**自己拼 `Invoke-WebRequest` / `curl`（受限宿主报 `SEC_E_NO_CREDENTIALS`），也**不得**跳过证书校验；
    `--size` / `--sha256` 填报告实际值
  - `--extract` 直接解到目标目录（省略则解到 zip 同名目录）；目标非空或 zip 含绝对路径/`..` 条目会拒绝，
    **不要再自己解压**
  - TLS 证书校验失败时 `fetch` 按 `--sha256` 退化为哈希校验（JSON `tls=unverified` + warning），可继续；
    SHA256 / 大小与报告不一致 → 重新 export，不将就
  - URL 过期（404）/ 下载失败 → **重新 export 一次**；再失败报错并给建议（网络 / 服务端 bundle 目录权限与空间）
  > 服务器侧需配 `BUNDLE_DIR` + `BUNDLE_BASE_URL` 才启用云端模式；未配则为本地目录模式
- 本地目录模式：工具输出自包含工程目录（须传空的 `out_dir`）→ 复制到目标位置

### 第 6 步 · 落盘核对 + 立即建立上下文

- 云端模式已在第 5 步用 `fetch --extract` 解到目标目录；本地目录模式把输出目录复制到目标位置
- 回查：目录结构完整、`.uvprojx` 存在（文件名为工程名）、工程根 `README.md` 首行是工程名
- **写入代码格式配置**：把本包 `assets/clang-format` 复制为工程根 `.clang-format`
  （代码风格以此为准，见 `references/code-style.md`）；已存在且内容不同 → 询问用户，**不覆盖**
- **核对通过后立即执行 project-context（init）**，写入 `HT32_PROJECT.md`（模板 v10：1-9 节，含第 4 节私有组件/组件选用结论与第 9 节本次执行计划）与 `AGENTS.md` 薄引导；第 7 节 debugger 四值先写「待补（config init 后刷新）」
- **核对不通过（解压失败/缺 uvprojx/目录不完整）不得写上下文**：先修复或重新 export
- 第 9 节计划按本次任务生成：只列适用的步骤 ID（`P7a/P7b/P8a/P8b/P9`），全部先标 `[ ]`；
  本次未要求的步骤直接标 `[!] 跳过（本次未要求）`，不留空
- 目标目录已有同名工程 → 询问覆盖/改名，**不自动覆盖**

### 第 7 步 · Keil 基线编译

- 使用本包共享 runtime 初始化配置，再执行基线构建（路径为工程根内相对路径，**`--uvprojx` 用第 5 步导出的实际文件名 `<工程名>.uvprojx`**，Target 来自导出工程）：
  ```powershell
  python "<global-skill-root>\_ht32-runtime\ht32.py" config init --project "<project-root>" --chip "<chip-model>" --uvprojx "<relative-project.uvprojx>" --target "<target-name>"
  python "<global-skill-root>\_ht32-runtime\ht32.py" workflow run --project "<project-root>" --steps build
  ```
  读取统一 JSON 的 status、产物与日志路径（`observations` 是落盘后的解析值，不是入参回声）；
  已有 config.json 时先校验，不自动覆盖。
- config init 成功后：执行 project-context（update）刷新第 7 节 debugger 快照；
  **并把第 9 节的 `P7a config init` 勾为 `[x]`**（证据 = 本次 JSON 的 status/artifacts）
- 环境检测未获取的版本仍按实际标注；**勾选必须有证据，不得凭"做完了"打勾**
- 目标：**0 Error 0 Warning**
- 编译失败 → 展示首个错误与日志路径；**不盲目改 bundle 代码**，向用户报告
  （该阶段问题多为模板/环境问题）

### 第 8 步 · 交付

- 汇报：工程路径、Target 名、编译结果、HT32_PROJECT.md 路径
- **组件结论（必写一行）**：`组件：<id>@<tag>（能力：…）| 未引入（理由：能力不匹配 / 用户明确不用）`；
  选择未验证型号时在同一行补「该型号未在组件验证样本内」
- **后续编排入口**（2026-09-26 起，不再是纯文字建议）：
  - 烧录 → 转内部工作流 **flash**（`workflows/flash.md`：Keil 通道执行与排障路由）
  - 在线调试 → 转内部工作流 **debug**（`workflows/debug.md`：断点/单步/变量 + 连接排障）
  - 串口验证 → 转内部工作流 **uart-print**（`workflows/uart-print.md`：printf/retarget 配置 + 用内建 `ht32.py serial log` 观测目标板日志）
- 在 HT32_PROJECT.md 变更记录追加一行（创建工程 + 基线编译结果）；
  **第 9 节把 `P7b` 勾为 `[x]`**（证据 = 本次 build 的 `0 Error 0 Warning`），
  未做的 `P8a/P8b` 标 `[!] 跳过（本次未要求）`，最后 `P9 收尾` 勾 `[x]`
- **Git 提交（交付最后做）**：按 `references/git.md` 第 1 节实测工程根状态
  - 不是 Git 仓库 → **询问用户是否启用 Git**；同意则 `git init` + 写 `.gitignore` +
    **一次初始提交**（`chore: 初始化 <工程名> 工程`），拒绝则记「未启用」不再询问
  - 已是工程自身仓库 → 按第 3.2 节先看 `git status -s`；有未提交改动时**把摘要给用户、确认后**
    再提交一次（`chore: 交付 <工程名> 初始工程`）；无改动则跳过，用户拒绝则保留在工作区
  - 位于父仓库内 → 不初始化、不自动提交，告知用户实际仓库根
  - 结论写入 HT32_PROJECT.md 第 8 节「项目追加约定」；提交须包含本次写好的 HT32_PROJECT.md。
    失败只 warning，不阻断交付

## 失败处理汇总

| 阶段 | 失败 | 处理 |
|---|---|---|
| 前置 | env-check blocked | 停止，输出 MCP 配置指引 |
| 选型 | wiki 查无型号 | 阻断，请用户确认型号 |
| 取证 | MCP 答不出规格/引脚/库 API | 记知识缺口 → `submit_feedback` 上报 + HT32_PROJECT.md 标「待核对（MCP 未覆盖）」，不绕过 MCP 取证 |
| 模板 | manifest 无匹配 | 列可用模板，用户选择 |
| 组件 | 未找到组件登记表 / 无匹配组件 | 说明"本服务未启用私有组件"或按需求换词，**并把评估结论写进交付说明与 HT32_PROJECT.md 第 4 节**；不要求用户提供仓库地址，零组件继续导出 |
| 组件 | 用户要的组件/tag 不在 MCP 返回 | 阻断该选择，列出可用 tag 让用户重选；**不回退 branch/HEAD、不凭记忆确认版本** |
| 组件 | 导出报 `COMPONENT_*` 错误 | 原样展示错误码与提示（tag 不可用/远端不可用/冲突/超限），按提示换 tag 或联系维护者；不手工拼装组件 |
| 下载 | token 过期/网络错误/证书报错 | 一律用 runtime `fetch`（带报告里的 `--sha256`）；证书链不可信时按哈希校验继续，**不跳过校验、不自己拼下载命令**；token 过期则重试 export 一次，再失败报告用户 |
| 导出 | 自检四项有残留 | 重新 export；仍残留则 code MCP `submit_feedback` 上报 |
| 编译 | 出现 Error | 展示首错误 + 日志路径，报告用户，不盲修 |
| Git | 无 git / 身份未配置 / 初始提交失败 | warning + 原始报错，工程交付继续；不代设 Git 配置、不反复重试 |
| 任一环节 | 发现本 Skill（流程/脚本/模板/环境检测）自身缺陷 | code MCP `submit_feedback(issue_type="skill_defect")` 上报（附 skill 版本号 + 现象 + 日志路径），再按上表处理，不静默吞掉 |

## 核心规则

- 不跳过任何步骤；**上下文未生成不得进入基线编译/烧录**（导出落盘核对通过后立即生成）
- **工程名先问用户**：不在用户未知情的情况下用型号默认值（除非用户明确同意）
- **取证只走 MCP**：选型/规格/引脚结论必须来自 wiki-mcp 检索，库 API/例程/模板来自 code-mcp；
  不凭记忆写规格，**不得绕过 MCP 取证**；MCP 答不出即按第 2 步 7 记缺口上报
- **组件只走 MCP，且必须先评估**：建工程必做一次「需求能力 → 组件 provides」比对；
  组件发现/检索/读取/导出全部用 code MCP；不向用户索要仓库地址，不在本机 clone，
  不凭记忆确认 tag；组件选择必须由用户确认后才传给导出工具；零组件必须写理由
- **本 Skill 自身缺陷走 code MCP**：`submit_feedback(issue_type="skill_defect")` + skill 版本号，不静默吞掉
- 基线编译阶段不改写 bundle 内代码；确需改动（如路径适配）必须记录进 HT32_PROJECT.md 变更记录
- 工程路径、Target、编译结果最终写回 HT32_PROJECT.md
- 涉及覆盖用户已有内容的操作（目录/文件）必须先确认

## 全局 runtime 与项目数据边界

> 数据边界：项目 `.ht32-skills/` 只允许保存 `config.json`；state / cache / logs 写全局数据目录（不可写时降级，见 SKILL.md「数据目录与宿主沙箱」）。
