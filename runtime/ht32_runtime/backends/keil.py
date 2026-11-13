from __future__ import annotations

from datetime import datetime, timezone
import ntpath
from pathlib import Path
import re
import uuid

import xml.etree.ElementTree as ET

from .. import keil_config
from ..artifacts import collect_build_artifacts, list_targets, list_uvprojx
from ..environment import find_ht32_dfp
from ..models import OperationResult
from ..paths import LOG_UNAVAILABLE, writable_dir
from ..process import ToolExecutionError
from . import OperationContext, Runner


_KEIL_DEBUGGER_TEXT = re.compile(
    r"cmsis[\s_-]*dap|cmsis_agdi|j[\s_-]*link|jl2cm3|ulink|ul2cm3|st[\s_-]*link|elink|e-link",
    re.IGNORECASE)
_KEIL_DEBUGGER_DRIVERS = {
    "cmsis_agdi.dll", "jl2cm3.dll", "ul2cm3.dll", "stlink.dll", "st-link.dll",
    "elink.dll", "htlink.dll",
}
_DRIVER_TAGS = {"driver", "flash2", "pmon"}

_COMPILER_RE = re.compile(r"Using Compiler '([^']+)',\s*folder: '([^']*)'", re.IGNORECASE)
_AC6_HINT_RE = re.compile(r"armclang|arm compiler 6|\\armclang\\", re.IGNORECASE)
_AC6_WARNING = ("工程使用 ARM Compiler 6（{version}）；本 Skill 只支持 ARM Compiler 5（AC5），"
                "请在 Keil 的 Options for Target → Target → ARM Compiler 切回 V5.06 update x 后重新构建")

_COUNTS_RE = re.compile(r"(\d+)\s+Error\(s\),\s*(\d+)\s+Warning\(s\)")
_SIZE_RE = re.compile(r"Program Size:\s*Code=(\d+)\s+RO-data=(\d+)\s+RW-data=(\d+)\s+ZI-data=(\d+)")
_FLASH_FLAGS = {"erase_done": "Erase Done", "programming_done": "Programming Done",
                "verify_ok": "Verify OK", "application_running": "Application running"}


def _read_tool_log(tool_log: Path | None, process) -> tuple[str, str]:
    """取 UV4 的真实输出：优先 -o 日志（stdout 往往为空），退回进程 stdout/stderr。"""
    if tool_log is not None and tool_log.is_file():
        try:
            return tool_log.read_text(encoding="utf-8", errors="replace"), "tool_log"
        except OSError:
            pass
    return (process.stdout or "") + (process.stderr or ""), "stdout"


_ALGORITHM_NOT_CONFIGURED = (
    "Programming Algorithm 未配置（-FN0 / 缺 -FF0）：在 Keil Options for Target → Utilities → "
    "Settings → Flash Download → Add 选择本器件的 Flash 算法（HT32F490x1 Int.Flash）")


def _flash_algorithm_issue(active: str) -> str | None:
    """Flash Download 的 Programming Algorithm 是否配置（`-FN<count>` / `-FF<index><name>`）。

    返回 None 表示"看起来配了"或"无从判断"（uvoptx 里连 `-FN` 令牌都没有时不下结论，
    交给 UV4 实际结果，避免误拦手工精简过的工程）。
    """
    m = re.search(r"-FN(\d+)", active)
    if not m:
        return None
    if int(m.group(1)) == 0 or not re.search(r"-FF0\S", active):
        return _ALGORITHM_NOT_CONFIGURED
    return None


