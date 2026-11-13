---
name: ht32
version: 1.0.0
description: >-
  HT32 单一主入口：芯片选型咨询、从零新建工程、已有工程续开发（写码/改码/修 bug）、
  同系列型号迁移（如 HT32F50230 → HT32F50241）、环境检测、HT32_PROJECT.md 生成与更新、
  AGENTS.md 引导维护、构建、烧录、在线调试、串口观测与 printf/retarget、工程 Git 初始化与提交。
  当用户提到 HT32 选型、建工程、写代码、编译、烧录、下载固件、调试、串口打印、
  printf、看串口日志、换型号/迁移型号、环境检查、HardFault、死机、跑飞、复位环路、外设不工作、
  时钟树、低功耗、唤醒源、电流偏大、Git 初始化或 HT32_PROJECT.md / AGENTS.md 时自动触发。
  内部工作流由本 Skill 自动路由，用户无需选择子 Skill。
argument-hint: "<需求描述或意图> [--project <工程路径>]"
---

# HT32 主入口

HT32 MCU 开发的唯一宿主入口：按意图路由到 `workflows/*.md`。所有工作流共享同一 Python
runtime（`_ht32-runtime/ht32.py`，与本 Skill 目录同级）。**本文件只做路由、后端与边界，
细节在各工作流与 `references/`，不在这里重复。**

## 版本与更新

- 版本两处一致：frontmatter `version` 与 `VERSION` 首行；`ht32.py version` 读出 `skill_version`
- 工程型工作流（project-create / dev-assist / migrate / build / flash / debug）首次进入或
  env-check 时跑一次 `version`；只有 `update.check_due=true` 才联网查
- 更新检查走 code MCP `check_skill_update(current=...)`，结果用 `version --record-update` 缓存
  24h；**非阻断**，升级须用户同意，下次会话生效。详细流程见 `workflows/env-check.md` 第 3 步
- 清单在 ht32-code 侧 `skills-release.json`，两仓发版成对做

## 意图路由

| 用户意图（示例） | 内部工作流 |
|---|---|
| 选芯片、型号对比、推荐（不建工程） | `workflows/select.md` |
| 从零新建工程、初始化项目 | `workflows/project-create.md` |
| 已有工程加功能、写码/改码、修 bug、续开发 | `workflows/dev-assist.md` |
| 换型号、同系列型号迁移 | `workflows/migrate.md` |
| 环境检测、安装验证、更新检查 | `workflows/env-check.md` |
| 生成 / 更新 HT32_PROJECT.md、维护 AGENTS.md 引导 | `workflows/project-context.md` |
| 只构建、编译 | `workflows/workflow.md`（`--steps build`） |
| 烧录固件、下载程序 | `workflows/flash.md` |
| 在线调试、断点、单步、看变量、调试器连不上 | `workflows/debug.md` |
| 换 / 切仿真器、改 Keil Target、看调试器配置 | `workflows/keil.md`（`keil configure`） |
| 直连 J-Link（不经 Keil） | `workflows/jlink.md`（实验性备用通道，先告知未经真机验收） |
| 死机、跑飞、复位环路、HardFault、外设不工作 | `workflows/troubleshoot.md` |
| 时钟树、低功耗、唤醒源、电流偏大 | `references/clock-power.md` |
| 统一代码风格、格式化、风格检查 | `references/code-style.md` + 工程根 `.clang-format` |
| 看目标板输出、printf、串口乱码、串口日志 | `workflows/uart-print.md` |
| 下载工程 zip 失败、TLS/证书报错、SHA256 不一致 | `workflows/project-create.md`（第 5 步 `fetch`） |

路由规则：

1. 意图含糊（如「开始搞 HT32」）→ 先询问澄清，不猜测
2. 型号比较 / 推荐 → select；确认型号后从零建工程 → project-create
3. 已有 `.uvprojx` 或 `HT32_PROJECT.md` → dev-assist；用户自有 `AGENTS.md` 不构成判据
4. **env-check 必跑矩阵**：project-create / migrate 执行前必须完整跑；dev-assist / flash / debug
   只在首次进入、工具链变动、上一步报「工具缺失」时补跑；select 只做知识级预检。
   `blocked` → 停止并输出 MCP 配置指引
