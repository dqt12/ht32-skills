# HT32 串口打印（printf / retarget）与串口观测（uart-print 工作流）

> 本文件是 `ht32` 主 Skill 的内部工作流（无 frontmatter，不是独立宿主 Skill），由主入口 `SKILL.md` 按意图路由进入。

把 C 库 `printf` 重定向到板级调试串口，并用本包内建命令观测输出。知识来源：wiki MCP
`ht32-uart-printf-retarget` 与 code MCP `retarget`——**结论以 MCP 检索结果为准，本工作流只做流程编排**。

## 前置

1. 读工程根 **HT32_PROJECT.md**：芯片型号、板级调试串口实例与引脚、当前开发状态
2. 确认工具链（Keil / GNU / HT32-IDE）——工程文件清单按工具链有别（见「手动配置」第 3 节）
3. 知识依据（MCP-only）：检索 wiki `ht32-uart-printf-retarget` + code `retarget`；答不出记知识缺口并
   `submit_feedback` 上报，不凭记忆编造宏名与路径（工具速查见 `references/mcp-tools.md`）
4. 实测前从板卡原理图 / 跳线核实调试 UART 实例、TX/RX 引脚、USB-UART 桥接与板端连接，并与
   `conf.h`、初始化代码、串口工具参数对照；软件配置不能证明物理路径已接通，取不到证据标「未核实」
5. 调试器附带 UART/VCP：核实探针型号/固件是否支持、对应 COM 口与实际接线；SWD 通不证明 UART 可用

## 串口后端选择（用户可自动化能力优先，内置兜底）

**任何一次串口观测之前，先确认用哪个后端。** 只选 **Agent 能自动驱动**的后端——用户已有的
可自动化串口能力优先，本包内建 `serial` 只在没有时兜底。

| 优先级 | 情况 | 做法 |
|---|---|---|
| 1 | 用户自己的串口 **MCP**（如 ComAssistant） | 用该 MCP 的端口枚举 / 打开 / 读取 / 发送 / 关闭工具；参数与内置一致 |
| 2 | 用户自己的串口 **skill** | 按该 skill 的用法调用；结果注明「数据由用户串口 skill 采集」 |
| 3 | 用户串口助手的 **HTTP / 命令行**接口 | 按用户给的地址或命令调用；只调用明确提供的接口，不猜端点、不扫端口 |
| 4 | 没有可自动化的用户串口能力 | 本包 `serial` 命令组（见「观测命令」），唯一保底路径 |

- 询问话术（同一会话只问一次）：「串口观测用你自己常用的串口工具，还是用内置的？你常用的那个有
  MCP、HTTP 或命令行接口能让我直接枚举 / 打开 / 读写吗？」
- 用户说「以后都这个工程用它」→ 在 `HT32_PROJECT.md` 第 3 节追加 `串口观测后端：<工具名>（<接入方式>）`
- 只能在界面上手点的工具不纳入后端，直接改用内建 `serial`
- 外部后端准入：能列端口、开关端口、读原始字节、发送、给毫秒时间戳、能在烧录前打开；任一项做不到
  → 用内置 `serial` 做那一步并说明原因
- 证据口径：汇报注明工具名 / 端口 / 波特率 / 采集起止时间与「数据由用户工具采集」；无时间戳不下时序
  结论；无法保证烧录前已打开则启动日志标「可能缺失」；非 runtime 采集的日志不进 `artifacts`

## 默认路径（导出的工程已经配好）

`export_project_bundle` 默认 profile 已预置 retarget：`conf.h` 为 `_RETARGET=1` + `RETARGET_PORT=10`
（COM1），工程含 `ht32f5xxxx_usart.c` + `ht32_retarget.c` + `ht32_serial.c`（Keil **不含** `printf.c`），
模板 `main.c` 已调用 `RETARGET_Configuration()`。多数情况只需核对通道后直接观测：

1. 读工程 `README.md` 与 HT32_PROJECT.md，确认被占用的 UART 实例与引脚
2. 与外设计划冲突时改 `RETARGET_PORT`（或换 profile）；波特率与观测端一致（默认 115200）
3. 按「观测命令」采集；烧录场景务必先开后烧
4. 采不到 / 乱码 / 链接报错 → 走「失败处理表」；先读该工程 `conf.h` 实际值再下结论

工程被手工改造、用 `no-retarget` profile、工具链非 Keil，或 `conf.h` 实际值不是默认 → 转「手动配置」。
exporter 侧缺口走 code MCP `submit_feedback` 上报，不静默吞掉。

