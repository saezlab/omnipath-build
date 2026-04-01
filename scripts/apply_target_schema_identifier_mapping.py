#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Iterable

import polars as pl

from omnipath_build.target_schema.id_mapping_tables import (
    CHEMICAL_REFERENCE_KEY_TYPES,
    PROTEIN_REFERENCE_KEY_TYPES,
)
from scripts.target_schema_entity_dedup import deduplicate_target_schema_dir

BIO_ENTITY_TYPES = (
    'MI:0326:Protein',
    'MI:0250:Gene',
    'MI:0311:Rna',
    'MI:0319:Dna',
)
CHEM_ENTITY_TYPES = (
    'MI:0328:Small Molecule',
    'OM:0011:Lipid',
)
PROTEIN_FALLBACK_PRIORITY = (
    'OM:0221:Uniprot Entry Name',
    'MI:0477:Entrez',
    'MI:0476:Ensembl',
    'OM:0200:Gene Name Primary',
)
CHEMICAL_PRIORITY = CHEMICAL_REFERENCE_KEY_TYPES


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Apply target-schema identifier mapping using materialized parquet mapping tables.')
    parser.add_argument('sources', nargs='*', help='Optional source names; defaults to all sources under target-schema-root except _mapping_tables.')
    parser.add_argument('--target-schema-root', type=Path, default=Path('data_v2/target_schema'))
    parser.add_argument('--mapping-dir', type=Path, default=Path('data_v2/target_schema/_mapping_tables'))
    parser.add_argument('--dry-run', action='store_true')
    return parser.parse_args()


def _load_unique_uniprot_reference(mapping_dir: Path) -> tuple[pl.DataFrame, set[str]]:
    df = pl.read_parquet(mapping_dir / 'uniprot_reference_mappings.parquet').filter(pl.col('mapping_count') == 1)
    canonical = set(df.filter(pl.col('key_type') == 'MI:1097:Uniprot')['key_value'].to_list())
    return df.select(['key_type', 'key_value', 'taxonomy_id', 'mapped_identifier']), canonical


def _load_unique_secondary_map(mapping_dir: Path) -> pl.DataFrame:
    return (
        pl.read_parquet(mapping_dir / 'uniprot_secondary_to_primary.parquet')
        .group_by('secondary_uniprot')
        .agg([
            pl.col('primary_uniprot').n_unique().alias('mapping_count'),
            pl.col('primary_uniprot').first().alias('mapped_identifier'),
        ])
        .filter(pl.col('mapping_count') == 1)
        .select(['secondary_uniprot', 'mapped_identifier'])
    )


def _load_unique_chemical_reference(mapping_dir: Path) -> pl.DataFrame:
    return (
        pl.read_parquet(mapping_dir / 'chemical_reference_to_standard_inchi.parquet')
        .filter(pl.col('mapping_count') == 1)
        .select(['key_type', 'key_value', 'mapped_identifier'])
    )


def _priority_expr(col_name: str, values: Iterable[str]) -> pl.Expr:
    expr = pl.lit(len(tuple(values)), dtype=pl.Int64)
    for idx, value in reversed(list(enumerate(values))):
        expr = pl.when(pl.col(col_name) == value).then(pl.lit(idx, dtype=pl.Int64)).otherwise(expr)
    return expr


