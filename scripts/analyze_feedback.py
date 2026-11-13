#!/usr/bin/env python3
"""HT32 MCP 反馈与用量日志分析工具（wiki + code 合并）。

读取 `<FEEDBACK_ROOT>/<service>/usage_*.jsonl` 与 `feedback_*.jsonl`，
输出需求信号、调用分布、反馈分布与性能统计，用于：

- 用户需求洞察：谁在要哪颗芯片、哪个模板、检索什么关键词 → 指导知识页/系列覆盖优先级
- 质量跟踪：调用失败、Agent 主动反馈的问题类型 → 指导修缺陷
- 性能观察：各工具耗时分布
- 入口覆盖：会话级 Skill 覆盖率（有多少会话真的走了 ht32 Skill，而不是直接调 MCP）

零依赖（仅标准库），可直接在服务器或本地跑。

用法：
    python analyze_feedback.py <FEEDBACK_ROOT> [选项]

    # 线上环境（日志目录挂到本机可读路径后直接分析）
    python analyze_feedback.py /path/to/feedback-root

    # 只看最近 7 天、只统计 code
    python analyze_feedback.py /path/to/feedback-root --days 7 --service code

    # 机器可读输出
    python analyze_feedback.py /path/to/feedback-root --json

日志格式约定见两仓库的 `usage.py`（两副本逐字节相同）：每条 JSONL 含
`kind` / `service` / `tool` / `args` / `ok` / `duration_ms` / `kb_version`。
`ok=false` 表示**工具没完成工作**（参数错/路径越界/导出失败），"无数据"
（未找到…）记为 `ok=true` 以保留为需求信号。本工具兼容统一前的旧记录
（无 `service` 字段、`wiki_version`/`code_version` 字段名、日志平铺在根目录）。
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# 需求信号：从这些工具的哪个参数里提取"用户在要什么"。
# 只收"业务意图"类参数（要什么芯片/外设/查什么），不收实现细节：
# get_file 的 path 属 Agent 内部取文件行为，不是用户需求，且路径写错时会产生噪声。
DEMAND_SOURCES: dict[str, tuple[str, ...]] = {
    "export_project_bundle": ("chip",),        # 生成的工程模板 → 芯片需求
    "find_example": ("peripheral",),           # 找例程 → 外设需求
    "search_knowledge": ("query",),            # wiki 检索 → 知识关注点
    "search_code_knowledge": ("query",),       # code 检索
    "search_datasheets": ("query",),
    "get_chip_info": ("model",),
    "get_model": ("model",),
    "get_peripheral_info": ("name",),
    "get_datasheet": ("series",),
    "compare_chips": ("models",),
}

# 会话覆盖率（入口前置是否生效）的判定口径：
# - 带 Skill 标记 = 该会话调用过上报型工具，或后续记录的顶层 skill_version 非空
#   （服务端中间件会把上报后的版本打到该会话后续所有记录上）
# - 工程型会话 = 调用过会改动/导出用户工程的工具；这类会话本该由 Skill 编排
SKILL_REPORT_ARGS: dict[str, str] = {
    "check_skill_update": "current",
    "report_skill_version": "version",
    "submit_feedback": "skill_version",
}
ENGINEERING_TOOLS = {"export_project_bundle", "get_project_manifest"}


def _out(text: str = "") -> None:
    """控制台输出（Windows 终端编码兜底，避免中文报错中断分析）。"""
    try:
        print(text)
    except UnicodeEncodeError:
        enc = sys.stdout.encoding or "utf-8"
        print(text.encode(enc, errors="replace").decode(enc, errors="replace"))


def _percentile(values: list[int], pct: float) -> int:
    """简单百分位（最近邻取整），避免空列表与插值边界问题。"""
    if not values:
        return 0
    ordered = sorted(values)
    idx = min(int(len(ordered) * pct / 100.0), len(ordered) - 1)
    return ordered[idx]


def _record_service(rec: dict[str, Any], path: Path, root: Path) -> str:
    """推断记录所属服务：记录字段 > 父目录名 > unknown。

    兼容三种历史形态：统一后（service 字段 + 服务子目录）、
    统一前 wiki/code 各自目录平铺、以及未知来源。
    """
    svc = str(rec.get("service") or "").strip()
    if svc:
        return svc
    try:
        parent = path.parent.relative_to(root)
        if parent.parts:
            return parent.parts[0]
    except ValueError:
        pass
    return "unknown"


def _record_ts(rec: dict[str, Any]) -> datetime | None:
    raw = rec.get("timestamp")
    if not isinstance(raw, str) or not raw:
        return None
    try:
        ts = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def load_records(root: Path, service: str, days: int | None) -> tuple[list[dict], int]:
    """递归读取全部 jsonl（含旧版平铺布局）。

    返回 (记录列表, 跳过行数)。坏行只计数不中断——日志分析工具不该被
    单行损坏拖垮。
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)) if days else None
    records: list[dict] = []
    skipped = 0

    paths = sorted(set(root.rglob("usage_*.jsonl")) | set(root.rglob("feedback_*.jsonl")))
    for path in paths:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                skipped += 1
                continue
            if not isinstance(rec, dict):
                skipped += 1
                continue
            if cutoff is not None:
                ts = _record_ts(rec)
                if ts is not None and ts < cutoff:
                    continue
            rec["_service"] = _record_service(rec, path, root)
            if service != "all" and rec["_service"] != service:
                continue
            records.append(rec)
    return records, skipped


