# HT32 环境检测（env-check 工作流）

> 本文件是 `ht32` 主 Skill 的内部工作流（无 frontmatter，不是独立宿主 Skill），由主入口 `SKILL.md` 按意图路由进入，也被 project-create / dev-assist 作为前置步骤执行。

检测 MCP 与本地工具链，输出结构化结论；是 project-create / dev-assist 的共享前置子流程。

## 检测项与失败策略

| # | 检测项 | 方法 | 缺失时 |
|---|--------|------|--------|
| 1 | ht32-wiki-mcp 连通 | `get_wiki_version` 一次 | **阻断**：输出配置指引，停止后续流程 |
| 2 | ht32-code-mcp 连通 | `get_code_version` 一次 | **阻断**：输出配置指引，停止后续流程 |
| 3 | Keil5（UV4.exe） | 本地检测脚本 | 警告：无法基线编译/烧录 |
| 4 | HT32 DFP pack | 本地检测脚本（解析 pack 名与版本） | 警告：编译找不到器件；工程存在时比对「已装 ≥ 工程要求」 |
| 5 | CMSIS-DAP 调试器 | 本地检测脚本（USB 枚举：名称或 VID/PID，含合泰 `VID_04D9&PID_802F/807C`） | 警告：无法烧录/在线调试 |
| 6 | 串口（COM） | 本地检测脚本 | 警告：无法查看目标板日志 |
| 7 | clang-format | 本地检测脚本（PATH 与 LLVM 默认目录） | 警告：写码后无法校验格式，不阻断 |
| 8 | 工程路径字符 | 检查是否含中文/空格/特殊符号 | 警告：bin/hex 生成与 `_CreateProject.bat` 可能失败 |
| 9 | 本 Skill 版本 | `_ht32-runtime/ht32.py version`（或 `env check` 的 `skill.version`） | 不阻断：填「未获取（无 VERSION 文件）」 |
| 10 | Skill 更新 | runtime `version` + 需查时 `check_skill_update(current)`，结果缓存 24h | 不阻断：落后只提示 + 询问是否升级，升级须用户同意 |

## 执行流程

1. **MCP 连通（1–2）**：在宿主 MCP 列表找 ht32-wiki / ht32-code（URL 或启动命令含对应名），各调一次
   版本工具；成功即连通并记录版本，找不到或失败/超时判缺失。仅用户明确只测工具链时可跳过，
   结论标「MCP 未检测」。
2. **本地工具链（3–7）**：有工程配置时优先跑共享 runtime：
   ```powershell
   python "<global-skill-root>\_ht32-runtime\ht32.py" env check --project "<project-root>"
   ```
   `--project` 可省略（按当前目录；无配置则裸机检测，`project_id: null` + `project_source: "none"`
   属正常）。新建工程尚未导出时跑只读脚本：
   ```powershell
   powershell -ExecutionPolicy Bypass -File "<主包目录>\scripts\check_local_tools.ps1" -Json
   ```
   脚本异常或拿不到 JSON → 相关项按「未知」处理。runtime 返回 OperationResult，检测项在 observations；
   当前后端必需工具缺失为 blocked，知识核对与文档阶段仍可继续。
3. **Skill 更新（10）**：跑 `version`；`update.check_due=true` 时调 code MCP
   `check_skill_update(current="<observations.skill_version>")`，再用
   `version --record-update "<最新版本>"` 写回缓存（24h 内不再查）。`update.latest` 只是上次查到的值，
   `cache_valid=false` / `latest_may_be_stale=true` 时先刷新再判断。落后 → 告知当前/最新/要点/步骤并
   询问是否升级；用户同意后按宿主自己的 Skill 安装机制重装，下次会话生效。工具不可用 / 清单缺失 /
   版本取不到 → 记「未获取（原因）」，不阻断、不重试。会话级打标：code MCP 由 `check_skill_update`
   顺带记入，wiki MCP 调 `report_skill_version(version=...)`。
