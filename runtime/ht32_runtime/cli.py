from __future__ import annotations

import argparse
import json
import sys
import traceback
from dataclasses import asdict
from pathlib import Path

from .backends import get_backend, make_context
from .config import ConfigError, config_view, init_project_config, load_machine_config, load_project_config, resolve_debugger
from .context import project_context
from .environment import check_environment, read_dfp_requirement
from .fetch import run_fetch
from .models import DebuggerConfig, OperationResult
from .paths import RuntimePaths
from .process import ToolExecutionError
from .serial import (
    SerialError,
    SerialTap,
    parse_send_payload,
    run_log as serial_run_log,
    run_ports as serial_run_ports,
    run_send as serial_run_send,
)
from .version import record_update_check, skill_version, update_status
from .workflow import ObservePlan, WorkflowStep, execute_workflow


class JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        raise argparse.ArgumentError(None, message)


def _positive(value):
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return number


def add_debugger_overrides(parser):
    parser.add_argument("--backend", choices=("keil", "jlink"))
    parser.add_argument("--probe-serial")
    parser.add_argument("--interface", choices=("swd", "jtag"))
    parser.add_argument("--speed-khz", type=_positive)


def _leaf(parent, name, *, overrides=False, project_required=True):
    parser = parent.add_parser(name, exit_on_error=False)
    parser.add_argument("--project", type=Path, required=project_required, default=None)
    if overrides:
        add_debugger_overrides(parser)
    return parser


