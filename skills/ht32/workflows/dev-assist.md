# HT32 续开发（场景 b 主流程）

> 本文件是 `ht32` 主 Skill 的内部工作流（无 frontmatter，不是独立宿主 Skill），由主入口 `SKILL.md` 按意图路由进入。

面向已有工程的功能开发。上下文锚点是工程根 `HT32_PROJECT.md`；写码依据是 ht32-code 的真实知识。

## 前置

1. **首次**续开发或工具链变动时先跑 **env-check**；日常改代码 / 编译不重复跑，build 报「工具缺失」时补跑
2. MCP 缺失 `blocked` → 停止并配置；runtime 缺本地工具 `blocked` → 记录该操作不可执行，知识核对与写码可继续，交付时说明未编译验证
3. 定位工程根：`--project` > 当前 workspace > 询问用户；缺项目配置时读实际 `.uvprojx`、Target 与 HT32_PROJECT.md 已核对型号，用 `config init` 初始化，不从目录名猜型号、不覆盖既有配置

## 执行流程

### 第 1 步 · 读 HT32_PROJECT.md

- 读 `<工程根>/HT32_PROJECT.md`（同目录 `AGENTS.md` 仅薄引导），掌握芯片选型、外设清单、库路径、需求记录
- **组件锁定清单**（存在时）：`manifest/components-lock.json` 记录 `component_id` / `tag` / `commit` / 导出目录 / 逐文件 SHA256；本轮涉及这些组件时按锁定清单的组件与 tag/commit 走 MCP，不假定「最新 tag」，**不读本机 Git 历史**
- 老工程无清单 → 不假定组件版本：需要时用 `list_components` 重新发现并**询问用户**确认组件与 tag
- 不存在 → 先跑 **project-context**（init）生成；用户无法提供芯片型号等必要信息时**阻断写码**
- 同时初始化第 9 节本次任务计划（步骤 ID + 全部 `[ ]`）；完成一步勾一步，勾选必须有证据
- **Git 状态**：读第 8 节「项目追加约定」的 `Git：…` 记录；无仓库且无记录 → 按
  `references/git.md` 第 2 节询问一次是否启用 Git；已有仓库但无记录 → 先问一次是否自动提交

### 第 2 步 · 风格基线确认（既有工程必做）

- **触发**：工程内已有自研 `.c/.h`（排除 `library/`、`startup_*`、`system_*`、`components/` 等只读或第三方目录）
- **检测**：抽样 2–3 个自研文件，记录命名（PascalCase / snake_case）、缩进、括号风格、分段线样式，与 `references/code-style.md` 比对
- **一致** → 直接沿用，不打断用户
- **不一致** → **必须询问用户**二选一，不得自行决定：
  - **A. 沿用工程现有风格（默认推荐）**：新代码跟随现有命名与排版；**不覆盖**工程根 `.clang-format`，**不重排**既有文件
  - **B. 采用 skill 规范**：新代码按 `references/code-style.md`；`.clang-format` 缺失则复制，已存在且内容不同则再问是否替换；既有文件仍不批量重排
- **记录**：选择写入 `HT32_PROJECT.md` 第 8 节「项目追加约定」，并在第 6 节变更记录追加一行；后续会话读到该记录**不再重复询问**
- 用户未确认前**不得开始写码**

### 第 3 步 · 功能核对

- 与用户确认本轮功能关键点（输入/输出/时序/中断行为等）
- 新外设：wiki MCP `get_peripheral_info` + `get_chip_info`；引脚 / 复用：wiki MCP pinmux 检索
- 芯片不支持 → **阻断**并附 wiki 依据；支持 → 下一步

### 第 4 步 · 更新 HT32_PROJECT.md

- 跑 **project-context**（update）：需求记录追加本轮需求；外设清单补新行（状态 = 开发中）；第 9 节按本轮任务重开子块（不覆盖历史）

### 第 5 步 · 检索真实知识（写码前置，不可跳过）

**写码依据唯一来源 = MCP 工具**。终端用户机器上只有 MCP 端点 + skill + 本地工具链，没有 ht32-code 仓库副本——结论只能由 code MCP 产出，**不得绕过 code MCP 取证**。

