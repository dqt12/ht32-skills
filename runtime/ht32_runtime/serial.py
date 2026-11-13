"""串口观测能力（ht32 自足化）：端口枚举、限时采集、烧录前预开的长窗口采集、数据发送。

串口观测由此模块自足完成（不经过宿主其他 Skill）：
  * 端口枚举（Windows 注册表 + PnP 设备名，见 environment.enumerate_serial_port_details）
  * 打开 / 关闭、按波特率读写（Windows 走 ctypes 调 Win32 串口 API，纯标准库）
  * 带超时的限时采集，行 / Hex 两种呈现；每批数据带相对/绝对到达时刻
  * 采集期按时间点发送（`--send/--send-at`）、多端口共享时钟、DTR/RTS 脉冲复位
  * 采集产物三件套：`.log`（带时间戳的可读文本）/ `.jsonl`（逐批到达时刻 + hex）/ `.bin`（原始字节）
  * `SerialTap`：**流程开始前就打开端口**并后台持续读取，供 workflow 在烧录期间一并接收
    （目标板复位后立刻打印的启动日志不会丢）；端口中途消失时按上限重开
  * 写数据（文本或 Hex，可追加 CRLF）

数据边界：本模块只把采集原文写到**全局**用户数据目录（logs/serial/），
绝不在用户工程目录写任何文件，也不创建宿主 skill 的私有状态目录。
"""

from __future__ import annotations

import codecs
import ctypes
import json
import os
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from .environment import enumerate_serial_port_details
from .models import OperationResult
from .paths import LOG_UNAVAILABLE

_PORT_PATTERN = re.compile(r"^(?:\\\.\\)?COM\d{1,4}$", re.IGNORECASE)
_PORT_EXTRACT = re.compile(r"COM\d{1,4}", re.IGNORECASE)
_HEX_PATTERN = re.compile(r"^(?:[0-9a-fA-F]{2})+\s*$")

# 观测窗口的默认静默早停秒数：窗口内已有数据、且连续这么久无新数据即提前收工
DEFAULT_SILENCE_SECONDS = 2.0
# 端口中途消失（如 MCU 自带 USB-CDC 复位重新枚举）时的重开次数上限
DEFAULT_MAX_REOPENS = 3

# Win32 常量
_GENERIC_READ = 0x80000000
_GENERIC_WRITE = 0x40000000
_OPEN_EXISTING = 3
_FILE_ATTRIBUTE_NORMAL = 0x80
_INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
_ERROR_FILE_NOT_FOUND = 2
_ERROR_PATH_NOT_FOUND = 3
_ERROR_ACCESS_DENIED = 5
_PURGE_RXABORT = 0x0002
_PURGE_RXCLEAR = 0x0004
_PURGE_TXABORT = 0x0001
_PURGE_TXCLEAR = 0x0008
_NOPARITY = 0
_ONESTOPBIT = 0
_DTR_CONTROL_ENABLE = 1
_RTS_CONTROL_ENABLE = 1
_MAXDWORD = 0xFFFFFFFF
# EscapeCommFunction 的 DTR/RTS 控制码（Win32 常量）
_SETRTS = 3
_CLRRTS = 4
_SETDTR = 5
_CLRDTR = 6


class SerialError(ValueError):
    """串口操作失败（端口不存在 / 被占用 / 权限不足 / 参数非法 / 平台不支持）。"""


@dataclass(frozen=True)
class PortInfo:
    port: str
    name: str


class _DCB(ctypes.Structure):
    _fields_ = [
        ("DCBlength", ctypes.c_ulong),
        ("BaudRate", ctypes.c_ulong),
        ("fBinary", ctypes.c_ulong, 1),
        ("fParity", ctypes.c_ulong, 1),
        ("fOutxCtsFlow", ctypes.c_ulong, 1),
        ("fOutxDsrFlow", ctypes.c_ulong, 1),
        ("fDtrControl", ctypes.c_ulong, 2),
        ("fDsrSensitivity", ctypes.c_ulong, 1),
        ("fTXContinueOnXoff", ctypes.c_ulong, 1),
        ("fOutX", ctypes.c_ulong, 1),
        ("fInX", ctypes.c_ulong, 1),
        ("fErrorChar", ctypes.c_ulong, 1),
        ("fNull", ctypes.c_ulong, 1),
        ("fRtsControl", ctypes.c_ulong, 2),
        ("fAbortOnError", ctypes.c_ulong, 1),
        ("fDummy2", ctypes.c_ulong, 17),
        ("wReserved", ctypes.c_ushort),
        ("XonLim", ctypes.c_ushort),
        ("XoffLim", ctypes.c_ushort),
        ("ByteSize", ctypes.c_byte),
        ("Parity", ctypes.c_byte),
        ("StopBits", ctypes.c_byte),
        ("XonChar", ctypes.c_char),
        ("XoffChar", ctypes.c_char),
        ("ErrorChar", ctypes.c_char),
        ("EofChar", ctypes.c_char),
        ("EvtChar", ctypes.c_char),
        ("wReserved1", ctypes.c_ushort),
    ]


