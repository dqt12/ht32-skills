from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence


class ToolExecutionError(RuntimeError):
    def __init__(self, message: str, *, log_path: Path | None = None):
        super().__init__(message)
        self.log_path = log_path


def console_encoding() -> str:
    """外部工具输出按**控制台代码页**解码，而不是按 Python 的 UTF-8 模式。

    宿主设置 PYTHONUTF8=1 时，`text=True` 会强制 UTF-8，中文 Windows 上工具输出的
    GBK 字节会在 reader 线程里抛 UnicodeDecodeError，结果是 `stdout=None`（调用方
    表现为 TypeError/内部错误）。Windows 用 mbcs，其他平台用 UTF-8；解码错误一律
    replace，宁可显示替换字符也不丢整段输出。
    """
    return "mbcs" if os.name == "nt" else "utf-8"


@dataclass
class BackgroundProcess:
    process: subprocess.Popen
    log_path: Path | None
    log_handle: object

    def stop(self, timeout_seconds: int = 5) -> None:
        poll_error: BaseException | None = None
        exit_error: BaseException | None = None

        try:
            try:
                stopped = self.process.poll() is not None
            except BaseException as exc:
                poll_error = exc
                stopped = False
            if not stopped:
                try:
                    self.process.terminate()
                except BaseException:
                    should_kill = True
                else:
                    try:
                        self.process.wait(timeout=timeout_seconds)
                    except BaseException:
                        should_kill = True
                    else:
                        should_kill = False
                if should_kill:
                    try:
                        self.process.kill()
                    except BaseException:
                        pass
                    try:
                        self.process.wait(timeout=timeout_seconds)
                    except BaseException as exc:
                        exit_error = exc
        finally:
            self.log_handle.close()
        if exit_error is not None:
            raise ToolExecutionError(
                f"无法停止后台进程: {exit_error}", log_path=self.log_path
            ) from exit_error
        if poll_error is not None:
            raise poll_error


@dataclass(frozen=True)
class ProcessResult:
    argv: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str
    duration_ms: int
    timed_out: bool
    log_path: Path | None


def _text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    return value


def _redacted_argv(argv: Sequence[str]) -> tuple[str, ...]:
    result: list[str] = []
    redact_next = False
    for item in argv:
        if redact_next:
            result.append("***")
            redact_next = False
        elif item in {"--password", "--token"}:
            result.append(item)
            redact_next = True
        elif item.startswith("--password=") or item.startswith("--token="):
            result.append(item.split("=", 1)[0] + "=***")
        else:
            result.append(item)
    return tuple(result)


def start_process(
    argv: Sequence[str],
    *,
    cwd: Path,
    log_path: Path | None,
    env: Mapping[str, str] | None = None,
) -> BackgroundProcess:
    command = tuple(str(item) for item in argv)
    if not command:
        raise ValueError("argv must not be empty")
    absolute_log: Path | None = None
    if log_path is None:
        # 日志目录不可写（宿主沙箱）：不落盘，输出丢弃，进程本身照常启动
        handle = open(os.devnull, "w", encoding="utf-8")
    else:
        absolute_log = log_path.resolve()
        absolute_log.parent.mkdir(parents=True, exist_ok=True)
        handle = absolute_log.open("w", encoding="utf-8")
        handle.write(f"[command] {subprocess.list2cmdline(_redacted_argv(command))}\n[output]\n")
        handle.flush()
    try:
        process = subprocess.Popen(command, cwd=cwd, stdout=handle, stderr=subprocess.STDOUT, text=True, env=dict(env) if env is not None else None, shell=False)
    except FileNotFoundError:
        handle.close()
        raise
    except OSError as exc:
        handle.close()
        raise ToolExecutionError(f"无法启动外部工具 {command[0]}: {exc}") from exc
    return BackgroundProcess(process, absolute_log, handle)


def run_process(
    argv: Sequence[str],
    *,
    cwd: Path,
    timeout_seconds: int,
    log_path: Path | None,
    env: Mapping[str, str] | None = None,
) -> ProcessResult:
    command = tuple(str(item) for item in argv)
    if not command:
        raise ValueError("argv must not be empty")
    started = time.monotonic()
    timed_out = False
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            timeout=timeout_seconds,
            capture_output=True,
            text=True,
            encoding=console_encoding(),
            errors="replace",
            env=dict(env) if env is not None else None,
            shell=False,
        )
        returncode = completed.returncode
        stdout = completed.stdout
        stderr = completed.stderr
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        returncode = -1
        stdout = _text(exc.stdout)
        stderr = _text(exc.stderr)
    except FileNotFoundError:
        raise
    except OSError as exc:
        raise ToolExecutionError(f"无法执行外部工具 {command[0]}: {exc}") from exc
    duration_ms = round((time.monotonic() - started) * 1000)
    absolute_log: Path | None = None
    if log_path is not None:
        display_command = subprocess.list2cmdline(_redacted_argv(command))
        try:
            absolute_log = log_path.resolve()
            absolute_log.parent.mkdir(parents=True, exist_ok=True)
            absolute_log.write_text(
                f"[command] {display_command}\n[stdout]\n{stdout}\n[stderr]\n{stderr}\n",
                encoding="utf-8",
            )
        except OSError:
            absolute_log = None   # 日志写不进去不影响已完成的执行结论
    return ProcessResult(command, returncode, stdout, stderr, duration_ms, timed_out, absolute_log)
