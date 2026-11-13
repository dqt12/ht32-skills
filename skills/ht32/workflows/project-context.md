# HT32 项目上下文生成与更新（project-context 工作流）

> 本文件是 `ht32` 主 Skill 的内部工作流（无 frontmatter，不是独立宿主 Skill），由主入口 `SKILL.md` 按意图路由进入，也被 project-create / dev-assist 工作流作为共享子流程调用。

`HT32_PROJECT.md` 是用户工程 HT32 开发约定的唯一事实源，生成于工程根目录，是每次会话的上下文锚点。
工程根的用户 `AGENTS.md` 不承载 HT32 事实，只承载一段由标记包围的薄引导，让通用 Agent 找到本文件。

## 模板

- 项目事实模板 v10：主包目录下 `templates/HT32_PROJECT.template.md`（与仓库根 `templates/HT32_PROJECT.template.md` 字节一致——安装时由安装方同步；开发态从仓库源使用时为仓库根模板）
- 薄引导模板：主包目录下 `templates/AGENTS.guide.md`（同样与仓库根模板字节一致，安装时同步）
- 九个固定 section：项目概述 / 芯片选型核对结论 / 外设与资源清单 / 使用的库与例程路径 /
  需求记录 / 变更记录 / **工具链与版本记录** / **代码规范与约定** / **本次执行计划与进度**
- 结构契约（脚本可消费）：`{{field}}` 占位符填充后不得残留；section 标题文本不可改；
  需求记录/变更记录表只追加行；第 7 节为**当前版本快照**（刷新时同步在第 6 节追加一行）
- 第 9 节是**本次任务**的执行计划与勾选（不是永久历史）：步骤行格式
  `- [ ] P7b 基线编译` / `- [x] ...` / `- [!] 失败或跳过（原因）`；
  勾选必须有证据（runtime JSON status、`0 Error 0 Warning`、文件存在、串口字节数）；
  任务结束把结论收敛成一行进第 6 节，第 9 节归档/清空，下个任务重新开块
- 版本锚点：`<!-- ht32-project:template-version:10 -->`
- 可用仓库 `scripts/validate_template.ps1` 校验模板

## 上下文发现顺序

1. 存在 `<工程根>/HT32_PROJECT.md` → 读取并更新它，同时确保 `AGENTS.md` 引导存在
2. 不存在，但 `AGENTS.md` 是用户自有规则文件 → 保留全文，初始化 `HT32_PROJECT.md` 并追加引导块
3. 两个文件都不存在 → 初始化 `HT32_PROJECT.md`，再创建最小 `AGENTS.md`

## 执行流程

### init（工程根目录无 HT32_PROJECT.md）

> **时机**：新建工程在"导出/解压落盘核对通过后、config init 之前"立即执行；
> 已有工程在确认目录结构完整后立即执行。落盘不完整（缺 `.uvprojx`）时不得写。

