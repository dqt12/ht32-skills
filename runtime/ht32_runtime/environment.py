from __future__ import annotations

import glob
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

from .process import console_encoding
from .version import skill_version


def _existing(configured: str | None, names: tuple[str, ...]) -> str | None:
    if configured and Path(configured).is_file():
        return str(Path(configured).resolve())
    for name in names:
        found = shutil.which(name)
        if found:
            return found
    return None


def find_uv4(configured: str | None) -> str | None:
    return _existing(configured, ("UV4.exe",))


def _windows_file_version(path: str) -> str | None:
    """读 PE 文件版本（纯标准库 ctypes）；非 Windows 或读取失败返回 None。"""
    if os.name != "nt":
        return None
    try:
        import ctypes

        size = ctypes.windll.version.GetFileVersionInfoSizeW(str(path), None)
        if not size:
            return None
        buffer = ctypes.create_string_buffer(size)
        if not ctypes.windll.version.GetFileVersionInfoW(str(path), 0, size, buffer):
            return None
        pointer = ctypes.c_void_p()
        length = ctypes.c_uint()
        if not ctypes.windll.version.VerQueryValueW(
                buffer, "\\", ctypes.byref(pointer), ctypes.byref(length)):
            return None

        class FixedFileInfo(ctypes.Structure):
            _fields_ = [
                ("dwSignature", ctypes.c_uint32), ("dwStrucVersion", ctypes.c_uint32),
                ("dwFileVersionMS", ctypes.c_uint32), ("dwFileVersionLS", ctypes.c_uint32),
                ("dwProductVersionMS", ctypes.c_uint32), ("dwProductVersionLS", ctypes.c_uint32),
                ("dwFileFlagsMask", ctypes.c_uint32), ("dwFileFlags", ctypes.c_uint32),
                ("dwFileOS", ctypes.c_uint32), ("dwFileType", ctypes.c_uint32),
                ("dwFileSubtype", ctypes.c_uint32), ("dwFileDateMS", ctypes.c_uint32),
                ("dwFileDateLS", ctypes.c_uint32),
            ]

        info = ctypes.cast(pointer, ctypes.POINTER(FixedFileInfo)).contents
        return (f"{info.dwFileVersionMS >> 16}.{info.dwFileVersionMS & 0xFFFF}."
                f"{info.dwFileVersionLS >> 16}.{info.dwFileVersionLS & 0xFFFF}")
    except (OSError, AttributeError, ValueError):
        return None


def uv4_info(uv4: str | None) -> dict[str, object]:
    """UV4 可用性 + 文件版本（project-context 第 7 节的 keil_version 来源）。"""
    return {"available": uv4 is not None, "path": uv4,
            "version": _windows_file_version(uv4) if uv4 else None}


def armcc_info(uv4: str | None) -> dict[str, object]:
    """定位 UV4 同目录下的 ARMCC 并取版本；取不到时给出原因（不编造）。"""
    armcc = None
    if uv4:
        root = Path(uv4).resolve().parent.parent
        candidate = root / "ARM" / "ARMCC" / "bin" / "armcc.exe"
        if candidate.is_file():
            armcc = str(candidate)
    if not armcc:
        return {"available": False, "path": None, "version": None,
                "reason": "未找到 <Keil>/ARM/ARMCC/bin/armcc.exe"}
    for flag in ("--vsn", "--version"):
        try:
            completed = subprocess.run([armcc, flag], capture_output=True, text=True,
                                       encoding=console_encoding(), errors="replace",
                                       timeout=10, shell=False)
        except (OSError, subprocess.TimeoutExpired):
            continue
        text = (completed.stdout or "") + (completed.stderr or "")
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        line = next((ln for ln in lines if "ARM Compiler" in ln), None)
        if line is None and lines:
            line = lines[0]
        if line:
            return {"available": True, "path": armcc,
                    "version": re.sub(r"^(Component|Product):\s*", "", line),
                    "reason": None}
    return {"available": False, "path": armcc, "version": None,
            "reason": "armcc 未返回版本（--vsn/--version 均无输出）"}