def build_parser():
    parser = JsonArgumentParser(prog="ht32", exit_on_error=False)
    groups = parser.add_subparsers(dest="group", required=True, parser_class=JsonArgumentParser)

    config = groups.add_parser("config", exit_on_error=False).add_subparsers(dest="action", required=True, parser_class=JsonArgumentParser)
    init = _leaf(config, "init", overrides=True)
    init.add_argument("--chip", required=True); init.add_argument("--uvprojx", required=True); init.add_argument("--target"); init.add_argument("--force", action="store_true")
    _leaf(config, "show", overrides=True); _leaf(config, "validate")

    # 版本查询不需要工程上下文（追溯用：skill 自身版本）
    version = groups.add_parser("version", exit_on_error=False)
    version.add_argument("--record-update", metavar="LATEST",
                         help="记录本次查到的云端最新版本（写全局缓存，供 24h TTL 判定）")

    # 下载（export 云端模式的 zip）：固定用 Python TLS + SHA256 校验，避开宿主 schannel
    fetch = groups.add_parser("fetch", exit_on_error=False)
    fetch.add_argument("--url", required=True, help="https 下载地址（export 报告里的 URL）")
    fetch.add_argument("--out", type=Path, required=True, help="保存路径（含文件名）")
    fetch.add_argument("--sha256", help="export 报告里的 SHA256（64 位十六进制）")
    fetch.add_argument("--size", type=_positive, help="export 报告里的字节数")
    fetch.add_argument("--allow-unverified", action="store_true",
                       help="允许在证书校验失败时继续下载（仅在已提供 SHA256 时有意义）")
    fetch.add_argument("--extract", nargs="?", const="", metavar="DIR",
                       help="下载并通过校验后解压（省略 DIR 时解到 zip 同名目录）")

    env = groups.add_parser("env", exit_on_error=False).add_subparsers(dest="action", required=True, parser_class=JsonArgumentParser)
    _leaf(env, "check", overrides=True, project_required=False)

    keil = groups.add_parser("keil", exit_on_error=False).add_subparsers(dest="action", required=True, parser_class=JsonArgumentParser)
    for name in ("scan", "targets", "build", "rebuild", "clean", "flash", "debug"):
        _leaf(keil, name, overrides=name in {"flash", "debug"})
    # 直接改工程里的调试器配置（用户要求「帮用户切调试器 / 改 Keil 配置」）
    configure = _leaf(keil, "configure")
    configure.add_argument("--debugger", metavar="{cmsis-dap,jlink}",
                           help="目标调试器（elink32 视作 cmsis-dap）；省略则只报告当前配置")
    configure.add_argument("--speed-khz", type=_positive, help="SWD 速率（写入 -TC，千赫）")
    configure.add_argument("--probe-serial", help="探针序列号（写入 -U）")
    configure.add_argument("--dry-run", action="store_true", help="只报告将要改什么，不落盘")

    jlink = groups.add_parser("jlink", exit_on_error=False).add_subparsers(dest="action", required=True, parser_class=JsonArgumentParser)
    leaves = {name: _leaf(jlink, name, overrides=name in {"connect", "flash", "debug", "rtt"}) for name in ("probes", "connect", "flash", "reset", "read-memory", "write-memory", "registers", "write-register", "debug", "rtt")}
    leaves["flash"].add_argument("--image", type=Path)
    for name in ("read-memory", "write-memory"):
        leaves[name].add_argument("--address", required=True); leaves[name].add_argument("--width", type=int, choices=(8, 16, 32), default=32)
    leaves["read-memory"].add_argument("--count", type=_positive, required=True)
    leaves["write-memory"].add_argument("--values", nargs="+", required=True); leaves["write-memory"].add_argument("--yes", action="store_true")
    leaves["write-register"].add_argument("--name", required=True); leaves["write-register"].add_argument("--value", required=True); leaves["write-register"].add_argument("--yes", action="store_true")
    leaves["debug"].add_argument("--elf", type=Path, required=True); leaves["debug"].add_argument("--actions", default="backtrace"); leaves["debug"].add_argument("--port", type=_positive, default=2331)
    leaves["rtt"].add_argument("--elf", type=Path); leaves["rtt"].add_argument("--seconds", type=_positive, default=5); leaves["rtt"].add_argument("--port", type=_positive, default=2331); leaves["rtt"].add_argument("--rtt-port", type=_positive, default=19021)

    workflow = groups.add_parser("workflow", exit_on_error=False).add_subparsers(dest="action", required=True, parser_class=JsonArgumentParser)
    run = _leaf(workflow, "run", overrides=True); run.add_argument("--steps", required=True); run.add_argument("--image", type=Path); run.add_argument("--elf", type=Path); run.add_argument("--seconds", type=_positive, default=5); run.add_argument("--actions", default="backtrace")
    # 烧录前预开的串口观测（可选）：给了 --observe-port 就在跑步骤之前打开端口持续采集
    run.add_argument("--observe-port"); run.add_argument("--observe-baud", type=_positive, default=115200)
    run.add_argument("--observe-seconds", type=_positive, default=15); run.add_argument("--observe-hex", action="store_true")
    run.add_argument("--observe-encoding", default="utf-8")
    run.add_argument("--observe-stamp", choices=("none", "rel", "abs", "both"), default="both",
                     help="观测日志的时间列：rel=相对毫秒 / abs=墙钟 / both=两者")

    # 串口观测（自足化）：不需要工程上下文，与 version 一样在 _execute 前段分流
    serial = groups.add_parser("serial", exit_on_error=False).add_subparsers(dest="action", required=True, parser_class=JsonArgumentParser)
    serial.add_parser("ports", exit_on_error=False)
    serial_log = serial.add_parser("log", exit_on_error=False)
    serial_log.add_argument("--port", required=True,
                            help="COM13；或逗号分隔多口 COM13,COM14（共享时钟，发送只走第一个口）")
    serial_log.add_argument("--baud", type=_positive, default=115200); serial_log.add_argument("--seconds", type=_positive, default=5)
    serial_log.add_argument("--hex", action="store_true"); serial_log.add_argument("--encoding", default="utf-8")
    serial_log.add_argument("--stamp", choices=("none", "rel", "abs", "both"), default="both",
                            help="日志时间列：rel=相对毫秒 / abs=墙钟 / both=两者")
    serial_log.add_argument("--send", action="append", default=[], metavar="DATA",
                            help="采集期间发送的数据（与 --send-at 一一对应，可重复）")
    serial_log.add_argument("--send-at", action="append", default=[], type=float, metavar="SECONDS",
                            help="对应的发送时刻（相对采集开始，秒）")
    serial_log.add_argument("--send-hex", action="store_true", help="--send 按 hex 解析")
    serial_log.add_argument("--send-newline", action="store_true", help="发送内容追加 CRLF")
    serial_log.add_argument("--analyze", choices=("none", "intervals"), default="none",
                            help="采集后做间隔统计（intervals：含 marker 的行之间的到达间隔）")
    serial_log.add_argument("--marker", default="#", help="intervals 分析用的行内标记，默认 #")
    serial_log.add_argument("--reset", action="store_true", help="采集前打 DTR/RTS 脉冲复位（best-effort）")
    serial_send = serial.add_parser("send", exit_on_error=False)
    serial_send.add_argument("--port", required=True); serial_send.add_argument("--baud", type=_positive, default=115200); serial_send.add_argument("--data", required=True)
    serial_send.add_argument("--hex", action="store_true"); serial_send.add_argument("--newline", action="store_true"); serial_send.add_argument("--encoding", default="utf-8")
    return parser


