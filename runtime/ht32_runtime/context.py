"""工程上下文（HT32_PROJECT.md / AGENTS.md 引导 / 第 9 节执行计划）的只读检查。

这些检查**不阻断**任何流程：只在 `config init` / `workflow run` 的 observations
里给出状态，并在缺失/矛盾时追加 warning。工程是否继续由调用方决定。
"""
from __future__ import annotations

import re
from pathlib import Path

CONTEXT_FILE = "HT32_PROJECT.md"
AGENTS_FILE = "AGENTS.md"
PLAN_SECTION = "## 9. 本次执行计划与进度"
GUIDE_START = "<!-- ht32-project-guide:start -->"
GUIDE_END = "<!-- ht32-project-guide:end -->"

# 只认 project-create 的步骤 ID（P7a/P7b/P8a/P8b/P9）；自由文本行忽略。
STEP_RE = re.compile(r"^\s*-\s*\[( |x|!)\]\s*(P\d+[a-z]?)\s+(.*)$", re.M)
_STATUS = {" ": "pending", "x": "done", "!": "failed"}
_OPERATION_STEP = {"build": "P7b", "flash": "P8a", "serial": "P8b"}


def parse_plan(text: str) -> dict[str, dict[str, str]]:
    """解析第 9 节的步骤行；同一 ID 多次出现时以**最后一次**为准（当前任务）。

    第 9 节缺失时返回空 dict（旧 v1 工程兼容）。
    """
    if PLAN_SECTION not in text:
        return {}
    section = text.split(PLAN_SECTION, 1)[1]
    # 只解析到下一个 H2（如果有）为止，避免把后面追加的任务混进当前任务
    section = re.split(r"(?m)^##\s+", section, maxsplit=1)[0]
    steps: dict[str, dict[str, str]] = {}
    for mark, step_id, desc in STEP_RE.findall(section):
        steps[step_id] = {"status": _STATUS[mark], "text": desc.strip()}
    return steps


def read_plan(root: Path) -> dict[str, dict[str, str]]:
    """读取工程根 HT32_PROJECT.md 的第 9 节；文件缺失返回空 dict。"""
    path = Path(root) / CONTEXT_FILE
    if not path.is_file():
        return {}
    return parse_plan(path.read_text(encoding="utf-8", errors="replace"))


def project_context(root: Path) -> dict[str, str]:
    """工程上下文快照：{ht32_project, agents_guide, plan}。"""
    root = Path(root)
    context = root / CONTEXT_FILE
    agents = root / AGENTS_FILE
    status = {
        "ht32_project": "present" if context.is_file() else "missing",
        "agents_guide": "missing",
        "plan": "missing",
    }
    if agents.is_file():
        text = agents.read_text(encoding="utf-8", errors="replace")
        starts, ends = text.count(GUIDE_START), text.count(GUIDE_END)
        if starts == 1 and ends == 1 and text.index(GUIDE_START) < text.index(GUIDE_END):
            status["agents_guide"] = "present"
        elif starts or ends:
            status["agents_guide"] = "partial"
    if context.is_file():
        text = context.read_text(encoding="utf-8", errors="replace")
        steps = parse_plan(text)
        if steps:
            status["plan"] = "present"
        elif PLAN_SECTION in text:
            status["plan"] = "empty"
    return status


def plan_warnings(steps: list[dict[str, object]], plan: dict[str, dict[str, str]]) -> list[str]:
    """勾选与运行结果矛盾时的 warning（非阻断）。

    `steps` = workflow result.observations["steps"]（每项含 operation/status）。
    失败/阻断却标 `[x]` 才告警；成功但还没勾不告警（agent 可能尚未回写）。
    """
    warnings: list[str] = []
    labels = {"build": "P7b 基线编译", "flash": "P8a 烧录", "serial": "P8b 串口观测"}
    for step in steps:
        operation = str(step.get("operation") or "")
        step_id = _OPERATION_STEP.get(operation)
        if not step_id:
            continue
        if step.get("status") in {"error", "blocked"} and plan.get(step_id, {}).get("status") == "done":
            warnings.append(
                f"HT32_PROJECT.md 第 9 节把「{labels[operation]}」标为已完成（[x]），"
                f"但本次 {operation} 结果为 {step.get('status')}：请核对并改正计划状态")
    return warnings
