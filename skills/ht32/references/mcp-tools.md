# ht32-code MCP 工具速查

> 本文件是 `ht32` 主 Skill 的参考资料（不是工作流，无 frontmatter）：把 ht32-code MCP 的
> 14 个工具浓缩成一页，减少每次调用前反复读 descriptor 的开销。**权威定义以 MCP server
> 实际返回的 descriptor 为准**；本页与 descriptor 冲突时以 descriptor 为准，并按
> skill_defect 反馈（code MCP `submit_feedback`）。
>
> 取证纪律不变：库 API / 例程 / 模板结论只能来自 code MCP；规格 / 引脚 / 寄存器走 wiki MCP。

## 总览

| 工具 | 一句话用途 | 关键参数 | 返回要点 |
|---|---|---|---|
| `get_code_version` | 知识库版本与规模统计（写 HT32_PROJECT.md 第 7 节用） | 无 | 知识库版本、MCP server 版本、固件库覆盖、模板状态、库模块/例程/知识页/raw 统计、索引构建时间、组件目录状态 |
| `list_libraries` | 枚举函数库模块与覆盖系列（不知道模块官方名前先查它） | `series`（f5/l5/F493x5/M0+/M3/M4）；`module`（模块名子串，如 gpio/usart）；`compact`（默认 false；true 时每包一行只给模块名清单，省 token）；`legacy`（默认 false；true 时把 `raw/legacy/` 历史归档包一并列出并标 `[历史版本]`） | 默认返回每模块的驱动源码/头文件 raw 路径、变体数、例程数、知识页路径；`compact=true` 只给模块名（要路径再用 `module=` 或 `compact=false`）；`module` 查不到时回退列全部模块名；有历史归档包时末尾给一行摘要（现行库不含归档包） |
| `find_example` | 按外设/系列/芯片找官方例程 | `peripheral`（必填，如 gpio/usart/adc）；`series`（f5/l5/F493x5/M0+/M3/M4）；`chip`；`limit=10`；`legacy`（默认 false；true 时并入 `raw/legacy/` 归档例程并标 `[历史版本]`） | 例程路径与简介；`chip` 先对照模板 devices.json 确认受支持再按系列过滤 |
| `get_file` | 读 raw/ 原档真实内容（只读） | `path`（raw/ 相对路径，可省略 raw/ 前缀）；`max_length=50000`（-1 取全文） | 文件文本；拒绝绝对路径与 `..`；超长按 max_length 截断；**路径属于已升级归档的旧包时，直接返回 `raw/legacy/<包名>/...` 归档新路径 + 版本差异页指引**（旧工程按提示改路径即可，不要猜新库同名文件） |
| `search_code_knowledge` | 检索函数库知识页 + 系列差异对比页 | `query`（如 "GPIO 输出初始化"/"子系列差异"/"固件库 版本 差异"）；`limit=5` | 按相关性排序的结果：标题、路径、片段；查不到时给换词建议 |
| `get_knowledge_page` | 读知识页全文（`entities/`、`comparisons/`） | `path`（仓库相对路径，如 `entities/libraries/gpio.md`）；`max_length=50000`（-1 取全文） | 页面全文；页面不存在时给相近页候选 |
| `get_project_manifest` | 工程模板清单（建工程第 4 步） | 无 | 各模板结构、默认芯片、支持型号数、路径 |
| `export_project_bundle` | 按 config_id 导出自包含可编译 Keil 工程（可并入私有组件） | `config_id`（必填，`<device>.<board>.<compiler>.<clock>.<retarget>`，如 `ht32f50241.ht32f50241-sk.ac5.hsi-safe.retarget`）；也接受 `device`+`board`（多板型必须给 board）；`out_dir`（本地模式，须为空）；`project_name`（落到工程名/Target/产物名）；`components`（字符串数组，元素 `"<component_id>@<tag>"`，零组件不传）。旧 `chip` 参数仅只读兼容，会带弃用警告，不要再使用 | 云端模式：带 TTL 的 zip URL + SHA256 + 大小；本地模式：物化目录报告。工程文件按工程名重命名，模板自用文件不外发；选了组件时报告列出 `id@tag`、commit、文件数与 tree SHA256 |
| `list_components` | 列出已登记的私有组件（全部对 MCP 用户可见） | `query`（关键词，也接受型号）；`series`（f5/l5/F493x5/`HT32F50241`，支持**族前缀**：`HT32F50241` 命中登记的 `HT32F5`，同族未枚举型号也能查到；也支持通配符 `HT32F49*`；`*`=已登记为全部型号）；`purpose`（bsp/middleware/driver）；`limit=50` | 组件 ID/名称、用途、**能力摘要（provides 前 3 项）**、适用系列（非白名单）、默认 tag、可用 tag + commit 摘要；"已验证型号"折叠在详情；不返回 Git 地址 |
| `get_component_info` | 组件详情与能力契约 | `component_id` | **能力（provides）/ 明确不提供（not_provides）/ 何时用（when_to_use）/ 何时不用（when_not_to_use）/ 组合性（composability）/ 接入文档（docs）**、已验证型号（**验证样本，非适用白名单**）、tag 与 commit、导出落点与仓库导出根、排除规则、容量上限、许可/维护者 |
| `search_component_code` | 在组件指定 tag 的快照中检索源码 | `component_id`；`tag`（空则用登记默认 tag）；`query`（必填）；`limit=10` | 文件路径:行号 + 片段；带 tag/commit 证据；不读 Git 历史；**零命中会提示先看 `get_component_info` 的能力/不提供/文档，而不是自行实现** |
| `read_component_file` | 读组件导出根内单个文件 | `component_id`；`path`（相对 posix）；`tag`；`max_length=50000` | 文件文本 + SHA256 前 16 位；拒绝绝对路径/`..`/越界/二进制 |
| `check_skill_update` | 查 ht32-skills 是否有新版本（防用旧 skill） | `current`（本机版本，可空） | 云端最新版本与发布说明；取不到时记"未获取（原因）"，非阻断 |
| `submit_feedback` | 上报语义性问题（含 skill 自身缺陷） | `question`（必填，抄录用户原始需求）；`issue_type`（wrong_api/missing_info/wrong_path/export_defect/**skill_defect**/unclear/other）；`answer`/`correction`/`details`/`model_id`/`tools_called`；**`skill_version`（本机 `ht32.py version` 的版本号，skill_defect 必填）** | 受理确认；skill_defect 必附 `skill_version` + 现象 + 日志路径（服务端拿不到客户端版本，不传则该条无法定位版本） |

