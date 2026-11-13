from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from .paths import global_root_dir

UNKNOWN = "未获取（安装包无 VERSION 文件）"
UPDATE_TTL_HOURS = 24
UPDATE_CACHE_NAME = "update-check.json"

_FIRST_LINE = re.compile(r"^v?\d+\.\d+\.\d+")
_VERSION = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")


def _candidate_files() -> tuple[Path, ...]:
    """版本文件位置：安装态在 <skills>/ht32/VERSION，开发态在仓库根 VERSION。"""
    root = Path(__file__).resolve().parents[2]
    return (root / "ht32" / "VERSION", root / "VERSION")


def skill_version() -> tuple[str, str | None]:
    """返回 (版本号, 来源文件)；找不到版本文件时返回 ("未获取（安装包无 VERSION 文件）", None)。"""
    for path in _candidate_files():
        try:
            first = path.read_text(encoding="utf-8").splitlines()[0].strip()
        except (OSError, IndexError, UnicodeDecodeError):
            continue
        if _FIRST_LINE.match(first):
            return first, str(path)
    return UNKNOWN, None


def parse_version(text: str) -> tuple[int, int, int] | None:
    match = _VERSION.match((text or "").strip())
    if not match:
        return None
    major, minor, patch = match.groups()
    return int(major), int(minor), int(patch)


def compare_versions(current: str, latest: str) -> str:
    """本机版本 vs 云端最新版本 → behind / same / ahead / unknown。"""
    now, newest = parse_version(current), parse_version(latest)
    if now is None or newest is None:
        return "unknown"
    if now < newest:
        return "behind"
    return "ahead" if now > newest else "same"


def update_cache_path() -> Path:
    """更新检查缓存（全局用户数据目录；只存版本号与时间戳，不碰用户工程）。"""
    return global_root_dir() / "state" / UPDATE_CACHE_NAME


def read_update_cache() -> dict:
    path = update_cache_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def record_update_check(latest: str, notes: str = "") -> Path | None:
    """记录一次更新检查结果（由 Agent 调 MCP `check_skill_update` 后写入）。

    缓存是辅助产物：全局目录不可写（宿主沙箱）时返回 None，调用方按 warning 处理。
    """
    path = update_cache_path()
    payload = {
        "latest": (latest or "").strip(),
        "notes": notes,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return path
    except OSError:
        return None


def update_status(ttl_hours: int = UPDATE_TTL_HOURS) -> dict:
    """本机版本与上次检查结果的比对状态，供 Agent 决定是否再查云端。

    口径（避免误读缓存）：`latest` 只是**上次**查到的云端版本，不保证仍是云端最新。
    `check_due=true` 表示缓存已过期（或从未查过），此时 `behind` 只能当「上次比对」的结论；
    要判断当前是否落后，仍需再调 MCP `check_skill_update` 刷新缓存。
    """
    version, _ = skill_version()
    cache = read_update_cache()
    latest = str(cache.get("latest") or "")
    checked_at = str(cache.get("checked_at") or "")
    age_hours: float | None = None
    if checked_at:
        try:
            stamp = datetime.fromisoformat(checked_at)
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=timezone.utc)
            age_hours = round((datetime.now(timezone.utc) - stamp).total_seconds() / 3600, 2)
        except ValueError:
            age_hours = None
    due = age_hours is None or age_hours > ttl_hours
    cache_valid = age_hours is not None and age_hours <= ttl_hours
    relation = compare_versions(version, latest)
    return {
        "local": version,
        "latest": latest,
        "checked_at": checked_at,
        "age_hours": age_hours,
        "check_due": due,
        "cache_valid": cache_valid,
        "ttl_hours": ttl_hours,
        "relation": relation,
        "behind": relation == "behind",
        "latest_may_be_stale": due and bool(latest),
        "cache_file": str(update_cache_path()),
        "hint": ("缓存已过期：请再调 MCP check_skill_update 刷新，别按上次结论判断是否落后"
                 if due and latest else
                 "首次检查：请调 MCP check_skill_update 获取云端最新版本" if not latest else
                 "缓存有效：behind 结论可用"),
    }