class _COMMTIMEOUTS(ctypes.Structure):
    _fields_ = [
        ("ReadIntervalTimeout", ctypes.c_ulong),
        ("ReadTotalTimeoutMultiplier", ctypes.c_ulong),
        ("ReadTotalTimeoutConstant", ctypes.c_ulong),
        ("WriteTotalTimeoutMultiplier", ctypes.c_ulong),
        ("WriteTotalTimeoutConstant", ctypes.c_ulong),
    ]


# 带 use_last_error 的 kernel32 绑定：只有这样 ctypes.get_last_error() 才可靠
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_CreateFileW = _kernel32.CreateFileW
_CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p]
_CreateFileW.restype = ctypes.c_void_p
_SetCommState = _kernel32.SetCommState
_SetCommState.argtypes = [ctypes.c_void_p, ctypes.POINTER(_DCB)]
_SetCommState.restype = ctypes.c_int
_SetCommTimeouts = _kernel32.SetCommTimeouts
_SetCommTimeouts.argtypes = [ctypes.c_void_p, ctypes.POINTER(_COMMTIMEOUTS)]
_SetCommTimeouts.restype = ctypes.c_int
_SetupComm = _kernel32.SetupComm
_SetupComm.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32]
_SetupComm.restype = ctypes.c_int
_PurgeComm = _kernel32.PurgeComm
_PurgeComm.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
_PurgeComm.restype = ctypes.c_int
_ReadFile = _kernel32.ReadFile
_ReadFile.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32, ctypes.POINTER(ctypes.c_ulong), ctypes.c_void_p]
_ReadFile.restype = ctypes.c_int
_WriteFile = _kernel32.WriteFile
_WriteFile.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32, ctypes.POINTER(ctypes.c_ulong), ctypes.c_void_p]
_WriteFile.restype = ctypes.c_int
_CloseHandle = _kernel32.CloseHandle
_CloseHandle.argtypes = [ctypes.c_void_p]
_CloseHandle.restype = ctypes.c_int
_EscapeCommFunction = _kernel32.EscapeCommFunction
_EscapeCommFunction.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
_EscapeCommFunction.restype = ctypes.c_int


def _iso_ms() -> str:
    """本地时区的 ISO 时间（毫秒精度），用于每批数据的绝对到达时刻。"""
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


def _normalize_port(port: str) -> str:
    text = str(port).strip()
    if not _PORT_PATTERN.match(text):
        raise SerialError(f"串口名不合法: {port!r}（期望形如 COM13）")
    match = _PORT_EXTRACT.search(text)
    return match.group(0).upper()


class WindowsSerialPort:
    """基于 Win32 API 的串口（CreateFileW / SetCommState / ReadFile / WriteFile）。"""

    def __init__(self, port: str, baudrate: int, *, write_timeout_ms: int = 2000):
        self.port = _normalize_port(port)
        self.baudrate = int(baudrate)
        self.write_timeout_ms = write_timeout_ms
        self._handle = None

    def open(self) -> "WindowsSerialPort":
        handle = _CreateFileW(
            rf"\\.\{self.port}",
            _GENERIC_READ | _GENERIC_WRITE,
            0,
            None,
            _OPEN_EXISTING,
            _FILE_ATTRIBUTE_NORMAL,
            None,
        )
        if not handle or handle == _INVALID_HANDLE_VALUE:
            code = ctypes.get_last_error()
            if code in (_ERROR_FILE_NOT_FOUND, _ERROR_PATH_NOT_FOUND):
                raise SerialError(f"串口 {self.port} 不存在：请先用 `serial ports` 确认可用端口")
            if code == _ERROR_ACCESS_DENIED:
                raise SerialError(
                    f"串口 {self.port} 被占用或拒绝访问（Win32 错误 5）："
                    "关闭其他正在使用该串口的程序后重试；若 MCP 的 ComAssistant 正占用该端口，"
                    "先执行 mcp_comassistant_serial_close 再采集"
                )
            raise SerialError(f"打开串口 {self.port} 失败（Win32 错误 {code}）")
        self._handle = handle
        dcb = _DCB()
        dcb.DCBlength = ctypes.sizeof(_DCB)
        dcb.BaudRate = self.baudrate
        dcb.ByteSize = 8
        dcb.Parity = _NOPARITY
        dcb.StopBits = _ONESTOPBIT
        dcb.fDtrControl = _DTR_CONTROL_ENABLE
        dcb.fRtsControl = _RTS_CONTROL_ENABLE
        if not _SetCommState(handle, ctypes.byref(dcb)):
            code = ctypes.get_last_error()
            self.close()
            raise SerialError(f"设置串口参数失败（波特率 {self.baudrate} 可能不被设备支持，Win32 错误 {code}）")
        timeouts = _COMMTIMEOUTS()
        timeouts.ReadIntervalTimeout = _MAXDWORD
        timeouts.ReadTotalTimeoutMultiplier = 0
        timeouts.ReadTotalTimeoutConstant = 0
        timeouts.WriteTotalTimeoutMultiplier = 0
        timeouts.WriteTotalTimeoutConstant = self.write_timeout_ms
        if not _SetCommTimeouts(handle, ctypes.byref(timeouts)):
            self.close()
            raise SerialError(f"设置串口超时参数失败（Win32 错误 {ctypes.get_last_error()}）")
        _SetupComm(handle, 4096, 4096)
        _PurgeComm(handle, _PURGE_RXABORT | _PURGE_RXCLEAR | _PURGE_TXABORT | _PURGE_TXCLEAR)
        return self

    def read(self, size: int) -> bytes:
        if self._handle is None:
            raise SerialError("串口未打开")
        buffer = ctypes.create_string_buffer(size)
        read = ctypes.c_ulong(0)
        if not _ReadFile(self._handle, buffer, size, ctypes.byref(read), None):
            raise SerialError(f"读取串口 {self.port} 失败（Win32 错误 {ctypes.get_last_error()}）")
        return buffer.raw[: read.value]

    def write(self, data: bytes) -> int:
        if self._handle is None:
            raise SerialError("串口未打开")
        written = ctypes.c_ulong(0)
        if not _WriteFile(self._handle, data, len(data), ctypes.byref(written), None):
            raise SerialError(f"写入串口 {self.port} 失败（Win32 错误 {ctypes.get_last_error()}）")
        return written.value

    def pulse_reset(self, *, hold: float = 0.1, sleep=time.sleep) -> bool:
        """DTR/RTS 脉冲复位（best-effort）：拉低 hold 秒后释放。

        只有把 DTR/RTS 接到复位脚的板子（常见 USB-UART 桥）才会真复位；
        没接线时函数仍返回 True 只是「信号已发」，以采集到的启动日志为准。
        """
        if self._handle is None:
            raise SerialError("串口未打开")
        low = bool(_EscapeCommFunction(self._handle, _CLRDTR))
        low = bool(_EscapeCommFunction(self._handle, _CLRRTS)) and low
        sleep(hold)
        high = bool(_EscapeCommFunction(self._handle, _SETDTR))
        high = bool(_EscapeCommFunction(self._handle, _SETRTS)) and high
        return low and high

    def close(self) -> None:
        if self._handle is not None:
            _CloseHandle(self._handle)
            self._handle = None

    def __enter__(self) -> "WindowsSerialPort":
        return self.open()

    def __exit__(self, *exc_info) -> None:
        self.close()


