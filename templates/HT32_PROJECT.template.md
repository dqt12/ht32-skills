<!-- ht32-project:template-version:10 -->
<!-- 由 ht32-skills 生成与维护。结构契约：9 个固定 H2 标题不可改；花括号占位符填充后不得残留；
     第 5/6 节只追加；第 7 节为当前快照；第 9 节只记本次任务。勿删版本标记。 -->

# HT32_PROJECT.md — {{project_name}}

> 本工程 HT32 开发的唯一事实源。会话开始先读；需求或选型变更随之更新；历史只追加。

## 1. 项目概述

| 字段 | 值 |
|---|---|
| 项目名称 | {{project_name}} |
| 项目目标 | {{project_goal}} |
| 创建日期 | {{created_date}} |
| 最近更新 | {{updated_date}} |

{{project_overview}}

## 2. 芯片选型核对结论

> 规格来自 ht32-wiki MCP；来源与结论成对出现，未核对标「待核对」。

| 核对项 | 值 |
|---|---|
| 目标芯片 | {{chip_model}} |
| 内核 / 主频 | {{chip_core_clock}} |
| 封装 / 引脚数 | {{chip_package}} |
| Flash / SRAM | {{chip_flash_sram}} |
| 工作电压 | {{chip_voltage}} |
| 核对来源 | {{wiki_source}} |
| 核对结论 | {{verify_conclusion}} |

{{chip_notes}}

## 3. 外设与资源清单

| 外设 | 用途 | 引脚/通道 | 库模块 | 例程参考（ht32-code 路径） | 状态 |
|---|---|---|---|---|---|

<!-- 状态：计划中 / 开发中 / 已启用 -->

资源占用说明（中断优先级 / DMA / 定时器分配等）：{{resource_notes}}

## 4. 使用的库与例程路径

| 字段 | 值 |
|---|---|
| 固件库版本 | {{library_version}} |
| 库路径（工程内） | {{library_path}} |
| 参考例程（ht32-code 路径） | {{example_paths}} |
| 工程模板来源 | {{template_source}} |
| 私有组件 | {{components}} |
| 组件选用结论 | {{component_decision}} |

<!-- 私有组件：`<id>@<tag>`（可附 commit）→ `components/<id>/`，零组件写「无」。
     组件选用结论：选 / 不选 + 理由（需求能力比对结果）；选用未验证型号时标注「该型号未在组件验证样本内，本工程实测」。 -->

## 5. 需求记录

> 只追加。

| 日期 | 需求/变更 | 来源 | 状态 |
|---|---|---|---|

## 6. 变更记录

> 只追加。

| 日期 | 变更内容 | 依据 |
|---|---|---|
| {{created_date}} | 初始化 HT32_PROJECT.md | ht32 |

## 7. 工具链与版本记录

> 当前快照（{{toolchain_snapshot_date}}）。版本值必须实测；MCP 不可用写「未获取（原因）」。
> 环境升级后刷新本表，并在第 6 节追加一行。

| 组件 | 版本 | 来源 |
|---|---|---|
| ht32-wiki-mcp | {{wiki_mcp_version}} | https://mcp.s621.work/ht32-wiki |
| ht32-wiki 知识库 | {{wiki_kb_version}} | 同上 |
| ht32-code-mcp | {{code_mcp_version}} | https://mcp.s621.work/ht32-code |
| ht32-code 知识库 | {{code_kb_version}} | 同上 |
| ht32-skills | {{skills_version}} | 本次使用：{{skills_used}} |
| HT32_PROJECT.md 模板 | {{template_version}} | ht32-skills |
| Keil MDK / ARMCC | {{keil_version}} | UV4 批编译 |
| HT32_DFP pack | {{dfp_version}} | {{dfp_source}} |
| debugger backend | {{debugger_backend}} | config show |
| debugger probe | {{debugger_probe_serial}} | config show |
| debugger interface | {{debugger_interface}} | config show |
| debugger speed | {{debugger_speed_khz}} kHz | config show |

## 8. 代码规范与约定

> 完整规范见 ht32 skill 内置的代码风格参考（skill 安装目录下 `references/code-style.md`）；排版由工程根 `.clang-format` 强制。
> 若「项目追加约定」记了「风格基线：沿用工程现有风格」，则命名/排版/注释/骨架随现有代码，
> 类型宽度、中断与共享数据、安全禁令仍按规范。

- **分层命名**：驱动/板级 `模块_PascalCase`（`BSP_LED_Init`），应用/组件 `模块_snake_case`（`app_key_scan`）；同一文件不混用；库 API 原样调用
- **类型与状态**：类型 `xxx_t`（参数 `xxx_cfg_t`、快照 `xxx_info_t`）；每模块一个实例，由调用方持有并传指针；禁止全局变量与模块内 `static` 状态
- **通用命名**：枚举/宏全大写带模块前缀；头文件保护 `__MODULE_H`
- **类型宽度**：自研数据用 `uintN_t` / `int32_t`；库签名照抄 `u32` / `u16` / `u8`
- **排版**：写完 `clang-format -i --style=file`，交付前 `--dry-run --Werror` 必须通过
- **时间操作**：优先 BFTM0（周期任务用中断时基，阻塞等待用计数器轮询）；不用 SysTick 做延时

项目追加约定：{{code_conventions}}

## 9. 本次执行计划与进度

> 只记本次任务；勾选需证据（runtime JSON / 0 Error 0 Warning / 文件存在 / 串口字节）；
> 失败或跳过用 `[!]`；任务结束把结论写进第 6 节，本节归档。
> 子块格式：`### 任务：<任务名>（<起始日期> 起）`，一个任务一个子块。

### 任务：初始化工程（{{created_date}} 起）

- [ ] P7a config init
- [ ] P7b 基线编译（0 Error 0 Warning）
- [ ] P8a 烧录（application_running=true）
- [ ] P8b 串口观测（收到启动日志）
- [ ] P9 收尾（刷新第 7 节 + 追加第 6 节）
