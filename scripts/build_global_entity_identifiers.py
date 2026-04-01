#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

import polars as pl

GLOBAL_IDENTIFIERS_SCHEMA = {
    'global_entity_id': pl.Int64,
    'identifier': pl.Utf8,
    'identifier_type': pl.Utf8,
    'taxonomy_id': pl.Utf8,
    'is_canonical': pl.Boolean,
    'sources': pl.List(pl.Utf8),
}

BRIDGE_SCHEMA = {
    'source': pl.Utf8,
    'source_entity_id': pl.Int64,
    'global_entity_id': pl.Int64,
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Build global_entity_identifiers from per-source target-schema outputs.')
    parser.add_argument('--target-schema-root', type=Path, default=Path('data_v2/gold'))
    parser.add_argument('--output-dir', type=Path, default=Path('data_v2/gold/_global'))
    parser.add_argument('sources', nargs='*', help='Optional sources to process in order; default is all source dirs under target-schema-root except _mapping_tables.')
    return parser.parse_args()


def _empty_df(schema: dict[str, pl.DataType]) -> pl.DataFrame:
    return pl.DataFrame({k: pl.Series([], dtype=v) for k, v in schema.items()})


def _load_or_empty(path: Path, schema: dict[str, pl.DataType]) -> pl.DataFrame:
    return pl.read_parquet(path) if path.exists() else _empty_df(schema)


def _global_keys_from_identifiers(global_identifiers: pl.DataFrame) -> pl.DataFrame:
    if global_identifiers.is_empty():
        return _empty_df({
            'global_entity_id': pl.Int64,
            'canonical_identifier': pl.Utf8,
            'canonical_identifier_type': pl.Utf8,
            'taxonomy_id': pl.Utf8,
        })
    return (
        global_identifiers
        .filter(pl.col('is_canonical'))
        .select([
            'global_entity_id',
            pl.col('identifier').alias('canonical_identifier'),
            pl.col('identifier_type').alias('canonical_identifier_type'),
            'taxonomy_id',
        ])
        .unique()
    )


def _normalize_taxonomy(expr: pl.Expr) -> pl.Expr:
    return pl.when(expr.is_null() | (expr == '')).then(pl.lit(None, dtype=pl.Utf8)).otherwise(expr.cast(pl.Utf8))


def _discover_sources(root: Path) -> list[str]:
    return sorted(
        p.name for p in root.iterdir()
        if p.is_dir() and p.name != '_mapping_tables'
    )


def _read_source_entities(source_dir: Path, source: str) -> pl.DataFrame:
    path = source_dir / 'entities.parquet'
    if not path.exists():
        return _empty_df({
            'source': pl.Utf8,
            'source_entity_id': pl.Int64,
            'canonical_identifier': pl.Utf8,
            'canonical_identifier_type': pl.Utf8,
            'taxonomy_id': pl.Utf8,
        })

    return (
        pl.read_parquet(path)
        .select([
            pl.lit(source).alias('source'),
            pl.col('entity_id').alias('source_entity_id'),
            pl.col('canonical_identifier').cast(pl.Utf8),
            pl.col('canonical_identifier_type').cast(pl.Utf8),
            _normalize_taxonomy(pl.col('taxonomy_id')).alias('taxonomy_id'),
        ])
        .filter(pl.col('canonical_identifier').is_not_null() & pl.col('canonical_identifier_type').is_not_null())
        .unique()
    )


def _read_source_identifiers(source_dir: Path, source: str) -> pl.DataFrame:
    path = source_dir / 'entity_identifiers.parquet'
    if not path.exists():
        return _empty_df({
            'source': pl.Utf8,
            'source_entity_id': pl.Int64,
            'identifier': pl.Utf8,
            'identifier_type': pl.Utf8,
            'is_canonical': pl.Boolean,
        })

    return (
        pl.read_parquet(path)
        .select([
            pl.lit(source).alias('source'),
            pl.col('entity_id').alias('source_entity_id'),
            pl.col('identifier').cast(pl.Utf8),
            pl.col('identifier_type').cast(pl.Utf8),
            pl.col('is_canonical').cast(pl.Boolean),
        ])
        .filter(pl.col('identifier').is_not_null() & pl.col('identifier_type').is_not_null())
        .unique()
    )


def _assign_global_ids(source_keys: pl.DataFrame, global_keys: pl.DataFrame) -> tuple[pl.DataFrame, pl.DataFrame, int, int]:
    join_cols = ['canonical_identifier', 'canonical_identifier_type', 'taxonomy_id']
    matched = source_keys.join(global_keys, on=join_cols, how='inner')
    unmatched = source_keys.join(global_keys, on=join_cols, how='anti')

    max_global_id = 0 if global_keys.is_empty() else int(global_keys['global_entity_id'].max())
    if unmatched.is_empty():
        new_keys = _empty_df({
            'global_entity_id': pl.Int64,
            'canonical_identifier': pl.Utf8,
            'canonical_identifier_type': pl.Utf8,
            'taxonomy_id': pl.Utf8,
        })
    else:
        new_keys = (
            unmatched
            .sort(join_cols)
            .with_row_index('row_idx', offset=1)
            .with_columns((pl.col('row_idx') + pl.lit(max_global_id)).cast(pl.Int64).alias('global_entity_id'))
            .select(['global_entity_id', *join_cols])
        )

    key_map = pl.concat([
        matched.select(['global_entity_id', *join_cols]),
        new_keys,
    ], how='vertical_relaxed').unique()

    return key_map, new_keys, int(matched.height), int(new_keys.height)


def _merge_global_identifiers(existing: pl.DataFrame, incoming: pl.DataFrame) -> pl.DataFrame:
    if existing.is_empty():
        return incoming.sort(['global_entity_id', 'identifier_type', 'identifier'])
    if incoming.is_empty():
        return existing.sort(['global_entity_id', 'identifier_type', 'identifier'])

    combined = pl.concat([existing, incoming], how='vertical_relaxed')
    return (
        combined
        .group_by(['global_entity_id', 'identifier', 'identifier_type', 'taxonomy_id'])
        .agg([
            pl.col('is_canonical').any().alias('is_canonical'),
            pl.col('sources').explode().drop_nulls().unique().sort().alias('sources'),
        ])
        .sort(['global_entity_id', 'identifier_type', 'identifier'])
    )


def process_source(source: str, source_dir: Path, output_dir: Path) -> dict[str, int]:
    global_identifiers_path = output_dir / 'global_entity_identifiers.parquet'
    bridge_path = output_dir / 'source_entity_to_global_entity.parquet'

    global_identifiers = _load_or_empty(global_identifiers_path, GLOBAL_IDENTIFIERS_SCHEMA)
    global_keys = _global_keys_from_identifiers(global_identifiers)
    existing_bridge = _load_or_empty(bridge_path, BRIDGE_SCHEMA)

    source_entities = _read_source_entities(source_dir, source)
    source_identifiers = _read_source_identifiers(source_dir, source)

    if source_entities.is_empty():
        return {
            'source_entities_with_canonical_key': 0,
            'matched_existing_global_entities': 0,
            'new_global_entities_added': 0,
            'global_identifier_rows_added': 0,
        }

    source_keys = source_entities.select(['canonical_identifier', 'canonical_identifier_type', 'taxonomy_id']).unique()
    key_map, new_keys, matched_existing, new_global = _assign_global_ids(source_keys, global_keys)

    source_bridge = (
        source_entities
        .join(key_map, on=['canonical_identifier', 'canonical_identifier_type', 'taxonomy_id'], how='inner')
        .select(['source', 'source_entity_id', 'global_entity_id'])
        .unique()
    )

    incoming_identifiers = (
        source_identifiers
        .join(source_bridge, on=['source', 'source_entity_id'], how='inner')
        .join(source_entities.select(['source', 'source_entity_id', 'taxonomy_id']), on=['source', 'source_entity_id'], how='inner')
        .group_by(['global_entity_id', 'identifier', 'identifier_type', 'taxonomy_id'])
        .agg([
            pl.col('is_canonical').any().alias('is_canonical'),
            pl.col('source').unique().sort().alias('sources'),
        ])
        .sort(['global_entity_id', 'identifier_type', 'identifier'])
    )

    before_rows = 0 if global_identifiers.is_empty() else int(global_identifiers.height)
    updated_global_identifiers = _merge_global_identifiers(global_identifiers, incoming_identifiers)
    after_rows = int(updated_global_identifiers.height)
    added_rows = after_rows - before_rows

    updated_bridge = pl.concat([existing_bridge, source_bridge], how='vertical_relaxed').unique().sort(['source', 'source_entity_id'])

    updated_global_identifiers.write_parquet(global_identifiers_path)
    updated_bridge.write_parquet(bridge_path)

    return {
        'source_entities_with_canonical_key': int(source_entities.height),
        'matched_existing_global_entities': matched_existing,
        'new_global_entities_added': new_global,
        'global_identifier_rows_added': added_rows,
    }


def main() -> int:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    sources = args.sources or _discover_sources(args.target_schema_root)
    total_matched = 0
    total_new = 0
    total_identifier_rows_added = 0

    for source in sources:
        source_dir = args.target_schema_root / source
        summary = process_source(source, source_dir, args.output_dir)
        total_matched += summary['matched_existing_global_entities']
        total_new += summary['new_global_entities_added']
        total_identifier_rows_added += summary['global_identifier_rows_added']
        print(f'[{source}] {summary}')

    global_identifiers = _load_or_empty(args.output_dir / 'global_entity_identifiers.parquet', GLOBAL_IDENTIFIERS_SCHEMA)
    global_keys = _global_keys_from_identifiers(global_identifiers)

    print('\nFinal summary:')
    print(f'  global entities: {len(global_keys):,}')
    print(f'  global identifier rows: {len(global_identifiers):,}')
    print(f'  matched existing global entities: {total_matched:,}')
    print(f'  new global entities added: {total_new:,}')
    print(f'  global identifier rows added: {total_identifier_rows_added:,}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
