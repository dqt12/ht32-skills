# HT32 J-Link 后端工作流（jlink）

> **实验性备用通道（主入口可路由）**：仅在用户明确要求"不经 Keil、直接用 J-Link"时使用，
> 且必须先告知用户该通道**未经真机验收**；常规 J-Link 用法是在 Keil 工程里配调试器
> （`workflows/keil.md` 的 `keil configure`）。不要把本工作流当作默认烧录/调试路径。

统一通过共享 runtime 执行完整操作：`probes`、`connect`、`flash`、`reset`、`read-memory`、`write-memory`、`registers`、`write-register`、`debug`、`rtt`。读操作可直接执行；写内存、写寄存器等破坏性操作，必须先说明目标与影响并取得用户明确确认。

从主包目录向上一级定位 global skills 根目录：

```powershell
python "<global-skill-root>\_ht32-runtime\ht32.py" jlink probes --project "<project-root>"
python "<global-skill-root>\_ht32-runtime\ht32.py" jlink connect --project "<project-root>"
python "<global-skill-root>\_ht32-runtime\ht32.py" jlink flash --project "<project-root>" --image "<firmware.hex>"
python "<global-skill-root>\_ht32-runtime\ht32.py" jlink reset --project "<project-root>"
python "<global-skill-root>\_ht32-runtime\ht32.py" jlink read-memory --project "<project-root>" --address 0x20000000 --count 4 --width 32
python "<global-skill-root>\_ht32-runtime\ht32.py" jlink write-memory --project "<project-root>" --address 0x20000000 --values 0x1 --width 32 --yes
python "<global-skill-root>\_ht32-runtime\ht32.py" jlink registers --project "<project-root>"
python "<global-skill-root>\_ht32-runtime\ht32.py" jlink write-register --project "<project-root>" --name r0 --value 0x1 --yes
python "<global-skill-root>\_ht32-runtime\ht32.py" jlink debug --project "<project-root>" --elf "<firmware.axf>" --actions "backtrace"
python "<global-skill-root>\_ht32-runtime\ht32.py" jlink rtt --project "<project-root>" --elf "<firmware.axf>" --seconds 5
```

多探针限制：当前 J-Link Commander 类命令（probes/connect/flash/reset/read-memory/write-memory/registers/write-register）不按 `probe_serial` 选择探针（probes 仅在列表结果中过滤），多探针接 PC 时必须先拔除多余探针，仅保留目标探针；`--probe-serial` 按序列号选择探针仅 GDB Server 路径（debug/rtt）生效。该限制将在硬件验收批次跟进。

地址、寄存器、数据和产物路径须替换为用户确认的实际值；写操作只有取得确认后才能传 `--yes`。mass erase 当前 CLI 未提供入口，不得构造命令。debug 必须提供 `--elf`；rtt 当前忽略 `--elf` 参数，ELF 由 J-Link 自动从工程产物解析。输出统一 JSON，并汇报 status、产物和日志路径。项目 `.ht32-skills/` 只允许 `config.json`；state、cache、logs 均保存在全局 `.ht32-skills` 数据目录。
