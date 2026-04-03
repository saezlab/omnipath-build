from __future__ import annotations

from dataclasses import fields, is_dataclass
from datetime import UTC, datetime
import importlib
from pathlib import Path
from typing import Any

import polars as pl
import pyarrow.parquet as pq

from omnipath_build.pipeline.paths import read_latest_pointer, source_version_dir
from omnipath_build.silver.build import discover_resources
from pypath.inputs_v2.base import Resource


def _iso_utc(timestamp: float | None) -> str | None:
    if timestamp is None:
        return None
    return datetime.fromtimestamp(timestamp, tz=UTC).isoformat().replace('+00:00', 'Z')


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _pypath_data_root() -> Path:
    return _project_root() / 'pypath-data'


def _parquet_rows(path: Path) -> int:
    return int(pq.ParquetFile(path).metadata.num_rows)


def _term_count_from_obo(path: Path) -> int:
    count = 0
    with path.open('r', encoding='utf-8', errors='ignore') as handle:
        for line in handle:
            if line.strip() == '[Term]':
                count += 1
    return count


def _collect_subfolders(obj: Any, seen: set[int] | None = None) -> set[str]:
    if obj is None:
        return set()

    if seen is None:
        seen = set()

    obj_id = id(obj)
    if obj_id in seen:
        return set()
    seen.add(obj_id)

    if isinstance(obj, (str, bytes, int, float, bool, Path)):
        return set()

    subfolder = getattr(obj, 'subfolder', None)
    if isinstance(subfolder, str) and subfolder:
        return {subfolder}

    if isinstance(obj, dict):
        result: set[str] = set()
        for value in obj.values():
            result.update(_collect_subfolders(value, seen))
        return result

    if isinstance(obj, (list, tuple, set)):
        result: set[str] = set()
        for value in obj:
            result.update(_collect_subfolders(value, seen))
        return result

    if is_dataclass(obj):
        result: set[str] = set()
        for field in fields(obj):
            result.update(_collect_subfolders(getattr(obj, field.name), seen))
        return result

    if hasattr(obj, '__dict__'):
        result: set[str] = set()
        for value in vars(obj).values():
            result.update(_collect_subfolders(value, seen))
        return result

    return set()


def _latest_file_mtime(paths: list[Path]) -> float | None:
    mtimes = [path.stat().st_mtime for path in paths if path.exists() and path.is_file()]
    return max(mtimes) if mtimes else None


def _resource_download_mtime(resource: Resource) -> str | None:
    data_root = _pypath_data_root()
    subfolders: set[str] = set()
    for dataset in resource.datasets().values():
        subfolders.update(_collect_subfolders(getattr(dataset, 'download', None)))

    files: list[Path] = []
    for subfolder in sorted(subfolders):
        folder = data_root / subfolder
        if folder.exists():
            files.extend(path for path in folder.rglob('*') if path.is_file())

    return _iso_utc(_latest_file_mtime(files))


def _current_gold_dir(gold_root: Path, source: str) -> Path | None:
    version = read_latest_pointer(gold_root, source)
    if not version:
        return None
    path = source_version_dir(gold_root, source, version)
    return path if path.exists() else None


def _data_modalities(version_dir: Path | None) -> list[str]:
    if version_dir is None:
        return []

    modalities: list[str] = []
    file_names = {path.name for path in version_dir.iterdir() if path.is_file()}
    if 'entities.parquet' in file_names:
        modalities.append('entities')
    if 'interactions.parquet' in file_names:
        modalities.append('interactions')
    if 'associations.parquet' in file_names:
        modalities.append('associations')
    if 'annotations.parquet' in file_names:
        modalities.append('annotations')
    if any(path.suffix == '.obo' for path in version_dir.iterdir() if path.is_file()):
        modalities.append('ontology')
    return modalities


def _count_file(version_dir: Path | None, name: str) -> int:
    if version_dir is None:
        return 0
    path = version_dir / name
    if not path.exists():
        return 0
    return _parquet_rows(path)


def _ontology_term_count(version_dir: Path | None) -> int:
    if version_dir is None:
        return 0
    total = 0
    for path in version_dir.iterdir():
        if path.is_file() and path.suffix == '.obo':
            total += _term_count_from_obo(path)
    return total


def _gold_files(version_dir: Path | None) -> list[Path]:
    if version_dir is None:
        return []
    return sorted(path for path in version_dir.iterdir() if path.is_file())


def _interaction_participant_types(version_dir: Path | None) -> list[str]:
    if version_dir is None:
        return []

    entities_path = version_dir / 'entities.parquet'
    interactions_path = version_dir / 'interactions.parquet'
    if not entities_path.exists() or not interactions_path.exists():
        return []

    entities = pl.read_parquet(entities_path, columns=['entity_id', 'entity_type'])
    interactions = pl.read_parquet(interactions_path, columns=['entity_a_id', 'entity_b_id'])

    pairs = (
        interactions
        .join(
            entities.rename({'entity_id': 'entity_a_id', 'entity_type': 'entity_a_type'}),
            on='entity_a_id',
            how='left',
        )
        .join(
            entities.rename({'entity_id': 'entity_b_id', 'entity_type': 'entity_b_type'}),
            on='entity_b_id',
            how='left',
        )
        .select('entity_a_type', 'entity_b_type')
        .drop_nulls()
        .iter_rows()
    )

    result = {
        '|'.join(sorted((left, right)))
        for left, right in pairs
        if left and right
    }
    return sorted(result)


def _resource_row(*, source: str, resource: Resource, gold_root: Path) -> dict[str, Any]:
    config = resource.config
    version_dir = _current_gold_dir(gold_root, source)
    gold_files = _gold_files(version_dir)
    last_built_at = _iso_utc(_latest_file_mtime(gold_files))

    return {
        'resource_id': source,
        'resource_name': config.name,
        'description': config.description,
        'homepage_url': config.url,
        'license': str(config.license),
        'pubmed_id': config.pubmed,
        'primary_category': config.primary_category,
        'data_modalities': _data_modalities(version_dir),
        'interaction_participant_types': _interaction_participant_types(version_dir),
        'entity_count': _count_file(version_dir, 'entities.parquet'),
        'interaction_count': _count_file(version_dir, 'interactions.parquet'),
        'association_count': _count_file(version_dir, 'associations.parquet'),
        'identifier_count': _count_file(version_dir, 'entity_identifiers.parquet'),
        'ontology_term_count': _ontology_term_count(version_dir),
        'total_size_bytes': sum(path.stat().st_size for path in gold_files),
        'last_downloaded_at': _resource_download_mtime(resource),
        'last_built_at': last_built_at,
        'build_status': 'success' if gold_files else 'not_built',
    }


def build_resources_parquet(*, gold_root: Path, inputs_package: str) -> Path:
    discovered, _ = discover_resources(
        database_name='.',
        base_path=None,
        inputs_package=inputs_package,
    )

    rows: list[dict[str, Any]] = []
    for source in sorted(discovered):
        module = importlib.import_module(f'{inputs_package}.{source}')
        resource = getattr(module, 'resource', None)
        if not isinstance(resource, Resource):
            continue
        rows.append(_resource_row(source=source, resource=resource, gold_root=gold_root))

    output_path = gold_root / 'resources.parquet'
    output_path.parent.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(rows).sort('resource_id').write_parquet(output_path)
    return output_path
