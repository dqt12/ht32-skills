"""analyze_feedback.py 验收测试（自包含，不依赖任何外部数据）。

自造 fixture 日志覆盖：正常统计、--json/--service/--days 参数、
旧格式兼容（无 service 字段 / 平铺目录 / 旧版本字段名）、
损坏行容错、空目录与不存在目录、失败调用不计入需求信号。

用法：python test_analyze_feedback.py
退出码：0 全绿；1 有失败。
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

TOOL = Path(__file__).resolve().parent / "analyze_feedback.py"

failures = []


def run(args):
    p = subprocess.run([sys.executable, str(TOOL), *args],
                       capture_output=True, text=True, encoding="utf-8")
    return p.returncode, p.stdout, p.stderr


def check(name, cond, detail=""):
    mark = "PASS" if cond else "FAIL"
    print(f"[{mark}] {name}" + (f" — {detail}" if detail else ""))
    if not cond:
        failures.append(name)


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
        encoding="utf-8")


# ---- fixture：同根 + 服务子目录，覆盖两服务/两版本/成功与失败 ----
ROOT = Path(tempfile.mkdtemp(prefix="ht32-fb-demo-"))
write_jsonl(ROOT / "wiki" / "usage_2026-09-21.jsonl", [
    {"timestamp": "2026-01-15T09:00:00+00:00", "kind": "tool_call",
     "service": "wiki", "tool": "search_knowledge",
     "args": {"query": "F52352 USB"}, "ok": True, "duration_ms": 45,
     "kb_version": "v1.2.3"},
    {"timestamp": "2026-01-15T09:00:01+00:00", "kind": "tool_call",
     "service": "wiki", "tool": "search_knowledge",
     "args": {"query": "F52352 USB"}, "ok": True, "duration_ms": 55,
     "kb_version": "v1.2.3"},
    {"timestamp": "2026-01-15T09:00:02+00:00", "kind": "tool_call",
     "service": "wiki", "tool": "get_chip_info",
     "args": {"model": "HT32F52352"}, "ok": True, "duration_ms": 2,
     "kb_version": "v1.2.3"},
])
write_jsonl(ROOT / "wiki" / "feedback_2026-09-21.jsonl", [
    {"timestamp": "2026-01-15T09:00:03+00:00", "kind": "feedback",
     "service": "wiki", "issue_type": "wrong_model",
     "question": "F52352 的 USB 支持 OTG 吗", "model_id": "deepseek-v4-flash",
     "kb_version": "v1.2.3"},
])
write_jsonl(ROOT / "code" / "usage_2026-09-21.jsonl", [
    {"timestamp": "2026-01-15T09:01:00+00:00", "kind": "tool_call",
     "service": "code", "tool": "export_project_bundle",
     "args": {"chip": "HT32F52352"}, "ok": True, "duration_ms": 1187,
     "kb_version": "v0.1.0"},
    {"timestamp": "2026-01-15T09:01:01+00:00", "kind": "tool_call",
     "service": "code", "tool": "export_project_bundle",
     "args": {"chip": "HT32F52352"}, "ok": True, "duration_ms": 900,
     "kb_version": "v0.1.0"},
    {"timestamp": "2026-01-15T09:01:02+00:00", "kind": "tool_call",
     "service": "code", "tool": "find_example",
     "args": {"peripheral": "gpio"}, "ok": True, "duration_ms": 1,
     "kb_version": "v0.1.0"},
    {"timestamp": "2026-01-15T09:01:03+00:00", "kind": "tool_call",
     "service": "code", "tool": "get_file",
     "args": {"path": "raw/nope/missing.c"}, "ok": False,
     "error": "ToolError: boom", "duration_ms": 4, "kb_version": "v0.1.0"},
])
write_jsonl(ROOT / "code" / "feedback_2026-09-21.jsonl", [
    {"timestamp": "2026-01-15T09:01:04+00:00", "kind": "feedback",
     "service": "code", "issue_type": "export_defect",
     "question": "导出的工程编译报错", "model_id": "deepseek-v4-flash",
     "kb_version": "v0.1.0"},
])

EXPECT_CALLS = 7
EXPECT_FEEDBACKS = 2


# ---- 1. 基本文本报告 ----
rc, out, err = run([str(ROOT)])
check("退出码 0", rc == 0, f"rc={rc} {err[:200]}")
check("含标题", "HT32 MCP 反馈与用量日志分析" in out)
check("识别两个服务", "code, wiki" in out)
check("需求信号含芯片", "HT32F52352" in out)
check("反馈类型分区", "code/export_defect" in out and "wiki/wrong_model" in out)
check("耗时含 p95", "p95=" in out)
# get_file 的 path 是 Agent 内部行为、非用户需求；但失败详情里出现是**应该的**
# （要看得到失败原因），故只校验它不出现在"需求信号"区块
demand_block = out.split("-- 需求信号")[1].split("--")[0] if "-- 需求信号" in out else ""
check("get_file 的 path 不进需求信号", "missing.c" not in demand_block,
      demand_block[:120])

# ---- 2. --json 与口径一致 ----
rc, out, err = run([str(ROOT), "--json"])
check("json 模式退出码 0", rc == 0)
rep = json.loads(out)
check(f"json tool_calls == {EXPECT_CALLS}",
      rep["totals"]["tool_calls"] == EXPECT_CALLS,
      str(rep["totals"]["tool_calls"]))
check("json feedbacks == 2", rep["totals"]["feedbacks"] == EXPECT_FEEDBACKS)
check("json 服务列表", rep["totals"]["services"] == ["code", "wiki"])
check("json 记录 kb_version 两版", rep["totals"]["kb_versions"] == ["v0.1.0", "v1.2.3"],
      str(rep["totals"]["kb_versions"]))
check("失败调用进 failures", rep["failures"].get("code/get_file") == 1)
check("失败详情进 recent_errors", len(rep["recent_errors"]) == 1)

# ---- 3. --service 过滤 ----
rc, out, _ = run([str(ROOT), "--json", "--service", "code"])
rep = json.loads(out)
check("code 过滤后只有 code", rep["totals"]["services"] == ["code"])
check("code 过滤后无 wiki 调用",
      all(k.startswith("code/") for k in rep["calls"]))

# ---- 4. --days 过滤（fixture 用固定历史日期，断言不受运行时间影响）----
rc, out, _ = run([str(ROOT), "--json", "--days", "1"])
check("days=1 滤掉历史数据", json.loads(out)["totals"]["tool_calls"] == 0)
rc, out, _ = run([str(ROOT), "--json", "--days", "3650"])
check("days=3650 保留全部", json.loads(out)["totals"]["tool_calls"] == EXPECT_CALLS)
rc, out, err = run([str(ROOT), "--json", "--days", "0"])
check("days=0 被拒绝（语义无意义，不静默当不过滤）", rc != 0, f"rc={rc}")
rc, out, err = run([str(ROOT), "--json", "--days", "-3"])
check("负数 days 被拒绝", rc != 0)
rc, out, err = run([str(ROOT), "--json", "--days", "abc"])
check("非整数 days 被拒绝", rc != 0)

# ---- 5. 旧格式兼容：无 service 字段、平铺无子目录、旧版本字段名 ----
legacy = Path(tempfile.mkdtemp(prefix="legacy-"))
write_jsonl(legacy / "usage_2026-09-01.jsonl", [
    {"timestamp": "2026-09-01T00:00:00+00:00", "kind": "tool_call",
     "tool": "get_chip_info", "args": {"model": "HT32F12345"},
     "ok": True, "duration_ms": 7, "wiki_version": "v1.0.0"}])
write_jsonl(legacy / "feedback_2026-09-01.jsonl", [
    {"timestamp": "2026-09-01T00:00:01+00:00", "kind": "feedback",
     "issue_type": "wrong_spec", "question": "旧格式反馈",
     "wiki_version": "v1.0.0"}])
rc, out, _ = run([str(legacy), "--json"])
rep = json.loads(out)
check("旧格式可读", rc == 0 and rep["totals"]["tool_calls"] == 1)
check("旧格式 wiki_version 归一到 kb_version",
      rep["totals"]["kb_versions"] == ["v1.0.0"], str(rep["totals"]["kb_versions"]))
check("旧格式平铺记录服务归为 unknown",
      rep["totals"]["services"] == ["unknown"], str(rep["totals"]["services"]))

# ---- 6. 损坏行不中断 ----
broken = Path(tempfile.mkdtemp(prefix="broken-"))
(broken / "code").mkdir()
(broken / "code" / "usage_2026-09-01.jsonl").write_text(
    '{"broken json\n' + json.dumps({"kind": "tool_call", "service": "code",
                                    "tool": "get_file", "args": {}, "ok": True},
                                   ensure_ascii=False) + "\nnot json at all\n",
    encoding="utf-8")
rc, out, _ = run([str(broken)])
check("损坏行仍退出 0", rc == 0)
check("损坏行被跳过并提示", "跳过 2 行损坏记录" in out,
      out.splitlines()[-1] if out else "")

# ---- 7. 空目录 / 不存在目录 ----
empty = Path(tempfile.mkdtemp(prefix="empty-"))
rc, out, _ = run([str(empty)])
check("空目录给出提示", rc == 0 and "无记录" in out)
rc, out, _ = run([str(empty / "nope")])
check("不存在目录退出 2", rc == 2 and "不存在" in out)

# ---- 8. 失败调用不计入需求信号 ----
mix = Path(tempfile.mkdtemp(prefix="mix-"))
write_jsonl(mix / "code" / "usage_2026-09-01.jsonl", [
    {"kind": "tool_call", "service": "code", "tool": "export_project_bundle",
     "args": {"chip": "HT32F52352"}, "ok": False, "error": "boom"},
    {"kind": "tool_call", "service": "code", "tool": "export_project_bundle",
     "args": {"chip": "HT32F99999"}, "ok": True},
])
rc, out, _ = run([str(mix), "--json"])
rep = json.loads(out)
demand = dict(rep["demand"].get("chip", []))
check("失败调用不计入需求", "HT32F52352" not in demand, str(demand))
check("成功调用计入需求", demand.get("HT32F99999") == 1, str(demand))
check("失败仍计入 calls/failures",
      rep["calls"]["code/export_project_bundle"] == 2
      and rep["failures"]["code/export_project_bundle"] == 1)

# ---- 9. 会话级 Skill 覆盖率 ----
cov = Path(tempfile.mkdtemp(prefix="cov-"))
write_jsonl(cov / "wiki" / "usage_2026-09-01.jsonl", [
    # s-w1：上报型工具的版本在 args 里（无顶层 skill_version）也应算作带标记
    {"kind": "tool_call", "service": "wiki", "tool": "report_skill_version",
     "args": {"version": "v0.1.42"}, "session_id": "s-w1", "ok": True},
    {"kind": "tool_call", "service": "wiki", "tool": "search_knowledge",
     "args": {"query": "gpio"}, "session_id": "s-w1", "ok": True,
     "skill_version": "v0.1.42"},
    # s-w2：直接调 MCP，无 Skill 标记
    {"kind": "tool_call", "service": "wiki", "tool": "search_knowledge",
     "args": {"query": "adc"}, "session_id": "s-w2", "ok": True},
])
write_jsonl(cov / "code" / "usage_2026-09-01.jsonl", [
    # s-c1：工程型会话，标记在前一条上报记录上
    {"kind": "tool_call", "service": "code", "tool": "check_skill_update",
     "args": {"current": "v0.1.42"}, "session_id": "s-c1", "ok": True},
    {"kind": "tool_call", "service": "code", "tool": "export_project_bundle",
     "args": {"chip": "HT32F52352"}, "session_id": "s-c1", "ok": True},
    # s-c2：工程型会话，无标记
    {"kind": "tool_call", "service": "code", "tool": "export_project_bundle",
     "args": {"chip": "HT32F52352"}, "session_id": "s-c2", "ok": True},
    # s-c3：纯知识查询会话，无标记（允许直接调用，不算工程型）
    {"kind": "tool_call", "service": "code", "tool": "search_code_knowledge",
     "args": {"query": "uart"}, "session_id": "s-c3", "ok": True},
])
rc, out, _ = run([str(cov), "--json"])
rep = json.loads(out)
s = rep["sessions"]
check("会话总数 = 5", s["sessions"] == 5, str(s))
check("带 Skill 标记会话 = 2", s["skill_sessions"] == 2, str(s))
check("总会话覆盖率 40.0%", s["coverage_pct"] == 40.0, str(s["coverage_pct"]))
check("工程型会话 = 2", s["engineering_sessions"] == 2, str(s))
check("工程型覆盖率 50.0%", s["engineering_coverage_pct"] == 50.0,
      str(s["engineering_coverage_pct"]))
check("wiki 覆盖率 50.0%", s["by_service"]["wiki"]["coverage_pct"] == 50.0,
      str(s["by_service"]["wiki"]))
check("code 覆盖率 33.3%", s["by_service"]["code"]["coverage_pct"] == 33.3,
      str(s["by_service"]["code"]))
check("code 工程型覆盖率 50.0%",
      s["by_service"]["code"]["engineering_coverage_pct"] == 50.0,
      str(s["by_service"]["code"]))
rc, out, _ = run([str(cov)])
check("文本报告含覆盖率分区", "Skill 会话覆盖率" in out)
check("文本报告含工程型统计", "工程型会话 2" in out)

print()
if failures:
    print(f"❌ {len(failures)} 项失败：" + "; ".join(failures))
    sys.exit(1)
print("✅ 全部通过")

