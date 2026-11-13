from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal, Sequence

from .backends import OperationContext, get_backend
from .artifacts import collect_build_artifacts
from .context import plan_warnings, project_context, read_plan
from .models import OperationResult
from .paths import LOG_UNAVAILABLE, RuntimePaths
from .process import ToolExecutionError
from .serial import (SerialError, SerialTap, render_hex, render_lines, render_timed_lines,
                     timing_summary, write_capture_bundle)


@dataclass(frozen=True)
class WorkflowStep:
    operation: Literal["build", "flash", "debug", "rtt"]
    options: dict[str, object]


@dataclass(frozen=True)
class ObservePlan:
    """烧录前预开的串口观测计划（贯穿式采集）。

    关键点：`tap` 在**流程第一步之前**就打开端口并持续读取，所以烧录复位后立刻打印的
    启动日志能被接到；流程结束后再续读 `tail_seconds`（已有数据且静默即提前收工）。
    观测失败只降级为 warn，不影响构建/烧录结论。
    """

    tap: SerialTap
    tail_seconds: float = 15.0
    hex_mode: bool = False
    encoding: str = "utf-8"
    stamp: str = "both"


def resolve_reset_boundary(flash_return: dict[str, object] | None,
                           reopen_events: Sequence[dict[str, object]]) -> tuple[int | None, str | None]:
    """把「烧录命令返回」升级为「芯片复位」的分界偏移。

    烧录工具（UV4）退出比芯片复位早约 1 s，直接取返回时刻的字节数会把复位后的前一段
    启动打印误判成烧录前。优先用**烧录返回之后第一次端口重开**（USB-CDC 重新枚举 =
    芯片复位）作为分界；没有重新枚举（独立 USB-UART / 未接 DTR）才退回返回时刻并标注近似。
    """
    if not flash_return:
        return None, None
    returned_ms = flash_return.get("ms")
    for event in reopen_events:
        if returned_ms is None or float(event.get("ms", 0.0)) >= float(returned_ms):
            return int(event.get("offset", 0)), "usb_reenumerate"
    return int(flash_return.get("offset", 0)), "flash_command_return"


def load_project_state(paths: RuntimePaths) -> dict[str, object] | None:
    if not paths.state_file.is_file(): return None
    return json.loads(paths.state_file.read_text(encoding="utf-8"))


def resolve_flash_image(ctx: OperationContext, summaries: Sequence[dict[str, object]]) -> str | None:
    """flash 步骤没给 --image 时自动找镜像：先看本次 build 产物，再查工程输出目录。

    J-Link 烧录必须 `loadfile` 具体镜像；只说 `--steps flash` 时不该逼用户再抄一遍路径。
    Keil 后端忽略该选项（按工程 Utilities 配置烧），所以自动解析对两者都安全。
    """
    for summary in reversed(list(summaries)):
        artifacts = summary.get("artifacts")
        if not isinstance(artifacts, dict):
            continue
        for extension in (".axf", ".hex", ".bin"):
            paths = artifacts.get(extension)
            if paths:
                return str(paths[0])
    try:
        artifacts = collect_build_artifacts(ctx.uvprojx, ctx.target)
    except Exception:  # noqa: BLE001 —— 工程文件缺失/损坏时交给 flash 自己报错
        return None
    for extension in (".axf", ".hex", ".bin"):
        if artifacts.get(extension):
            return artifacts[extension][0]
    return None


