from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path


_EXTENSIONS = ("axf", "elf", "hex", "bin")


class ArtifactError(ValueError):
    pass


def list_uvprojx(project_root: Path) -> list[Path]:
    return sorted(path.resolve() for path in project_root.rglob("*.uvprojx") if path.is_file())


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _child_text(element: ET.Element, name: str) -> str | None:
    for child in element.iter():
        if _local_name(child.tag) == name and child.text:
            return child.text.strip()
    return None


def _targets(uvprojx: Path) -> list[ET.Element]:
    if not uvprojx.exists():
        raise ArtifactError(f"uvprojx 不存在: {uvprojx}")
    try:
        root = ET.parse(uvprojx).getroot()
    except ET.ParseError as exc:
        raise ArtifactError(f"uvprojx XML 损坏: {uvprojx}: {exc}") from exc
    except OSError as exc:
        raise ArtifactError(f"无法读取 uvprojx: {uvprojx}: {exc}") from exc
    return [element for element in root.iter() if _local_name(element.tag) == "Target"]


def list_uvprojx_targets(uvprojx: Path) -> list[str]:
    return [name for element in _targets(uvprojx) if (name := _child_text(element, "TargetName"))]


list_targets = list_uvprojx_targets


def collect_build_artifacts(uvprojx: Path, target: str | None) -> dict[str, list[str]]:
    artifacts = {extension: [] for extension in _EXTENSIONS}
    project_dir = uvprojx.resolve().parent
    for element in _targets(uvprojx):
        name = _child_text(element, "TargetName")
        if target is not None and name != target:
            continue
        output_directory = _child_text(element, "OutputDirectory")
        output_name = _child_text(element, "OutputName")
        if not output_directory or not output_name:
            continue
        output_dir = (project_dir / Path(output_directory.replace("\\", "/"))).resolve()
        for extension in _EXTENSIONS:
            candidate = (output_dir / f"{output_name}.{extension}").resolve()
            if candidate.is_file():
                artifacts[extension].append(str(candidate))
    return artifacts
