from __future__ import annotations

import hashlib
import ntpath
import os
from dataclasses import dataclass
from pathlib import Path


def normalize_project_path(project_root: str | Path) -> str:
    raw = os.path.expandvars(os.path.expanduser(str(project_root)))
    if ntpath.splitdrive(raw)[0] or "\\" in raw:
        normalized = ntpath.abspath(raw.replace("/", "\\")).replace("\\", "/").lower()
    else:
        normalized = str(Path(raw).resolve()).replace("\\", "/")
        if os.name == "nt":
            normalized = normalized.lower()
    drive, tail = ntpath.splitdrive(normalized.replace("/", "\\"))
    if drive and tail in {"", "\\"}:
        return drive.replace("\\", "/") + "/"
    return normalized.rstrip("/")


def project_id_for(project_root: str | Path) -> str:
    return hashlib.sha256(normalize_project_path(project_root).encode("utf-8")).hexdigest()[:16]


DATA_HOME_ENV = "HT32_SKILLS_HOME"

LOG_UNAVAILABLE = (
    "全局日志目录不可写（{path}）：本次未落盘日志，命令本身已执行。"
    "宿主工作区沙箱会拦 home 目录写入——需要日志时把环境变量 HT32_SKILLS_HOME "
    "指向可写目录（如沙箱允许的临时目录）后重跑。"
)


def _home_dir() -> Path:
    home = os.environ.get("USERPROFILE") or os.environ.get("HOME")
    return Path(home) if home else Path.home()


def global_root_dir() -> Path:
    """全局用户数据根目录（机器配置 / 状态 / 缓存 / 日志的家）。

    默认 `%USERPROFILE%/.ht32-skills`。宿主启用工作区沙箱时，home 下写入会被拦，
    可用环境变量 `HT32_SKILLS_HOME` 把整份数据目录重定向到可写位置（只影响本机数据，
    与用户工程无关）。
    """
    override = (os.environ.get(DATA_HOME_ENV) or "").strip()
    if override:
        return Path(os.path.expandvars(os.path.expanduser(override)))
    return _home_dir() / ".ht32-skills"


def writable_dir(path: Path) -> Path | None:
    """确保目录存在且真的可写；不可写（常见于宿主沙箱拦全局目录）返回 None。

    只做一次落盘探测：日志/缓存这类**辅助产物**不得因为不可写而中断真正的构建、
    烧录或串口采集——调用方拿到 None 时应降级继续，并在 JSON 里给 warning。
    """
    probe = path / f".ht32-write-probe-{os.getpid()}"
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe.write_text("", encoding="utf-8")
        return path
    except OSError:
        return None
    finally:
        try:
            probe.unlink(missing_ok=True)
        except OSError:
            pass


@dataclass(frozen=True)
class RuntimePaths:
    project_root: Path
    project_id: str
    project_config: Path
    global_root: Path
    machine_config: Path
    state_file: Path
    cache_dir: Path
    logs_dir: Path

    @classmethod
    def for_project(cls, project_root: Path) -> RuntimePaths:
        root = project_root.resolve()
        project_id = project_id_for(root)
        global_root = global_root_dir()
        return cls(
            root,
            project_id,
            root / ".ht32-skills" / "config.json",
            global_root,
            global_root / "config.json",
            global_root / "state" / "projects" / f"{project_id}.json",
            global_root / "cache" / "projects" / project_id,
            global_root / "logs" / project_id,
        )

    def ensure_global_dirs(self) -> None:
        self.project_config.parent.mkdir(parents=True, exist_ok=True)
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)

    @property
    def serial_logs_dir(self) -> Path:
        """串口观测日志目录（全局；串口命令不依赖工程上下文，固定子目录）。"""
        return self.global_root / "logs" / "serial"


def ensure_global_dirs(project_root: Path) -> RuntimePaths:
    paths = RuntimePaths.for_project(project_root)
    paths.ensure_global_dirs()
    return paths
