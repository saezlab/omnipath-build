from __future__ import annotations

import argparse
import logging
import os
import tempfile
from pathlib import Path

import polars as pl

from omnipath_build.gold.build_entity_identifiers_v2 import (
    MERGE_SAFE_BY_BUCKET_CODE,
    _canonicalize,
    _entity_bucket,
    _extract_tax_annotations,
    _is_tax_scoped_gene_name_merge_safe,
    build_entity_identifiers_v2,
)
from omnipath_build.gold.build_entity_identifiers import (
    MERGE_UNSAFE_IDENTIFIER_TYPES,
    UNKNOWN_ENTITY_TYPE_KEY,
)

logger = logging.getLogger(__name__)

MERGE_EDGE_DEBUG_SCHEMA: dict[str, pl.DataType] = {
    'run_id': pl.Utf8,
    'entity_key': pl.Utf8,
    'left_source_ref': pl.Utf8,
    'left_local_entity_id': pl.Int64,
    'right_source_ref': pl.Utf8,
    'right_local_entity_id': pl.Int64,
    'entity_bucket': pl.Utf8,
    'tax_partition': pl.Utf8,
    'merge_type_id': pl.Utf8,
    'merge_identifier': pl.Utf8,
    'performed_union': pl.Boolean,
}


def _iter_search_roots(path: Path) -> list[Path]:
    roots: list[Path] = []
    seen: set[Path] = set()

    def add(candidate: Path) -> None:
        candidate = candidate.expanduser()
        try:
            resolved = candidate.resolve()
        except FileNotFoundError:
            return
        if not resolved.exists() or resolved in seen:
            return
        seen.add(resolved)
        roots.append(resolved)

    add(path)
    add(path / 'local_tables')

    if path.exists() and path.is_dir():
        for child in path.iterdir():
            if not child.is_dir() and not child.is_symlink():
                continue
            add(child)
            add(child / 'local_tables')
            add(child / 'gold')
            add(child / 'gold' / 'local_tables')

    return roots


def _resolve_paths(path: Path) -> tuple[Path, Path]:
    path = path.expanduser().resolve()
    search_roots = _iter_search_roots(path)

    entity_files: list[Path] = []
    identifier_files: list[Path] = []
    for root in search_roots:
        entity_files.extend([
            p for p in root.glob('local_entity_*.parquet')
            if 'annotation' not in p.name and 'identifier' not in p.name and 'instance' not in p.name
        ])
        identifier_files.extend(root.glob('local_entity_identifier_*.parquet'))

    if not entity_files and not identifier_files:
        raise FileNotFoundError(
            f'Could not find local entity parquet files under: {path}\n'
            'Pass one of:\n'
            '- a gold build directory\n'
            '- a local_tables directory\n'
            '- or a per_source directory containing many */gold local table symlinks.'
        )

    return path, path


def _collect_local_table_files(path: Path) -> tuple[list[Path], list[Path], list[Path]]:
    entity_files: list[Path] = []
    identifier_files: list[Path] = []
    instance_files: list[Path] = []
    seen: set[Path] = set()

    for root in _iter_search_roots(path):
        for candidate in root.glob('local_entity_*.parquet'):
            if 'annotation' in candidate.name or 'identifier' in candidate.name or 'instance' in candidate.name:
                continue
            resolved = candidate.resolve()
            if resolved not in seen:
                seen.add(resolved)
                entity_files.append(resolved)
        for candidate in root.glob('local_entity_identifier_*.parquet'):
            resolved = candidate.resolve()
            if resolved not in seen:
                seen.add(resolved)
                identifier_files.append(resolved)
        for candidate in root.glob('local_entity_instance_*.parquet'):
            resolved = candidate.resolve()
            if resolved not in seen:
                seen.add(resolved)
                instance_files.append(resolved)

    return sorted(entity_files), sorted(identifier_files), sorted(instance_files)


