from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal

Status = Literal["ok", "warn", "blocked", "error"]


@dataclass(frozen=True)
class DebuggerSettings:
    backend: Literal["keil", "jlink"] | None = None
    probe_serial: str | None = None
    interface: Literal["swd", "jtag"] | None = None
    speed_khz: int | None = None


@dataclass(frozen=True)
class DebuggerConfig:
    backend: Literal["keil", "jlink"] = "keil"
    probe_serial: str | None = None
    interface: Literal["swd", "jtag"] = "swd"
    speed_khz: int = 1000


@dataclass(frozen=True)
class ProjectConfig:
    schema_version: int
    project_root: Path
    chip: str
    uvprojx: Path
    target: str | None
    debugger: DebuggerSettings


@dataclass(frozen=True)
class MachineConfig:
    schema_version: int
    tools: dict[str, str | None]
    debugger: DebuggerSettings


@dataclass
class OperationResult:
    schema_version: int
    operation: str
    status: Status
    backend: str | None
    project_id: str | None
    started_at: str
    finished_at: str | None = None
    artifacts: dict[str, object] = field(default_factory=dict)
    observations: dict[str, object] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    error: dict[str, object] | None = None
    log_path: str | None = None

    @classmethod
    def start(cls, operation: str, backend: str | None, project_id: str | None) -> OperationResult:
        return cls(1, operation, "ok", backend, project_id, datetime.now().astimezone().isoformat())

    def finish(self, status: Status) -> OperationResult:
        if status not in {"ok", "warn", "blocked", "error"}:
            raise ValueError(f"invalid operation status: {status}")
        self.status = status
        self.finished_at = datetime.now().astimezone().isoformat()
        return self

    @property
    def exit_code(self) -> int:
        return {"ok": 0, "warn": 0, "blocked": 2, "error": 3}[self.status]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)