def open_port(port: str, baudrate: int = 115200) -> WindowsSerialPort:
    """打开串口（平台相关）；失败抛出带明确原因的 SerialError。"""
    if os.name != "nt":
        raise SerialError(f"内建串口观测仅支持 Windows（当前平台 {os.name}）；请使用系统自带串口工具")
    return WindowsSerialPort(port, baudrate).open()


def list_port_infos() -> list[PortInfo]:
    """枚举本机串口（端口名 + 设备友好名）。"""
    return [PortInfo(item["port"], item.get("name") or item["port"]) for item in enumerate_serial_port_details()]


def capture_bytes(
    port,
    seconds: float,
    *,
    chunk_size: int = 4096,
    poll_interval: float = 0.02,
    monotonic=time.monotonic,
    sleep=time.sleep,
) -> bytes:
    """在 seconds 秒窗口内持续读取串口，返回原始字节（无数据则返回 b""）。"""
    data, _ = capture_bytes_timed(port, seconds, chunk_size=chunk_size,
                                  poll_interval=poll_interval, monotonic=monotonic, sleep=sleep)
    return data


def capture_bytes_timed(
    port,
    seconds: float,
    *,
    chunk_size: int = 4096,
    poll_interval: float = 0.02,
    monotonic=time.monotonic,
    sleep=time.sleep,
    send_plan: Sequence[tuple[float, bytes]] = (),
) -> tuple[bytes, list[dict[str, object]]]:
    """同 capture_bytes，但记录每批到达时刻（相对开始的毫秒）与字节偏移。

    USB-CDC 常按 ~107 ms 批量刷新，因此时间戳打在其驱动读到数据的时刻，并显式标注；
    不要把它当成固件的逐字节发送时刻。`send_plan` 是 [(相对秒, 字节)]，在同一进程内到点写端口。
    """
    captured = capture_ports_timed([("", port)], seconds, send_plan=send_plan,
                                   chunk_size=chunk_size, poll_interval=poll_interval,
                                   monotonic=monotonic, sleep=sleep)
    return captured[""]