def find_jlink_tools(tools: dict[str, str | None]) -> dict[str, str | None]:
    return {
        "commander": _existing(tools.get("jlink_exe"), ("JLink.exe",)),
        "gdb_server": _existing(tools.get("jlink_gdb_server_exe"), ("JLinkGDBServerCL.exe", "JLinkGDBServerCLExe")),
        "rtt_client": _existing(tools.get("jlink_rtt_client_exe"), ("JLinkRTTClient.exe",)),
    }


def find_ht32_dfp() -> list[str]:
    roots = [Path(os.environ.get("LOCALAPPDATA", "")) / "Arm" / "Packs" / "Holtek", Path("C:/Keil_v5/ARM/PACK/Holtek")]
    return [str(path) for root in roots if root.is_dir() for path in root.glob("HT32*DFP*")]


VERSION_ONLY_RE = re.compile(r"^\d+(?:\.\d+){1,3}$")
# 只锚定结尾（配 search）：允许 <Vendor>. 前缀，取名称里最后一个 *DFP（+可选版本）
DFP_NAME_RE = re.compile(r"(?P<pack>[A-Za-z0-9_]*DFP)(?:[.\-_]?(?P<version>\d+(?:\.\d+){1,3}))?$")
PDSC_RELEASE_RE = re.compile(r"<release[^>]*\bversion=\"(?P<version>\d+(?:\.\d+){1,3})\"", re.I)


def _dfp_version_from_dir(p: Path) -> str:
    """Keil 展开目录 `<Pack>/` 本身不带版本：取版本子目录（1.0.78）或 pdsc 的 release 版本。"""
    try:
        versions = [c.name for c in p.iterdir() if c.is_dir() and VERSION_ONLY_RE.match(c.name)]
    except OSError:
        versions = []
    if versions:
        return max(versions, key=_version_key)
    try:
        pdsc = next(iter(p.glob("*.pdsc")), None)
        if pdsc is not None:
            m = PDSC_RELEASE_RE.search(pdsc.read_text(encoding="utf-8", errors="replace"))
            if m:
                return m.group("version")
    except OSError:
        pass
    return ""


def parse_dfp_pack(path: str) -> dict[str, str]:
    """把 Keil pack 目录/文件名解析为 {pack, version}（拿不到版本则 version=""）。

    兼容四种形态：Keil 展开目录 `<...>/Holtek/<Pack>/<x.y.z>`、
    展开目录但版本在子目录里 `<...>/Holtek/<Pack>/1.0.78/`（本机 Keil 的 DFP 就是这种）、
    版本嵌在名称里 `<...>/Holtek/<Pack>.<x.y.z>`、以及 `.Download` 缓存文件
    `<Vendor>.<Pack>.<x.y.z>.pack`（去掉 vendor 前缀与 .pack 后缀）。
    """
    p = Path(path)
    name, version = p.name, ""
    if VERSION_ONLY_RE.match(name) and p.parent.name:
        name, version = p.parent.name, name
    name = re.sub(r"\.pack$", "", name, flags=re.I)
    m = DFP_NAME_RE.search(name)          # search：允许 <Vendor>. 前缀
    if not m:
        return {"pack": name, "version": version}
    version = version or (m.group("version") or "")
    if not version and p.is_dir():
        version = _dfp_version_from_dir(p)
    return {"pack": m.group("pack"), "version": version}


