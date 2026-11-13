# HT32 Keil 后端工作流（keil）

> 本文件是 `ht32` 主 Skill 的内部工作流（无 frontmatter，不是独立宿主 Skill），backend=keil 时由主入口路由进入。

负责 Keil 后端 `backend=keil` 操作——本 Skill 编译、烧录、调试的唯一执行路径（UV4 命令行 + ARM Compiler 5）。调试器（e-Link32 Pro / CMSIS-DAP / J-Link）放在 Keil 工程的 Options for Target → Debug 里，runtime 能读也能直接改（`keil configure`，见下文）。缺少项目配置时读取实际工程文件与 Target，再调用 runtime `config init`；project-context 工作流维护 HT32_PROJECT.md 与 AGENTS.md 薄引导。

从主包目录向上一级定位 global skills 根目录，统一调用共享 runtime：

```powershell
python "<global-skill-root>\_ht32-runtime\ht32.py" keil scan --project "<project-root>"
python "<global-skill-root>\_ht32-runtime\ht32.py" keil targets --project "<project-root>"
python "<global-skill-root>\_ht32-runtime\ht32.py" keil build --project "<project-root>"
python "<global-skill-root>\_ht32-runtime\ht32.py" keil flash --project "<project-root>"
python "<global-skill-root>\_ht32-runtime\ht32.py" keil debug --project "<project-root>"
```

`keil flash` / `keil debug` 由 UV4 按工程配置执行。所有命令结果以 JSON 状态、产物路径及日志路径汇报。项目 `.ht32-skills/` 只允许 `config.json`；状态、缓存和日志均由 runtime 放在全局数据目录。

## 调试器判定口径（flash / debug / probe 前置）

- Keil 的调试器驱动选择写在 **`.uvoptx`**（Options for Target → Debug 的结果，如
  `<pMon>BIN\CMSIS_AGDI.dll</pMon>` 或 `BIN\JL2CM3.dll`）；`.uvprojx` 只有 `<Flash2>` 等默认项。
- runtime 的判定顺序：`.uvoptx` → `.uvprojx`（兜底手写工程），命中任一 Keil 调试器驱动即放行；
  两处都没有调试器证据 → `blocked`，提示语给出 Keil 侧配置步骤（**不笼统说"找不到调试器"**）。
- 导出模板不含 `.uvoptx`，所以刚导出的工程首次 flash/debug 前需要在 Keil 里保存一次工程
  （生成 `.uvoptx` 并选好调试器）；构建（`keil build`）不受该判定影响。也可以直接让 runtime 来选：
  `keil configure --debugger cmsis-dap`（见下节）。
- 判定本身出错（如 Keil 已选调试器仍被拦）→ 按 **skill_defect** 上报 code MCP
  `submit_feedback`，附 `.uvoptx` 的 `<pMon>` 实际值与本 Skill 版本号。

## 切换调试器（直接改工程，不用用户在 Keil 里手点）

```powershell
python "<global-skill-root>\_ht32-runtime\ht32.py" keil configure --project "<project-root>"
python "<global-skill-root>\_ht32-runtime\ht32.py" keil configure --project "<project-root>" --debugger jlink --dry-run
python "<global-skill-root>\_ht32-runtime\ht32.py" keil configure --project "<project-root>" --debugger cmsis-dap
```

第一条只报告不动文件；第二条加 `--dry-run` 是预览；第三条才是真正落盘。

- `--debugger` 取值：`cmsis-dap`（e-Link32 Pro 及其它 CMSIS-DAP）、`jlink`；省略 = 只报告当前配置与各驱动是否装好，不改文件。
- 一次改四处：`.uvprojx` 的 `<DebugOption><TargetDlls><Driver>`（调试）与 `<Utilities><Flash2>`（烧录）、
  `.uvoptx` 的 `<pMon>`、`<TargetDriverDllRegistry>` 里该驱动的参数串；`--probe-serial` / `--speed-khz`
  写入 `-U` / `-TC`，目标与 Flash 算法参数（`-N00(...)` 之后）沿用工程原值。
- 改前自动备份 `<工程文件>.bak-<时间戳>`；先 `--dry-run` 看 `changed` 与 `before` / `after`。
- 工程没有 `.uvoptx` → `blocked`：请在 Keil 里保存一次工程（或在 Debug 页选一次驱动）后再执行。
- 选 J-Link 前确认装了 J-Link 官方软件包（J-Link Software and Documentation Pack）与 HT32_J-Link_Plugin（`JL2CM3.dll` 装在 Keil 的 `ARM\Segger`）；
  缺驱动文件时结果是 `warn`，按 warning 补齐后重试。
- 切换后提醒用户：Debug → Settings 里确认探针与 Max Clock（1MHz 起，连不稳降到 500kHz），Vtref 不供电。