def _load_records_and_ids(local_tables_dir: Path) -> tuple[pl.DataFrame, pl.DataFrame]:
    entity_files, identifier_files, _ = _collect_local_table_files(local_tables_dir)

    tax_annotations = _extract_tax_annotations(local_tables_dir)

    all_entities: list[pl.DataFrame] = []
    for path in entity_files:
        df = pl.read_parquet(path)
        if len(df) == 0:
            continue
        entity_part = (
            df.select(['source_ref', 'local_entity_id', 'entity_type'])
            .with_columns([
                pl.col('source_ref').cast(pl.Utf8),
                pl.col('local_entity_id').cast(pl.Int64),
                pl.col('entity_type').cast(pl.Utf8),
            ])
        )
        if len(tax_annotations) > 0:
            entity_part = entity_part.join(
                tax_annotations,
                on=['source_ref', 'local_entity_id'],
                how='left',
            )
        else:
            entity_part = entity_part.with_columns(pl.lit(None, dtype=pl.Utf8).alias('tax_id'))
        all_entities.append(entity_part)

    if not all_entities:
        return pl.DataFrame(), pl.DataFrame()

    all_identifiers: list[pl.DataFrame] = []
    for path in identifier_files:
        df = pl.read_parquet(path)
        if len(df) == 0:
            continue
        all_identifiers.append(
            df.select([
                'source_ref',
                'local_entity_id',
                pl.col('type_id').cast(pl.Utf8).alias('type_id'),
                pl.col('identifier').cast(pl.Utf8).alias('identifier'),
            ])
            .filter(pl.col('type_id').is_not_null() & pl.col('identifier').is_not_null())
        )

    entities_all = pl.concat(all_entities, how='diagonal_relaxed').unique(subset=['source_ref', 'local_entity_id'])
    ids_all = pl.concat(all_identifiers, how='diagonal_relaxed') if all_identifiers else pl.DataFrame({
        'source_ref': pl.Series([], dtype=pl.Utf8),
        'local_entity_id': pl.Series([], dtype=pl.Int64),
        'type_id': pl.Series([], dtype=pl.Utf8),
        'identifier': pl.Series([], dtype=pl.Utf8),
    })

    records = (
        entities_all
        .with_columns(pl.col('entity_type').fill_null(UNKNOWN_ENTITY_TYPE_KEY))
        .with_columns(pl.col('entity_type').map_elements(_entity_bucket, return_dtype=pl.Utf8).alias('entity_bucket'))
        .with_columns(pl.col('tax_id').cast(pl.Utf8))
        .select(['source_ref', 'local_entity_id', 'entity_bucket', 'tax_id'])
    )

    ids_with_context = (
        ids_all
        .join(
            records.select(['source_ref', 'local_entity_id', 'entity_bucket', 'tax_id']),
            on=['source_ref', 'local_entity_id'],
            how='left',
        )
        .with_columns(pl.col('entity_bucket').fill_null('X'))
    )

    rows: list[dict[str, object]] = []
    for row in ids_with_context.iter_rows(named=True):
        bucket = str(row['entity_bucket'])
        type_id = str(row['type_id'])
        canonical_identifier = _canonicalize(type_id, str(row['identifier']))
        tax_id = row.get('tax_id')
        allowed = MERGE_SAFE_BY_BUCKET_CODE.get(bucket)
        if _is_tax_scoped_gene_name_merge_safe(bucket, type_id, tax_id):
            is_merge_safe = True
            merge_partition = str(tax_id)
        elif allowed is not None:
            is_merge_safe = type_id in allowed
            merge_partition = None
        else:
            is_merge_safe = type_id not in MERGE_UNSAFE_IDENTIFIER_TYPES
            merge_partition = None
        rows.append({
            'source_ref': str(row['source_ref']),
            'local_entity_id': int(row['local_entity_id']),
            'entity_bucket': bucket,
            'type_id': type_id,
            'canonical_identifier': canonical_identifier,
            'is_merge_safe': bool(is_merge_safe),
            'merge_partition': merge_partition,
        })

    ids_canonical = pl.DataFrame(rows) if rows else pl.DataFrame({
        'source_ref': pl.Series([], dtype=pl.Utf8),
        'local_entity_id': pl.Series([], dtype=pl.Int64),
        'entity_bucket': pl.Series([], dtype=pl.Utf8),
        'type_id': pl.Series([], dtype=pl.Utf8),
        'canonical_identifier': pl.Series([], dtype=pl.Utf8),
        'is_merge_safe': pl.Series([], dtype=pl.Boolean),
        'merge_partition': pl.Series([], dtype=pl.Utf8),
    })

    return records, ids_canonical


def _materialize_local_tables_view(input_root: Path) -> Path:
    entity_files, identifier_files, instance_files = _collect_local_table_files(input_root)
    temp_dir = Path(tempfile.mkdtemp(prefix='merge_edge_debug_'))

    for source_file in [*entity_files, *identifier_files, *instance_files]:
        link_path = temp_dir / source_file.name
        try:
            os.symlink(source_file, link_path)
        except FileExistsError:
            pass

    return temp_dir