def save_project_state(paths: RuntimePaths, result: OperationResult) -> None:
    """把最近一次流程结果落到全局状态目录（辅助产物，不可写时只告警不失败）。"""
    try:
        paths.state_file.parent.mkdir(parents=True, exist_ok=True)
        paths.state_file.write_text(json.dumps(result.to_dict(), ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    except OSError:
        result.warnings.append(
            f"流程状态未写入全局目录（{paths.state_file.parent}）：本次结果只在本 JSON 里，"
            "下次读取不到历史状态；宿主沙箱拦截 home 目录时可设置 HT32_SKILLS_HOME 重定向。")


def execute_workflow(ctx: OperationContext, steps: Sequence[WorkflowStep], *,
                     observe: ObservePlan | None = None) -> OperationResult:
    result = OperationResult.start("workflow", ctx.debugger.backend, ctx.project_id)
    debugger_backend = ctx.backend or get_backend(ctx.debugger.backend)
    build_backend = ctx.backend or get_backend("keil")
    paths = RuntimePaths.for_project(ctx.project_root)
    if observe is not None:
        # 先开串口再跑步骤：烧录复位后立刻打印的启动日志才不会漏
        try:
            observe.tap.start()
        except SerialError as exc:
            result.warnings.append(
                f"串口观测未启动（{exc}）：本次只执行流程步骤，不含目标板启动日志")
            observe = None
    summaries = []
    statuses = []
    flash_return: dict[str, object] | None = None
    for step in steps:
        backend = build_backend if step.operation == "build" else debugger_backend
        step_ctx = replace(ctx, debugger=replace(ctx.debugger, backend="keil")) if step.operation == "build" else ctx
        method = getattr(backend, step.operation, None)
        try:
            if method is None:
                current = OperationResult.start(step.operation, ctx.debugger.backend, ctx.project_id)
                current.error = {"message": f"{ctx.debugger.backend} 不支持 {step.operation}"}
                current.finish("blocked")
            elif step.operation == "flash":
                image = step.options.get("image") or resolve_flash_image(step_ctx, summaries)
                current = method(step_ctx, image)
            elif step.operation == "rtt": current = method(step_ctx, step.options.get("elf"), step.options.get("seconds", 5))
            elif step.operation == "debug" and getattr(backend, "name", None) == "keil": current = method(step_ctx)
            else: current = method(step_ctx, **step.options)
        except ToolExecutionError as exc:
            current = OperationResult.start(step.operation, getattr(backend, "name", ctx.debugger.backend), ctx.project_id)
            current.error = {"message": str(exc)}
            log_path = getattr(exc, "log_path", None)
            current.log_path = str(log_path) if log_path is not None else None
            current.finish("error")
        statuses.append(current.status)
        summary = {"operation": current.operation, "status": current.status, "log_path": current.log_path}
        if current.observations.get("summary"):
            summary["summary"] = current.observations["summary"]
        if current.artifacts:
            summary["artifacts"] = current.artifacts
        if current.error is not None:
            summary["error"] = current.error
        summaries.append(summary)
        if observe is not None and step.operation == "flash" and current.status != "error":
            # 先开后烧：记下「烧录命令返回」时的字节数与时刻；真正的复位分界在采集结束后
            # 用之后的端口重开（USB-CDC 重新枚举）校正，见 resolve_reset_boundary。
            elapsed = getattr(observe.tap, "elapsed_ms", None)
            flash_return = {"offset": observe.tap.bytes_so_far(),
                            "ms": elapsed() if callable(elapsed) else None}
        if current.status in {"blocked", "error"}: break
    result.observations["steps"] = summaries
    # 上下文/计划检查（非阻断）：缺 HT32_PROJECT.md 只提示；第 9 节与本次结果矛盾才告警。
    context = project_context(ctx.project_root)
    result.observations["project_context"] = context
    if context["ht32_project"] == "missing":
        result.warnings.append(
            "HT32_PROJECT.md 缺失：本工程尚无事实源（建议先执行 project-context init）。")
    if context["plan"] == "present":
        result.warnings.extend(plan_warnings(summaries, read_plan(ctx.project_root)))
    observe_warnings: list[str] = []
    if observe is not None:
        report = observe.tap.stop(tail_seconds=observe.tail_seconds)
        post_flash_offset, boundary_source = resolve_reset_boundary(
            flash_return, list(getattr(report, "reopen_events", []) or []))
        bundle = write_capture_bundle(
            paths.serial_logs_dir, observe.tap.port, observe.tap.baudrate,
            observe.tail_seconds, report.data, report.chunks, stamp=observe.stamp,
            encoding=observe.encoding, marker_at=post_flash_offset,
            marker_source=boundary_source)
        if bundle is not None:
            result.artifacts["serial_log"] = str(bundle["log"])
            result.artifacts["serial_jsonl"] = str(bundle["jsonl"])
            result.artifacts["serial_bin"] = str(bundle["bin"])
        else:
            observe_warnings.append(LOG_UNAVAILABLE.format(path=paths.serial_logs_dir))
        view = render_hex(report.data) if observe.hex_mode else render_lines(report.data, observe.encoding)
        result.observations["observe"] = {
            "port": observe.tap.port,
            "baudrate": observe.tap.baudrate,
            "tail_seconds": observe.tail_seconds,
            "encoding": observe.encoding,
            "stamp": observe.stamp,
            "bytes_received": len(report.data),
            "chunks": report.chunks,
            "timing": timing_summary(report.chunks),
            "reopens": report.reopens,
            "stop_reason": report.stopped_by,
            "silence_seconds": report.silence_used,
            "hex" if observe.hex_mode else "lines": view,
        }
        if not observe.hex_mode:
            result.observations["observe"]["lines_with_time"] = render_timed_lines(
                report.data, report.chunks, observe.encoding)
        if post_flash_offset is not None:
            result.observations["observe"].update({
                "flash_return_offset": (flash_return or {}).get("offset"),
                "post_flash_offset": post_flash_offset,
                "post_flash_offset_source": boundary_source,
            })
            if boundary_source == "flash_command_return":
                # 不作为 warning（很常见：板子走独立 USB-UART，本来就不会重新枚举），
                # 但必须把「这是近似分界」写进结果，避免又被当成复位时刻。
                result.observations["observe"]["post_flash_offset_note"] = (
                    "串口没有出现烧录后的重新枚举，分界只能取「烧录命令返回」时刻，是近似值："
                    "芯片复位可能晚约 1 s，紧邻分界的几行归属不确定，请结合打印内容判断")
            if post_flash_offset and len(report.data) <= post_flash_offset:
                observe_warnings.append(
                    f"观测到的 {post_flash_offset} 字节全部来自烧录前（旧固件）：烧录后未见新输出，"
                    "怀疑未复位/未运行或 TX 接线断开（别把旧固件的打印当成本次结果）")
        if not report.data:
            observe_warnings.append(
                f"观测窗口内未从 {observe.tap.port} 收到任何数据：检查 TX/RX 接线、波特率是否与固件一致、"
                "目标板是否在运行（排障见 uart-print 工作流）")
        if report.reopens:
            observe_warnings.append(
                f"观测期间串口重开 {report.reopens} 次（目标板 USB-CDC 随复位重新枚举？）："
                "启动打印可能分段，已合并进同一份日志")
        observe_warnings.extend(report.errors)
        result.warnings.extend(observe_warnings)
    rank = {"ok": 0, "warn": 1, "blocked": 2, "error": 3}
    status = max(statuses, key=rank.get) if statuses else "ok"
    if observe_warnings and status == "ok":
        status = "warn"
    result.finish(status)
    save_project_state(paths, result)
    return result