def _kb_version(rec: dict[str, Any]) -> str:
    """知识库版本：统一后为 kb_version，兼容旧字段名。"""
    for key in ("kb_version", "wiki_version", "code_version"):
        val = rec.get(key)
        if isinstance(val, str) and val:
            return val
    return ""


def _skill_version(rec: dict[str, Any]) -> str:
    """会话的 ht32-skills 版本标记。

    优先取记录顶层 `skill_version`（中间件在上报后会给该会话后续记录补上）；
    老记录没有该字段时，从上报型工具的参数兜底。
    """
    val = rec.get("skill_version")
    if isinstance(val, str) and val:
        return val
    arg = SKILL_REPORT_ARGS.get(str(rec.get("tool") or ""))
    if arg:
        val = (rec.get("args") or {}).get(arg)
        if isinstance(val, str) and val:
            return val
    return ""


def _pct(part: int, total: int) -> float | None:
    return round(part * 100.0 / total, 1) if total else None


def _fmt_pct(value: float | None) -> str:
    return "—" if value is None else f"{value:.1f}%"


def _session_coverage(usage: list[dict[str, Any]]) -> dict[str, Any]:
    """按会话统计 Skill 覆盖率（入口前置是否生效的代理指标）。

    分母 = 有 session_id 的会话；分子 = 组内任一记录带 Skill 标记的会话。
    工程型会话单列：调用过 export_project_bundle / get_project_manifest 的会话，
    这类会话本就该由 Skill 编排，是真正需要盯的跳过率。
    """
    sessions: dict[str, dict[str, Any]] = {}
    for rec in usage:
        sid = str(rec.get("session_id") or "").strip()
        if not sid:
            continue
        ent = sessions.setdefault(
            sid, {"service": rec["_service"], "skill": False, "eng": False})
        if _skill_version(rec):
            ent["skill"] = True
        if str(rec.get("tool") or "") in ENGINEERING_TOOLS:
            ent["eng"] = True

    def summarize(items: list[dict[str, Any]]) -> dict[str, Any]:
        total = len(items)
        skill = sum(1 for e in items if e["skill"])
        eng = [e for e in items if e["eng"]]
        eng_skill = sum(1 for e in eng if e["skill"])
        return {
            "sessions": total,
            "skill_sessions": skill,
            "coverage_pct": _pct(skill, total),
            "engineering_sessions": len(eng),
            "engineering_skill_sessions": eng_skill,
            "engineering_coverage_pct": _pct(eng_skill, len(eng)),
        }

    all_items = list(sessions.values())
    by_service = {
        svc: summarize([e for e in all_items if e["service"] == svc])
        for svc in sorted({e["service"] for e in all_items})
    }
    return {**summarize(all_items), "by_service": by_service}