def build_merge_edge_debug_snapshot(local_tables_dir: Path) -> tuple[pl.DataFrame, pl.DataFrame]:
    materialized_local_tables_dir = _materialize_local_tables_view(local_tables_dir)
    record_identity_snapshot, _, _, _ = build_entity_identifiers_v2(local_tables_dir=materialized_local_tables_dir)
    records, ids_canonical = _load_records_and_ids(local_tables_dir)

    if len(records) == 0 or len(ids_canonical) == 0:
        return record_identity_snapshot, pl.DataFrame(schema=MERGE_EDGE_DEBUG_SCHEMA)

    ms_edges = (
        ids_canonical
        .filter(pl.col('is_merge_safe'))
        .join(
            records,
            on=['source_ref', 'local_entity_id', 'entity_bucket'],
            how='inner',
        )
        .select([
            'source_ref',
            'local_entity_id',
            'entity_bucket',
            'merge_partition',
            'type_id',
            'canonical_identifier',
        ])
        .unique()
    )

    record_keys = [
        (str(row['source_ref']), int(row['local_entity_id']))
        for row in records.select(['source_ref', 'local_entity_id']).unique().iter_rows(named=True)
    ]
    rec_index = {rk: i for i, rk in enumerate(record_keys)}
    parent = list(range(len(record_keys)))
    rank = [0] * len(record_keys)

    def _find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def _union(i: int, j: int) -> bool:
        ri = _find(i)
        rj = _find(j)
        if ri == rj:
            return False
        if rank[ri] < rank[rj]:
            parent[ri] = rj
        elif rank[ri] > rank[rj]:
            parent[rj] = ri
        else:
            parent[rj] = ri
            rank[ri] += 1
        return True

    merge_edge_rows: list[dict[str, object]] = []
    ms_grouped = (
        ms_edges
        .group_by(['type_id', 'canonical_identifier', 'entity_bucket', 'merge_partition'])
        .agg(pl.struct(['source_ref', 'local_entity_id']).alias('members'))
    )

    for row in ms_grouped.iter_rows(named=True):
        members = row.get('members') or []
        if len(members) < 2:
            continue

        merge_type_id = str(row['type_id'])
        merge_identifier = str(row['canonical_identifier'])
        entity_bucket = str(row['entity_bucket'])
        merge_partition = row['merge_partition']

        base = members[0]
        base_key = (str(base['source_ref']), int(base['local_entity_id']))
        base_idx = rec_index.get(base_key)
        if base_idx is None:
            continue

        for member in members[1:]:
            other_key = (str(member['source_ref']), int(member['local_entity_id']))
            other_idx = rec_index.get(other_key)
            if other_idx is None:
                continue
            performed_union = _union(base_idx, other_idx)
            merge_edge_rows.append({
                'left_source_ref': base_key[0],
                'left_local_entity_id': base_key[1],
                'right_source_ref': other_key[0],
                'right_local_entity_id': other_key[1],
                'entity_bucket': entity_bucket,
                'tax_partition': None if merge_partition is None else str(merge_partition),
                'merge_type_id': merge_type_id,
                'merge_identifier': merge_identifier,
                'performed_union': performed_union,
            })

    if not merge_edge_rows:
        return record_identity_snapshot, pl.DataFrame(schema=MERGE_EDGE_DEBUG_SCHEMA)

    merge_edge_debug_snapshot = (
        pl.DataFrame(merge_edge_rows)
        .join(
            record_identity_snapshot.select([
                pl.col('run_id').alias('snapshot_run_id'),
                pl.col('source_ref').alias('left_source_ref'),
                pl.col('local_entity_id').alias('left_local_entity_id'),
                pl.col('entity_key').alias('snapshot_entity_key'),
            ]),
            on=['left_source_ref', 'left_local_entity_id'],
            how='left',
        )
        .select([
            pl.col('snapshot_run_id').alias('run_id'),
            pl.col('snapshot_entity_key').alias('entity_key'),
            'left_source_ref',
            'left_local_entity_id',
            'right_source_ref',
            'right_local_entity_id',
            'entity_bucket',
            'tax_partition',
            'merge_type_id',
            'merge_identifier',
            'performed_union',
        ])
        .sort([
            'entity_key',
            'merge_type_id',
            'merge_identifier',
            'left_source_ref',
            'left_local_entity_id',
            'right_source_ref',
            'right_local_entity_id',
        ])
    )

    return record_identity_snapshot, merge_edge_debug_snapshot


def main() -> None:
    parser = argparse.ArgumentParser(
        description='Build a standalone merge-edge debug snapshot from previous local tables, including recursive scans across per_source directories.'
    )
    parser.add_argument(
        'path',
        type=Path,
        help='Path to a gold build directory, a local_tables directory, or a per_source directory containing many source gold outputs.',
    )
    parser.add_argument(
        '--output',
        type=Path,
        default=None,
        help='Optional output parquet path. Defaults to <input_path>/merge_edge_debug_snapshot.parquet',
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')

    local_tables_dir, output_dir = _resolve_paths(args.path)
    output_path = (args.output.expanduser().resolve() if args.output else output_dir / 'merge_edge_debug_snapshot.parquet')

    entity_files, identifier_files, _ = _collect_local_table_files(local_tables_dir)

    logger.info('Using input root: %s', local_tables_dir)
    logger.info('Found entity files: %s', len(entity_files))
    logger.info('Found identifier files: %s', len(identifier_files))

    record_identity_snapshot, merge_edge_debug_snapshot = build_merge_edge_debug_snapshot(local_tables_dir)
    merge_edge_debug_snapshot.write_parquet(output_path)

    logger.info('Record identity rows: %s', f'{len(record_identity_snapshot):,}')
    logger.info('Merge-edge debug rows: %s', f'{len(merge_edge_debug_snapshot):,}')
    logger.info('Wrote: %s', output_path)


if __name__ == '__main__':
    main()