def _protein_mappings(
    identifiers: pl.DataFrame,
    entities: pl.DataFrame,
    uniprot_reference: pl.DataFrame,
    canonical_uniprots: set[str],
    secondary_map: pl.DataFrame,
) -> tuple[pl.DataFrame, dict[str, int]]:
    bio_entities = entities.filter(pl.col('entity_type').is_in(BIO_ENTITY_TYPES)).select(['entity_id', 'taxonomy_id'])
    if bio_entities.is_empty():
        return pl.DataFrame(schema={'entity_id': pl.Int64, 'identifier': pl.Utf8, 'identifier_type': pl.Utf8}), {
            'protein_entities': 0,
            'protein_mapped': 0,
            'protein_direct_uniprot_normalized': 0,
            'protein_fallback_mapped': 0,
        }

    bio_ids = identifiers.join(bio_entities, on='entity_id', how='inner').select([
        'entity_id', 'taxonomy_id', 'identifier_type', 'identifier'
    ]).unique()

    direct_uniprot = bio_ids.filter(pl.col('identifier_type') == 'MI:1097:Uniprot').select([
        'entity_id', 'taxonomy_id', pl.col('identifier').alias('source_identifier')
    ]).unique()

    canonical_direct = (
        direct_uniprot
        .filter(pl.col('source_identifier').is_in(list(canonical_uniprots)))
        .select(['entity_id', pl.col('source_identifier').alias('mapped_identifier')])
        .unique()
    )

    isoform_direct = (
        direct_uniprot
        .with_columns(pl.col('source_identifier').str.replace(r'-\d+$', '').alias('base_identifier'))
        .filter(pl.col('base_identifier') != pl.col('source_identifier'))
        .filter(pl.col('base_identifier').is_in(list(canonical_uniprots)))
        .select(['entity_id', pl.col('base_identifier').alias('mapped_identifier')])
        .unique()
    )

    secondary_direct = (
        direct_uniprot
        .join(secondary_map, left_on='source_identifier', right_on='secondary_uniprot', how='inner')
        .select(['entity_id', 'mapped_identifier'])
        .unique()
    )

    direct_candidates = (
        pl.concat([canonical_direct, isoform_direct, secondary_direct], how='vertical_relaxed')
        .group_by('entity_id')
        .agg([
            pl.col('mapped_identifier').n_unique().alias('n_map'),
            pl.col('mapped_identifier').first().alias('mapped_identifier'),
        ])
        .filter(pl.col('n_map') == 1)
        .select(['entity_id', 'mapped_identifier'])
    )

    direct_entity_ids = set(direct_candidates['entity_id'].to_list()) if not direct_candidates.is_empty() else set()

    fallback_ref = uniprot_reference.filter(pl.col('key_type').is_in(PROTEIN_FALLBACK_PRIORITY))
    fallback_candidates = (
        bio_ids
        .filter(pl.col('identifier_type').is_in(PROTEIN_FALLBACK_PRIORITY))
        .filter(~pl.col('entity_id').is_in(list(direct_entity_ids)))
        .join(
            fallback_ref,
            left_on=['identifier_type', 'identifier', 'taxonomy_id'],
            right_on=['key_type', 'key_value', 'taxonomy_id'],
            how='inner',
        )
        .with_columns(_priority_expr('identifier_type', PROTEIN_FALLBACK_PRIORITY).alias('route_priority'))
        .sort(['entity_id', 'route_priority', 'mapped_identifier'])
        .group_by('entity_id')
        .agg([
            pl.col('mapped_identifier').first().alias('mapped_identifier'),
        ])
    )

    mapped = pl.concat([direct_candidates, fallback_candidates], how='vertical_relaxed').unique().with_columns([
        pl.col('mapped_identifier').alias('identifier'),
        pl.lit('MI:1097:Uniprot').alias('identifier_type'),
    ]).select(['entity_id', 'identifier', 'identifier_type'])

    stats = {
        'protein_entities': int(bio_entities.height),
        'protein_mapped': int(mapped['entity_id'].n_unique()) if not mapped.is_empty() else 0,
        'protein_direct_uniprot_normalized': int(direct_candidates.height),
        'protein_fallback_mapped': int(fallback_candidates.height),
    }
    return mapped, stats


def _chemical_mappings(
    identifiers: pl.DataFrame,
    entities: pl.DataFrame,
    chemical_reference: pl.DataFrame,
) -> tuple[pl.DataFrame, dict[str, int]]:
    chem_entities = entities.filter(pl.col('entity_type').is_in(CHEM_ENTITY_TYPES)).select(['entity_id'])
    if chem_entities.is_empty():
        return pl.DataFrame(schema={'entity_id': pl.Int64, 'identifier': pl.Utf8, 'identifier_type': pl.Utf8}), {
            'chemical_entities': 0,
            'chemical_mapped': 0,
        }

    chem_ids = identifiers.join(chem_entities, on='entity_id', how='inner').select(['entity_id', 'identifier_type', 'identifier']).unique()
    have_inchi = set(
        chem_ids.filter(pl.col('identifier_type') == 'MI:2010:Standard Inchi')['entity_id'].unique().to_list()
    )
    need_entities = set(chem_entities.filter(~pl.col('entity_id').is_in(list(have_inchi)))['entity_id'].to_list())

    candidates = (
        chem_ids
        .filter(pl.col('entity_id').is_in(list(need_entities)))
        .filter(pl.col('identifier_type').is_in(CHEMICAL_PRIORITY))
        .join(
            chemical_reference,
            left_on=['identifier_type', 'identifier'],
            right_on=['key_type', 'key_value'],
            how='inner',
        )
        .with_columns(_priority_expr('identifier_type', CHEMICAL_PRIORITY).alias('route_priority'))
        .sort(['entity_id', 'route_priority', 'mapped_identifier'])
        .group_by('entity_id')
        .agg([
            pl.col('mapped_identifier').first().alias('identifier'),
        ])
        .with_columns(pl.lit('MI:2010:Standard Inchi').alias('identifier_type'))
        .select(['entity_id', 'identifier', 'identifier_type'])
    )

    stats = {
        'chemical_entities': int(chem_entities.height),
        'chemical_mapped': int(candidates['entity_id'].n_unique()) if not candidates.is_empty() else 0,
    }
    return candidates, stats


