from __future__ import annotations

import argparse
import logging
from pathlib import Path

import polars as pl

from omnipath_build.gold.build_entity_identifiers_v2 import (
    MERGE_EDGE_DEBUG_SCHEMA,
    REQUIRES_TAX_PARTITION,
    MERGE_SAFE_BY_BUCKET_CODE,
    _canonicalize,
    _entity_bucket,
    build_entity_identifiers_v2,
)
from omnipath_build.gold.build_entity_identifiers import (
    MERGE_UNSAFE_IDENTIFIER_TYPES,
    UNKNOWN_ENTITY_TYPE_KEY,
)

logger = logging.getLogger(__name__)


def _resolve_paths(path: Path) -> tuple[Path, Path]:
    path = path.expanduser().resolve()
    if path.name == 'local_tables':
        local_tables_dir = path
        output_dir = path.parent
    elif (path / 'local_tables').exists():
        local_tables_dir = path / 'local_tables'
        output_dir = path
    else:
        raise FileNotFoundError(
            f'Could not find local_tables under: {path}\n'
            'Pass either a gold build directory or the local_tables directory itself.'
        )
    return local_tables_dir, output_dir


def _load_records_and_ids(local_tables_dir: Path) -> tuple[pl.DataFrame, pl.DataFrame]:
    entity_files = sorted(
        p
        for p in local_tables_dir.rglob('local_entity_*.parquet')
        if 'annotation' not in p.name and 'identifier' not in p.name and 'instance' not in p.name
    )
    identifier_files = sorted(local_tables_dir.rglob('local_entity_identifier_*.parquet'))

    all_entities: list[pl.DataFrame] = []
    for path in entity_files:
        df = pl.read_parquet(path)
        if len(df) == 0:
            continue
        all_entities.append(
            df.select(['source_ref', 'local_entity_id', 'entity_type'])
            .with_columns([
                pl.col('source_ref').cast(pl.Utf8),
                pl.col('entity_type').cast(pl.Utf8),
                pl.lit(None, dtype=pl.Utf8).alias('tax_id'),
            ])
        )

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
        .with_columns(
            pl.when(pl.col('entity_bucket').is_in(list(REQUIRES_TAX_PARTITION)))
            .then(pl.coalesce([pl.col('tax_id'), pl.lit('UNK')]))
            .otherwise(pl.lit(None, dtype=pl.Utf8))
            .alias('tax_partition')
        )
        .select(['source_ref', 'local_entity_id', 'entity_bucket', 'tax_partition'])
    )

    ids_with_context = (
        ids_all
        .join(records.select(['source_ref', 'local_entity_id', 'entity_bucket']), on=['source_ref', 'local_entity_id'], how='left')
        .with_columns(pl.col('entity_bucket').fill_null('X'))
    )

    rows: list[dict[str, object]] = []
    for row in ids_with_context.iter_rows(named=True):
        bucket = str(row['entity_bucket'])
        type_id = str(row['type_id'])
        canonical_identifier = _canonicalize(type_id, str(row['identifier']))
        allowed = MERGE_SAFE_BY_BUCKET_CODE.get(bucket)
        if allowed is not None:
            is_merge_safe = type_id in allowed
        else:
            is_merge_safe = type_id not in MERGE_UNSAFE_IDENTIFIER_TYPES
        rows.append({
            'source_ref': str(row['source_ref']),
            'local_entity_id': int(row['local_entity_id']),
            'entity_bucket': bucket,
            'type_id': type_id,
            'canonical_identifier': canonical_identifier,
            'is_merge_safe': bool(is_merge_safe),
        })

    ids_canonical = pl.DataFrame(rows) if rows else pl.DataFrame({
        'source_ref': pl.Series([], dtype=pl.Utf8),
        'local_entity_id': pl.Series([], dtype=pl.Int64),
        'entity_bucket': pl.Series([], dtype=pl.Utf8),
        'type_id': pl.Series([], dtype=pl.Utf8),
        'canonical_identifier': pl.Series([], dtype=pl.Utf8),
        'is_merge_safe': pl.Series([], dtype=pl.Boolean),
    })

    return records, ids_canonical


def build_merge_edge_debug_snapshot(local_tables_dir: Path) -> tuple[pl.DataFrame, pl.DataFrame]:
    record_identity_snapshot, _, _, _ = build_entity_identifiers_v2(local_tables_dir=local_tables_dir)
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
            'tax_partition',
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
        .group_by(['type_id', 'canonical_identifier', 'entity_bucket', 'tax_partition'])
        .agg(pl.struct(['source_ref', 'local_entity_id']).alias('members'))
    )

    for row in ms_grouped.iter_rows(named=True):
        members = row.get('members') or []
        if len(members) < 2:
            continue

        merge_type_id = str(row['type_id'])
        merge_identifier = str(row['canonical_identifier'])
        entity_bucket = str(row['entity_bucket'])
        tax_partition = row['tax_partition']

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
                'tax_partition': None if tax_partition is None else str(tax_partition),
                'merge_type_id': merge_type_id,
                'merge_identifier': merge_identifier,
                'performed_union': performed_union,
            })

    if not merge_edge_rows:
        return record_identity_snapshot, pl.DataFrame(schema=MERGE_EDGE_DEBUG_SCHEMA)

    merge_edge_debug_snapshot = (
        pl.DataFrame(merge_edge_rows, schema=MERGE_EDGE_DEBUG_SCHEMA)
        .join(
            record_identity_snapshot.select([
                pl.col('run_id'),
                pl.col('source_ref').alias('left_source_ref'),
                pl.col('local_entity_id').alias('left_local_entity_id'),
                'entity_key',
            ]),
            on=['left_source_ref', 'left_local_entity_id'],
            how='left',
        )
        .select([
            'run_id',
            'entity_key',
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
        description='Build a standalone merge-edge debug snapshot from the local_tables of a previous gold build.'
    )
    parser.add_argument(
        'path',
        type=Path,
        help='Path to a gold build directory or directly to its local_tables directory.',
    )
    parser.add_argument(
        '--output',
        type=Path,
        default=None,
        help='Optional output parquet path. Defaults to <gold_dir>/merge_edge_debug_snapshot.parquet',
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')

    local_tables_dir, output_dir = _resolve_paths(args.path)
    output_path = (args.output.expanduser().resolve() if args.output else output_dir / 'merge_edge_debug_snapshot.parquet')

    logger.info('Using local tables: %s', local_tables_dir)
    record_identity_snapshot, merge_edge_debug_snapshot = build_merge_edge_debug_snapshot(local_tables_dir)
    merge_edge_debug_snapshot.write_parquet(output_path)

    logger.info('Record identity rows: %s', f'{len(record_identity_snapshot):,}')
    logger.info('Merge-edge debug rows: %s', f'{len(merge_edge_debug_snapshot):,}')
    logger.info('Wrote: %s', output_path)


if __name__ == '__main__':
    main()