1. 定位工程根目录：用户指定 > 当前 workspace > 询问用户；**不猜测**
2. 收集字段：
   - **必填：芯片型号**。当前会话已有 wiki 核对结果则直接使用；否则调 wiki-mcp
     `get_model` + `get_chip_info` 核对，并把页面来源写入 `{{wiki_source}}`
   - 工程名 / 项目目标 / 外设需求：来自用户或主 skill 传入；缺失时询问
   - 固件库版本 / 例程路径：有 code-mcp 时用 `get_code_version` / `find_example` 补充；无则填"待补"
   - 私有组件 / 组件选用结论（第 4 节）：来自建工程第 4 步的能力评估结果——
     `{{components}}` 写 `<id>@<tag>`（可附 commit）与落点，零组件写「无」；
     `{{component_decision}}` 写选/不选 + 理由，选用未验证型号时标注「该型号未在组件验证样本内」
   - **工具链版本快照（第 7 节，必填）**：逐项实测取值为准，不得凭记忆填写
     | 字段 | 取值方式 |
     |---|---|
     | `{{wiki_mcp_version}}` / `{{wiki_kb_version}}` | wiki-mcp `get_wiki_version`（分别取 `MCP Server:` 行与首行知识库版本） |
     | `{{code_mcp_version}}` / `{{code_kb_version}}` | code-mcp `get_code_version`（同上） |
     | `{{skills_version}}` / `{{skills_used}}` | **实测**：`_ht32-runtime/ht32.py version` 返回的 `skill_version`（读安装包/仓库 VERSION 文件；装不上版本文件时才写「未获取（无 VERSION 文件）」）；记录本次实际用到的内部工作流名 |
     | `{{template_version}}` | 当前模板版本标记（v10 填 `v10（template-version:10）`） |
     | `{{keil_version}}` / `{{dfp_version}}` / `{{dfp_source}}` | 复用 **env-check** 实测结果：`uv4.version`（UV4.exe FileVersion）、`armcc.version`（ARMCC 版本行）、DFP pack 版本与路径 |
     | `{{debugger_backend}}` / `{{debugger_probe_serial}}` / `{{debugger_interface}}` / `{{debugger_speed_khz}}` | runtime `config show --project <工程路径>` 返回的 resolved debugger；probe_serial 未指定时写「自动选择」；快照读取不带单次覆盖参数 |
     | `{{toolchain_snapshot_date}}` | 本次生成日期 |
     | `{{code_conventions}}` | 第 8 节「项目追加约定」：用户明确提出的追加代码约定；**既有工程须先记录风格基线**（沿用工程现有风格 / 采用 skill 规范，见 dev-assist 第 2 步）；无则写「无」 |
   - **第 8 节固定规则照抄模板**（分层命名：驱动贴 FWLib / 应用用 C 库惯例、通用命名、
     类型宽度、clang-format 排版、BFTM0 时间操作、标准组件优先），只按需填写末尾追加约定，
     不得改写固定规则文本
   - MCP 或工具链不可用时按实际写"未获取（原因）"，**不编造版本号**
3. 填充：用核对结果替换所有 `{{...}}` 占位符；无法确定的字段写"待核对/待补"，不留空占位符
4. 初始化变更记录：确认首行为 `| <日期> | 初始化 HT32_PROJECT.md | ht32 |`
5. **初始化第 9 节计划**：按本次任务只列适用步骤（`P7a/P7b/P8a/P8b/P9`），全部 `[ ]`；
   本次未要求的步骤写 `[!] 跳过（本次未要求）`
6. 写入 `<工程根>/HT32_PROJECT.md`
7. 写入或刷新 `AGENTS.md` 引导（见下"薄引导维护"）
8. 回读校验：九个 section 齐全、文件内无残留 `{{`；第 9 节步骤行格式合法；引导起止标记各一个；有则修复后再交付

### update（已存在 HT32_PROJECT.md）

1. 读取现有文件，按 H2 标题解析九个 section（旧 v1 文件只有八个，按兼容处理）
2. 只做五类修改：
   - **追加**：需求记录 / 变更记录表在末尾追加新行，不改既有行
   - **刷新快照**：第 7 节版本表按本次实测结果刷新（快照日期同步更新），并在第 6 节追加一行说明
   - **更新结论**：仅当携带新的 wiki 核对依据时，更新第 2/3 节对应结论（新值 + 新来源一起更新）
   - **结构升级**：模板版本标记落后时补入新增 section 或快照字段（v1→v2 补第 9 节；
     v2→v3 第 8 节改为 FWLib 风格 + `.clang-format` 排版；v3→v4 第 8 节改为分层命名：
     驱动贴 FWLib、应用用 C 库惯例；v4→v5 删除第 8 节「标准组件后续发布」的过期承诺；v5→v6 第 8 节补「类型统一 `xxx_t`、每模块一个 `xxx_t`、禁止全局变量」；v6→v7 第 8 节补「既有工程风格基线」例外说明；v7→v8 精简全文说明文字，规则与占位符不变；v8→v9 第 7 节 debugger 拆四行、第 9 节补子块格式、第 8 节规范引用改为 skill 内路径说明；v9→v10 第 4 节新增「私有组件」「组件选用结论」两行），
     缺失值按 init 现场采集，旧描述保留，升级依据追加到第 6 节，再更新版本标记，不删除、不重排既有内容
   - **追加约定**：第 8 节固定规则文本不得改写（模板结构升级除外）；末尾「项目追加约定」按用户新提出的约定更新，
     既有工程的风格基线（沿用现有风格 / 采用 skill 规范）也记在这里，依据追加到第 6 节