def _overrides(args):
    return {key: getattr(args, key, None) for key in ("backend", "probe_serial", "interface", "speed_khz")}


_AUTO_PROJECT_ID = object()


def _base_result(operation, project, backend=None, project_id=_AUTO_PROJECT_ID):
    # 裸机命令（如不带 --project 的 env check）没有工程上下文时 project_id 留 null，
    # 不要拿 cwd 硬算一个——那会让人以为这是某个真实工程的运行结果。
    if project_id is _AUTO_PROJECT_ID:
        project_id = RuntimePaths.for_project(project).project_id
    return OperationResult.start(operation, backend, project_id)


def _context(args, forced_backend=None):
    project = load_project_config(args.project); machine = load_machine_config()
    overrides = _overrides(args)
    if forced_backend is not None:
        overrides["backend"] = forced_backend
    debugger = resolve_debugger(machine.debugger, project.debugger, overrides)
    paths = RuntimePaths.for_project(project.project_root)
    return project, machine, debugger, paths, make_context(project, debugger, machine.tools, paths)


def _execute(args):
    if args.group == "version":
        recorded = None
        latest = getattr(args, "record_update", None)
        if latest:
            recorded = record_update_check(latest)
        version, source = skill_version()
        result = OperationResult.start("version", None, "global")
        result.observations = {"skill_version": version, "source": source,
                               "python": sys.version.split()[0],
                               "update": update_status()}
        if recorded is not None:
            result.artifacts["update_cache"] = str(recorded)
        elif latest:
            result.warnings.append("更新检查结果未写入全局缓存（目录不可写）：下次仍会联网重新检查")
        return result.finish("warn" if result.warnings else "ok")

    if args.group == "fetch":
        # 工程 zip 下载：Python TLS + 大小/SHA256 校验，不依赖宿主 schannel
        extract_to: Path | None = None
        if args.extract is not None:
            if args.extract:
                extract_to = Path(args.extract)
            else:  # --extract 不带目录：就地解到同名目录（xxx.zip → xxx/）
                stem = args.out.name[:-4] if args.out.name.lower().endswith(".zip") else args.out.name
                extract_to = args.out.with_name(stem)
        return run_fetch(args.url, args.out, sha256=args.sha256, size=args.size,
                         allow_unverified=args.allow_unverified, extract_to=extract_to)

    if args.group == "config":
        if args.action == "init":
            debugger = DebuggerConfig(**{key: value for key, value in _overrides(args).items() if value is not None})
            path = init_project_config(args.project, args.chip, args.uvprojx, args.target, debugger, force=args.force)
            # 回显落盘后的**解析值**（uvprojx 已归一化、debugger 已套用默认值），
            # 免得调用方拿自己的入参当真值，再去猜实际写进去了什么。
            resolved = load_project_config(args.project)
            result = _base_result("config-init", args.project, resolved.debugger.backend)
            result.artifacts["config"] = str(path)
            result.observations = {
                "config_file": str(path),
                "project": {
                    "root": str(resolved.project_root),
                    "chip": resolved.chip,
                    "uvprojx": str(resolved.uvprojx),
                    "target": resolved.target,
                },
                "debugger": asdict(resolved.debugger),
                "project_context": project_context(args.project),
            }
            context = result.observations["project_context"]
            if context["ht32_project"] == "missing":
                result.warnings.append(
                    "HT32_PROJECT.md 缺失：本工程尚无事实源。建议先执行 project-context 工作流"
                    "（init）再写码；该检查不阻断本次配置。")
            if args.force:
                result.warnings.append("使用了 --force：已覆盖原有的 .ht32/project.json")
            return result.finish("warn" if result.warnings else "ok")
        if args.action == "show":
            result = _base_result("config-show", args.project); result.observations["config"] = config_view(args.project, _overrides(args)); return result.finish("ok")
        load_project_config(args.project)
        return _base_result("config-validate", args.project).finish("ok")

    if args.group == "env":
        # 工程上下文可选：不带 --project 时按裸机环境检测（机器工具/探针/串口），
        # 不因缺少工程配置就 argparse exit 2——调用方拿到的始终是统一 JSON。
        project = args.project or Path.cwd()
        machine = load_machine_config()
        try:
            project_cfg = load_project_config(project)
            requirement = read_dfp_requirement(project)
            debugger = resolve_debugger(machine.debugger, project_cfg.debugger, _overrides(args))
        except ConfigError:
            project_cfg = None
            requirement = None
            debugger = resolve_debugger(machine.debugger, None, _overrides(args))
        result = _base_result("env-check", project, debugger.backend,
                              project_id=None if project_cfg is None else _AUTO_PROJECT_ID)
        result.observations = check_environment(machine.tools, requirement=requirement)
        result.observations["project_source"] = "none" if project_cfg is None else "config"
        if project_cfg is None:
            result.warnings.append(
                f"未找到工程配置（{project}）：按裸机环境检测（project_id=null），未包含工程 DFP 要求；"
                "在工程目录执行或传 --project <工程目录> 可纳入工程上下文")
        required = result.observations["uv4"]["available"] if debugger.backend == "keil" else result.observations["jlink_tools"]["available"]
        if not required:
            result.error = {"message": f"当前 {debugger.backend} 场景缺少必需工具"}
            return result.finish("blocked")
        optional_missing = [key for key in ("dfp", "cmsis_dap", "serial_ports", "clang_format") if not result.observations[key]["available"]]
        if optional_missing:
            result.warnings.append("未检测到: " + ", ".join(optional_missing))
        requirement = result.observations["dfp"].get("requirement")
        if requirement:
            hint = requirement["bundled"] or f'{requirement["pack"]} >= {requirement["min_version"]}'
            if requirement["status"] == "below-minimum":
                result.warnings.append(
                    f'DFP {requirement["pack"]} 已装 {requirement["installed"]} 低于工程要求 '
                    f'{requirement["min_version"]}：请安装工程内随包 {hint}（同版本或更高均可）')
            elif requirement["status"] == "missing":
                result.warnings.append(
                    f'未检测到工程要求的 DFP {requirement["pack"]}：请安装工程内随包 {hint}'
                    "（同版本或更高均可）")
        return result.finish("warn" if result.warnings else "ok")

    if args.group == "serial":
        # 串口观测自足化：不依赖工程配置；日志只写全局 logs/serial/，不碰用户工程目录
        paths = RuntimePaths.for_project(Path.cwd())
        if args.action == "ports":
            return serial_run_ports("serial")
        if args.action == "log":
            if len(args.send) != len(args.send_at):
                raise ConfigError("--send 与 --send-at 必须成对出现且数量一致")
            send_plan = []
            for text, at in zip(args.send, args.send_at):
                if at < 0 or at >= args.seconds:
                    raise ConfigError(f"--send-at {at}s 超出采集窗口（要求 0 ≤ t < {args.seconds}）")
                payload = parse_send_payload(text, hex_mode=args.send_hex, encoding=args.encoding)
                if args.send_newline:
                    payload += b"\r\n"
                send_plan.append((at, payload))
            return serial_run_log("serial", port=args.port, baud=args.baud, seconds=args.seconds,
                                  hex_mode=args.hex, encoding=args.encoding, logs_dir=paths.serial_logs_dir,
                                  stamp=args.stamp, send_plan=send_plan,
                                  analyze=None if args.analyze == "none" else args.analyze,
                                  marker=args.marker, reset=args.reset)
        return serial_run_send("serial", port=args.port, baud=args.baud, data=args.data,
                               hex_mode=args.hex, newline=args.newline, encoding=args.encoding)

    forced = args.group if args.group in {"keil", "jlink"} else None
    project, machine, debugger, paths, ctx = _context(args, forced)
    if args.group == "keil":
        backend = get_backend("keil")
        if args.action == "flash":
            return backend.flash(ctx, None)
        if args.action == "configure":
            return backend.configure(ctx, debugger=args.debugger, speed_khz=args.speed_khz,
                                     probe_serial=args.probe_serial, dry_run=args.dry_run)
        return getattr(backend, args.action)(ctx)
    if args.group == "jlink":
        backend = get_backend("jlink")
        calls = {
            "probes": lambda: backend.list_probes(ctx), "connect": lambda: backend.connect(ctx),
            "flash": lambda: backend.flash(ctx, args.image), "reset": lambda: backend.reset(ctx),
            "read-memory": lambda: backend.read_memory(ctx, args.address, args.count, args.width),
            "write-memory": lambda: backend.write_memory(ctx, args.address, args.values, args.width, args.yes),
            "registers": lambda: backend.read_registers(ctx),
            "write-register": lambda: backend.write_register(ctx, args.name, args.value, args.yes),
            "debug": lambda: backend.debug(ctx, args.elf, [item.strip() for item in args.actions.split(",") if item.strip()], args.port),
            "rtt": lambda: backend.rtt(ctx, args.elf, args.seconds, args.port, args.rtt_port),
        }
        return calls[args.action]()

    operations = [item.strip() for item in args.steps.split(",") if item.strip()]
    if not operations or any(item not in {"build", "flash", "debug", "rtt"} for item in operations):
        raise ConfigError("workflow steps 仅支持 build,flash,debug,rtt")
    options = {"flash": {"image": args.image}, "debug": {"elf": args.elf, "actions": [item.strip() for item in args.actions.split(",") if item.strip()]}, "rtt": {"elf": args.elf, "seconds": args.seconds}}
    observe = None
    if getattr(args, "observe_port", None):
        observe = ObservePlan(tap=SerialTap(args.observe_port, args.observe_baud),
                              tail_seconds=args.observe_seconds, hex_mode=args.observe_hex,
                              encoding=args.observe_encoding, stamp=args.observe_stamp)
    return execute_workflow(ctx, [WorkflowStep(item, options.get(item, {})) for item in operations], observe=observe)