def _resolve_flm(spec: str) -> Path | None:
    """把 `-FP0($$Device:<器件>$Flash\\xxx.FLM)` 还原为已装 DFP 里的实际文件。

    `$$Device:` 是 Keil 的 DFP 相对路径写法，真实位置在
    `<PackRoot>/Holtek/<Pack>/<version>/Flash/xxx.FLM`（版本可能在子目录）。
    解析不到说明 DFP 没装或算法文件缺失——按尽力而为处理，不做硬判定。
    """
    if not spec:
        return None
    if spec.startswith("$$"):
        parts = spec.split("$")
        if len(parts) < 3:
            return None
        relative = Path(parts[-1].replace("\\", "/"))
        if relative.is_absolute():
            return relative if relative.is_file() else None
        for pack in find_ht32_dfp():
            root = Path(pack)
            try:
                candidates = [root, *(child for child in root.iterdir() if child.is_dir())]
            except OSError:
                candidates = [root]
            for candidate in candidates:
                if (candidate / relative).is_file():
                    return candidate / relative
        return None
    path = Path(spec.replace("\\", "/"))
    return path if path.is_file() else None


def _flash_diagnosis(ctx: OperationContext) -> dict[str, object]:
    """flash 失败时的工程侧诊断：注册项、-FC/-FL0/-FL1 与 uvprojx 内存节点对照。

    只读工程文件，不猜芯片：`-FL0` 与 uvprojx `<IROM>` 比、`-FC` 是否为 0、
    是否缺 `CMSIS_AGDI`（MDK 5.23 下 RAM for Algorithm=0 / Reset and Run 不勾的根因）、
    `-FN`/`-FF0` 是否配了 Programming Algorithm（Flash Download 算法列表为空时，
    UV4 只报 `Erase Failed! Flash Download failed - "Cortex-M4"`，没有任何指向性）。
    结论拼进异常消息（结果是 error，observations 不会随异常返回）。
    """
    side = ctx.uvprojx.with_suffix(".uvoptx")
    issues: list[str] = []
    params: dict[str, object] = {}
    if not side.is_file():
        return {"uvoptx": None, "params": {}, "issues": ["工程无 .uvoptx（调试器/下载配置未保存）"]}
    text = side.read_text(encoding="utf-8", errors="replace")
    entries = {key: name for key, name in re.findall(
        r"<Key>([^<]+)</Key>\s*<Name>([^<]*)</Name>", text, re.S)}
    pmon = re.search(r"<pMon>([^<]*)</pMon>", text)
    params["pMon"] = pmon.group(1) if pmon else None
    params["keys"] = sorted(entries)
    active = entries.get("UL2CM3") or entries.get("CMSIS_AGDI") or ""

    def value(flag: str) -> int | None:
        m = re.search(rf"-{flag}([0-9A-Fa-f]+)", active)
        return int(m.group(1), 16) if m else None

    params.update({"fc": value("FC"), "fl0": value("FL0"), "fl1": value("FL1")})
    fn = re.search(r"-FN(\d+)", active)
    params["fn"] = int(fn.group(1)) if fn else None
    params["algorithms"] = re.findall(r"-FF(\d+)([^\s)]+)", active)
    fp = re.search(r"-FP0\(([^)]*)\)", active)
    params["fp0"] = fp.group(1) if fp else None
    algorithm_issue = _flash_algorithm_issue(active)
    if algorithm_issue:
        issues.append(algorithm_issue)
    if params["fp0"]:
        resolved = _resolve_flm(str(params["fp0"]))
        params["flm"] = str(resolved) if resolved else None
        if resolved is None:
            issues.append(
                f"DFP 未注册或 Flash 算法文件缺失（-FP0 解析不到 {params['fp0']}）："
                "确认本器件 DFP 已安装（工程内 utilities\\pack\\*.pack 双击安装），"
                "再到 Flash Download 重新 Add 算法")
    if "CMSIS_AGDI" not in entries:
        issues.append("缺 CMSIS_AGDI 注册项（MDK 5.23 下 RAM for Algorithm 可能为 0、Reset and Run 不勾）")
    elif "-FO15" not in entries["CMSIS_AGDI"]:
        issues.append("CMSIS_AGDI 缺 -FO15（Reset and Run 未启用）")
    if not params["fc"]:
        issues.append("-FC 缺失或为 0（Flash 算法没有 RAM 工作区）")
    project_text = ctx.uvprojx.read_text(encoding="utf-8", errors="replace")
    m = re.search(r"<IROM>.*?<Size>(0x[0-9A-Fa-f]+)</Size>", project_text, re.S)
    if m and params["fl0"] is not None:
        expected = int(m.group(1), 16)
        if expected != params["fl0"]:
            issues.append(f"uvoptx -FL0=0x{params['fl0']:X} 与 uvprojx IROM=0x{expected:X} 不符")
    return {"uvoptx": str(side), "params": params, "issues": issues,
            "algorithm_issue": algorithm_issue}


