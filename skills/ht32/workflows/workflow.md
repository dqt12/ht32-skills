# HT32 构建烧录调试闭环工作流（workflow）

> 本文件是 `ht32` 主 Skill 的内部工作流（无 frontmatter，不是独立宿主 Skill），由主入口按「构建/烧录/调试」意图路由进入。

作为构建/烧录/调试的闭环工作流，先读取项目 `.ht32-skills/config.json` 和机器配置，执行 `config validate`，再按用户需求决定步骤。默认执行 `build,flash`；仅用户明确要求在线调试时才追加 `debug`。debugger 后端统一为 keil（调试器由 Keil 工程配置决定）；命令行覆盖只作用于本次执行，不写回配置。

从主包目录向上一级定位 global skills 根目录，然后调用：

```powershell
python "<global-skill-root>\_ht32-runtime\ht32.py" workflow run --project "<project-root>" --steps "build,flash"
# 烧录 + 抓启动日志（推荐）：串口在第一步之前就打开，复位后立刻打印的启动日志不会丢
python "<global-skill-root>\_ht32-runtime\ht32.py" workflow run --project "<project-root>" --steps "build,flash" --observe-port "COM13" --observe-baud 115200 --observe-seconds 15
```

步骤仅支持 `build,flash,debug`。**串口观测是选项而不是步骤**：给了 `--observe-port`
就在跑第一步**之前**打开串口并后台持续采集，一路覆盖到烧录（含烧录期间），烧录完成后继续采
`--observe-seconds` 秒（默认 15；期间已有数据且静默约 2s 即提前收工），最后把行文本
（`--observe-hex` 则 Hex）放进 JSON 的 `observations.observe`、采集原文落全局 `logs/serial/`
（`artifacts.serial_log`）。

## 读结果（按顺序，别先读大文件）

1. `status` / `exit_code`：ok=0 / warn=0 / blocked=2 / error=3
2. `steps[]`：每步的 `status`、`log_path`、`summary`、`artifacts`
   - `summary`（编译）已含 `errors`/`warnings` 与 `code`/`ro_data`/`rw_data`/`zi_data`；
     烧录步含 `flash.{erase_done,programming_done,verify_ok,application_running}` 与失败时的
     `error_lines`（前 5 条）
   - `log_path` 是 UV4 的真实 `-o` 日志（`artifacts.tool_log`），`artifacts.runtime_log` 是
     runtime 包装日志（命令 + stdout/stderr）。**summary 够用就别 tail 整个日志**
3. `observations.observe`：`lines`（或 `hex`）、`bytes_received`、`reopens`，
   以及烧录场景的 `post_flash_offset` + `post_flash_offset_source`；**时序量化**看
   `chunks`（每批 `offset/ms/at/bytes`）、`timing`（首/末字节毫秒、批量间隔中位数）、
   `lines_with_time`（每行到达时刻），日志 `.log` 默认带时间列（`--observe-stamp` 可改为
   `rel`/`abs`/`none`）。
   注意 USB-CDC 常按 ~107 ms 批量刷新：时间戳是驱动读到数据的时刻，不是固件逐字节发送时刻
4. `observations.project_context`：`ht32_project` / `agents_guide` / `plan` 状态；
   `plan=present` 且本次 build/flash 失败却标了 `[x]` 时，warnings 会给出矛盾提示（不阻断）

## 烧录前后输出的区分（先开后烧的证据链）

`--observe-port` 从第一步之前就开始采，所以采集里天然混有**烧录前旧固件**的打印：

- `observations.observe.post_flash_offset`：烧录前后分界的字节偏移，
  必须与 `post_flash_offset_source` 一起读：`usb_reenumerate`（烧录后端口重新枚举 = 真复位，
  可信）/ `flash_command_return`（没有重新枚举，只能取 UV4 返回时刻，复位可能晚约 1 s，
  属近似分界，`post_flash_offset_note` 会说明）
- `artifacts.serial_log` 的日志正文里有分界行
  `[ht32-skills] ---- target reset detected (USB CDC re-enumerated); below is post-reset output ----`
  或 `---- flash command returned; chip reset may lag ~1s, boundary approximate ----`
- warnings 出现「观测到的 N 字节全部来自烧录前」→ 本次启动日志缺失（未复位/未接线/未运行），
  **不得**把烧录前输出当成烧录结果；按 `workflows/uart-print.md` 排障

## flash 步骤的镜像

`workflow run --steps flash` 不带 `--image` 时，runtime 自动用本次 build 产物或工程输出目录里的
`.axf`/`.hex`/`.bin`（Keil 按工程配置烧，会优先用工程 Target 产物）。工程不在手/产物
不在默认位置时显式传 `--image <路径>`。

- **为什么必须"先开后烧"**：烧录成功后目标板立即复位运行并打印，等烧完再开串口必然丢启动
  日志（`serial log` 打开时还会清接收缓冲，躺在驱动缓冲里的那批字节也一并丢）。所以只要关心
  启动日志，就用 `--observe-port`。
- 端口不存在 / 被其他程序占用 / 采集为零字节 → 只记 warn，**不影响构建烧录结论**；目标板
  USB-CDC 随复位重新枚举导致端口消失时按上限自动重开，次数记在 `observations.observe.reopens`
  （启动打印可能分段，已合并进同一份日志）。
- **不经 workflow 的 Keil IDE 手工下载**：照样要"先开"——**先**起一个后台采集再下载——
  `serial log --port COM13 --baud 115200 --seconds 60` 在另一终端/后台跑着，烧完（含复位打印）
  再读它落下的全局日志；不要"先烧后开"。

产物、状态和日志按 runtime 契约写入全局 `.ht32-skills` 数据目录；项目 `.ht32-skills/` 只允许保存 `config.json`。统一返回 JSON，按 status/exit code 汇报并链接产物和日志。