## 手动配置（工程被改造 / 非 Keil / 未开启 retarget 时）

### 1. 确认 / 选择通道

- 板级调试串口实例与引脚以 HT32_PROJECT.md 与板级原理图为准；换板以板的原理图为准
- M0+/M3（F5/L5/F1/F175x）：`RETARGET_PORT` 选通道——10=COM1（默认）、11=COM2、12=USART0、
  13=USART1、14~17=UART0~UART3、1=USB 虚拟串口、2=Syslog；**F175x 旧包默认 2，需显式改**。
  选 12~17 时用裸外设实例，GPIO AFIO 按 UserManual 复用表自选
- M4（F490x1/F491x3/F493x5）：库无 retarget 三件，钩子改在板级文件自写（`PUTCHAR_PROTOTYPE`；
  GNU 非 clang 用 `__io_putchar`，其余编译器用 `fputc`）；官方 printf 例程默认 USART1 的 PA9
- 该 UART 实例不能再挪作业务通信：`RETARGET_Configuration()` 会初始化并占用一个实例

### 2. 改 conf.h（M0+/M3）

| 宏 | 默认值 | 何时改 |
|---|---|---|
| `_RETARGET` | 1 | 总开关，保持 1 |
| `RETARGET_PORT` | 10 | 按第 1 步结论选通道 |
| `RETARGET_UxART_BAUDRATE` | 115200 | 必须与观测端 `--baud` 一致 |
| `_AUTO_RETURN` | 0 | 换行显示成两行时改 1 |
| `PRINTF_USE_CLIB` | 0 | 仅 GNU / HT32-IDE 有意义，对 Keil 无意义 |
| `RETARGET_UxART_CLKSEL` / `RETARGET_UxART_CLK_MANUAL` | 0 / 20000000 | 乱码且波特率已核对时查这两项 |

M4 无上述 conf.h retarget 宏矩阵，钩子实现细节查两知识页。

### 3. 工程文件清单纪律（不要手工增删库文件）

| 工具链 | retarget 相关工程文件 | printf 来自 |
|---|---|---|
| Keil | `ht32f5xxxx_usart.c` + `ht32_retarget.c` + `ht32_serial.c`，**不含 `printf.c`** | Keil C 库，经 `fputc` 钩子重定向 |
| GNU / HT32-IDE | Keil 三件 **+ `printf.c`** | `PRINTF_USE_CLIB=0` 用 printf.c 的 lite printf；=1 用 GNU 标准库 |

- ⚠️ **不要把 `printf.c` 加进 Keil 工程**：与 C 库同名符号双重定义，链接报 **L6200E: Symbol multiply defined**
- 文件是否进工程由工具链 / 构建文件决定，不由 `PRINTF_USE_CLIB` 决定；不要用「手工增删库文件」修清单问题

### 4. 调用 RETARGET_Configuration()

- `main()` 初始化段调用一次：内部完成 UxART 实例选择、波特率与 GPIO AFIO 初始化
  （`RETARGET_INT_MODE=1` 时另开 NVIC 中断）
- 之后直接 `printf(...)`；轮询模式每字节阻塞发送，大段输出会拖慢主循环——高频日志改用
  `SERIAL_PutChar` 或 PDMA（细节查知识页）

## 观测命令（内置后端）

```powershell
python "<global-skill-root>\_ht32-runtime\ht32.py" serial ports
python "<global-skill-root>\_ht32-runtime\ht32.py" serial log --port "COM13" --baud 115200 --seconds 10
python "<global-skill-root>\_ht32-runtime\ht32.py" serial log --port "COM13" --baud 115200 --seconds 10 --hex
# 采集期发激励 + 统计心跳间隔；--reset 打 DTR/RTS 复位脉冲；多口共享时间基准
python "<global-skill-root>\_ht32-runtime\ht32.py" serial log --port "COM13" --seconds 10 --send "PING" --send-at 1 --send-newline --analyze intervals --marker "#"
python "<global-skill-root>\_ht32-runtime\ht32.py" serial log --port "COM13" --seconds 5 --reset
python "<global-skill-root>\_ht32-runtime\ht32.py" serial log --port "COM13,COM14" --seconds 10
```

- `--baud` 与 `RETARGET_UxART_BAUDRATE` 一致；`--hex` 用于二进制或编码存疑的输出
- 采集产物三件套落全局日志：`.log`（带时间列）、`.jsonl`（逐批 `t_rel_ms/t_abs/offset/hex`）、
  `.bin`（原始字节）；`--stamp rel|abs|both|none` 控制 `.log` 时间列（默认 `both`）