def build_report(records: list[dict], top: int) -> dict[str, Any]:
    usage = [r for r in records if r.get("kind") == "tool_call"]
    feedback = [r for r in records if r.get("kind") == "feedback"]

    timestamps = [t for t in (_record_ts(r) for r in records) if t is not None]

    calls: Counter = Counter()
    failures: Counter = Counter()
    durations: dict[tuple[str, str], list[int]] = defaultdict(list)
    demand: dict[str, Counter] = {k: Counter() for k in
                                  {v for vals in DEMAND_SOURCES.values() for v in vals}}

    for rec in usage:
        svc = rec["_service"]
        tool = str(rec.get("tool") or "?")
        failed = rec.get("ok") is False
        calls[(svc, tool)] += 1
        if failed:
            failures[(svc, tool)] += 1
        d = rec.get("duration_ms")
        if isinstance(d, (int, float)):
            durations[(svc, tool)].append(int(d))
        # 需求信号只统计成功调用：失败的调用（如猜错的 get_file 路径）
        # 不代表需求，混进来会污染"用户在要什么"的结论
        if failed:
            continue
        for arg in DEMAND_SOURCES.get(tool, ()):
            val = (rec.get("args") or {}).get(arg)
            if isinstance(val, list):
                for item in val:
                    if item:
                        demand[arg][str(item)] += 1
            elif val:
                demand[arg][str(val)] += 1

    fb_types: Counter = Counter()
    for rec in feedback:
        fb_types[(rec["_service"], str(rec.get("issue_type") or "?"))] += 1

    errors = [
        {
            "service": r["_service"], "tool": r.get("tool", "?"),
            "error": str(r.get("error") or ""), "args": r.get("args") or {},
            "timestamp": r.get("timestamp", ""),
        }
        for r in usage if r.get("ok") is False
    ]

    return {
        "totals": {
            "records": len(records),
            "tool_calls": len(usage),
            "feedbacks": len(feedback),
            "services": sorted({r["_service"] for r in records}),
            "first": min(timestamps).isoformat() if timestamps else None,
            "last": max(timestamps).isoformat() if timestamps else None,
            "kb_versions": sorted({v for v in (_kb_version(r) for r in records) if v}),
        },
        "calls": {f"{s}/{t}": n for (s, t), n in calls.most_common()},
        "failures": {f"{s}/{t}": n for (s, t), n in failures.most_common()},
        "sessions": _session_coverage(usage),
        "demand": {arg: c.most_common(top) for arg, c in demand.items() if c},
        "feedback_types": {f"{s}/{t}": n for (s, t), n in fb_types.most_common()},
        "latency_ms": {
            f"{s}/{t}": {
                "count": len(v),
                "p50": _percentile(v, 50),
                "p95": _percentile(v, 95),
                "max": max(v),
            }
            for (s, t), v in sorted(durations.items(), key=lambda kv: -len(kv[1]))
            if v
        },
        "recent_errors": errors[-top:],
        "recent_feedback": [
            {
                "service": r["_service"],
                "issue_type": r.get("issue_type", "?"),
                "question": str(r.get("question") or "")[:160],
                "model_id": r.get("model_id", ""),
                "timestamp": r.get("timestamp", ""),
            }
            for r in feedback[-top:]
        ],
    }