def _version_key(text: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", text or "")[:3])


def dfp_name(pack_id: str) -> str:
    """从 pack id 取纯 DFP 名：`Holtek.HT32F490x1_DFP.1.0.1` → `HT32F490x1_DFP`。

    工程 manifest 的 `pack.id` 带 vendor 前缀、甚至把版本号也缀在名字后面，
    而 Keil 安装目录只有 `<Vendor>/<Pack>`；直接按第一个点切会留下
    `HT32F490x1_DFP.1.0.1`，和目录名 `HT32F490x1_DFP` 永远比不上（已装也报 missing）。
    """
    text = re.sub(r"\.pack$", "", str(pack_id or "").strip(), flags=re.I)
    m = DFP_NAME_RE.search(text)
    return m.group("pack") if m else text


def dfp_requirement_status(installed: list[dict[str, str]],
                           requirement: dict[str, str] | None) -> dict[str, str] | None:
    """已装 DFP 是否满足工程要求（用户口径 2026-10-18：同版本或更高即可）。

    status：ok / below-minimum（装了但低于要求）/ missing（没装）。
    """
    if not requirement:
        return None
    pack = str(requirement.get("pack") or "")
    name = dfp_name(pack)
    minimum = str(requirement.get("min_version") or "")
    matched = [i for i in installed
               if i.get("version") and dfp_name(i.get("pack", "")).lower() == name.lower()]
    best = max(matched, key=lambda i: _version_key(i["version"]), default=None)
    if best is None:
        status = "missing"
    elif minimum and _version_key(best["version"]) < _version_key(minimum):
        status = "below-minimum"
    else:
        status = "ok"
    return {"pack": pack, "name": name, "min_version": minimum, "status": status,
            "installed": best["version"] if best else "",
            "bundled": str(requirement.get("bundled_file") or ""),
            "note": "同版本或更高均可；安装包随工程提供（见工程 README「前置条件」）"}


def read_dfp_requirement(project) -> dict[str, str] | None:
    """从工程 manifest/project-lock.json 读随包 DFP 要求（pack / 最低版本 / 随包文件）。"""
    if not project:
        return None
    lock = Path(project) / "manifest" / "project-lock.json"
    try:
        data = json.loads(lock.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    pack = data.get("pack") or {}
    requires = data.get("requires") or {}
    pack_id = pack.get("id") or requires.get("pack") or ""
    if not pack_id:
        return None
    return {"pack": pack_id,
            "min_version": pack.get("min_version") or requires.get("pack_min_version") or "",
            "bundled_file": pack.get("file") or ""}


# Holtek 自家调试器（e-Link32 Pro / e-Link 的 CMSIS-DAP 接口）。
# VID_04D9 是合泰通用 VID（键鼠等外设也用），故按「VID+PID」精确匹配，
# 避免把合泰键鼠误判成调试器；这两个 PID 的 WinUSB 接口在 Windows 里
# 显示为 "Holtek(WinUSB) CMSIS-DAP"，但 HID 固件模式下接口名是通用的
# "USB 输入设备 / HID-compliant vendor-defined device + USB 串行设备"。
HOLTEK_DAP_IDS = ("vid_04d9&pid_802f", "vid_04d9&pid_807c")

_MARKERS = ("cmsis-dap", "daplink", "dap-link", "e-link", "ulink",
            "vid_0d28", "vid_c251", "vid_1241") + HOLTEK_DAP_IDS


def _classify_cmsis_dap(descriptions) -> dict[str, list[str]]:
    confirmed: list[str] = []
    suspected: list[str] = []
    for description in descriptions:
        lowered = description.lower()
        if any(marker in lowered for marker in _MARKERS):
            confirmed.append(description)
        elif re.search(r"(?:^|[^a-z])dap(?:[^a-z]|$)", lowered):
            suspected.append(description)
    return {"confirmed": confirmed, "suspected": suspected}


def _decode_console_bytes(raw: bytes) -> str:
    """子进程控制台输出解码：UTF-8 优先，退回 ANSI（中文 Windows 的 mbcs/GBK）。

    PowerShell 5.1 重定向到管道时，输出编码取决于宿主控制台状态（本机实测 UTF-8，
    旧环境可能是 GBK）；只有按字节收、两种编码都试，才不会把「串行设备」解成
    「涓茶尪」或替换字符。
    """
    for encoding in ("utf-8-sig", "mbcs"):
        try:
            return raw.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


def _powershell_pnp_instances() -> list[dict[str, object]]:
    command = [
        "powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
        "Get-CimInstance Win32_PnPEntity | Select-Object Name,FriendlyName,PNPDeviceID | ConvertTo-Json -Compress",
    ]
    completed = subprocess.run(command, capture_output=True, timeout=10, shell=False)
    if completed.returncode != 0 or not completed.stdout or not completed.stdout.strip():
        return []
    payload = json.loads(_decode_console_bytes(completed.stdout))
    return payload if isinstance(payload, list) else [payload]


def _registry_usb_descriptions(registry) -> list[str]:
    root = registry.OpenKey(registry.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Enum\USB")
    descriptions: list[str] = []

    def visit(key, path: str) -> None:
        texts = [path]
        child_count, value_count, *_ = registry.QueryInfoKey(key)
        for index in range(value_count):
            name, value, *_ = registry.EnumValue(key, index)
            texts.extend((str(name), str(value)))
        if texts[1:] or child_count == 0:
            descriptions.append(" | ".join(texts))
        for index in range(child_count):
            name = registry.EnumKey(key, index)
            visit(registry.OpenKey(key, name), path + "\\" + name)

    visit(root, r"USB")
    return descriptions


def enumerate_cmsis_dap(*, pnp_provider=None, registry=None, platform: str | None = None) -> dict[str, list[str]]:
    if (platform or os.name) != "nt":
        return {"confirmed": [], "suspected": []}
    if pnp_provider is not None:
        instances = pnp_provider()
    elif registry is None:
        try:
            instances = _powershell_pnp_instances()
        except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
            instances = []
    else:
        instances = []
    if instances:
        descriptions = [
            " | ".join(f"{key}={item.get(key)}" for key in ("Name", "FriendlyName", "PNPDeviceID") if item.get(key))
            for item in instances
        ]
        return _classify_cmsis_dap(descriptions)
    try:
        if registry is None:
            import winreg as registry
        return _classify_cmsis_dap(_registry_usb_descriptions(registry))
    except OSError:
        return {"confirmed": [], "suspected": []}


def enumerate_serial_ports() -> list[str]:
    if os.name != "nt":
        return glob.glob("/dev/tty*")
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DEVICEMAP\SERIALCOMM")
        return [winreg.EnumValue(key, index)[1] for index in range(winreg.QueryInfoKey(key)[1])]
    except OSError:
        return []


def _registry_serial_descriptions(registry) -> dict[str, str]:
    """从注册表读 COM 口友好名（Unicode 原生，完全不经过控制台代码页）。

    路径：HKLM\\SYSTEM\\CurrentControlSet\\Enum\\USB\\<instance>\\Device Parameters\\PortName
    友好名优先 FriendlyName，其次 DeviceDesc（`@inf,%..%` 形式的占位描述跳过）。
    """
    try:
        root = registry.OpenKey(registry.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Enum\USB")
    except OSError:
        return {}

    def value(key, name: str) -> str | None:
        try:
            return str(registry.QueryValueEx(key, name)[0])
        except OSError:
            return None

    names: dict[str, str] = {}

    def visit(key, depth: int) -> None:
        friendly = value(key, "FriendlyName")
        if not friendly or friendly.startswith("@"):
            device_desc = value(key, "DeviceDesc") or ""
            friendly = device_desc.split(";", 1)[-1].strip() or None
        try:
            params = registry.OpenKey(key, "Device Parameters")
        except OSError:
            params = None
        if params is not None:
            port = value(params, "PortName")
            if port and friendly and re.fullmatch(r"COM\d+", port, re.I):
                names.setdefault(port.upper(), friendly)
        if depth >= 6:
            return
        try:
            child_count = registry.QueryInfoKey(key)[0]
        except OSError:
            return
        for index in range(child_count):
            try:
                visit(registry.OpenKey(key, registry.EnumKey(key, index)), depth + 1)
            except OSError:
                continue

    visit(root, 0)
    return names


def _serial_port_descriptions() -> dict[str, str]:
    """COM 口号 -> PnP 设备名（尽力而为：查不到时返回空 dict，调用方用端口名兜底）。"""
    if os.name != "nt":
        return {}
    descriptions: dict[str, str] = {}
    try:
        import winreg
        descriptions.update(_registry_serial_descriptions(winreg))
    except (ImportError, OSError):
        pass
    try:
        instances = _powershell_pnp_instances()
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return descriptions
    for item in instances:
        if not isinstance(item, dict):
            continue
        name = item.get("Name") or item.get("FriendlyName") or ""
        match = re.search(r"\((COM\d+)\)", str(name))
        if match:
            descriptions.setdefault(match.group(1), str(name))
    return descriptions


def enumerate_serial_port_details() -> list[dict[str, str]]:
    """串口清单：[{port, name}]；name 为设备友好名，查不到时回落为端口名本身。"""
    descriptions = _serial_port_descriptions()
    return [{"port": port, "name": descriptions.get(port, port)} for port in sorted(enumerate_serial_ports())]


def enumerate_jlink_probes(jlink_exe: str | None) -> list[str]:
    if not jlink_exe:
        return []
    try:
        completed = subprocess.run([jlink_exe, "-USBList"], capture_output=True, text=True,
                                   encoding=console_encoding(), errors="replace",
                                   timeout=10, shell=False)
    except (OSError, subprocess.TimeoutExpired):
        return []
    return re.findall(r"(?:S/N|Serial number)\s*:\s*(\d+)", completed.stdout, re.I)


def find_clang_format() -> dict[str, object]:
    """定位 clang-format（写码后的格式校验依赖它）：PATH → LLVM 默认目录。

    取不到版本就留空，不编造；缺失只影响格式校验，不阻断构建 / 烧录。
    """
    candidates: list[str] = []
    found = shutil.which("clang-format")
    if found:
        candidates.append(found)
    for base in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)")):
        if base:
            candidates.append(str(Path(base) / "LLVM" / "bin" / "clang-format.exe"))
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            version = None
            try:
                completed = subprocess.run([candidate, "--version"], capture_output=True,
                                           text=True, timeout=10, errors="replace")
                version = (completed.stdout or "").strip() or None
            except (OSError, subprocess.SubprocessError):
                version = None
            return {"available": True, "path": candidate, "version": version}
    return {"available": False, "path": None, "version": None,
            "reason": "未找到 clang-format（PATH 与 LLVM 默认目录）"}


def check_environment(tools: dict[str, str | None],
                      requirement: dict[str, str] | None = None) -> dict[str, object]:
    uv4 = find_uv4(tools.get("uv4_exe")); jlink = find_jlink_tools(tools); dfp = find_ht32_dfp(); cmsis = enumerate_cmsis_dap(); probes = enumerate_jlink_probes(jlink["commander"]); serial = enumerate_serial_ports()
    dfp_packs = [parse_dfp_pack(p) for p in dfp]
    version, version_source = skill_version()
    return {
        "python": {"available": True},
        "skill": {"version": version, "source": version_source},
        "uv4": uv4_info(uv4),
        "armcc": armcc_info(uv4),
        "dfp": {"available": bool(dfp), "items": dfp, "packs": dfp_packs,
                "requirement": dfp_requirement_status(dfp_packs, requirement)},
        "cmsis_dap": {"available": bool(cmsis["confirmed"]), "items": cmsis["confirmed"], "suspected": cmsis["suspected"]},
        "jlink_tools": {"available": bool(jlink["commander"] and jlink["gdb_server"]), "items": jlink},
        "jlink_probes": {"available": bool(probes), "items": probes},
        "serial_ports": {"available": bool(serial), "items": serial},
        "clang_format": find_clang_format(),
    }