def capture_ports_timed(
    sessions: Sequence[tuple[str, object]],
    seconds: float,
    *,
    chunk_size: int = 4096,
    poll_interval: float = 0.02,
    monotonic=time.monotonic,
    sleep=time.sleep,
    send_plan: Sequence[tuple[float, bytes]] = (),
) -> dict[str, tuple[bytes, list[dict[str, object]]]]:
    """多端口共享时钟采集：同一时刻起算，按轮询依次读每个端口。

    返回 {端口名: (原始字节, 分批元数据)}；元数据含 offset / ms（相对开始）/ at（绝对时刻）/ bytes。
    `send_plan` 只在**第一个**端口上按时间点写入——用于「采集中发激励、看响应」的单口联调。
    """
    ports = [name for name, _ in sessions]
    data: dict[str, bytearray] = {name: bytearray() for name in ports}
    meta: dict[str, list[dict[str, object]]] = {name: [] for name in ports}
    sends = sorted(send_plan or (), key=lambda item: item[0])
    target = sessions[0][1] if sessions else None
    index = 0
    started = monotonic()
    deadline = started + seconds
    last = started
    while True:
        while index < len(sends) and sends[index][0] <= last - started:
            if target is not None:
                target.write(sends[index][1])
            index += 1
        got = False
        read: list[tuple[str, bytes]] = []
        for name, session in sessions:
            chunk = session.read(chunk_size)
            if chunk:
                read.append((name, chunk))
                got = True
        now = monotonic()
        for name, chunk in read:
            meta[name].append({"offset": len(data[name]),
                               "ms": round((now - started) * 1000, 3),
                               "at": _iso_ms(), "bytes": len(chunk)})
            data[name].extend(chunk)
        if now >= deadline:
            break
        if not got:
            sleep(poll_interval)
        last = now
    return {name: (bytes(data[name]), meta[name]) for name in ports}


@dataclass(frozen=True)
class TapReport:
    """一次贯穿式观测的结果：原始字节 + 重开次数 + 采集期错误 + 分块到达时刻。"""

    data: bytes
    reopens: int
    errors: list[str]
    stopped_by: str = "timeout"      # idle=静默提前收工；timeout=采满窗口
    silence_used: float | None = None  # 实际生效的静默阈值（2s 节拍等场景会自适应放大）
    chunks: list[dict[str, object]] = field(default_factory=list)
    reopen_events: list[dict[str, object]] = field(default_factory=list)  # 每次重开时的 ms + 字节偏移


