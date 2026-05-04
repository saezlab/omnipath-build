from __future__ import annotations

import argparse
import importlib
from dataclasses import fields, is_dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import polars as pl
import pyarrow.parquet as pq

from omnipath_build.gold.utils.canonicalization import ONTOLOGY_ENTITY_TYPE_LABEL
from omnipath_build.gold.utils.cv_terms import format_cv_term
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


def _resource_categories(
    *,
    interaction_count: int,
    membership_count: int,
    annotation_count: int,
    ontology_term_count: int,
) -> list[str]:
    categories: list[str] = []

    if interaction_count > 0:
        categories.append('interaction')
    if membership_count > 0:
        categories.append('membership')
    if annotation_count > 0 or ontology_term_count > 0:
        categories.append('annotation')

    return categories


def _source_gold_dir(gold_root: Path, source: str) -> Path | None:
    path = gold_root / source
    return path if path.exists() and path.is_dir() else None


def _count_file(source_dir: Path | None, relative_path: str) -> int:
    if source_dir is None:
        return 0
    path = source_dir / relative_path
    if not path.exists():
        return 0
    return _parquet_rows(path)


def _identifier_count(source_dir: Path | None) -> int:
    if source_dir is None:
        return 0

    path = source_dir / 'entities' / 'entity.parquet'
    if not path.exists():
        return 0

    entity_frame = pl.read_parquet(path, columns=['canonical_identifier', 'identifiers'])
    if entity_frame.is_empty():
        return 0

    nested_identifier_count = int(
        entity_frame
        .select(pl.col('identifiers').list.len().fill_null(0).sum().alias('identifier_count'))
        .item()
        or 0
    )
    return int(entity_frame.height + nested_identifier_count)


def _ontology_entity_count(source_dir: Path | None) -> int:
    if source_dir is None:
        return 0
    path = source_dir / 'entities' / 'entity.parquet'
    if not path.exists():
        return 0
    frame = pl.read_parquet(path, columns=['entity_type'])
    if frame.is_empty():
        return 0
    return int(frame.filter(pl.col('entity_type') == ONTOLOGY_ENTITY_TYPE_LABEL).height)


def _relation_category_counts(source_dir: Path | None) -> dict[str, int]:
    if source_dir is None:
        return {}

    path = source_dir / 'relations' / 'entity_relation.parquet'
    if not path.exists():
        return {}

    frame = pl.read_parquet(path, columns=['relation_category'])
    if frame.is_empty():
        return {}

    return {
        str(category): int(count)
        for category, count in frame.group_by('relation_category').len().iter_rows()
    }


def _gold_files(source_dir: Path | None) -> list[Path]:
    if source_dir is None:
        return []
    return sorted(path for path in source_dir.rglob('*') if path.is_file())


def _ontology_labels(resource: Resource) -> list[str]:
    values = []
    for ontology in getattr(resource.config, 'annotation_ontologies', ()):
        label = getattr(ontology, 'definition', None) or str(ontology)
        values.append(str(label))
    return values


def _resource_row(*, source: str, resource: Resource, gold_root: Path) -> dict[str, Any]:
    config = resource.config
    source_dir = _source_gold_dir(gold_root, source)
    gold_files = _gold_files(source_dir)
    relation_category_counts = _relation_category_counts(source_dir)

    interaction_count = int(relation_category_counts.get('interaction', 0))
    membership_count = int(relation_category_counts.get('membership', 0))
    annotation_count = int(relation_category_counts.get('annotation', 0))
    ontology_term_count = _ontology_entity_count(source_dir)

    return {
        'resource_id': source,
        'resource_name': config.name,
        'description': config.description,
        'homepage_url': config.url,
        'license': format_cv_term(str(config.license)) or str(config.license),
        'pubmed_id': config.pubmed,
        'resource_kind': getattr(config, 'resource_kind', 'data_resource'),
        'categories': _resource_categories(
            interaction_count=interaction_count,
            membership_count=membership_count,
            annotation_count=annotation_count,
            ontology_term_count=ontology_term_count,
        ),
        'annotation_ontologies': _ontology_labels(resource),
        'entity_count': _count_file(source_dir, 'entities/entity.parquet'),
        'interaction_count': interaction_count,
        'membership_count': membership_count,
        'annotation_count': annotation_count,
        'identifier_count': _identifier_count(source_dir),
        'ontology_term_count': ontology_term_count,
        'total_size_bytes': sum(path.stat().st_size for path in gold_files),
        'last_downloaded_at': _resource_download_mtime(resource),
        'last_built_at': _iso_utc(_latest_file_mtime(gold_files)),
        'build_status': 'success' if gold_files else 'not_built',
    }


def build_resources_parquet(
    *,
    gold_root: str | Path = 'data/gold',
    output_path: str | Path = 'data/combined/resources.parquet',
    inputs_package: str = 'pypath.inputs_v2',
) -> Path:
    gold_root = Path(gold_root)
    output_path = Path(output_path)

    discovered, _ = discover_resources(
        database_name='.',
        base_path=None,
        inputs_package=inputs_package,
    )

    rows: list[dict[str, Any]] = []
    for source in sorted(discovered):
        try:
            module = importlib.import_module(f'{inputs_package}.{source}')
        except Exception as exc:  # noqa: BLE001
            print(
                f'[build_resources_parquet] skipping {inputs_package}.{source}: '
                f'{exc.__class__.__name__}: {exc}'
            )
            continue

        resource = getattr(module, 'resource', None)
        if not isinstance(resource, Resource):
            continue

        rows.append(_resource_row(source=source, resource=resource, gold_root=gold_root))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(rows).sort('resource_id').write_parquet(output_path)
    return output_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description='Build resources.parquet from B3 per-source gold outputs.',
    )
    parser.add_argument(
        '--gold-root',
        type=Path,
        default=Path('data/gold'),
        help='Root directory containing per-source gold outputs (default: data/gold)',
    )
    parser.add_argument(
        '--output-path',
        type=Path,
        default=Path('data/combined/resources.parquet'),
        help='Output parquet path (default: data/combined/resources.parquet)',
    )
    parser.add_argument(
        '--inputs-package',
        type=str,
        default='pypath.inputs_v2',
        help='Python package containing resource definitions.',
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    build_resources_parquet(
        gold_root=args.gold_root,
        output_path=args.output_path,
        inputs_package=args.inputs_package,
    )
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
