# HT32 烧录编排（flash 工作流）

> 本文件是 `ht32` 主 Skill 的内部工作流（无 frontmatter，不是独立宿主 Skill），由主入口 `SKILL.md` 按意图路由进入。

把编译产物可靠地烧进目标板：通道选择有依据，失败排障有知识来源，不盲试。

## 前置

1. **首次**烧录或工具链变动时，先执行内部工作流 **env-check**（读取主包 `workflows/env-check.md`；
   工具链缺失会导致 `keil flash` 无法执行）；日常烧录不重复跑，报「工具缺失」时补跑
2. 读用户工程根目录 **HT32_PROJECT.md**：确认芯片型号、模板来源；无 HT32_PROJECT.md 时向用户确认型号
3. 定位固件：Keil 按工程配置的 Target 下载，产物由 UV4 自己取（不用手工指镜像）
4. 烧录知识依据：通过 ht32-wiki-mcp 检索 `development-tools` / `swd-debug` / `jlink` 相关页面，不凭记忆给步骤

## 通道

烧录通道只有一条：Keil MDK v5（UV4 命令行）下载到当前 Target。调试器
（e-Link32 Pro / CMSIS-DAP / J-Link）在 Keil 的 **Options for Target → Debug** 里选，
runtime 通过 `keil flash` 执行，`backend=keil`。

| 情况 | 做法 |
|------|------|
| 工程在手 + Keil + 已配好调试器 | 内部工作流 **keil** 经共享 runtime 执行 `keil flash` |
| 工程没配调试器 / 配的不是手上的探针 | 直接帮用户改：`keil configure --project <工程> --debugger <cmsis-dap\|jlink>`，改前自动备份 |
| 用 J-Link 探针 | 同上切 `jlink`（需已装 J-Link 官方软件包（J-Link Software and Documentation Pack）+ HT32_J-Link_Plugin，驱动在 Keil 的 `ARM\Segger`） |

## 执行流程

1. **确认目标**：HT32_PROJECT.md 芯片型号 vs 工程 Target 一致；不一致先停下确认
2. **准备观测（先开后烧）**：关心启动日志时，给同一个 workflow 调用加 `--observe-port`，runtime 在
   第一步之前打开端口并持续采集（细节见 `workflows/workflow.md`）：
   `_ht32-runtime/ht32.py workflow run --project <工程路径> --steps "build,flash" --observe-port COMx --observe-baud <波特率> --observe-seconds 15`
   不经 workflow 的 Keil IDE 手工下载：先起后台 `serial log --port COMx --baud <波特率> --seconds 60`，再下载
   - `--observe-port` 走**内置后端**。若用户此前选了自家可自动化的串口能力（串口 MCP / 串口 skill /
     HTTP 接口，见 **uart-print** 的「串口后端选择」），要么由 Agent 先用该后端打开端口并持续采集、
     再烧录，要么明确改回内置后端——**不能烧完再开**，启动打印已丢
3. **烧录**：`keil flash`（经共享 runtime 自动执行 UV4 下载）
4. **校验**：
   - 烧录工具的 Verify 结果（失败即按排障路由处理）
   - 需要更强证据时：重新 `keil flash` 看 Verify 是否稳定通过 + 结合启动日志判断
   - 看第 2 步那份采集里的启动日志确认业务行为（`observations.observe.lines` 或 `artifacts.serial_log`）；
     零字节 / 明显异常时按内部工作流 **uart-print** 排障（不要在烧录后另开 `serial log` 补采——
     启动打印此时已经丢了）
5. **记录与归档**：
   - 把烧录结果（通道、产物、校验结论、启动日志结论）追加到 HT32_PROJECT.md 变更记录
   - **先询问用户是否归档 `.hex`**；未确认就只记录构建产物现有路径，不复制文件
   - 用户确认后使用用户指定、且已存在的归档目录；本流程**不自动创建** `firmware/` 或其他目录
   - 命名建议 `<工程名>_<YYYYMMDD>-<HHMM>.hex`；目标同名文件已存在时停止并询问新文件名，**不得覆盖**
   - 归档文件默认不提交 Git；是否纳入版本控制由用户另行决定。完成后报告源路径、目标路径与 SHA-256；
     回滚时可烧归档件，不要靠"重新编译旧提交"重建已烧过的固件

## 失败排障路由