4. **汇总结论**：任一 MCP 缺失 → `blocked`；MCP 齐全但有工具链缺失/未知 → `warn`；全部通过 → `ok`
   （Skill 落后只提示，不改变结论）。
5. **输出**：结果表格 + JSON。

## 失败处理

### MCP 缺失（阻断）

必须显式告知并停止（不得静默降级），输出配置指引：

- wiki-mcp：`https://mcp.s621.work/ht32-wiki`
- code-mcp：`https://mcp.s621.work/ht32-code`
- 配置后重启 AI IDE 使 MCP 生效，再重跑本工作流

### 工具链缺失（警告降级）

| 缺失项 | 影响 | 补救指引 |
|---|---|---|
| Keil5 | 无法基线编译/烧录 | 安装 Keil MDK v5（UV4 位于 `<Keil>\UV4\UV4.exe`）；或在全局 `.ht32-skills/config.json` 的 `tools.uv4_exe` 配置路径 |
| HT32 DFP | 编译报找不到器件 | **随包 DFP 优先**：导出工程自带 `utilities/pack/<Vendor>.<Name>.<x.y.z>.pack`，双击安装；本机已装同名且版本 ≥ 要求可跳过。无工程时用 Keil Pack Installer 装 Holtek HT32 DFP |
| CMSIS-DAP | 无法烧录/在线调试 | 接上调试板并确认驱动（HID/WinUSB）。合泰探针（`VID_04D9&PID_802F/807C`）在 HID 固件模式下接口名通用，靠 VID+PID 识别；插着却报未检测到 → 按 **skill_defect** 上报 code MCP `submit_feedback` |
| 串口 | 无法看日志 | 安装 USB 转串口驱动（CH340/CP210x 等）并接好调试串口 |

> `cmsis_dap.probe_limited=true` 表示 USB 枚举受限（沙箱拦 CIM，脚本已回退注册表）：按 **unknown**
> 处理，不要让用户重插探针或判「无法烧录」；有工程配置时以 runtime `env check` 为准。

流程继续，但必须把警告与影响明确告知用户。

### 附加提示

- 检测到 **J-Link**：可直接 `keil configure --project <工程> --debugger jlink`（改前备份），之后由 UV4 驱动
- 检测项 8 命中：建议移到纯英文无空格路径后重建
- e-Link32 Pro：SWD 驱动随开发环境自带；VCP 串口另需 HT32_VCP_Driver（检索 wiki `ht32-elink32-pro`）

## 输出格式

JSON（供主 skill 汇总与 HT32_PROJECT.md 记录）：

```json
{
  "verdict": "ok",
  "skill": { "version": "v0.1.0", "source": "<...>/ht32/VERSION" },
  "mcp": { "ht32-wiki": { "connected": true, "version": "..." }, "ht32-code": { "connected": true, "version": "..." } },
  "toolchain": { "keil": { "found": true }, "ht32_dfp": { "found": true }, "cmsis_dap": { "found": true }, "serial": { "count": 0 } },
  "blockers": [],
  "warnings": []
}
```

## 核心规则

- 版本工具各只调一次，不盲目重试；找不到 server 与调用失败同视为缺失
- MCP 缺失必须阻断并输出配置指引，禁止跳过检测直接进入主流程
- 工具链缺失只警告，不阻断；检测脚本只读，不安装、不修改系统配置
- 结论三态 ok / warn / blocked，必须显式告知用户
- **不要每轮必跑**：必跑矩阵以主包 SKILL.md 路由规则 4 为准
- **`unknown` ≠ 缺失**：沙箱下探测受限（`probe_limited: true`）且无设备记录时给 unknown，
  不要据此拦截流程或申请更大权限

## 全局 runtime 与项目数据边界

> 数据边界：项目 `.ht32-skills/` 只允许保存 `config.json`；state / cache / logs 写全局数据目录（不可写时降级，见 SKILL.md「数据目录与宿主沙箱」）。