def _summarize_tool_output(operation: str, text: str) -> dict[str, object]:
    """从 UV4 输出里摘编译统计/烧录阶段，省得 agent 再读日志文件。"""
    summary: dict[str, object] = {}
    if m := _COUNTS_RE.search(text):
        summary["errors"], summary["warnings"] = int(m.group(1)), int(m.group(2))
    if m := _SIZE_RE.search(text):
        summary.update({"code": int(m.group(1)), "ro_data": int(m.group(2)),
                        "rw_data": int(m.group(3)), "zi_data": int(m.group(4))})
    if m := _COMPILER_RE.search(text):
        version, folder = m.group(1), m.group(2)
        summary["compiler"] = version
        if _AC6_HINT_RE.search(folder) or version.upper().startswith("V6"):
            summary["compiler_supported"] = False
    if operation in {"flash", "debug"}:
        summary["flash"] = {key: (flag in text) for key, flag in _FLASH_FLAGS.items()}
    error_lines = [line.strip() for line in text.splitlines()
                   if re.search(r"\berror\b|错误", line, re.IGNORECASE) and not _COUNTS_RE.search(line)]
    if error_lines:
        summary["error_lines"] = error_lines[:5]
    return summary


def _mentions_keil_debugger(path: Path) -> bool:
    """文件里是否出现 Keil 调试器证据（CMSIS-DAP / J-Link / ULINK / ST-Link / e-Link 驱动或字样）。"""
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError):
        return False
    for element in root.iter():
        text = (element.text or "").strip()
        if not text:
            continue
        if _KEIL_DEBUGGER_TEXT.search(text):
            return True
        if element.tag.rsplit("}", 1)[-1].lower() in _DRIVER_TAGS:
            normalized = ntpath.normpath(text.replace("/", "\\"))
            if ntpath.basename(normalized).lower() in _KEIL_DEBUGGER_DRIVERS:
                return True
    return False


def _uses_keil_debugger(uvprojx: Path) -> str:
    """返回调试器判定依据（依据所在文件名），未配置返回空串。

    Keil 的调试器驱动选择落在 **.uvoptx**（Options for Target → Debug 的结果，如
    `<pMon>BIN\\CMSIS_AGDI.dll</pMon>` 或 J-Link 的 `BIN\\JL2CM3.dll`），工程文件 .uvprojx
    只保留 `<Flash2>` 等默认项。只看 uvprojx 会漏判：用户在 Keil 里明明选了调试器，却被判
    "未配置"而拦下 flash/debug。
    """
    for path in (uvprojx.with_suffix(".uvoptx"), uvprojx):
        if path.is_file() and _mentions_keil_debugger(path):
            return path.name
    return ""


def _debugger_not_configured_reason(uvprojx: Path) -> str:
    uvoptx = uvprojx.with_suffix(".uvoptx")
    hint = (f"{uvoptx.name} 中未见已知调试器驱动"
            if uvoptx.is_file()
            else f"工程无 {uvoptx.name}（模板不含该文件，Keil 保存工程后才生成）")
    return (f"工程未配置调试器（{hint}）：请在 Keil 的 "
            "Options for Target → Debug 选择调试器（CMSIS-DAP / e-Link32 Pro / J-Link）并指定探针，"
            "Utilities → Settings 勾选 Use Debug Driver，保存工程后重试")


