from __future__ import annotations

from pathlib import Path
import zipfile


RESOURCE_ARCHIVE_SUFFIX = '.zip'


def resource_archive_name(resource_id: str) -> str:
    return f'{resource_id}{RESOURCE_ARCHIVE_SUFFIX}'


def resource_archive_path(version_dir: Path, resource_id: str) -> Path:
    return version_dir / resource_archive_name(resource_id)


def iter_resource_archive_inputs(version_dir: Path, resource_id: str):
    archive_path = resource_archive_path(version_dir, resource_id)
    for path in sorted(version_dir.iterdir()):
        if not path.is_file():
            continue
        if path == archive_path:
            continue
        yield path


def build_resource_archive(version_dir: Path, resource_id: str) -> Path:
    version_dir = Path(version_dir)
    version_dir.mkdir(parents=True, exist_ok=True)

    archive_path = resource_archive_path(version_dir, resource_id)
    inputs = list(iter_resource_archive_inputs(version_dir, resource_id))
    if not inputs:
        raise ValueError(f'No gold artifacts available to archive for resource {resource_id!r} in {version_dir}')

    if archive_path.exists():
        archive_path.unlink()

    with zipfile.ZipFile(archive_path, mode='w', compression=zipfile.ZIP_DEFLATED) as zf:
        for source_path in inputs:
            zf.write(source_path, arcname=source_path.name)

    return archive_path