## 调用时机速查

| 场景 | 先用 |
|---|---|
| 选型 / 规格 / 引脚 / 寄存器 | **wiki MCP**（本页不展开；规格类结论不得用 code MCP 兜底） |
| 不知道库模块官方名 / 有哪些模块 | `list_libraries(compact=true)`（要路径再 `module="<模块名>"`） |
| 某外设怎么初始化、API 易错点 | `search_code_knowledge` |
| 找官方例程照抄结构 | `find_example`（要原文再 `get_file`） |
| 工程固件库版本旧于现行库 / 编译报"未定义 API" | `search_code_knowledge("固件库 版本 差异")` 查 `comparisons/fwlib-version-history.md`，再按 `raw/legacy/` 取证 |
| 从零建工程 | `get_project_manifest` → `list_components`（**必做一次**，结果可为 0 个但须留评估结论）→ `export_project_bundle` |
| 需要 BSP / 中间件 / 私有驱动包 | `list_components` → `get_component_info` → `search_component_code` / `read_component_file` |
| 判断某组件能不能用 / 能不能与现有实现共存 | `get_component_info`（看 `provides` / `not_provides` / `composability` / `docs`，再 `read_component_file` 读文档） |
| 写已有工程的组件相关代码 | 先读工程 `manifest/components-lock.json`，用锁定 `id` + `tag` 查组件源码 |
| 写版本记录 / 判断知识库新旧 | `get_code_version` |
| 会话开始 / env-check 防旧版 | `check_skill_update`（本地 24h TTL 缓存，见 `version` 命令） |
| 发现答错 / 库缺口 / 导出缺陷 / 本 Skill 缺陷 | `submit_feedback`（skill 自身缺陷用 `skill_defect`） |

## 注意

- 工具调用已由服务端自动记用量日志；**不需要**为每次调用提交 feedback，只报语义性问题
- `export_project_bundle` 云端模式返回的是**下载 URL**：下载只用 runtime `fetch --url ... --out ... --sha256 ... --size ...`（Python TLS 不经 Windows schannel；证书链不可信时按哈希校验），**不要**用 `Invoke-WebRequest` / `curl`，也不要写「跳过证书校验」的脚本
- `fetch` 可加 `--extract [<目录>]`：校验通过后直接安全解压（拒绝非空目标 / 绝对路径 / `..` 条目），省略目录时解到 zip 同名目录
- `get_file` 只读 raw/ 冻结原档；知识页写"怎么用"，原码证据走 `find_example` + `get_file`
- **私有组件只按登记 tag 取用**：`components` 元素必须是 MCP 返回过的 `"<component_id>@<tag>"`；
  未知组件/tag 会整体失败（`COMPONENT_NOT_FOUND` / `COMPONENT_TAG_NOT_FOUND` /
  `COMPONENT_TAG_NOT_ALLOWED`），**不回退 branch/HEAD**；组件目录未配置时组件工具会明确提示
- **`not_provides` ≠ 冲突**：它只说明组件不实现该能力（如 BSP 不提供 LED 抽象 / printf 重定向），
  引入组件不等于要替换既有实现；共存边界看 `composability`（如只调 `BSP_Init()` 时基时，
  可与官方 retarget printf、`HT32F_DVB_LED*` 同时使用）
- **「已验证型号」是验证样本，不是适用白名单**：未列出的同族/同系列型号同样可选用，
  但要在交付说明与 HT32_PROJECT.md 第 4 节标注「未在组件验证样本内」并在工程实测
- 组件导出产物含 `components/<id>/` 与 `manifest/components-lock.json`（tag/commit/逐文件 SHA256）；
  **不得**手工 clone 私有仓库、手工拷贝组件源码或改写锁定清单
- MCP 答不出 → 记知识缺口 + `submit_feedback`，**不得绕过 MCP 取证**，更不得凭记忆编造