5. 跨工作流转场（select → project-create、dev-assist → flash 等）由本入口衔接，用户不用换 Skill
6. 型号迁移只支持**同系列**（同一固件库包，用 `list_libraries` 实测判定）；跨系列说明不支持，
   给替代路径（新建工程 + 功能搬运），不硬改工程

## 执行后端

默认 **Keil MDK v5（UV4）+ ARM Compiler 5**：编译、烧录、调试都经 UV4；调试器（e-Link32 Pro /
CMSIS-DAP / J-Link）在 Keil 的 Options for Target → Debug 里配，runtime 能读也能改。

- `backend=keil`（默认）→ `workflows/keil.md`
- **J-Link 直连（实验性备用通道）** → `workflows/jlink.md`：只在用户明确要求「不经 Keil、
  直接用 J-Link」时启用，且必须先说明该通道**未经真机验收**；常规 J-Link 用法仍是在 Keil 里配
- 换探针不用让用户在 Keil 里手点：`keil configure --project <工程> --debugger <cmsis-dap|jlink>`，
  自动改 `.uvprojx` / `.uvoptx`，改前备份；`--dry-run` 先预览

**串口观测先问用户用哪个后端**：只选 Agent 能自动驱动的——用户已有的可自动化串口能力优先，
本包内建 `serial` 兜底（`serial ports` / `serial log` / `serial send`）。

1. 用户自己的串口助手有 **MCP** 接口（如 ComAssistant）→ 优先用用户的
2. 用户自己的串口 **skill** → 次选
3. 用户串口助手的 **HTTP / 命令行**接口 → 再次
4. 以上都没有 / 不可自动化 → runtime 内建 `serial` 命令组兜底

同一会话只问一次；用户要求记住时写进 `HT32_PROJECT.md` 第 3 节。细节见 `workflows/uart-print.md`。

**关心启动日志时「先开后烧」**：给 workflow 加 `--observe-port`，runtime 在跑步骤前打开端口并持续采集：

```powershell
python "<global-skill-root>\_ht32-runtime\ht32.py" workflow run --project "<project-root>" --steps "build,flash" --observe-port "COM13" --observe-baud 115200 --observe-seconds 15
```

## 执行纪律

1. **env-check 是前置，不是每轮必跑**（矩阵见路由规则 4）；`unknown` ≠ 缺失，先继续，
   真失败再按 `workflows/env-check.md` 排障
2. **闭环三段**：改代码 → build 成功 → flash → observe；不跳过 flash 直接看串口，
   不拿旧固件打印当本次结果
3. **一次调用拿全证据**：`workflow run --steps "build,flash" --observe-port COMx` 一条命令给出
   编译、烧录与启动日志，不拆成三次单步
4. **先看摘要**：`steps[].summary` 已含错误/警告数与 `Code/RO/RW/ZI`（编译）、
   `erase_done/programming_done/verify_ok/application_running`（烧录）；摘要够用不 tail 整个日志
5. **烧录前后要能区分**：`observations.observe.post_flash_offset` 必须连同
   `post_flash_offset_source` 读——`usb_reenumerate` 可信，`flash_command_return` 是近似；
   出现「全部来自烧录前」即本次证据作废
6. **串口先证通信再归因固件**：先用选定后端确认真有字节（波特率与 `conf.h` 的
   `RETARGET_UxART_BAUDRATE` 一致、8N1），再查固件与 retarget
7. **默认烧录走 Keil**；用户明确要求直连 J-Link 时按 `workflows/jlink.md` 的实验性通道执行
8. **调试器是独占资源**：Keil IDE 开着调试会话时，UV4 命令行会争用调试器，先让用户结束调试
9. **工程没配调试器 ≠ 环境坏了**：按提示在 Options for Target → Debug 选一次，不反复重试
10. **`serial ports` 只列 COM 口**；CMSIS-DAP 在 `env check` 的 `cmsis_dap` 项里