| 目的 | code MCP 工具 |
|---|---|
| 函数用法 / 库模块使用知识 | `search_code_knowledge` |
| 相近例程 | `find_example` |
| 库源码 / 例程文件本体 | `get_file` |
| 已集成私有组件源码 | `search_component_code` / `read_component_file`（组件 ID + 锁定清单 tag） |
| 发现可用新组件 | `list_components` → `get_component_info`（必要时询问用户是否加入） |

- **规格 / 寄存器 / 电气参数**：走 wiki MCP。先用 `search_datasheets("<关键词>")` 定位章节，
  取正文用 `get_datasheet_section(series, "<章节>")`（**单章取文，不拉整篇**）；不确定章节名时先
  `list_datasheet_sections(series)` 看章节树。只有需要整篇通读时才用 `get_datasheet`。
- **版本对齐（取证第一步，必做）**：读 `HT32_PROJECT.md` 第 7 节的「固件库版本」，再用 `get_code_version` 取云端现行库版本。
  - 两者一致 → 正常取证
  - 工程版本**旧于**现行库 → 先 `search_code_knowledge("固件库 版本 差异")` 读版本差异页，确认要用到的 API 是否被**删除 / 改名 / 签名变更**；旧版原码用 `get_file` 取归档路径（直接引用旧路径会被自动提示归档新位置；`find_example(..., legacy=True)` 可列归档例程）
  - **禁止**把现行库的同名文件当成旧工程可用代码（旧路径取档会被自动提示归档新路径）；汇报中说明差异并提示可升级
- 组件相关：先读 `manifest/components-lock.json`，按 `component_id` + `tag` 调 `search_component_code` / `read_component_file`；MCP 无法解析锁定 tag → 按知识缺口处理并告知用户，**不自行改用 branch/HEAD 或本机仓库副本**
- 组件未集成但用户要用其 API → 用户确认后用 `export_project_bundle(components=[...])` 重建工程，**不手工拷贝组件源码进工程**
- 检索无果：换关键词重试一轮；用 `get_file` 直读库头文件确认 API 签名；仍无结果 → **记知识缺口**：code MCP `submit_feedback` 上报，并在 HT32_PROJECT.md 标「待核对（MCP 未覆盖）」；**不凭记忆写 API**

### 第 6 步 · 写码

- **风格基线**：按第 2 步确认的基线执行。基线为「沿用工程现有风格」时，命名 / 排版 / 注释 / 文件骨架跟随现有代码，`references/code-style.md` 只作参考；类型宽度、中断与共享数据、禁止清单中的安全条目（动态内存、头文件定义变量、改库文件、死代码）仍按规范执行
- **写码纪律**：按 `references/code-style.md` 与 `HT32_PROJECT.md` 第 8 节——分层命名（驱动贴 FWLib / 应用用 C 库惯例）、自研数据用 stdint 而库签名照抄 `u32`/`u16`/`u8`、BFTM0 时间基准（不用 SysTick 做死循环延时）。细则不在此重复
- **`.clang-format`（强制）**：工程根必须有；缺失时从 `assets/clang-format` 复制，已存在且内容不同 → 询问用户，**不静默覆盖**；基线为「沿用工程现有风格」时**不覆盖**
- 只使用第 5 步确认过的 API 与配置方式；**例程改写优先于从零手写**；不确定的行为回第 5 步查证，查不到标 `TODO(待查证)` 并在汇报中说明
- 引脚/外设冲突与 HT32_PROJECT.md 第 3 节核对；不修改 raw/ 冻结内容；不修改库文件本体（用户明确要求时除外，且记入变更记录）

### 第 7 步 · 格式化与编译回查

- 对本轮改动的 `.c/.h` 执行 `clang-format -i --style=file <文件>`，再 `clang-format --dry-run --Werror` 确认无输出后才编译；本机无 clang-format → 记 warning 继续，汇报说明「未做格式校验」
- **基线为「沿用工程现有风格」时**：先 `--dry-run` 看改动范围，若会重排本轮未触碰的行 → 不执行 `-i`（只格式化本轮新增文件），汇报说明「既有文件未重排」
- 通过 `_ht32-runtime/ht32.py workflow run --project <工程路径> --steps build` 编译，读统一 JSON 的 status、产物与日志路径
- **cppcheck 保持可选**，不作为交付门禁；本机无工具，或无法复现工程实际编译的 AC5 方言、宏定义和 include 路径时，跳过并说明「未做静态分析」，不以通用命令代替真实工程检查
- 只有能从工程设置 / 构建日志还原方言、宏和 include 路径时才运行；先对同一文件集与同一参数记录现有告警**基线**，只处理相对基线新增且经人工核实的问题，存量告警单列；不宣称与 AC5 等价，不通过 suppress 隐藏厂商扩展报错
- 静态栈/调用深度分析不作为完成判据：工具无法忠实建模 AC5、间接调用、递归/中断栈与工程实际宏和链接布局时，不报告精确栈余量或「栈安全」结论
- 遇到编译期 `#error`：记录首条诊断、文件/行号、触发条件及当前生效的宏定义/include 路径；通过 code MCP 核实配置要求，修正实际配置后重编，不删除/注释诊断或随意补宏绕过
- 有错误 → 回第 5–6 步修正后重编；反复失败 → 停止并报告首错误 + 日志路径

