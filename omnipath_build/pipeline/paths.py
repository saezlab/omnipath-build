from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PipelinePaths:
    data_root: Path
    silver_root: Path
    gold_root: Path
    reports_root: Path


def build_paths(data_root: str | Path = 'data_v2') -> PipelinePaths:
    data_root = Path(data_root)
    return PipelinePaths(
        data_root=data_root,
        silver_root=data_root / 'silver',
        gold_root=data_root / 'gold',
        reports_root=data_root / 'reports',
    )


def source_relpath(source: str) -> Path:
    return Path(source.replace('.', '/'))


def source_stage_dir(stage_root: Path, source: str) -> Path:
    return stage_root / source_relpath(source)


def source_version_dir(stage_root: Path, source: str, version: str) -> Path:
    return source_stage_dir(stage_root, source) / version


def stable_pointer_path(stage_root: Path, source: str) -> Path:
    return source_stage_dir(stage_root, source) / 'latest'


def update_latest_pointer(stage_root: Path, source: str, version: str) -> None:
    pointer = stable_pointer_path(stage_root, source)
    pointer.parent.mkdir(parents=True, exist_ok=True)
    payload = {'version': version}
    pointer.write_text(json.dumps(payload, indent=2) + '\n', encoding='utf-8')


def read_latest_pointer(stage_root: Path, source: str) -> str | None:
    pointer = stable_pointer_path(stage_root, source)
    if not pointer.exists():
        return None
    payload = json.loads(pointer.read_text(encoding='utf-8'))
    version = payload.get('version')
    return str(version) if version else None


def next_numeric_version(stage_root: Path, source: str) -> str:
    stage_dir = source_stage_dir(stage_root, source)
    if not stage_dir.exists():
        return '1'

    numeric_versions: list[int] = []
    for child in stage_dir.iterdir():
        if not child.is_dir():
            continue
        try:
            numeric_versions.append(int(child.name))
        except ValueError:
            continue

    if not numeric_versions:
        return '1'
    return str(max(numeric_versions) + 1)


def copy_tree(src: Path, dst: Path) -> None:
    dst.mkdir(parents=True, exist_ok=True)
    for path in sorted(src.rglob('*')):
        rel = path.relative_to(src)
        target = dst / rel
        if path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)


GoldPipelinePaths = PipelinePaths
