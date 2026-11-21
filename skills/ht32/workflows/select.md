# HT32 独立选型咨询（select 工作流）

> 本文件是 `ht32` 主 Skill 的内部工作流（无 frontmatter，不是独立宿主 Skill），由主入口 `SKILL.md` 按意图路由进入。

把模糊需求变成有 wiki 依据的候选型号对比；选型结论必须可溯源，不凭记忆写规格。

## 前置（知识级轻量预检，不跑完整 env-check）

1. `get_wiki_version` 1 次——不通则阻断（选型结论全部依赖 wiki 检索）
2. `get_code_version` 1 次——不通只记 warning，不阻断（选型不依赖 code MCP）
3. `report_skill_version(version=<本 Skill frontmatter 版本>)`——会话级用量打标；
   无此工具时记 warning，不阻断
4. `check_skill_update(current=<同版本>)`——顺带打标 + 更新检查；失败记「未获取」，不阻断

不跑本地工具链探测、不跑 `ht32.py version`、不读 `env-check.md` 全文。
用户确认型号后转 project-create 时，由该工作流补跑完整 env-check。

## 执行流程

### 第 1 步 · 需求要素提取

从用户描述中确认六要素（缺项追问，最多一轮）：

| 要素 | 典型问题 |
|------|----------|
| 必备外设 | 要不要 USB / CAN / LCD / 触控 / 24-bit ADC / 预驱？ |
| 电压 | 3.3V 标准还是 5V 宽压 / 电池低功耗？ |
| 内核与主频 | 有无性能/FPU 要求？ |
| 容量 | 代码/数据规模量级（Flash/SRAM 档位） |
| 封装 | 引脚数限制 |
| 特殊约束 | 温度范围、低功耗模式、认证（IEC 60730）、批量生产的烧录方式 |

### 第 2 步 · wiki 检索（知识依据）

按要素组合调用 wiki-mcp（search 工具 + 页面读取）：

1. **selection 决策树**：`selection-5v-mcu`（5V 宽压）、`selection-bldc-motor`（电机/预驱）、`selection-usb-can`（USB/CAN）、`selection-touch`（触控）——命中即沿决策树走
2. **系列页外设资源矩阵**：用 wiki MCP 检索候选系列的芯片规格页（20+ 字段矩阵）直接对参数（无需翻 datasheet 原档）
3. **按功能反查型号**：用户以功能为导向（要有几路 UART/SPI/I2C/ADC/PWM 等）时，先 `find_models_with_function("<功能>")`
   取具备该功能的型号与封装，再回第 2 条对矩阵；该工具是**功能→型号反查**，不替代型号→规格查询
4. 跨系列对比需求读 `ht32-m0p/m3/m4-series-overview` 与 `ht32-chip-registry`
5. 特殊主题补充：BLE → `ht32-ble-soc`；低功耗 → `ht32l52xxx` 系列页 + `power-domains`；安全认证 → `ht32-safety-certification`

### 第 3 步 · 输出对比与推荐

- 输出**候选对比表**：型号 / 内核主频 / Flash·SRAM / 关键外设（矩阵实数）/ 电压 / 封装 / 温度
- 给出**推荐结论 + 理由**，逐条标注 wiki 来源页面（如"依据 [[ht32f523xx]] 矩阵：USB 为 FS Device 单角色"）
- 明确说明不确定项（wiki 无数据的参数标注"待查 datasheet"，不编造）
- 用型号写法遵循 wiki LLM 友好规范（多型号顿号列举、容量按型号拆行）

### 第 4 步 · 转场引导

用户确认型号后：提示可转入 **project-create** 工作流（选型结论 → 规格核对 → 建工程全流程，见 `workflows/project-create.md`）；若用户需要先看烧录/调试环境，指向 **flash** / **debug** 工作流。

## 核心规则

- 所有规格结论必须来自 wiki-mcp 检索结果并标注来源页；查不到的不写
- 不在本工作流内生成工程/写代码——选型确认后转 project-create 工作流
- 输出对比表字段值必须与系列页矩阵一致（单一权威源约定）
- 用户需求超出 HT32 产品线覆盖时如实说明，不硬凑

## 全局 runtime 与项目数据边界

> 数据边界：项目 `.ht32-skills/` 只允许保存 `config.json`；state / cache / logs 写全局数据目录（不可写时降级，见 SKILL.md「数据目录与宿主沙箱」）。
