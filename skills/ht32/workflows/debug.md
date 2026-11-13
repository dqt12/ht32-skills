# HT32 在线调试与排障（debug 工作流）

> 本文件是 `ht32` 主 Skill 的内部工作流（无 frontmatter，不是独立宿主 Skill），由主入口 `SKILL.md` 按意图路由进入。

接上调试器，看到程序真实运行状态；连不上时按知识库排障，不盲试。

## 前置

1. **首次**调试或工具链变动时，先执行内部工作流 **env-check**（重点：调试器与 Keil 存在性）；
   日常调试不重复跑，连接报「工具缺失 / 未配置调试器」时补跑
2. 读用户工程根目录 **HT32_PROJECT.md**：确认芯片型号、外设清单与当前开发状态
3. 确认固件已烧录且与当前代码一致（不一致时先转 flash 工作流重新烧录）
4. 调试接口知识依据：通过 ht32-wiki-mcp 检索 `swd-debug` / `elink32-pro` / `jlink` 页面，不凭记忆给配置步骤
5. J-Link 兼容性按当前探针型号与固件、已安装 Keil/J-Link 驱动、工程 Debug/Utilities 配置及目标板连接结果逐项核验；runtime 能识别工程配置不代表探针/驱动组合已兼容。未做连接或下载实测时注明「兼容性未实测」。

## 执行流程

### 1. 连接前检查

- SWD 五线：VDD、SWDIO、SWCLK、nRST、VSS 一一对应（检索 wiki `swd-debug` 接线表）
- **nRST 必须引出**：SWDIO/SWCLK 被程序复用或进 DeepSleep 后，没有 nRST 将无法通过 SWD 恢复
- SWD 走线串 22~100Ω 防护电阻、调试器默认不对外供电（目标板需自供电）

连接失败或自制板首次上电时，先通过 wiki MCP 核对目标型号/封装与板卡原理图，按下表记录「资料要求、实测结果、测点与仪器、异常项」；未测项目标为未核实，不将某型号的电气值或接法外推到其他型号。

| 核对项 | 记录内容 |
|---|---|
| 供电域 | 该型号实际存在的供电域、允许范围及各测点电压 |
| 内核稳压外围 | 该型号是否有相关输出、允许的测量方式与外接元件要求，以及板上实际连接 |
| 复位与启动 | 目标启动模式所需电平/时序与实测波形；外围连接是否符合该型号要求 |
| SWD | 探针参考电压、共地、信号与复位连接、工程连接速率及实际连接结果 |
| 备份域 | 该型号是否存在备份域、无独立电源时的连接要求及板上实际接法 |

### 2. 连接与操作（内部工作流 keil，经共享 runtime）

- 连接 → 暂停/恢复 → 单步（进/跳/出）→ 断点 → 调用栈 → 变量查看
- 报告调用栈只描述调试器当前可见的帧；它不等于静态最坏栈深度分析，也不能单独证明栈空间充足。
- 全速运行观察变量：Keil 打开 Periodic Window Update（提示用户手动开启）

### 3. 运行行为异常识别

| 现象 | 根因与处理 |
|------|-----------|
| 断点/暂停后程序从头跑 | **WDT 溢出**——调试会话先关闭 WDT（初始化文件/system 文件的 WDT 配置块；机制见 ht32-code 知识库 startup 页） |
| 进入 Debug 就断开 SWD | WDT 溢出 / SWD 被复用为 GPIO / 进入 DeepSleep-Stop 三者之一（检索 wiki `ht32-swd-debug`） |

### 4. 连接失败排障路由

| 现象 | 处理路由 |
|------|----------|
| runtime 报「工程未配置调试器」(blocked) | 可以直接帮用户改：`keil configure --project <工程> --debugger <cmsis-dap\|jlink>`（自动改 `.uvprojx` 的 Driver / `<Flash2>` 与 `.uvoptx` 的 pMon，改前备份）；也可让用户在 Keil 里 Options for Target → Debug 选好调试器、Utilities → Settings 勾选 Use Debug Driver 后保存工程再重试；Keil 已选调试器仍被拦 → 按 **skill_defect** 上报 code MCP `submit_feedback`（附 `.uvoptx` 的 pMon 值 + skill 版本号） |
| `RDDI-DAP Error` / `Device could not be powered up` / 无 SW Device | 检索 wiki `ht32-swd-debug`：供电与硬件状态检查点（VDD/VDDIO/AVDD/VBAT 电压、LDO 1.5V、nRST/BOOT 高电平）→ 13 步排障 |
| SWDIO/SWCLK 曾被复用或进省电模式 | Connect under Reset + Reset: Autodetect 成对设置；必要时 BOOT 拉低 Mass Erase 恢复 |
| 多个调试器/USB 异常 | 只保留一个 Adapter；重插 USB；重启 PC（Step 1/2） |
| J-Link 探针特有（Failed to connect / 速率问题） | 检索 wiki `ht32-jlink`：Vtref 不供电、降速 500kHz、接 nRST |
| 恢复后仍失败 | 更换 MCU 判断硬件损坏/焊接短路 |

### 5. 收尾

- 汇报：连接结论、观察到的变量/调用栈/日志要点、异常与路由结果
- 发现代码问题 → 修改走 **dev-assist** 工作流流程（含 HT32_PROJECT.md 记录），本工作流不改代码

## 核心规则

- 调试会话**只读观察**，不改写用户代码；修改必须回续开发流程并记录 HT32_PROJECT.md
- 排障动作先检索 wiki 知识页再执行，不凭记忆编造配置路径
- Mass Erase / 解锁类操作会清空芯片内容，执行前必须向用户确认

## 全局 runtime 与项目数据边界

> 数据边界：项目 `.ht32-skills/` 只允许保存 `config.json`；state / cache / logs 写全局数据目录（不可写时降级，见 SKILL.md「数据目录与宿主沙箱」）。

## 自动执行入口

调用 `_ht32-runtime/ht32.py keil debug --project <工程路径>`（调试器取 Keil 工程配置）。命令均需传 `--project` 并按 CLI 要求提供参数。调试会话只读观察，修改用户代码转 dev-assist 工作流。
