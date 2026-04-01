#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

import polars as pl

from omnipath_build.target_schema.id_mapping_tables import (
    CHEMICAL_REFERENCE_KEY_TYPES,
    DEFAULT_CHEMICAL_REFERENCE_SOURCES,
    PROTEIN_REFERENCE_KEY_TYPES,
    materialize_mapping_tables,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Materialize optimized identifier mapping tables for target-schema mapping.')
    parser.add_argument('--target-schema-root', type=Path, default=Path('data_v2/target_schema'))
    parser.add_argument('--output-dir', type=Path, default=Path('data_v2/target_schema/_mapping_tables'))
    parser.add_argument(
        '--chemical-reference-sources',
        nargs='*',
        default=list(DEFAULT_CHEMICAL_REFERENCE_SOURCES),
    )
    return parser.parse_args()


def _print_mapping_quality(path: Path, key_types: tuple[str, ...]) -> None:
    df = pl.read_parquet(path)
    if df.is_empty():
        print(f'  {path.name}: empty')
        return
    print(f'  {path.name}: {len(df):,} rows')
    summary = (
        df.filter(pl.col('key_type').is_in(list(key_types)))
        .group_by('key_type')
        .agg([
            pl.len().alias('rows'),
            (pl.col('mapping_count') == 1).sum().alias('unique_rows'),
            (pl.col('mapping_count') > 1).sum().alias('ambiguous_rows'),
        ])
        .sort('key_type')
    )
    print(summary)


def main() -> int:
    args = parse_args()
    summary = materialize_mapping_tables(
        target_schema_root=args.target_schema_root,
        output_dir=args.output_dir,
        chemical_reference_sources=args.chemical_reference_sources,
    )
    print(f'Materialized mapping tables in {args.output_dir}')
    print(summary)
    print('Protein mapping quality:')
    _print_mapping_quality(args.output_dir / 'uniprot_reference_mappings.parquet', PROTEIN_REFERENCE_KEY_TYPES)
    print('Chemical mapping quality:')
    _print_mapping_quality(args.output_dir / 'chemical_reference_to_standard_inchi.parquet', CHEMICAL_REFERENCE_KEY_TYPES)
    print('Secondary UniProt table:')
    sec = pl.read_parquet(args.output_dir / 'uniprot_secondary_to_primary.parquet')
    print(f'  rows={len(sec):,}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