def print_report(rep: dict[str, Any], top: int) -> None:
    t = rep["totals"]
    _out("=" * 68)
    _out("HT32 MCP 反馈与用量日志分析")
    _out("=" * 68)
    _out(f"总记录：{t['records']}（工具调用 {t['tool_calls']} / 反馈 {t['feedbacks']}）")
    _out(f"服务：{', '.join(t['services']) or '（无）'}")
    if t["first"]:
        _out(f"时间范围：{t['first']}  ~  {t['last']}")
    _out(f"知识库版本：{', '.join(t['kb_versions']) or '（未知）'}")

    if rep["calls"]:
        _out("\n-- 调用分布 " + "-" * 54)
        for key, n in list(rep["calls"].items())[:top]:
            bad = rep["failures"].get(key, 0)
            flag = f"  ⚠ 失败 {bad}" if bad else ""
            _out(f"  {n:6d}  {key}{flag}")

    if rep["demand"]:
        _out("\n-- 需求信号（用户在要什么）" + "-" * 42)
        labels = {
            "chip": "芯片型号（export_project_bundle）",
            "model": "芯片型号（查询）",
            "series": "系列（取 Datasheet）",
            "peripheral": "外设（找例程）",
            "query": "检索关键词",
            "name": "外设详情查询",
            "models": "对比型号",
        }
        for arg, items in rep["demand"].items():
            _out(f"  [{labels.get(arg, arg)}]")
            for val, n in items:
                _out(f"      {n:5d}  {val}")

    cov = rep.get("sessions") or {}
    if cov.get("sessions"):
        _out("\n-- Skill 会话覆盖率（入口前置是否生效） " + "-" * 33)
        _out(f"  总会话 {cov['sessions']} · 带 Skill 标记 {cov['skill_sessions']}"
             f" · 覆盖率 {_fmt_pct(cov['coverage_pct'])}")
        _out(f"  工程型会话 {cov['engineering_sessions']} · 其中带标记 "
             f"{cov['engineering_skill_sessions']} · 覆盖率 "
             f"{_fmt_pct(cov['engineering_coverage_pct'])}")
        for svc, s in (cov.get("by_service") or {}).items():
            _out(f"  [{svc}] 会话 {s['sessions']} · 覆盖率 {_fmt_pct(s['coverage_pct'])}"
                 f" · 工程型 {s['engineering_sessions']} / "
                 f"{_fmt_pct(s['engineering_coverage_pct'])}")
        _out("  说明：标记来自 check_skill_update / report_skill_version / submit_feedback；")
        _out("        未上报的会话可能仍在用 Skill，故本覆盖率是下界（真实跳过率更低）。")

    if rep["feedback_types"]:
        _out("\n-- 反馈问题类型 " + "-" * 53)
        for key, n in rep["feedback_types"].items():
            _out(f"  {n:6d}  {key}")

    if rep["latency_ms"]:
        _out("\n-- 耗时（ms） " + "-" * 56)
        for key, s in list(rep["latency_ms"].items())[:top]:
            _out(f"  {key:38s} n={s['count']:5d}  p50={s['p50']:6d}  "
                 f"p95={s['p95']:6d}  max={s['max']:7d}")

    if rep["recent_errors"]:
        _out(f"\n-- 最近失败调用（{len(rep['recent_errors'])} 条） " + "-" * 40)
        for e in rep["recent_errors"]:
            _out(f"  [{e['timestamp'][:19]}] {e['service']}/{e['tool']}")
            _out(f"      args={e['args']}")
            _out(f"      {e['error'][:200]}")

    if rep["recent_feedback"]:
        _out(f"\n-- 最近反馈（{len(rep['recent_feedback'])} 条） " + "-" * 42)
        for f in rep["recent_feedback"]:
            _out(f"  [{f['timestamp'][:19]}] {f['service']}/{f['issue_type']}"
                 f"{'  by ' + f['model_id'] if f['model_id'] else ''}")
            _out(f"      {f['question']}")

    if not rep["calls"] and not rep["feedback_types"]:
        _out("\n（无记录：确认 FEEDBACK_ROOT 路径正确，且服务端已设该环境变量）")


def _positive_days(value: str) -> int:
    """--days 必须是 >=1 的整数。

    不允许 0/负数：它们在"最近 N 天"语义下无意义，若默默当成"不过滤"
    会让人误判统计范围（曾把 0 当 falsy 放过此问题）。
    """
    try:
        n = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"须为整数，得到 {value!r}")
    if n < 1:
        raise argparse.ArgumentTypeError(f"须 >=1（最近 N 天），得到 {n}")
    return n


def main() -> int:
    ap = argparse.ArgumentParser(
        description="HT32 MCP 反馈与用量日志分析（wiki + code 合并）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="示例：\n"
               "  python analyze_feedback.py /path/to/feedback-root\n"
               "  python analyze_feedback.py /path/to/feedback-root --days 7 --service code\n"
               "  python analyze_feedback.py /path/to/feedback-root --json > report.json\n",
    )
    ap.add_argument("root", help="反馈根目录（如 /path/to/feedback-root）")
    ap.add_argument("--service", default="all", choices=["all", "wiki", "code"],
                    help="只统计指定服务（默认 all）")
    ap.add_argument("--days", type=_positive_days, default=None,
                    help="只统计最近 N 天（N>=1，默认全部）")
    ap.add_argument("--top", type=int, default=20, help="各榜单条数（默认 20）")
    ap.add_argument("--json", action="store_true", help="输出 JSON 而非文本报告")
    args = ap.parse_args()

    root = Path(args.root).expanduser()
    if not root.is_dir():
        _out(f"错误：目录不存在：{root}")
        return 2

    records, skipped = load_records(root, args.service, args.days)
    rep = build_report(records, args.top)

    if args.json:
        _out(json.dumps(rep, ensure_ascii=False, indent=2))
    else:
        print_report(rep, args.top)
        if skipped:
            _out(f"\n（跳过 {skipped} 行损坏记录）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
