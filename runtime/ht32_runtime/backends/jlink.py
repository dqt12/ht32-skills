from __future__ import annotations

import re
import socket
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from ..models import OperationResult
from ..paths import LOG_UNAVAILABLE, writable_dir
from ..process import ToolExecutionError, start_process
from . import OperationContext, Runner

_NUMBER = re.compile(r"^(?:0x[0-9a-fA-F]+|[0-9]+)$")
_REGISTER = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
_ACTIONS = re.compile(r"^(?:break [A-Za-z0-9_:.]+|continue|step|next|finish|backtrace|print [A-Za-z0-9_.*>\-]+)$")


class JLinkBackend:
    name = "jlink"

    def __init__(self, runner: Runner, starter=start_process, *, server_host="127.0.0.1", socket_connector=socket.create_connection, wait=time.sleep, server_timeout=10):
        self.runner = runner
        self.starter = starter
        self.server_host = server_host
        self.socket_connector = socket_connector
        self.wait = wait
        self.server_timeout = server_timeout

    def _result(self, ctx, operation, status="blocked", message=None):
        result = OperationResult.start(operation, self.name, ctx.project_id)
        if message:
            result.error = {"message": message}
        return result.finish(status)

    def _tool(self, ctx, key, operation):
        value = ctx.tools.get(key)
        return value or self._result(ctx, operation, message=f"未配置 {key}")

    def _log(self, ctx, operation):
        log_dir = writable_dir(ctx.logs_dir)
        if log_dir is None:
            return None
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        return log_dir / f"jlink-{operation}-{stamp}-{uuid.uuid4().hex[:8]}.log"

    def _run(self, ctx, operation, argv, timeout=60):
        log_path = self._log(ctx, operation)
        try:
            process = self.runner(argv, cwd=ctx.project_root, timeout_seconds=timeout, log_path=log_path)
        except FileNotFoundError as exc:
            return self._result(ctx, operation, message=f"外部工具不存在: {exc.filename or argv[0]}")
        status = "ok" if process.returncode == 0 else "warn" if process.timed_out and operation == "rtt" else "error"
        if status == "error":
            raise ToolExecutionError(f"J-Link 工具执行失败（退出码 {process.returncode}）", log_path=process.log_path)
        result = OperationResult.start(operation, self.name, ctx.project_id)
        if process.log_path is not None: result.log_path = str(process.log_path.resolve())
        result.observations["stdout"] = process.stdout
        if log_path is None:
            result.warnings.append(LOG_UNAVAILABLE.format(path=ctx.logs_dir))
            if status == "ok": status = "warn"
        return result.finish(status)

    def _commander(self, ctx, operation, commands):
        tool = self._tool(ctx, "jlink_exe", operation)
        if not isinstance(tool, str):
            return tool
        # 临时脚本目录：全局 cache 不可写时退到系统临时目录，仍不可写才阻断
        script_dir = writable_dir(ctx.cache_dir) or writable_dir(Path(tempfile.gettempdir()) / "ht32-skills")
        if script_dir is None:
            return self._result(ctx, operation, message="缓存目录不可写，无法生成 J-Link 命令脚本")
        script = script_dir / f"{operation}-{uuid.uuid4().hex}.jlink"
        script.write_text("\n".join(commands) + "\n", encoding="utf-8")
        try:
            return self._run(ctx, operation, [tool, "-NoGui", "1", "-CommanderScript", str(script)])
        finally:
            script.unlink(missing_ok=True)

    def _prefix(self, ctx):
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", ctx.chip):
            raise ValueError("invalid chip")
        return [f"device {ctx.chip}", f"si {ctx.debugger.interface.upper()}", f"speed {ctx.debugger.speed_khz}", "connect"]

    def list_probes(self, ctx):
        tool = self._tool(ctx, "jlink_exe", "probes")
        if not isinstance(tool, str):
            return tool
        result = self._run(ctx, "probes", [tool, "-USBList"])
        probes = re.findall(r"(?:S/N|Serial number)\s*:\s*(\d+)", result.observations.pop("stdout", ""), re.I)
        if ctx.debugger.probe_serial:
            probes = [serial for serial in probes if serial == ctx.debugger.probe_serial]
        result.observations["probes"] = probes
        return result

    def probe(self, ctx): return self.list_probes(ctx)
    def connect(self, ctx): return self._commander(ctx, "connect", self._prefix(ctx) + ["exit"])

    def flash(self, ctx, image):
        raw_image = str(image) if image is not None else ""
        if "\n" in raw_image or "\r" in raw_image:
            raise ValueError("invalid image path")
        if image is None or not Path(image).is_file():
            return self._result(ctx, "flash", message="烧录镜像不存在")
        absolute_image = str(Path(image).resolve())
        return self._commander(ctx, "flash", self._prefix(ctx) + [f"loadfile {absolute_image}", "verify", "r", "g", "exit"])

    def reset(self, ctx): return self._commander(ctx, "reset", self._prefix(ctx) + ["r", "go", "exit"])

    def read_memory(self, ctx, address, count, width):
        if width not in {8, 16, 32} or not _NUMBER.fullmatch(str(address)) or type(count) is not int or count <= 0:
            raise ValueError("invalid memory arguments")
        return self._commander(ctx, "read-memory", self._prefix(ctx) + [f"mem{width} {address}, {count}", "exit"])

    def write_memory(self, ctx, address, values, width, confirmed):
        if not confirmed:
            return self._result(ctx, "write-memory", message="写内存需要 --yes")
        if width not in {8, 16, 32} or not _NUMBER.fullmatch(str(address)) or not values or any(not _NUMBER.fullmatch(str(value)) for value in values):
            raise ValueError("invalid memory arguments")
        return self._commander(ctx, "write-memory", self._prefix(ctx) + [f"w{width} {address}, " + ", ".join(map(str, values)), "exit"])

    def read_registers(self, ctx): return self._commander(ctx, "registers", self._prefix(ctx) + ["regs", "exit"])

    def write_register(self, ctx, name, value, confirmed):
        if not confirmed:
            return self._result(ctx, "write-register", message="写寄存器需要 --yes")
        if not _REGISTER.fullmatch(name) or not _NUMBER.fullmatch(str(value)):
            raise ValueError("invalid register arguments")
        return self._commander(ctx, "write-register", self._prefix(ctx) + [f"wreg {name}, {value}", "exit"])

    def _server_argv(self, ctx, port, rtt_port=None):
        tool = self._tool(ctx, "jlink_gdb_server_exe", "debug-server")
        if not isinstance(tool, str):
            return tool
        argv = [tool, "-select", f"USB={ctx.debugger.probe_serial or '0'}", "-device", ctx.chip, "-if", ctx.debugger.interface.upper(), "-speed", str(ctx.debugger.speed_khz), "-port", str(port), "-singlerun"]
        if rtt_port is not None:
            argv += ["-RTTTelnetPort", str(rtt_port)]
        return argv

    def start_debug_server(self, ctx, port, rtt_port=None):
        argv = self._server_argv(ctx, port, rtt_port)
        if not isinstance(argv, list):
            return argv
        try:
            server = self.starter(argv, cwd=ctx.project_root, log_path=self._log(ctx, "debug-server"))
        except FileNotFoundError as exc:
            return self._result(ctx, "debug-server", message=f"外部工具不存在: {exc.filename or argv[0]}")
        ready_port = rtt_port if rtt_port is not None else port
        deadline = time.monotonic() + self.server_timeout
        while True:
            process = getattr(server, "process", None)
            returncode = process.poll() if process is not None else None
            if returncode is not None:
                server.stop()
                raise ToolExecutionError(f"J-Link GDB Server 在端口就绪前退出（退出码 {returncode}）")
            try:
                connection = self.socket_connector((self.server_host, ready_port), 0.2)
            except OSError:
                if time.monotonic() >= deadline:
                    server.stop()
                    raise ToolExecutionError(f"J-Link GDB Server 端口 {ready_port} 就绪超时")
                self.wait(0.05)
            else:
                connection.close()
                return server

    def debug_script(self, ctx, elf, actions, port=2331):
        tool = self._tool(ctx, "gdb_exe", "debug")
        if not isinstance(tool, str):
            return tool
        if elf is None or not Path(elf).is_file():
            return self._result(ctx, "debug", message="ELF 不存在")
        if any(not _ACTIONS.fullmatch(action) for action in actions):
            raise ValueError("invalid gdb action")
        argv = [tool, "--batch", str(Path(elf).resolve()), "-ex", f"target remote localhost:{port}"]
        for action in actions:
            argv += ["-ex", action]
        return self._run(ctx, "debug", argv)

    def debug(self, ctx, elf=None, actions=None, port=2331):
        if elf is None:
            return self._result(ctx, "debug", message="J-Link debug 需要 --elf")
        server = self.start_debug_server(ctx, port)
        if isinstance(server, OperationResult):
            return server
        try:
            return self.debug_script(ctx, elf, actions or ["backtrace"], port)
        finally:
            server.stop()

    def rtt(self, ctx, elf=None, seconds=5, port=2331, rtt_port=19021):
        tool = self._tool(ctx, "jlink_rtt_client_exe", "rtt")
        if not isinstance(tool, str):
            return tool
        server = self.start_debug_server(ctx, port, rtt_port)
        if isinstance(server, OperationResult):
            return server
        try:
            return self._run(ctx, "rtt", [tool, "-RTTTelnetPort", str(rtt_port)], timeout=max(1, int(seconds)))
        finally:
            server.stop()