- `--send/--send-at` 一一对应，在采集窗口内到点发送；比另开 send 进程可靠（端口独占）
- 默认文本模式会过滤非可见字符，二进制/编码存疑时加 `--hex`；无数据返回 warn，不崩栈
- 端口被占用报 **Win32 错误 5**：先关串口助手；占用者是 ComAssistant 时先用其 `serial_close` 释放
- **先证通信再查固件**：看到字节才能归因固件；零字节先按失败处理表第 1 行核对波特率/接线/端口
- 汇报 UART 核验时列出工程配置值、板级通道/引脚证据、串口参数与原始字节；仅完成软件配置检查时
  不得写成「调试器 UART 已验证」

### 烧录场景：先开后烧

`serial log` 是「跑完再开」，烧录后目标板立即复位打印，等烧完再开就丢了。烧录时给 workflow 加
`--observe-port`，runtime 在第一步之前打开端口并持续采集（细节见 `workflows/workflow.md`）：

```powershell
python "<global-skill-root>\_ht32-runtime\ht32.py" workflow run --project "<project-root>" --steps "build,flash" --observe-port "COM13" --observe-baud 115200 --observe-seconds 15
```

不经 workflow 的 Keil IDE 手工下载照样要「先开」：先起后台
`serial log --port COM13 --baud 115200 --seconds 60`，再烧录。

## 时间戳与时序判定

- JSON 里有 `chunks`（每批 `offset/ms/at/bytes`）、`timing`（首/末字节毫秒、批量间隔中位数）与
  `lines_with_time`；`.log` 默认带时间列，`.jsonl` 给出逐批 `t_rel_ms` + `t_abs`
- **USB-CDC 常按 ~107 ms 批量刷新**：时间戳是驱动读到数据的时刻，不是固件逐字节发送时刻；
  判断 100 tick 是否为 1000 ms 时用同一行的到达时刻差，不要把批量间隔当固件抖动
- 固定节拍直接让 runtime 算：`--analyze intervals --marker "#"` 输出匹配行数、间隔数、均值/最小/最大/抖动
- 烧录前后分界看 `post_flash_offset` + `post_flash_offset_source`：`usb_reenumerate` 可信，
  `flash_command_return` 是近似值（复位可能晚约 1 s）

## 失败处理表

| 现象 | 处理 |
|---|---|
| 完全无输出 | ① 是否调用 `RETARGET_Configuration()`；② 是否链接 `ht32_retarget.c`；③ `--baud` 与 `RETARGET_UxART_BAUDRATE` 一致；④ TX 引脚与板级 COM 定义一致；⑤ `serial ports` 里有没有这个口 |
| 有输出但看不到启动日志 | **采晚了**：改 `--observe-port` 先开后烧（手工烧录则先起后台采集），重烧一次 |
| 输出乱码 | 波特率不匹配；或 `CLKSEL=1` 时手动时钟值必须等于 UxART 实际 IP 时钟；换行两行试 `_AUTO_RETURN=1` |
| Keil 链接 L6200E multiply defined | 工程误加了 `printf.c`，移除即可（Keil 只应有三件） |
| 该 UART 已被业务占用 | 换一个 `RETARGET_PORT`，别把调试串口与业务通信挤在同一实例 |
| 切了 C 库后输出失效 | 钩子档位不匹配；确认没有自行重复实现 `_write`/`fputc` 覆盖库版本 |
| Keil 下要 `%f` | lite printf 不支持浮点；先转字符串，或评估 GNU 工具链 |

## 全局 runtime 与项目数据边界

> 数据边界：项目 `.ht32-skills/` 只允许保存 `config.json`；state / cache / logs 写全局数据目录（不可写时降级，见 SKILL.md「数据目录与宿主沙箱」）。

## 核心规则

- 取证只走 MCP：配置矩阵 / 工程文件清单 / 例程结论以 wiki / code MCP 检索为准，不凭记忆写宏名与路径
- 默认值不等于现状：判断工程到底开没开 retarget，**读它 `conf.h` 的实际值**
- 不手工增删库文件；Keil 工程不加 `printf.c`
- **串口后端先问用户**：用户已有的可自动化串口能力优先；没有才用本包内建 `serial`
- 改 conf.h / 加初始化调用属于用户代码变更，按 dev-assist 流程记录进 HT32_PROJECT.md
- retarget 验证阶段可直接用 `printf`；业务代码里的调试打印按 `references/code-style.md` 用日志宏包装
