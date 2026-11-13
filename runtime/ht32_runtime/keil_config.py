"""Keil 工程调试器配置的读与写（Options for Target → Debug 的落盘形态）。

调试器选择在 Keil 工程里同时落在两个文件：

- ``.uvprojx``：``<DebugOption><TargetDlls><Driver>``（调试驱动）与
  ``<Utilities><Flash2>``（烧录驱动）
- ``.uvoptx``：``<DebugOpt><pMon>``（调试 DLL）与 ``<TargetDriverDllRegistry>`` 里
  对应驱动的参数串（探针序列号 ``-U``、速率 ``-TC``、Flash 算法 ``-FO/-FF/-FS/-FL/-FP``）

本模块只做**定向改写**（正则命中这几处后替换），不重排整个 XML——uvoptx 由 uVision
生成，整体重写容易把用户其它选项弄乱。改写前自动留 ``.bak-<时间戳>`` 备份。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import re
import shutil

import xml.etree.ElementTree as ET


class KeilConfigError(Exception):
    """工程配置不满足改写前提（缺 uvoptx / 缺可复用调试参数 / 驱动不可用）。"""


@dataclass(frozen=True)
class Driver:
    name: str
    label: str
    pmon: str            # uvoptx <pMon> / uvprojx <Driver>、<Flash2>
    key: str             # TargetDriverDllRegistry 里的 <Key>
    dll_names: tuple[str, ...]
    head: str            # 驱动专属参数前缀（到 -N00(...) 之前）
    any_probe: str       # 不指定序列号时驱动接受的占位（CMSIS-DAP 用 -UAny，J-Link 留空）


DRIVERS: dict[str, Driver] = {
    "cmsis-dap": Driver(
        name="cmsis-dap",
        label="CMSIS-DAP（e-Link32 / e-Link32 Pro / 其它 CMSIS-DAP 探针）",
        pmon=r"BIN\CMSIS_AGDI.dll",
        key="CMSIS_AGDI",
        dll_names=("CMSIS_AGDI.dll",),
        head='-X"Any" -UAny -O206 -S0 -C0 -P00',
        any_probe="-UAny",
    ),
    "jlink": Driver(
        name="jlink",
        label="J-Link（Keil 的 J-LINK / J-TRACE Cortex 驱动）",
        pmon=r"BIN\JL2CM3.dll",
        key="JL2CM3",
        dll_names=("JL2CM3.dll",),
        head="-O78 -S0 -A0 -C0 -JU1 -JI127.0.0.1 -JP0 -RST0",
        any_probe="",
    ),
}

# 用户口径 -> 规范名（elink32 就是 CMSIS-DAP，不单列驱动）
ALIASES = {
    "cmsis-dap": "cmsis-dap", "cmsis_dap": "cmsis-dap", "cmsisdap": "cmsis-dap",
    "cmsis": "cmsis-dap", "elink": "cmsis-dap", "e-link": "cmsis-dap",
    "elink32": "cmsis-dap", "e-link32": "cmsis-dap", "e-link32-pro": "cmsis-dap",
    "jlink": "jlink", "j-link": "jlink", "segger": "jlink",
}

_N00_RE = re.compile(r"-N00\(")
_TC_RE = re.compile(r"-TC(\d+)")
_U_RE = re.compile(r"-U(\S+)")
_PMON_RE = re.compile(r"<pMon>.*?</pMon>", re.S)
_FLASH2_RE = re.compile(r"<Flash2>.*?</Flash2>", re.S)
_DRIVER_RE = re.compile(r"<Driver>.*?</Driver>", re.S)
_REGISTRY_RE = re.compile(r"<TargetDriverDllRegistry>.*?</TargetDriverDllRegistry>", re.S)


def normalize(name: str) -> str:
    key = "-".join(name.strip().lower().replace("_", "-").split())
    if key in ALIASES:
        return ALIASES[key]
    # 宽松匹配：用户常说「J-Link」「SEGGER 那个 JLink」「e-Link32 Pro 仿真器」
    if "jlink" in key or "j-link" in key or "segger" in key:
        return "jlink"
    if "cmsis" in key or "elink" in key or "e-link" in key or "htlink" in key:
        return "cmsis-dap"
    raise KeilConfigError(f"未知调试器 {name!r}；可选 cmsis-dap（含 e-Link32 Pro）或 jlink")


def _read(path: Path) -> tuple[str, bool]:
    raw = path.read_bytes()
    bom = raw.startswith(b"\xef\xbb\xbf")
    text = raw.decode("utf-8-sig")
    return text, bom


def _write(path: Path, text: str, bom: bool) -> None:
    data = text.encode("utf-8")
    if bom:
        data = b"\xef\xbb\xbf" + data
    path.write_bytes(data)


def uvoptx_for(uvprojx: Path) -> Path:
    return uvprojx.with_suffix(".uvoptx")


def _registry_name(text: str, key: str) -> str:
    """取 uvoptx 里某个驱动的参数串（<SetRegEntry> ... <Key>K</Key> ... <Name>N</Name>）。"""
    pattern = re.compile(
        r"<SetRegEntry>\s*<Number>\d+</Number>\s*<Key>" + re.escape(key) + r"</Key>\s*<Name>(.*?)</Name>",
        re.S)
    match = pattern.search(text)
    return match.group(1) if match else ""


def _first_registry_name(text: str) -> tuple[str, str]:
    """任一驱动的参数串（用来复用 Flash 算法与 SWD 目标参数）。"""
    match = re.search(
        r"<SetRegEntry>\s*<Number>\d+</Number>\s*<Key>(.*?)</Key>\s*<Name>(.*?)</Name>", text, re.S)
    return (match.group(1), match.group(2)) if match else ("", "")


def _parse_info(project_text: str, option_text: str, uvprojx: Path, uvoptx: Path) -> dict[str, object]:
    info: dict[str, object] = {
        "uvprojx": str(uvprojx), "uvoptx": str(uvoptx) if uvoptx.is_file() else None,
        "pmon": None, "driver": None, "flash_driver": None,
        "registry_keys": [], "speed_khz": None, "probe_serial": None,
    }
    if match := _FLASH2_RE.search(project_text or ""):
        info["flash_driver"] = match.group(0)[len("<Flash2>"):-len("</Flash2>")]
    # <Driver> 在 TargetDlls 里；工程里可能还有别的 <Driver>，取第一个即可（Keil 只写一个）
    if match := _DRIVER_RE.search(project_text or ""):
        info["driver"] = match.group(0)[len("<Driver>"):-len("</Driver>")]
    if match := _PMON_RE.search(option_text or ""):
        info["pmon"] = match.group(0)[len("<pMon>"):-len("</pMon>")]
    if registry := _REGISTRY_RE.search(option_text or ""):
        info["registry_keys"] = re.findall(r"<Key>(.*?)</Key>", registry.group(0))
    name = _registry_name(option_text, "CMSIS_AGDI") or _registry_name(option_text, "JL2CM3") or _first_registry_name(option_text)[1]
    if speed := _TC_RE.search(name):
        info["speed_khz"] = int(speed.group(1)) // 1000
    if serial := _U_RE.search(name):
        info["probe_serial"] = None if serial.group(1).lower() == "any" else serial.group(1)
    return info


def read_config(uvprojx: Path) -> dict[str, object]:
    """读当前工程里的调试器配置（不修改任何文件）。"""
    return _parse_info(
        _read(uvprojx)[0] if uvprojx.is_file() else "",
        _read(uvoptx_for(uvprojx))[0] if uvoptx_for(uvprojx).is_file() else "",
        uvprojx, uvoptx_for(uvprojx))


def _build_name(driver: Driver, tail: str, *, speed_khz: int | None, probe_serial: str | None) -> str:
    head = driver.head
    if probe_serial:
        if driver.any_probe and driver.any_probe in head:
            head = head.replace(driver.any_probe, f"-U{probe_serial}")
        else:
            head = f"-U{probe_serial} {head}"
    elif driver.any_probe and "-U" not in head:
        head = f"{driver.any_probe} {head}"
    if speed_khz:
        if _TC_RE.search(tail):
            tail = _TC_RE.sub(lambda _m: f"-TC{speed_khz * 1000}", tail, count=1)
        else:
            tail = f"-TC{speed_khz * 1000} {tail}"
    return f"{head} {tail}".strip()


def _replace_registry(text: str, driver: Driver, name: str) -> tuple[str, bool]:
    """把 driver.key 的参数串换成 name；没有该条目就在注册表末尾插一条。"""
    pattern = re.compile(
        r"(<SetRegEntry>\s*<Number>\d+</Number>\s*<Key>" + re.escape(driver.key) +
        r"</Key>\s*<Name>)(.*?)(</Name>)", re.S)
    if pattern.search(text):
        return pattern.sub(lambda m: m.group(1) + name + m.group(3), text, count=1), True
    entry = (f"        <SetRegEntry>\n          <Number>0</Number>\n"
             f"          <Key>{driver.key}</Key>\n          <Name>{name}</Name>\n"
             f"        </SetRegEntry>\n")
    if registry := _REGISTRY_RE.search(text):
        block = registry.group(0)
        patched = block.replace("</TargetDriverDllRegistry>", entry + "      </TargetDriverDllRegistry>")
        return text[:registry.start()] + patched + text[registry.end():], True
    # 连注册表都没有：插在 </DebugOpt> 之后（Keil 接受这种结构，下次保存会补齐）
    if "</DebugOpt>" in text:
        return text.replace("</DebugOpt>",
                            "</DebugOpt>\n      <TargetDriverDllRegistry>\n"
                            + entry + "      </TargetDriverDllRegistry>", 1), True
    return text, False


def switch(uvprojx: Path, debugger: str, *, speed_khz: int | None = None,
           probe_serial: str | None = None, dry_run: bool = False) -> dict[str, object]:
    """把工程的调试器切换为指定驱动，返回改动前后的关键字段。

    只改「调试驱动 / 烧录驱动 / pMon / 该驱动的参数串」四处，其余选项原样保留；
    探针序列号与速率会写进参数串，但 Keil 的 Debug → Settings 仍是最终确认点。
    """
    driver = DRIVERS[normalize(debugger)]
    uvoptx = uvoptx_for(uvprojx)
    if not uvprojx.is_file():
        raise KeilConfigError(f"工程文件不存在: {uvprojx}")
    if not uvoptx.is_file():
        raise KeilConfigError(
            f"工程没有 {uvoptx.name}：先在 Keil 里打开工程、按 Options for Target → OK 保存一次"
            "（.uvoptx 由 Keil 生成），再切换调试器")
    before = read_config(uvprojx)
    project_text, project_bom = _read(uvprojx)
    option_text, option_bom = _read(uvoptx)

    key, existing = _first_registry_name(option_text)
    if not existing:
        raise KeilConfigError(
            f"{uvoptx.name} 里没有可复用的调试参数（缺 <TargetDriverDllRegistry>）："
            "在 Keil 里打开 Options for Target → Debug 选一次驱动并保存后再试")
    # 只复用 -N00(...) 之后的目标/Flash 算法参数；驱动前缀由目标驱动自己的格式给
    tail = existing[existing.index("-N00("):] if "-N00(" in existing else existing
    name = _build_name(driver, tail, speed_khz=speed_khz, probe_serial=probe_serial)

    new_project = _FLASH2_RE.sub(lambda _m: f"<Flash2>{driver.pmon}</Flash2>", project_text, count=1)
    new_project = _DRIVER_RE.sub(lambda _m: f"<Driver>{driver.pmon}</Driver>", new_project, count=1)
    new_option = _PMON_RE.sub(lambda _m: f"<pMon>{driver.pmon}</pMon>", option_text, count=1)
    new_option, _ = _replace_registry(new_option, driver, name)

    changed: list[str] = []
    if _DRIVER_RE.search(project_text).group(0) != _DRIVER_RE.search(new_project).group(0):
        changed.append("uvprojx:<DebugOption><TargetDlls><Driver>")
    if _FLASH2_RE.search(project_text).group(0) != _FLASH2_RE.search(new_project).group(0):
        changed.append("uvprojx:<Utilities><Flash2>")
    if _PMON_RE.search(option_text).group(0) != _PMON_RE.search(new_option).group(0):
        changed.append("uvoptx:<pMon>")
    if _registry_name(option_text, driver.key) != name:
        changed.append(f"uvoptx:<TargetDriverDllRegistry>[{driver.key}]")

    for text, label in ((new_project, uvprojx.name), (new_option, uvoptx.name)):
        try:
            ET.fromstring(text)
        except ET.ParseError as error:  # 理论上不会发生；宁可拒绝写坏工程
            raise KeilConfigError(f"改写后的 {label} 不是合法 XML（{error}），已放弃写入") from error

    backup = None
    if not dry_run and changed:
        stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
        backup = uvoptx.with_name(f"{uvoptx.name}.bak-{stamp}")
        shutil.copy2(uvoptx, backup)
        shutil.copy2(uvprojx, backup.with_name(f"{uvprojx.name}.bak-{stamp}"))
        _write(uvprojx, new_project, project_bom)
        _write(uvoptx, new_option, option_bom)

    after = _parse_info(new_project, new_option, uvprojx, uvoptx)
    return {
        "debugger": driver.name,
        "label": driver.label,
        "dry_run": dry_run,
        "changed": changed,
        "before": before,
        "after": after,
        "backup": str(backup) if backup else None,
        "registry_name": name,
    }


def missing_driver_files(uv4_exe: str | Path | None, debugger: str) -> list[str]:
    """Keil 安装目录里缺哪些驱动 DLL（J-Link 的 JL2CM3.dll 不在 ARM\\BIN 而在 ARM\\Segger）。"""
    driver = DRIVERS[normalize(debugger)]
    if not uv4_exe:
        return []
    root = Path(uv4_exe).resolve().parent.parent
    search_dirs = (root / "ARM" / "BIN", root / "ARM" / "Segger", root / "BIN")
    found = {name.lower() for directory in search_dirs if directory.is_dir()
             for name in (path.name for path in directory.iterdir() if path.is_file())}
    return [name for name in driver.dll_names if name.lower() not in found]


def available(uv4_exe: str | Path | None) -> dict[str, object]:
    """本机 Keil 里可用于切换的驱动（装没装 DLL）。没配 uv4_exe 时 installed=None（未知，不谎报已装）。"""
    report: dict[str, object] = {}
    for name, driver in DRIVERS.items():
        if not uv4_exe:
            report[name] = {"label": driver.label, "pmon": driver.pmon, "installed": None, "missing": [],
                            "hint": "未配置 tools.uv4_exe，无法校验 Keil 驱动是否安装"}
            continue
        missing = missing_driver_files(uv4_exe, name)
        report[name] = {"label": driver.label, "pmon": driver.pmon,
                        "installed": not missing, "missing": missing}
    return report