def _emit(result: OperationResult) -> None:
    """打印纯 JSON。

    管道/重定向（非 tty）下固定输出 ASCII JSON（``ensure_ascii=True``）：宿主工具按
    UTF-8 解码管道输出，若这里按 Windows 控制台编码（GBK）写中文，到宿主侧就是乱码。
    终端交互（tty）才保留本地编码的中文，便于人读；终端编码容纳不了的字符——典型是
    设备名里的私用区字符——按 ``\\uXXXX`` 转义写出：仍是合法 JSON，且不会让整次调用
    以 UnicodeEncodeError 崩掉。
    """
    stream = sys.stdout
    is_tty = getattr(stream, "isatty", None)
    piped = not (callable(is_tty) and is_tty())
    encoding = getattr(stream, "encoding", None) or "utf-8"
    if piped:
        data = json.dumps(result.to_dict(), ensure_ascii=True).encode("ascii")
    else:
        payload = json.dumps(result.to_dict(), ensure_ascii=False)
        try:
            data = payload.encode(encoding, errors="backslashreplace")
        except LookupError:
            data = json.dumps(result.to_dict(), ensure_ascii=True).encode("ascii")
    buffer = getattr(stream, "buffer", None)
    if buffer is not None:
        buffer.write(data + b"\n")
        buffer.flush()
    else:
        stream.write(data.decode("ascii" if piped else encoding, errors="replace") + "\n")
        stream.flush()