class KeilBackend:
    name = "keil"

    def __init__(self, runner: Runner): self.runner = runner

    def _blocked(self, ctx: OperationContext, operation: str, message: str) -> OperationResult:
        result = OperationResult.start(operation, self.name, ctx.project_id)
        result.error = {"message": message}
        return result.finish("blocked")

    def _validate(self, ctx: OperationContext, operation: str) -> OperationResult | None:
        if not ctx.tools.get("uv4_exe"): return self._blocked(ctx, operation, "未配置 UV4.exe")
        if operation in {"probe", "flash", "debug"}:
            if ctx.debugger.backend != "keil": return self._blocked(ctx, operation, "debugger backend 不是 keil")
            if ctx.debugger.interface != "swd": return self._blocked(ctx, operation, "Keil 调试器首期仅支持 SWD")
            if not _uses_keil_debugger(ctx.uvprojx):
                return self._blocked(ctx, operation, _debugger_not_configured_reason(ctx.uvprojx))
        return None

    def _run(self, ctx: OperationContext, operation: str, flag: str, *, interactive: bool = False) -> OperationResult:
        if invalid := self._validate(ctx, operation): return invalid
        # 烧录前置检查：算法列表为空时 UV4 只会报 `Flash Download failed - "Cortex-M4"`，
        # 与其让用户对着裸的退出码 2 猜，不如先读 uvoptx 给出指向性结论，不调 UV4。
        diagnosis = _flash_diagnosis(ctx) if operation == "flash" else None
        if diagnosis and diagnosis.get("algorithm_issue"):
            return self._blocked(ctx, operation, f"烧录前置检查未通过：{diagnosis['algorithm_issue']}")
        # 日志是辅助产物：全局目录被沙箱拦时降级为不落日志，绝不阻断构建/烧录
        log_dir = writable_dir(ctx.logs_dir)
        unique = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ") + "-" + uuid.uuid4().hex[:8]
        tool_log = log_dir / f"keil-{operation}-tool-{unique}.log" if log_dir else None
        runtime_log = log_dir / f"keil-{operation}-{unique}.log" if log_dir else None
        argv = [ctx.tools["uv4_exe"], flag, str(ctx.uvprojx)]
        if ctx.target: argv += ["-t", ctx.target]
        argv += ["-j0"]
        if tool_log is not None: argv += ["-o", str(tool_log)]
        try:
            process = self.runner(argv, cwd=ctx.project_root, timeout_seconds=600, log_path=runtime_log)
        except FileNotFoundError:
            return self._blocked(ctx, operation, f"UV4.exe 不存在: {ctx.tools['uv4_exe']}")
        result = OperationResult.start(operation, self.name, ctx.project_id)
        if operation in {"build", "rebuild"}: result.artifacts = collect_build_artifacts(ctx.uvprojx, ctx.target)
        if tool_log is not None and tool_log.is_file():
            result.log_path = str(tool_log.resolve())
            result.artifacts["tool_log"] = str(tool_log.resolve())
        elif process.log_path is not None:
            result.log_path = str(process.log_path.resolve())
        if process.log_path is not None:
            result.artifacts["runtime_log"] = str(process.log_path.resolve())
        if interactive: result.observations["session"] = "interactive"
        text, source = _read_tool_log(tool_log, process)
        summary = _summarize_tool_output(operation, text)
        if summary:
            summary["source"] = source
            result.observations["summary"] = summary
        status = "ok" if process.returncode == 0 else "warn" if process.returncode == 1 else "error"
        if summary and summary.get("compiler_supported") is False:
            result.warnings.append(_AC6_WARNING.format(version=summary.get("compiler", "AC6")))
            if status == "ok": status = "warn"
        if log_dir is None:
            result.warnings.append(LOG_UNAVAILABLE.format(path=ctx.logs_dir))
            if status == "ok": status = "warn"
        if status == "error":
            message = f"UV4 执行失败（退出码 {process.returncode}）"
            if operation == "flash" and diagnosis["issues"]:
                message += "；诊断：" + "；".join(diagnosis["issues"])
            raise ToolExecutionError(message, log_path=result.log_path)
        return result.finish(status)

    def scan(self, ctx: OperationContext) -> OperationResult:
        result = OperationResult.start("scan", self.name, ctx.project_id)
        result.observations["projects"] = [str(path) for path in list_uvprojx(ctx.project_root)]
        return result.finish("ok")
    def targets(self, ctx):
        result = OperationResult.start("targets", self.name, ctx.project_id); result.observations["targets"] = list_targets(ctx.uvprojx); return result.finish("ok")
    def build(self, ctx): return self._run(ctx, "build", "-b")
    def rebuild(self, ctx): return self._run(ctx, "rebuild", "-r")
    def clean(self, ctx): return self._run(ctx, "clean", "-c")
    def flash(self, ctx, image: Path | None = None): return self._run(ctx, "flash", "-f")
    def debug(self, ctx): return self._run(ctx, "debug", "-d", interactive=True)
    def probe(self, ctx): return self._validate(ctx, "probe") or OperationResult.start("probe", self.name, ctx.project_id).finish("ok")
    def connect(self, ctx): return self.probe(ctx)
    def reset(self, ctx): return self._blocked(ctx, "reset", "Keil reset 需要交互式调试会话")

    def configure(self, ctx: OperationContext, *, debugger: str | None = None,
                  speed_khz: int | None = None, probe_serial: str | None = None,
                  dry_run: bool = False) -> OperationResult:
        """读/改 Keil 工程的调试器配置（.uvprojx 的 Driver、<Flash2> 与 .uvoptx 的 pMon/参数串）。"""
        result = OperationResult.start("configure", self.name, ctx.project_id)
        try:
            result.observations["current"] = keil_config.read_config(ctx.uvprojx)
            result.observations["available"] = keil_config.available(ctx.tools.get("uv4_exe"))
            if not debugger:
                return result.finish("ok")   # 只报告，不改文件
            report = keil_config.switch(ctx.uvprojx, debugger, speed_khz=speed_khz,
                                       probe_serial=probe_serial, dry_run=dry_run)
        except keil_config.KeilConfigError as error:
            result.error = {"message": str(error)}
            return result.finish("blocked")
        result.observations.update(report)
        if not dry_run:
            result.artifacts["uvprojx"] = str(ctx.uvprojx)
            result.artifacts["uvoptx"] = str(keil_config.uvoptx_for(ctx.uvprojx))
            if report.get("backup"):
                result.artifacts["backup"] = report["backup"]
        if ctx.tools.get("uv4_exe"):
            if missing := keil_config.missing_driver_files(ctx.tools.get("uv4_exe"), debugger):
                result.warnings.append(
                    f"Keil 安装目录里没找到 {', '.join(missing)}："
                    "选 J-Link 需先装 J-Link 官方软件包与 HT32_J-Link_Plugin（驱动在 Keil 的 ARM\\Segger 目录）")
        else:
            result.warnings.append("未配置 tools.uv4_exe，无法校验 Keil 里是否装了该调试器驱动；工程文件已按所选驱动写好")
        if report.get("debugger") == "jlink" and not report.get("dry_run"):
            result.observations["next_steps"] = [
                "在 Keil 的 Options for Target → Debug → Settings 确认 J-Link 探针与 Max Clock（建议 1MHz 起）",
                "与 e-Link32 Pro 一样连 SWD；Vtref 不供电，目标板需独立供电",
            ]
        return result.finish("warn" if result.warnings else "ok")
