<div align="center">

# HT32 Skills

**合泰 HT32 系列 MCU 开发编排层 + AI Skill 套件**

一个入口，走完选型 · 建工程 · 写码 · 编译 · 烧录 · 调试

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](#许可证)
[![Host Skill](https://img.shields.io/badge/host--skill-ht32-blue.svg)](#这是什么)
[![Internal Workflows](https://img.shields.io/badge/internal--workflows-13-green.svg)](#能力概览)

</div>

## 这是什么

HT32 Skills 是 HT32 MCU 开发的 AI 编排层：一个宿主 Skill（`ht32`）、**13 个**内部工作流、一份共享 Python runtime。

它不存储知识。规格参数、库 API 由两个远程 MCP 提供，本仓库只决定**什么时候问谁**、**怎么把本地工具链串起来**：

| MCP 服务 | 提供什么 | 端点 |
| --- | --- | --- |
| `ht32-wiki` | 芯片选型、外设规格、系列差异、原档检索 | `https://mcp.s621.work/ht32-wiki` |
| `ht32-code` | 库 API 用法、官方例程、Keil 工程模板 | `https://mcp.s621.work/ht32-code` |

执行全部在本地（编译 / 烧录 / 调试 / 串口），云端只回答知识，两条链路不直连。

## 能力概览

- **选型**：说需求，按 wiki 决策树给出候选对比与推荐
- **新建工程**：取模板 → 导出可编译工程 → 生成 `HT32_PROJECT.md` → 基线编译 0 Error 0 Warning
- **续开发**：基于真实库知识与例程写码，不编造 API
- **型号迁移**：同系列换型号，库包一致性判定，跨系列直接阻断
- **构建 / 烧录 / 调试**：Keil UV4（AC5）一条路径，统一 JSON 结果与退出码
- **串口观测**：端口枚举、限时采集、采集期定时发送、烧录前先开串口抓启动日志
- **故障定位**：HardFault、复位环路、外设不工作的分阶段排查
- **环境检测**：两个 MCP + Keil / DFP / 调试器 / 串口，给出三态结论
- **工程 Git**：无仓库先询问，初始化并提交一次；后续每轮功能本地提交，不 push
- **代码风格**：驱动贴 FWLib、应用用 C 库惯例，`clang-format` 强制排版

用户只认识 `ht32` 一个入口，其余能力由主入口按意图自动路由。

## 环境要求

| 项 | 要求 |
| --- | --- |
| Python | ≥ 3.10（runtime 只用标准库，无第三方依赖） |
| Keil MDK v5 | UV4 命令行 + ARM Compiler 5，构建 / 烧录 / 调试的唯一路径 |
| 调试器 | e-Link32 Pro / CMSIS-DAP / J-Link，在 Keil 的 Options for Target → Debug 里选 |
| AI 宿主 | 支持 Agent Skill（`<skills 目录>/<name>/SKILL.md`）即可，不绑定具体 IDE |

## 安装（Agent 按本章执行）

> 本章写给 **AI Agent**：按 1 → 4 顺序执行，每步都有完成标志。各宿主的 skills 目录位置与 MCP 添加方式不同，**一律用你自己平台的机制**，不要照抄固定路径。

### 1. 获取本仓库

```bash
git clone https://github.com/dqt12/ht32-skills.git
# 或 https://gitee.com/dqt12/ht32-skills.git（等价入口，任选其一）
```

也可以不克隆：不少宿主支持直接从仓库 / URL 安装 Skill，按其官方文档操作即可。

### 2. 安装 Skill 与 runtime

要落地两份：`skills/ht32/` → 宿主 skills 目录下的 `ht32/`（**连同 `VERSION`**），`runtime/` → 同级的 `_ht32-runtime/`。

```
<你的 skills 目录>/
├── ht32/
│   ├── SKILL.md        # 唯一入口（frontmatter 含 version）
│   ├── VERSION         # 必须一起复制，version / env check 从它读取
│   ├── workflows/      # 13 个内部工作流
│   ├── references/     # MCP 工具速查 / 代码风格 / 时钟功耗 / Git 口径
│   ├── assets/         # clang-format 排版配置
│   ├── scripts/        # 环境检测脚本
│   └── templates/      # HT32_PROJECT.md 模板 + AGENTS.md 薄引导
└── _ht32-runtime/
    ├── ht32.py
    └── ht32_runtime/
```

完成标志：`python "_ht32-runtime/ht32.py" version` 输出与仓库 `VERSION` 首行一致（形如 `vX.Y.Z`），宿主 Skill 列表里出现 `ht32`。

> 升级用同一机制重装；若 skills 目录还留着早期顶层 `ht32-*` 目录，一并移除，只保留 `ht32` 单入口。本仓库不提供安装脚本，也不要寻找一键安装器。

### 3. 配置两个 MCP

两个都是远程 Streamable HTTP，URL 到 `/ht32-wiki`、`/ht32-code` 为止，**不要加 `/mcp` 后缀**：

| 名称 | URL |
| --- | --- |
| `ht32-wiki` | `https://mcp.s621.work/ht32-wiki` |
| `ht32-code` | `https://mcp.s621.work/ht32-code` |

完成标志：列出你已加载的工具，`ht32-code` 应有 **14 个**；或直接调 `get_code_version`，返回版本号即接通。

### 4. 验证环境

重启宿主，对 `ht32` 说「检查 HT32 环境」，应路由到 `env-check` 并给出三态结论：

- `blocked` —— MCP 缺失，输出配置指引后停止
- `warn` —— MCP 齐全但工具链缺项（如缺 Keil），降级继续并说明影响
- `ok` —— 全部通过

## 命令速查

Runtime 提供 8 组命令：`config` · `env` · `keil` · `jlink` · `serial` · `workflow` · `fetch` · `version`。

| 命令组 | 做什么 |
| --- | --- |
| `config` | 生成 / 查看 / 校验项目配置（芯片、工程文件、Target、debugger） |
| `env` | 检测本机工具链与调试器 |
| `keil` | 扫描 Target、build / rebuild / clean / flash / debug、读改调试器配置 |
| `jlink` | J-Link 直连实验通道（未真机验收） |
| `serial` | 端口枚举、限时采集、定时发送、间隔统计 |
| `workflow` | 串起 build / flash / debug，可选先开串口观测 |
| `fetch` | 下载工程 zip（HTTPS + 大小 / SHA256 校验） |
| `version` | 打印 Skill 版本与来源文件 |

```powershell
python "<global-skill-root>\_ht32-runtime\ht32.py" <命令组> <子命令> [参数]
```

所有命令输出一个 JSON：`status` ∈ `ok` / `warn` / `blocked` / `error`，退出码 0 / 0 / 2 / 3。完整参数用 `--help`；配置优先级、数据目录与设计取舍见 [AGENTS.md](AGENTS.md)。

## HT32_PROJECT.md 项目上下文

用户工程的 HT32 事实集中在根目录 `HT32_PROJECT.md`，由 `project-context` 工作流生成与增量维护：模板 v10、9 个固定 section、36 个占位符，结论只增不改、版本值必须实测。工程根 `AGENTS.md` 只写一段薄引导，指向这份唯一事实源。

## 仓库结构

```
ht32-skills/
├── skills/ht32/       # 唯一宿主 Skill（SKILL.md + workflows/ + references/ + templates/）
├── runtime/           # 共享 runtime，安装为同级 _ht32-runtime/
├── templates/         # HT32_PROJECT.md 模板 + AGENTS.md 薄引导（唯一事实源）
├── tests/             # 契约 / runtime / 后端 / 串口回归
└── scripts/           # 模板校验与反馈分析
```

## 文档索引

| 想看什么 | 去哪 |
| --- | --- |
| 维护约定、目录结构、设计说明、验证命令 | [AGENTS.md](AGENTS.md) |
| 主入口路由与后端决策 | `skills/ht32/SKILL.md` |
| 单条流程细节 | `skills/ht32/workflows/*.md` |
| MCP 工具速查 / 代码风格 / 时钟功耗 / Git 口径 | `skills/ht32/references/` |

## 许可证

本项目采用 **MIT 许可证**，全文见 [LICENSE](LICENSE)。

Copyright (c) 2026 David Deng

HT32、Holtek 及相关型号名称为**合泰半导体（Holtek Semiconductor Inc.）**的商标或注册商标；本仓库仅提供工具与流程编排，不含官方原档内容。
