from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

from ..models import DebuggerConfig, OperationResult, ProjectConfig
from ..process import BackgroundProcess, ProcessResult, run_process, start_process

Runner = Callable[..., ProcessResult]
Starter = Callable[..., BackgroundProcess]


@dataclass
class OperationContext:
    project_root: Path
    project_id: str
    chip: str
    uvprojx: Path
    target: str | None
    debugger: DebuggerConfig
    tools: dict[str, str | None]
    cache_dir: Path
    logs_dir: Path
    backend: object | None = None


class DebugBackend(Protocol):
    name: str
    def probe(self, ctx: OperationContext) -> OperationResult: ...
    def connect(self, ctx: OperationContext) -> OperationResult: ...
    def flash(self, ctx: OperationContext, image: Path | None = None) -> OperationResult: ...
    def reset(self, ctx: OperationContext) -> OperationResult: ...
    def debug(self, ctx: OperationContext) -> OperationResult: ...


def make_context(project: ProjectConfig, debugger: DebuggerConfig, tools: dict[str, str | None], paths) -> OperationContext:
    return OperationContext(project.project_root, paths.project_id, project.chip, project.uvprojx, project.target,
                            debugger, tools, paths.cache_dir, paths.logs_dir)


def get_backend(name: str, runner: Runner = run_process):
    if name == "keil":
        from .keil import KeilBackend
        return KeilBackend(runner)
    if name == "jlink":
        from .jlink import JLinkBackend
        return JLinkBackend(runner)
    raise ValueError(f"unknown backend: {name}")