def main(argv=None):
    project = Path.cwd(); result = None
    try:
        args = build_parser().parse_args(argv)
        project = getattr(args, "project", None) or Path.cwd()
        result = _execute(args); code = result.exit_code
    except (ConfigError, SerialError, argparse.ArgumentError, ValueError) as exc:
        result = _base_result("cli", project); result.error = {"message": str(exc)}; result.finish("blocked"); code = 2
    except FileNotFoundError as exc:
        result = _base_result("cli", project); result.error = {"message": f"外部工具不存在: {exc.filename}"}; result.finish("blocked"); code = 2
    except ToolExecutionError as exc:
        result = _base_result("cli", project); result.error = {"message": str(exc)}; result.finish("error"); code = 3
    except Exception as exc:
        result = _base_result("cli", project); result.error = {"message": str(exc)}; result.finish("error"); code = 4
        try:
            paths = RuntimePaths.for_project(project); paths.logs_dir.mkdir(parents=True, exist_ok=True)
            from datetime import datetime, timezone
            import uuid
            unique = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ") + "-" + uuid.uuid4().hex[:8]
            (paths.logs_dir / f"cli-error-{unique}.log").write_text(traceback.format_exc(), encoding="utf-8")
        except OSError:
            pass   # 日志目录不可写（宿主沙箱）时不能让错误报告本身再抛异常
    _emit(result)
    return code