| 现象 | 处理路由 |
|------|----------|
| runtime 报「工程未配置原生 CMSIS-DAP 调试器」(blocked) | 判定读 `.uvoptx`（`<pMon>`/`<TargetDlls>` 的 CMSIS_AGDI）；导出模板不含 `.uvoptx`，Keil 保存工程后才生成。让用户在 Keil 里 Options for Target → Debug 选 `CMSIS-DAP Debugger` 并指定探针、Utilities → Settings 勾选 Use Debug Driver，保存后重试；Keil 里明明已选 CMSIS-DAP 却仍被拦 → 按 **skill_defect** 上报（code MCP `submit_feedback`，附 `.uvoptx` 里 pMon 实际值 + skill 版本号） |
| 连接不上 / `RDDI-DAP Error` / `Device could not be powered up` | 检索 wiki `ht32-swd-debug`：13 步排障 + 供电检查点（调试器默认不供电、LDO 1.5V、nRST/BOOT 电平）；**Keil IDE 已开着调试会话时先关闭**——UV4 命令行会与 IDE 争用同一个调试器，表现为随机 `RDDI-DAP Error` |
| `Insufficient RAM for Flash Algorithms` / Flash Download 里 RAM for Algorithm = 0x0000 | 先看 runtime 报错里的「诊断」：`.uvoptx` 是否缺 `CMSIS_AGDI` 注册项 / 缺 `-FO15` / `-FC` 为 0 / `-FL0` 与 uvprojx IROM 不符。导出工程应自带 `CMSIS_AGDI + UL2CM3`；用户自有工程可用 `ht32.py keil configure` 重写调试器配置（改前备份、支持 `--dry-run`），或在 Keil 里勾一次 Reset and Run 后保存 |
| `Flash Download failed - "Cortex-Mxx"` / No Algorithm（UV4 只报 `Erase Failed!`，退出码 2） | 先看 runtime 的烧录前置检查 /「诊断」：`.uvoptx` 里 `-FN0`、缺 `-FF0`、或 `-FP0` 指向的 `.FLM` 解析不到，就是算法没配。**(a) Programming Algorithm 列表为空**（DFP 已装也会发生：新建 Target、手动删过、或沿用别人工程）→ Keil Options for Target → Utilities → Settings → Flash Download → Add，选本器件算法（如 `HT32F490x1 Int.Flash`，64k / On-chip Flash / 0x08000000-0x0800FFFF），确定后保存工程；**(b) DFP 确实没装**（`ARM\PACK\Holtek` 下没有对应 `*_DFP` 目录）→ 安装工程内 `utilities\pack\*.pack`，装完回到 (a) 确认算法列表非空。Image 超规格另行核对 |
| `Could not load file` | hex 未生成 → 先回编译流程（wiki `ht32-quickstart` 报错速查） |
| 之前能烧现在报错 | Option 区写保护 → Mass Erase 解锁，并核查程序 FMC 是否误写 Option 区 / 是否锁过 IC（wiki `ht32-quickstart` Mass Erase 节） |
| SWD 复用为 GPIO / 进 DeepSleep 后连不上 | Connect under Reset + Reset: Autodetect 成对设置；或 BOOT 拉低 Mass Erase（wiki `ht32-swd-debug`） |
| J-Link 探针 `Failed to connect`（Keil 内） | 降速 4000kHz→500kHz、接 nRST、确认 Vtref 不供电（wiki `ht32-jlink`） |
| 工程路径含中文/空格 | 提示移动工程（wiki `ht32-development-tools` 路径警告） |
| 命令跑很久、看不到输出（像卡住） | 正常：UV4 构建+烧录本身要数十秒，带 `--observe-port` 还要再采 `--observe-seconds`（默认 15s）。不要 Ctrl-C 打断——打断会留下占用调试器/串口的残留进程；只有明显超过「构建+烧录+观测」总时长才按排障处理 |

## 核心规则

- env-check 按必跑矩阵执行（首次烧录或工具链变动时补跑）；芯片型号必须与 HT32_PROJECT.md/工程一致才可烧录
- 失败不盲试：先按排障路由检索 wiki 知识页，给出有依据的修复动作
- **烧录前先开观测**（`--observe-port`）：启动日志只在复位的瞬间出现，烧完再开就丢了
- 烧录失败后芯片里仍是**旧固件**：此时串口/外设现象不能当作本次改动的结果
- 涉及 Mass Erase / 改动 Option Byte 的操作会**清空芯片程序与保护设置**，执行前必须向用户确认

## 全局 runtime 与项目数据边界

> 数据边界：项目 `.ht32-skills/` 只允许保存 `config.json`；state / cache / logs 写全局数据目录（不可写时降级，见 SKILL.md「数据目录与宿主沙箱」）。

## 自动执行入口

`_ht32-runtime/ht32.py keil flash --project <工程路径>`（UV4 对当前 Target 下载，调试器取 Keil 工程配置）。
烧录后观测优先用 workflow 的 `--observe-port`（见执行流程第 2 步）；手动 / 排查时的 `serial` 命令见
`workflows/uart-print.md`「观测命令」。