class SerialTap:
    """贯穿一段流程的串口采集：**开始前就 open**，后台线程边跑边读，按窗口 / 静默收工。

    与 `serial log`（同步限时采集，流程之后才开）的区别：本类在流程开始前打开端口并持续读取，
    因此目标板复位（烧录后自动运行）立刻打印的启动日志不会被漏掉——`serial log` 在后才开、
    且 open 时会清接收缓冲，那批字节必然丢。端口中途消失（MCU 自带 USB-CDC 重新枚举、
    板子掉电）时按 `max_reopens` 重开继续采，重开次数记进报告。
    """

    def __init__(
        self,
        port: str,
        baudrate: int = 115200,
        *,
        opener=open_port,
        sleep=time.sleep,
        monotonic=time.monotonic,
        poll_interval: float = 0.02,
        max_reopens: int = DEFAULT_MAX_REOPENS,
    ):
        self.port = _normalize_port(port)
        self.baudrate = int(baudrate)
        self._opener, self._sleep, self._monotonic = opener, sleep, monotonic
        self._poll_interval, self._max_reopens = poll_interval, max_reopens
        self._session = None
        self._thread: threading.Thread | None = None
        self._chunks: list[bytes] = []
        self._errors: list[str] = []
        self._reopens = 0
        self._last_data_at: float | None = None
        self._arrivals: list[float] = []
        self._chunk_meta: list[dict[str, object]] = []
        self._reopen_events: list[dict[str, object]] = []
        self._started_at: float | None = None
        self._stopping = threading.Event()

    def start(self) -> "SerialTap":
        """打开端口并起采集线程；端口不存在 / 被占用时抛 SerialError（由调用方决定降级）。"""
        self._session = self._opener(self.port, self.baudrate)
        self._started_at = self._monotonic()
        self._thread = threading.Thread(target=self._pump, name="ht32-serial-tap", daemon=True)
        self._thread.start()
        return self

    def _pump(self) -> None:
        while not self._stopping.is_set():
            try:
                chunk = self._session.read(4096)
            except SerialError as exc:
                if not self._reopen(exc):
                    break
                continue
            if chunk:
                now = self._monotonic()
                self._chunk_meta.append({
                    "offset": self.bytes_so_far(),
                    "ms": round((now - (self._started_at or now)) * 1000, 3),
                    "at": _iso_ms(),
                    "bytes": len(chunk),
                })
                self._chunks.append(chunk)
                self._last_data_at = now
                self._arrivals.append(now)
                if len(self._arrivals) > 64:
                    del self._arrivals[:32]
            else:
                self._sleep(self._poll_interval)

    def _reopen(self, exc: SerialError) -> bool:
        self._close_session()
        if self._reopens >= self._max_reopens:
            self._errors.append(f"读串口失败且重开已达上限（{self._max_reopens} 次）：{exc}")
            return False
        self._sleep(0.2)
        try:
            self._session = self._opener(self.port, self.baudrate)
        except SerialError as open_exc:
            self._errors.append(f"串口重开失败：{open_exc}")
            return False
        self._reopens += 1
        now = self._monotonic()
        # 复位后 USB-CDC 会重新枚举：这次重开就是「芯片已复位」最可信的时刻
        self._reopen_events.append({
            "ms": round((now - (self._started_at or now)) * 1000, 3),
            "offset": self.bytes_so_far(),
        })
        return True

    def _close_session(self) -> None:
        session, self._session = self._session, None
        if session is not None:
            try:
                session.close()
            except Exception:  # noqa: BLE001 - 关闭失败不应影响观测结论
                pass

    def bytes_so_far(self) -> int:
        """当前已累计收到的字节数（用于标记「烧录完成」在采集流里的偏移）。"""
        return sum(len(chunk) for chunk in self._chunks)

    def elapsed_ms(self) -> float | None:
        """距采集开始的毫秒数；未启动时返回 None（用于定位烧录命令返回时刻）。"""
        if self._started_at is None:
            return None
        return round((self._monotonic() - self._started_at) * 1000, 3)

    def _typical_interval(self) -> float | None:
        """相邻两批数据的典型间隔（中位数）；用于让静默阈值适配目标板的打印节拍。

        固件每 2s 打一行时，固定的 2s 静默阈值会在两次打印之间的缝隙里提前收工。
        """
        gaps = sorted(b - a for a, b in zip(self._arrivals, self._arrivals[1:])
                      if 0.02 < b - a <= 30.0)
        if not gaps:
            return None
        return gaps[len(gaps) // 2]

    def stop(self, *, tail_seconds: float,
             silence_seconds: float = DEFAULT_SILENCE_SECONDS) -> TapReport:
        """流程结束后继续采 `tail_seconds`；已有数据且静默满阈值即提前收工。

        阈值自适应：至少 `silence_seconds`，并放大到「典型打印间隔 × 1.5」（不超过 tail 窗口），
        避免 2s 节拍的固件在两次打印之间被误判为「已静默」。
        """
        stopped_by = "timeout"
        effective = float(silence_seconds)
        typical = self._typical_interval()
        if typical is not None:
            effective = max(effective, min(1.5 * typical, max(effective, float(tail_seconds))))
        if self._thread is not None:
            deadline = self._monotonic() + float(tail_seconds)
            while self._monotonic() < deadline:
                if (self._chunks and self._last_data_at is not None
                        and self._monotonic() - self._last_data_at >= effective):
                    stopped_by = "idle"
                    break
                self._sleep(self._poll_interval)
            self._stopping.set()
            self._thread.join(timeout=2.0)
        self._close_session()
        return TapReport(b"".join(self._chunks), self._reopens, list(self._errors),
                        stopped_by, effective, list(self._chunk_meta),
                        list(self._reopen_events))


def render_lines(data: bytes, encoding: str = "utf-8") -> list[str]:
    """按行呈现（兼容 CRLF / CR / LF；末尾空行剔除）。"""
    text = data.decode(encoding, errors="replace")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")
    while lines and lines[-1] == "":
        lines.pop()
    return lines


def timing_summary(chunks: list[dict[str, object]]) -> dict[str, object]:
    """分块到达统计：首/末字节时刻与典型批量间隔（用于识别 USB-CDC 批效应）。"""
    if not chunks:
        return {"chunk_count": 0, "first_byte_ms": None, "last_byte_ms": None,
                "median_interval_ms": None, "note": "USB-CDC 常按 ~107 ms 批量刷新，"
                                                     "时间戳是驱动读到数据的时刻，不是逐字节发送时刻"}
    times = [float(c["ms"]) for c in chunks]
    gaps = sorted(b - a for a, b in zip(times, times[1:]) if b > a)
    return {
        "chunk_count": len(chunks),
        "first_byte_ms": times[0],
        "last_byte_ms": times[-1],
        "median_interval_ms": round(gaps[len(gaps) // 2], 3) if gaps else None,
        "note": "USB-CDC 常按 ~107 ms 批量刷新，时间戳是驱动读到数据的时刻，不是逐字节发送时刻",
    }


def render_timed_lines(data: bytes, chunks: list[dict[str, object]],
                       encoding: str = "utf-8") -> list[str]:
    """按行呈现并给每行标上"该行完成时所属批次的到达时刻"。"""
    decoder = codecs.getincrementaldecoder(encoding)(errors="replace")
    pending = ""
    out: list[str] = []
    for chunk in chunks:
        offset = int(chunk["offset"])
        size = int(chunk["bytes"])
        text = decoder.decode(data[offset:offset + size]).replace("\r\n", "\n").replace("\r", "\n")
        pending += text
        while "\n" in pending:
            line, pending = pending.split("\n", 1)
            if line:
                out.append(f"[{float(chunk['ms']):.1f}ms] {line}")
    pending += decoder.decode(b"", final=True)
    if pending:
        mark = float(chunks[-1]["ms"]) if chunks else 0.0
        out.append(f"[{mark:.1f}ms] {pending}")
    return out


def render_hex(data: bytes) -> list[str]:
    """Hex dump 呈现（地址 + hex + ASCII）。"""
    rows: list[str] = []
    for offset in range(0, len(data), 16):
        chunk = data[offset : offset + 16]
        hex_part = " ".join(f"{byte:02x}" for byte in chunk)
        ascii_part = "".join(chr(byte) if 32 <= byte < 127 else "." for byte in chunk)
        rows.append(f"{offset:08x}  {hex_part:<47}  |{ascii_part}|")
    return rows


def parse_send_payload(data: str, *, hex_mode: bool, encoding: str = "utf-8") -> bytes:
    """把 --data 参数解析为待写字节（hex 模式要求偶数个 hex 数字）。"""
    text = str(data)
    if hex_mode:
        compact = "".join(text.split())
        if not compact or not _HEX_PATTERN.match(compact):
            raise SerialError("--hex 模式要求 --data 为偶数个 hex 数字（可带空格），如 '48 65 6c 6c 6f'")
        return bytes.fromhex(compact)
    if not text:
        raise SerialError("--data 不能为空")
    return text.encode(encoding, errors="replace")


_MARKER_LABELS = {
    "usb_reenumerate": "target reset detected (USB CDC re-enumerated); below is post-reset output",
    "flash_command_return": "flash command returned; chip reset may lag ~1s, boundary approximate",
}


def _marker_text(source: str | None, offset: int) -> str:
    """烧录分界行文案：按来源区分「真复位」与「烧录命令返回」，不再一律写成 post-reset。"""
    label = _MARKER_LABELS.get(source or "", "flash boundary marker")
    return f"[ht32-skills] ---- {label}; offset={offset} ----"


def _stamp_prefix(chunk: dict[str, object], stamp: str) -> str:
    if stamp == "none":
        return ""
    rel = f"+{float(chunk['ms']):.3f}ms"
    at = str(chunk.get("at") or "")
    clock = at[11:23] if len(at) >= 23 else at or "?"
    if stamp == "rel":
        return f"[{rel}] "
    if stamp == "abs":
        return f"[{clock}] "
    return f"[{clock} {rel}] "


def render_stamped_lines(data: bytes, chunks: list[dict[str, object]],
                         stamp: str = "both", encoding: str = "utf-8", *,
                         marker_at: int | None = None,
                         marker_source: str | None = None) -> list[str]:
    """按行呈现并加时间列（rel=相对毫秒 / abs=墙钟 / both=两者）。

    时间戳是驱动读到该批数据的时刻（USB-CDC 常按 ~107 ms 批量刷新），不是固件逐字节发送时刻。
    `marker_at` / `marker_source` 用于插入烧录分界行，文案按分界来源区分。
    """
    decoder = codecs.getincrementaldecoder(encoding)(errors="replace")
    pending = ""
    out: list[str] = []
    marker_done = False

    def flush(chunk: dict[str, object]) -> None:
        nonlocal pending
        while "\n" in pending:
            line, pending = pending.split("\n", 1)
            if line:
                out.append(_stamp_prefix(chunk, stamp) + line)

    if marker_at is not None and marker_at <= 0:
        out.append(_marker_text(marker_source, 0))
        marker_done = True
    for chunk in chunks:
        offset, size = int(chunk["offset"]), int(chunk["bytes"])
        if not marker_done and marker_at is not None and offset >= marker_at:
            if pending:  # 残行先落盘，别让分界行把一行劈成两半
                out.append(_stamp_prefix(chunk, stamp) + pending)
                pending = ""
            out.append(_marker_text(marker_source, marker_at))
            marker_done = True
        text = decoder.decode(data[offset:offset + size]).replace("\r\n", "\n").replace("\r", "\n")
        pending += text
        flush(chunk)
    pending += decoder.decode(b"", final=True)
    if pending:
        out.append(_stamp_prefix(chunks[-1] if chunks else {"ms": 0.0}, stamp) + pending)
    if marker_at is not None and not marker_done:
        out.append(_marker_text(marker_source, marker_at))
    return out


def analyze_intervals(data: bytes, chunks: list[dict[str, object]], *,
                      marker: str = "#", encoding: str = "utf-8") -> dict[str, object]:
    """统计含 marker 的行之间的到达间隔（count / mean / min / max / jitter）。

    用于「固件按固定节拍打印」的时序判定：先用 marker 圈出心跳行，再看间隔抖动。
    """
    stamped = render_timed_lines(data, chunks, encoding)
    times = [float(item[1:item.index("ms]")]) for item in stamped if marker in item]
    gaps = [round(b - a, 3) for a, b in zip(times, times[1:])]
    if not gaps:
        return {"marker": marker, "matched_lines": len(times), "intervals": 0,
                "mean_ms": None, "min_ms": None, "max_ms": None, "jitter_ms": None,
                "note": "匹配到的行不足 2 行，无法计算间隔"}
    mean = sum(gaps) / len(gaps)
    jitter = (sum((gap - mean) ** 2 for gap in gaps) / len(gaps)) ** 0.5
    return {"marker": marker, "matched_lines": len(times), "intervals": len(gaps),
            "mean_ms": round(mean, 3), "min_ms": min(gaps), "max_ms": max(gaps),
            "jitter_ms": round(jitter, 3),
            "note": "间隔基于驱动读到的批次到达时刻，USB-CDC 批效应会引入 ~107 ms 粒度"}


def write_capture_bundle(logs_dir: Path, port: str, baudrate: int, seconds: float,
                         data: bytes, chunks: list[dict[str, object]], *,
                         stamp: str = "both", encoding: str = "utf-8",
                         marker_at: int | None = None,
                         marker_source: str | None = None) -> dict[str, Path] | None:
    """落盘三件套：`.log`（带时间戳的可读文本）/ `.jsonl`（逐批时刻 + hex）/ `.bin`（原始字节）。

    日志是辅助产物：全局目录被宿主沙箱拦住时返回 None，采集结果本身照常从 JSON 返回。
    `marker_at` / `marker_source`：在采集流的该字节偏移处插入烧录分界行（先开后烧的观测用），
    并注明分界依据是「USB 重新枚举（真复位）」还是「烧录命令返回（近似）」。
    """
    unique = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ") + "-" + uuid.uuid4().hex[:8]
    paths = {"log": logs_dir / f"serial-log-{unique}.log",
             "jsonl": logs_dir / f"serial-log-{unique}.jsonl",
             "bin": logs_dir / f"serial-log-{unique}.bin"}
    if stamp != "none" and not chunks:
        stamp = "none"   # 没有分批元数据时无法打时间列，退回原始字节
    header_lines = [f"[port] {port}", f"[baudrate] {baudrate}", f"[seconds] {seconds}",
                    f"[captured_at] {_iso_ms()}", f"[bytes] {len(data)}", f"[stamp] {stamp}"]
    if marker_at is not None:
        header_lines.append(f"[post_flash_offset] {marker_at}")
    if marker_source:
        header_lines.append(f"[post_flash_offset_source] {marker_source}")
    if stamp == "none":
        split = marker_at if marker_at is not None and 0 < marker_at < len(data) else None
        if split is None:
            body = data
        else:
            body = (data[:split] + b"\r\n" + _marker_text(marker_source, marker_at).encode("utf-8")
                    + b"\r\n" + data[split:])
    else:
        lines = render_stamped_lines(data, chunks, stamp, encoding,
                                     marker_at=marker_at, marker_source=marker_source)
        body = ("\n".join(lines) + "\n").encode("utf-8") if lines else b""
    try:
        logs_dir.mkdir(parents=True, exist_ok=True)
        with paths["log"].open("wb") as handle:
            handle.write("\n".join(header_lines).encode("utf-8") + b"\n[data]\n")
            handle.write(body)
        with paths["jsonl"].open("w", encoding="utf-8", newline="\n") as handle:
            for chunk in chunks:
                offset, size = int(chunk["offset"]), int(chunk["bytes"])
                handle.write(json.dumps({
                    "offset": offset, "bytes": size,
                    "t_rel_ms": float(chunk["ms"]),
                    "t_abs": chunk.get("at"),
                    "hex": data[offset:offset + size].hex(),
                }, ensure_ascii=False) + "\n")
        paths["bin"].write_bytes(data)
        return paths
    except OSError:
        for path in paths.values():
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        return None


def write_capture_log(logs_dir: Path, port: str, baudrate: int, seconds: float, data: bytes,
                      *, marker_at: int | None = None, marker_source: str | None = None,
                      chunks: list[dict[str, object]] | None = None,
                      stamp: str = "none") -> Path | None:
    """只取三件套里的 `.log`（保留给调用方；新代码请直接用 write_capture_bundle）。"""
    bundle = write_capture_bundle(logs_dir, port, baudrate, seconds, data, list(chunks or []),
                                  stamp=stamp, marker_at=marker_at, marker_source=marker_source)
    return None if bundle is None else bundle["log"]


def run_ports(project_id: str, *, lister=list_port_infos) -> OperationResult:
    """serial ports：列出本机串口。"""
    result = OperationResult.start("serial-ports", "serial", project_id)
    ports = lister()
    result.observations["ports"] = [{"port": item.port, "name": item.name} for item in ports]
    result.observations["count"] = len(ports)
    if not ports:
        result.warnings.append("未发现串口（COM）设备：无法观测目标板日志（检查 USB 转串口驱动与连线）")
        return result.finish("warn")
    return result.finish("ok")


def parse_port_list(port: str) -> list[str]:
    """解析 `--port`：单个名字或逗号分隔的多口（共享时钟采集）。"""
    ports: list[str] = []
    for item in str(port).split(","):
        if not item.strip():
            continue
        normalized = _normalize_port(item)
        if normalized in ports:
            raise SerialError(f"--port 里重复出现 {normalized}")
        ports.append(normalized)
    if not ports:
        raise SerialError("--port 不能为空")
    return ports


def run_log(
    project_id: str,
    *,
    port: str,
    baud: int,
    seconds: float,
    hex_mode: bool = False,
    encoding: str = "utf-8",
    logs_dir: Path,
    stamp: str = "both",
    send_plan: Sequence[tuple[float, bytes]] = (),
    analyze: str | None = None,
    marker: str = "#",
    reset: bool = False,
    opener=open_port,
    monotonic=time.monotonic,
    sleep=time.sleep,
) -> OperationResult:
    """serial log：限时采集（可多口共享时钟），行 / Hex 呈现，落盘 .log/.jsonl/.bin。

    `send_plan` 用于同一进程里边采边发（[(相对秒, 字节)]，发给第一个端口）；
    `analyze="intervals"` 统计含 `marker` 的行之间的到达间隔；`reset` 在采集前打 DTR/RTS 脉冲。
    """
    result = OperationResult.start("serial-log", "serial", project_id)
    ports = parse_port_list(port)
    sessions: list[tuple[str, object]] = []
    try:
        for name in ports:
            sessions.append((name, opener(name, baud)))
        if reset:
            applied = [bool(getattr(session, "pulse_reset", lambda **_kw: False)(sleep=sleep))
                       for _, session in sessions]
            result.observations["reset"] = {"method": "dtr-rts-pulse", "applied": all(applied)}
            if not all(applied):
                result.warnings.append(
                    "DTR/RTS 复位脉冲未能发出（端口不支持该控制）：请手动复位，或确认板子把 DTR/RTS 接到复位脚")
            sleep(0.05)
        captures = capture_ports_timed(sessions, seconds, send_plan=send_plan,
                                       monotonic=monotonic, sleep=sleep)
    finally:
        for _, session in sessions:
            session.close()
    if send_plan:
        result.observations["sends"] = [{"at_seconds": at, "bytes": len(payload)}
                                       for at, payload in send_plan]

    data, chunks = captures[ports[0]]
    bundles: dict[str, dict[str, Path]] = {}
    for name in ports:
        bundle = write_capture_bundle(logs_dir, name, baud, seconds, *captures[name],
                                      stamp=stamp, encoding=encoding)
        if bundle is not None:
            bundles[name] = bundle
    if bundles:
        primary = bundles[ports[0]]
        result.artifacts["serial_log"] = str(primary["log"])
        result.artifacts["serial_jsonl"] = str(primary["jsonl"])
        result.artifacts["serial_bin"] = str(primary["bin"])
        for name, bundle in bundles.items():
            if len(ports) > 1:
                result.artifacts[f"serial_log_{name}"] = str(bundle["log"])
    else:
        result.warnings.append(LOG_UNAVAILABLE.format(path=logs_dir))
    result.observations.update({
        "port": ",".join(ports) if len(ports) > 1 else ports[0],
        "ports": ports,
        "baudrate": baud,
        "seconds": seconds,
        "encoding": encoding,
        "stamp": stamp,
        "bytes_received": len(data),
        "chunks": chunks,
        "timing": timing_summary(chunks),
    })
    if hex_mode:
        result.observations["hex"] = render_hex(data)
    else:
        result.observations["lines"] = render_lines(data, encoding)
        result.observations["lines_with_time"] = render_timed_lines(data, chunks, encoding)
    if len(ports) > 1:
        result.observations["per_port"] = {
            name: {"bytes_received": len(captured), "chunks": meta,
                   "timing": timing_summary(meta),
                   "lines": render_lines(captured, encoding)}
            for name, (captured, meta) in captures.items()
        }
    if analyze == "intervals":
        stats = analyze_intervals(data, chunks, marker=marker, encoding=encoding)
        result.observations["analyze"] = {"intervals": stats}
        if int(stats["intervals"]) < 1:
            result.warnings.append(
                f"analyze intervals 在 {ports[0]} 上只匹配到 {stats['matched_lines']} 行含 {marker!r}，"
                "不足 2 行算不出间隔：确认 --marker 与固件打印内容一致")
    empty = [name for name, (captured, _) in captures.items() if not captured]
    for name in empty:
        result.warnings.append(
            f"采集窗口（{seconds}s）内未从 {name} 收到任何数据："
            "检查 TX/RX 接线、波特率是否与固件一致、目标板是否在运行"
        )
    return result.finish("warn" if result.warnings else "ok")


def run_send(
    project_id: str,
    *,
    port: str,
    baud: int,
    data: str,
    hex_mode: bool = False,
    newline: bool = False,
    encoding: str = "utf-8",
    opener=open_port,
) -> OperationResult:
    """serial send：向串口写数据（文本或 Hex，可追加 CRLF）。"""
    result = OperationResult.start("serial-send", "serial", project_id)
    normalized = _normalize_port(port)
    payload = parse_send_payload(data, hex_mode=hex_mode, encoding=encoding)
    if newline:
        payload += b"\r\n"
    session = opener(port, baud)
    try:
        written = session.write(payload)
    finally:
        session.close()
    result.observations.update({
        "port": normalized,
        "baudrate": baud,
        "bytes_written": written,
        "hex_mode": hex_mode,
        "newline": newline,
    })
    if written != len(payload):
        result.warnings.append(f"写入不完整：期望 {len(payload)} 字节，实际 {written} 字节")
        return result.finish("warn")
    return result.finish("ok")