3. 刷新"最近更新"日期
4. 确保 `AGENTS.md` 引导存在且唯一（见下"薄引导维护"）
5. 写回并回读校验

## 薄引导维护（AGENTS.md）

固定受管块（`templates/AGENTS.guide.md` 逐字内容）：

```markdown
<!-- ht32-project-guide:start -->
## HT32 项目上下文

处理本工程的 HT32 选型、开发、构建、烧录或调试任务前，必须先读取根目录 `HT32_PROJECT.md`。该文件是 HT32 项目事实的唯一来源；本文件不重复维护其中内容。
<!-- ht32-project-guide:end -->
```

只允许三种操作：

| 情况 | 操作 |
|---|---|
| `AGENTS.md` 不存在 | 创建只含受管块的最小文件 |
| 已存在且无受管块 | 在文件末尾追加一个空行 + 完整受管块，**不改写块外任何内容** |
| 已存在且有一个完整受管块 | 仅原位替换 `start`/`end` 标记之间的文本 |

- 只有 `start` 或只有 `end`（半块）→ 视为结构损坏，**停止写入**并请用户处理，不猜测边界
- 不把芯片、引脚、工具链、需求或历史写进 `AGENTS.md`；该文件不承载 HT32 事实

## 幂等回读校验（交付前必做）

- `HT32_PROJECT.md`：九个 section 齐全、无 `{{` 残留；第 9 节步骤行格式合法
- `AGENTS.md`：`start`/`end` 标记各恰好一个、顺序正确
- 重复执行 init/update 不新增第二块引导；块外**正文**不被改写（仅允许调整块周围空白与文件末尾换行）

## 失败处理

| 情况 | 处理 |
|---|---|
| 找不到模板文件 | 阻断；提示重装/更新 ht32-skills（用宿主自己的 Skill 安装机制按 README「安装」重装） |
| 芯片型号无法经 wiki 核对 | 第 2 节填"待核对"并明确告知用户，**不编造规格** |
| MCP 版本工具调用失败/超时 | 第 7 节对应栏填"未获取（工具名 + 失败原因）"，其余字段照常填写，不阻断 |
| 拿不到 env-check 结果（如直接进入本工作流） | Keil/DFP/调试器栏填"未获取（未执行 env-check）"，或先执行 env-check 工作流补测 |
| 目标目录无写权限 | 报错并给出建议路径 |
| `AGENTS.md` 含半块引导标记 | 阻断，请用户修复标记后再执行 |
| 发现本 Skill（流程/模板/runtime）自身缺陷 | 用 code MCP `submit_feedback(issue_type="skill_defect")` 上报，附 skill 版本号（`ht32.py version`）与现象；再按上表处理 |

## 核心规则

- 历史只追加不改写；需求/变更记录绝不重写旧行
- 芯片规格字段只有 wiki 核对依据才填已核对值，否则标"待核对"
- 第 7 节版本值只有实测来源才填具体值（版本工具返回、`ht32.py version`、env-check 脚本输出），
  否则标"未获取"；禁止凭记忆或推测填版本号
- 本 Skill 自身缺陷（流程/模板/脚本/环境检测）→ code MCP `submit_feedback(issue_type="skill_defect")` 上报，不静默吞掉
- 不在 `HT32_PROJECT.md` 记录密钥、临时状态、代码正文
- `AGENTS.md` 只承载薄引导；**禁止**把 HT32 事实写回 `AGENTS.md`
- 一次只做与本轮需求相关的更新
- 写入后必须回读校验（section 齐全、无残留占位符、引导标记唯一）

## 全局 runtime 与项目数据边界

> 数据边界：项目 `.ht32-skills/` 只允许保存 `config.json`；state / cache / logs 写全局数据目录（不可写时降级，见 SKILL.md「数据目录与宿主沙箱」）。