def apply_identifier_mapping_to_source(
    source_dir: Path,
    mapping_dir: Path,
    dry_run: bool = False,
) -> dict[str, int]:
    entities_path = source_dir / 'entities.parquet'
    identifiers_path = source_dir / 'entity_identifiers.parquet'
    if not entities_path.exists() or not identifiers_path.exists():
        return {'added_identifier_rows': 0}

    entities = pl.read_parquet(entities_path)
    identifiers = pl.read_parquet(identifiers_path)
    if entities.is_empty() or identifiers.is_empty():
        return {'added_identifier_rows': 0}

    uniprot_reference, canonical_uniprots = _load_unique_uniprot_reference(mapping_dir)
    secondary_map = _load_unique_secondary_map(mapping_dir)
    chemical_reference = _load_unique_chemical_reference(mapping_dir)

    protein_rows, protein_stats = _protein_mappings(
        identifiers=identifiers,
        entities=entities,
        uniprot_reference=uniprot_reference,
        canonical_uniprots=canonical_uniprots,
        secondary_map=secondary_map,
    )
    chemical_rows, chemical_stats = _chemical_mappings(
        identifiers=identifiers,
        entities=entities,
        chemical_reference=chemical_reference,
    )

    new_rows = pl.concat([protein_rows, chemical_rows], how='vertical_relaxed') if (not protein_rows.is_empty() or not chemical_rows.is_empty()) else pl.DataFrame(schema={'entity_id': pl.Int64, 'identifier': pl.Utf8, 'identifier_type': pl.Utf8})
    if new_rows.is_empty():
        return {
            **protein_stats,
            **chemical_stats,
            'added_identifier_rows': 0,
            'dedup_merged_entities': 0,
        }

    existing_keys = identifiers.select(['entity_id', 'identifier', 'identifier_type']).unique()
    additions = (
        new_rows
        .unique()
        .join(existing_keys, on=['entity_id', 'identifier', 'identifier_type'], how='anti')
        .with_columns([
            pl.lit(False).alias('is_canonical'),
            pl.lit(source_dir.name).alias('source'),
        ])
        .select(identifiers.columns)
    )

    if additions.is_empty():
        return {
            **protein_stats,
            **chemical_stats,
            'added_identifier_rows': 0,
            'dedup_merged_entities': 0,
        }

    if not dry_run:
        updated_identifiers = pl.concat([identifiers, additions], how='vertical_relaxed')
        updated_identifiers.write_parquet(identifiers_path)
        dedup_summary = deduplicate_target_schema_dir(source_dir)
    else:
        dedup_summary = {'merged_entities': 0}

    return {
        **protein_stats,
        **chemical_stats,
        'added_identifier_rows': int(additions.height),
        'dedup_merged_entities': int(dedup_summary.get('merged_entities', 0)),
    }


def main() -> int:
    args = parse_args()
    if args.sources:
        sources = args.sources
    else:
        sources = sorted(
            p.name for p in args.target_schema_root.iterdir()
            if p.is_dir() and p.name != '_mapping_tables'
        )

    for source in sources:
        source_dir = args.target_schema_root / source
        summary = apply_identifier_mapping_to_source(
            source_dir=source_dir,
            mapping_dir=args.mapping_dir,
            dry_run=args.dry_run,
        )
        print(f'[{source}] {summary}')

    return 0


if __name__ == '__main__':
    raise SystemExit(main())