### 第 8 步 · 收尾

- 更新 HT32_PROJECT.md：本轮需求状态、外设清单状态、变更记录追加（改了哪些文件）
- **功能提交（Git 已启用时）**：按 `references/git.md` 第 4 节，只提交本轮改动文件与
  HT32_PROJECT.md，conventional commit（`feat(<模块>): …` / `fix(<模块>): …`）；
  **不 push、不改 Git 配置、不提交构建产物**；提交失败只 warning，交付继续
- 汇报：代码改动清单 + **每处改动的知识依据**（知识页/例程/库源码路径）；待查证 TODO 单列；
  Git 已启用时附本次 commit 短 sha

## 失败处理

| 情况 | 处理 |
|---|---|
| HT32_PROJECT.md 缺失且无法补齐 | 阻断写码 |
| 既有工程风格与规范不一致、用户未确认基线 | 阻断写码，先让用户在「沿用现有风格 / 采用 skill 规范」中二选一 |
| 芯片不支持所需外设 | 阻断，附 wiki 依据 |
| MCP 答不出引脚/规格/库 API，或 code MCP 检索无结果 | 换关键词 / 直读库头文件；仍无则记知识缺口 → 对应 MCP `submit_feedback` 上报 + HT32_PROJECT.md 标「待核对（MCP 未覆盖）」，不绕过 MCP 取证、不编造 |
| 组件锁定 tag 不可用 / 组件已下架 | 按知识缺口上报并告知用户；**不改用 branch/HEAD、不读本机 Git 历史补证** |
| 编译错误循环 | 停止迭代，报告用户首错误与日志路径 |
| Git 提交失败 / 身份未配置 / 位于父仓库 | warning + 原始报错，交付继续；不代设 Git 配置、不越界提交工程外文件 |
| 发现本 Skill 自身缺陷 | code MCP `submit_feedback(issue_type="skill_defect")` 上报（附 skill 版本号 + 现象 + 日志路径），再按上表处理 |

## 核心规则

- **取证只走 MCP**：写进代码的每个 API 必须有 code MCP 检索依据，规格/引脚依据必须有 wiki MCP 依据；MCP 答不出即记缺口上报
- **组件证据按锁定版本**：有 `manifest/components-lock.json` 时，组件 API 依据必须用清单里的 `component_id` + `tag/commit` 经 MCP 取得；不凭记忆、不读本机 Git 历史、不擅自换版本
- **本 Skill 自身缺陷走 code MCP**：`submit_feedback(issue_type="skill_defect")` + skill 版本号
- **杜绝编造**：不确定 = 查证或 TODO，不得凭记忆写 API
- **Git 只做本地提交**：口径见 `references/git.md`；一次任务一次提交，只提交本轮改动与
  HT32_PROJECT.md；不 push、不改 remote、不重写历史、不代设身份
- **写码约定**：先按第 2 步确认风格基线；再按分层命名 + `.clang-format` 排版 + BFTM0 时间操作；固定规则落在 `HT32_PROJECT.md` 第 8 节
- HT32_PROJECT.md 是唯一事实源：功能/外设变更先更新它再动代码；汇报必须附知识依据路径，可追溯
- 工程内既有文档（README 等）仅作参考；与 HT32_PROJECT.md 冲突时以后者为准，并向用户提示差异

## 全局 runtime 与项目数据边界

> 数据边界：项目 `.ht32-skills/` 只允许保存 `config.json`；state / cache / logs 写全局数据目录（不可写时降级，见 SKILL.md「数据目录与宿主沙箱」）。
