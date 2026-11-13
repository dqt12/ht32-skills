from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Mapping

from .models import DebuggerConfig, DebuggerSettings, MachineConfig, ProjectConfig
from .paths import RuntimePaths


class ConfigError(ValueError):
    pass


_TOOL_KEYS = {
    "uv4_exe",
    "jlink_exe",
    "jlink_gdb_server_exe",
    "jlink_rtt_client_exe",
    "gdb_exe",
}
_DEBUGGER_KEYS = {"backend", "probe_serial", "interface", "speed_khz"}


def _read_json(path: Path) -> dict[str, object]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"无法读取配置 {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError("配置根节点必须是对象")
    return data


def _only_keys(data: Mapping[str, object], allowed: set[str], section: str) -> None:
    unknown = set(data) - allowed
    if unknown:
        raise ConfigError(f"{section} 包含未知字段: {', '.join(sorted(unknown))}")


def _object(value: object, section: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ConfigError(f"{section} 必须是对象")
    return value


def _parse_debugger(value: object, section: str = "debugger") -> DebuggerSettings:
    data = _object(value, section)
    _only_keys(data, _DEBUGGER_KEYS, section)
    if "backend" in data and data["backend"] not in {"keil", "jlink"}:
        raise ConfigError("debugger.backend 必须是 keil 或 jlink")
    if "probe_serial" in data and data["probe_serial"] is not None and not isinstance(data["probe_serial"], str):
        raise ConfigError("debugger.probe_serial 必须是字符串或 null")
    if "interface" in data and data["interface"] not in {"swd", "jtag"}:
        raise ConfigError("debugger.interface 必须是 swd 或 jtag")
    if "speed_khz" in data:
        speed_khz = data["speed_khz"]
        if isinstance(speed_khz, bool) or not isinstance(speed_khz, int) or speed_khz <= 0:
            raise ConfigError("debugger.speed_khz 必须大于 0")
    return DebuggerSettings(**data)


def _schema_version(data: Mapping[str, object]) -> int:
    version = data.get("schema_version")
    if type(version) is not int or version != 1:
        raise ConfigError("仅支持 schema_version 1")
    return 1


def load_machine_config(path: Path | None = None) -> MachineConfig:
    config_path = path or RuntimePaths.for_project(Path.cwd()).machine_config
    if not config_path.exists():
        return MachineConfig(1, {key: None for key in sorted(_TOOL_KEYS)}, DebuggerSettings())
    data = _read_json(config_path)
    _only_keys(data, {"schema_version", "tools", "defaults"}, "机器配置")
    version = _schema_version(data)
    tools = _object(data.get("tools", {}), "tools")
    _only_keys(tools, _TOOL_KEYS, "tools")
    parsed_tools: dict[str, str | None] = {}
    for key in sorted(_TOOL_KEYS):
        value = tools.get(key)
        if value is not None and not isinstance(value, str):
            raise ConfigError(f"tools.{key} 必须是字符串或 null")
        parsed_tools[key] = value
    defaults = _object(data.get("defaults", {}), "defaults")
    _only_keys(defaults, {"debugger"}, "defaults")
    debugger = _parse_debugger(defaults.get("debugger", {}), "defaults.debugger")
    return MachineConfig(version, parsed_tools, debugger)


def _validate_project_data(root: Path, data: Mapping[str, object]) -> ProjectConfig:
    if "flash" in data or "debug" in data:
        raise ConfigError("flash/debug 不得持久化独立 debugger")
    _only_keys(data, {"schema_version", "project", "debugger"}, "项目配置")
    version = _schema_version(data)
    project = _object(data.get("project"), "project")
    _only_keys(project, {"chip", "uvprojx", "target"}, "project")
    chip = project.get("chip")
    uvprojx_value = project.get("uvprojx")
    target = project.get("target")
    if not isinstance(chip, str) or not chip:
        raise ConfigError("project.chip 必须是非空字符串")
    if not isinstance(uvprojx_value, str) or not uvprojx_value:
        raise ConfigError("project.uvprojx 必须是非空字符串")
    if target is not None and not isinstance(target, str):
        raise ConfigError("project.target 必须是字符串或 null")
    uvprojx = (root / Path(uvprojx_value.replace("\\", "/"))).resolve()
    try:
        uvprojx.relative_to(root)
    except ValueError as exc:
        raise ConfigError("project.uvprojx 不得逃逸工程根") from exc
    if not uvprojx.is_file():
        raise ConfigError(f"project.uvprojx 不存在: {uvprojx}")
    debugger = _parse_debugger(data.get("debugger", {}))
    return ProjectConfig(version, root, chip, uvprojx, target, debugger)


def load_project_config(project_root: Path) -> ProjectConfig:
    root = project_root.resolve()
    path = RuntimePaths.for_project(root).project_config
    return _validate_project_data(root, _read_json(path))


def init_project_config(
    project_root: Path,
    chip: str,
    uvprojx: str,
    target: str | None,
    debugger: DebuggerConfig,
    *,
    force: bool = False,
) -> Path:
    root = project_root.resolve()
    path = RuntimePaths.for_project(root).project_config
    if path.exists() and not force:
        raise ConfigError(f"项目配置已存在: {path}")
    debugger_data = asdict(debugger) if isinstance(debugger, DebuggerConfig) else debugger
    data = {
        "schema_version": 1,
        "project": {"chip": chip, "uvprojx": uvprojx, "target": target},
        "debugger": debugger_data,
    }
    validated = _validate_project_data(root, data)
    data["project"]["uvprojx"] = validated.uvprojx.relative_to(root).as_posix()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def resolve_debugger(
    machine: DebuggerSettings | DebuggerConfig,
    project: DebuggerSettings | DebuggerConfig | None,
    overrides: Mapping[str, object | None],
) -> DebuggerConfig:
    _only_keys(overrides, _DEBUGGER_KEYS, "debugger overrides")
    resolved = asdict(DebuggerConfig())
    resolved.update({key: value for key, value in asdict(machine).items() if value is not None})
    if project is not None:
        resolved.update({key: value for key, value in asdict(project).items() if value is not None})
    resolved.update({key: value for key, value in overrides.items() if value is not None})
    parsed = _parse_debugger(resolved)
    return DebuggerConfig(**asdict(parsed))


def config_view(project_root: Path, overrides: Mapping[str, object | None]) -> dict[str, object]:
    project = load_project_config(project_root)
    machine = load_machine_config()
    debugger = resolve_debugger(machine.debugger, project.debugger, overrides)
    return {
        "schema_version": 1,
        "project": {
            "root": str(project.project_root),
            "chip": project.chip,
            "uvprojx": str(project.uvprojx),
            "target": project.target,
        },
        "tools": dict(machine.tools),
        "debugger": asdict(debugger),
    }
