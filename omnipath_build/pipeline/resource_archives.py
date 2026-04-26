from __future__ import annotations

from pathlib import Path
import zipfile

RESOURCE_ARCHIVE_SUFFIX = '.zip'

ARCHIVE_EXCLUDED_NAMES = frozenset({
    'entity_map.parquet',
    'entity_occurrence_map.parquet',
    'canonicalization_report.md',
    'canonicalization_summary.json',
})


def resource_archive_name(resource_id: str) -> str:
    return f'{resource_id}{RESOURCE_ARCHIVE_SUFFIX}'


def resource_archive_path(source_dir: Path, resource_id: str) -> Path:
    return source_dir / resource_archive_name(resource_id)


def iter_resource_archive_inputs(source_dir: Path, resource_id: str):
    source_dir = Path(source_dir)
    archive_path = resource_archive_path(source_dir, resource_id)
    for path in sorted(source_dir.rglob('*')):
        if not path.is_file():
            continue
        if path == archive_path:
            continue
        if path.name in ARCHIVE_EXCLUDED_NAMES:
            continue
        yield path


def build_resource_archive(source_dir: Path, resource_id: str) -> Path:
    source_dir = Path(source_dir)
    source_dir.mkdir(parents=True, exist_ok=True)

    archive_path = resource_archive_path(source_dir, resource_id)
    inputs = list(iter_resource_archive_inputs(source_dir, resource_id))
    if not inputs:
        raise ValueError(f'No gold artifacts available to archive for resource {resource_id!r} in {source_dir}')

    if archive_path.exists():
        archive_path.unlink()

    with zipfile.ZipFile(archive_path, mode='w', compression=zipfile.ZIP_DEFLATED) as zf:
        for source_path in inputs:
            zf.write(source_path, arcname=source_path.name)

    return archive_path