## 数据与写码边界

- runtime 的 state / cache / logs 写全局数据目录（默认 `%USERPROFILE%\.ht32-skills`）；
  用户工程 `.ht32-skills/` **只允许**保存 `config.json`
- 沙箱拦 home 写入时 runtime 自动降级（照常执行、日志不落盘、JSON 带 warning），**不是失败**；
  需要落盘时用 `HT32_SKILLS_HOME` 重定向，不在用户工程里另开日志目录
- 下载工程 zip 一律用 runtime 内建 `fetch`（带 `--sha256`），不用 `Invoke-WebRequest` / `curl`，
  不跳过证书校验；细节见 `workflows/project-create.md` 第 5 步
- **写码约定**：`references/code-style.md`（分层命名 / 类型 / 注释）+ 工程 `HT32_PROJECT.md` 第 8 节
  + `.clang-format`（排版唯一源）。既有工程先定风格基线（沿用现有风格 / 采用 skill 规范，
  见 `workflows/dev-assist.md` 第 2 步）；RTOS 不在本 Skill 覆盖范围

## 安全边界

- 覆盖用户已有目录/文件、烧录、Mass Erase / 改 Option Byte 等副作用操作，必须先向用户确认
- **Git 只做本地版本控制**：工程无仓库时**先问用户**是否启用；同意后 `git init` + 一次初始提交；
  **已有自身仓库且创建前有未提交改动时，先问是否提交后再开始，拒绝则停止创建**；
  交付提交也要先确认，后续功能提交由 dev-assist 自动完成（只提交本轮改动）；
  **不 push、不拉取、不改 remote、不重写历史、不代设 Git 身份**；父仓库内的工程不初始化、
  不自动提交。细则见 `references/git.md`
- **不委派宿主 Skill（串口除外）**：构建 / 烧录 / 调试全部由本包 runtime 执行；串口观测是唯一
  例外——用户已有可自动化的串口能力优先，没有才用本包 `serial` 兜底；只调用用户明确给出的接口，
  不猜端点、不扫本机端口
- **宿主同名通用 Skill 不并行使用（串口除外）**：`keil` / `jlink` / `workflow` 走本包 runtime；
  串口 skill 按上面的后端优先级优先用用户的；外部 skill 结果**不能与本包证据混算**
- MCP-only 取证：规格 / 引脚 / 库 API 只来自 wiki / code MCP；答不出 → `submit_feedback` 上报 +
  `HT32_PROJECT.md` 标「待核对（MCP 未覆盖）」，不绕过、不编造
- **私有组件也只走 code MCP**：`list_components` 发现 → `search_component_code` /
  `read_component_file` 取证 → `export_project_bundle(components=[...])` 并入；只接受
  `"<component_id>@<tag>"`，不回退 branch/HEAD，不在本机 clone；已导出工程按
  `manifest/components-lock.json` 锁定
- **标准组件优先（必做评估）**：需求涉及时基/延时、串口收发（含中断发送）、Flash 参数存取、
  状态机时，建工程必须先经 code MCP `list_components` + `get_component_info` 比对已登记组件的
  `provides`，能复用则不重复实现；零组件也要给出理由并记入交付说明与 `HT32_PROJECT.md` 第 4 节。
  「已验证型号」是验证样本、不是适用白名单——未验证型号可选用，但须标注并实测；
  `not_provides` 只表示组件不做该能力，不等于引入后与既有实现冲突（共存边界看 `composability`）
- **本 Skill 自身缺陷走 code MCP** `submit_feedback(issue_type="skill_defect")`：`skill_version`
  传实测值，`question` 抄用户原话，`details` 写现象 + 日志路径；不静默吞掉
- 统一返回 JSON，按 status/exit code 汇报（ok=0 / warn=0 / blocked=2 / error=3 / 异常=4）
