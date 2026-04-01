from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Iterable

import polars as pl

from pypath.inputs import uniprot as uniprot_input

PROTEIN_REFERENCE_KEY_TYPES: tuple[str, ...] = (
    'MI:1097:Uniprot',
    'OM:0221:Uniprot Entry Name',
    'OM:0200:Gene Name Primary',
    'MI:0477:Entrez',
    'MI:0476:Ensembl',
)

TAXONOMY_SCOPED_PROTEIN_KEY_TYPES: frozenset[str] = frozenset({
    'OM:0200:Gene Name Primary',
    'MI:0477:Entrez',
    'MI:0476:Ensembl',
})

CHEMICAL_REFERENCE_KEY_TYPES: tuple[str, ...] = (
    'MI:0474:Chebi',
    'OM:0004:Hmdb',
    'OM:0003:Lipidmaps',
    'OM:0009:Swisslipids',
    'OM:0002:Pubchem Compound',
    'MI:0730:Pubchem',
    'MI:0967:Chembl Compound',
    'MI:2002:Drugbank',
    'MI:2012:Kegg Compound',
    'OM:0006:Bindingdb',
)

DEFAULT_CHEMICAL_REFERENCE_SOURCES: tuple[str, ...] = (
    'chebi',
    'hmdb',
    'lipidmaps',
    'swisslipids',
    'bindingdb',
    'foodb',
)

CHEMICAL_STAGING_SCHEMA = {
    'key_type': pl.Utf8,
    'key_value': pl.Utf8,
    'mapped_identifier': pl.Utf8,
    'reference_source': pl.Utf8,
}


def _target_schema_source_dir(root: str | Path, source: str) -> Path:
    return Path(root) / source


def _read_entities(root: str | Path, source: str) -> pl.DataFrame:
    return pl.read_parquet(_target_schema_source_dir(root, source) / 'entities.parquet')


def _read_identifiers(root: str | Path, source: str) -> pl.DataFrame:
    return pl.read_parquet(_target_schema_source_dir(root, source) / 'entity_identifiers.parquet')


def _empty_df(schema: dict[str, pl.DataType]) -> pl.DataFrame:
    return pl.DataFrame({k: pl.Series([], dtype=v) for k, v in schema.items()})


def build_uniprot_reference_mappings(target_schema_root: str | Path) -> pl.DataFrame:
    entities = _read_entities(target_schema_root, 'uniprot').select([
        'entity_id',
        'canonical_identifier',
        'taxonomy_id',
    ])
    identifiers = _read_identifiers(target_schema_root, 'uniprot').filter(
        pl.col('identifier_type').is_in(PROTEIN_REFERENCE_KEY_TYPES)
    )

    rows: list[pl.DataFrame] = []
    for key_type in PROTEIN_REFERENCE_KEY_TYPES:
        scoped = key_type in TAXONOMY_SCOPED_PROTEIN_KEY_TYPES
        current = (
            identifiers
            .filter(pl.col('identifier_type') == key_type)
            .join(entities, on='entity_id', how='inner')
            .filter(pl.col('canonical_identifier').is_not_null())
            .select([
                pl.col('identifier_type').alias('key_type'),
                pl.col('identifier').alias('key_value'),
                pl.when(pl.lit(scoped)).then(pl.col('taxonomy_id')).otherwise(pl.lit(None, dtype=pl.Utf8)).alias('taxonomy_id'),
                pl.col('canonical_identifier').alias('mapped_identifier'),
            ])
        )
        if not current.is_empty():
            rows.append(current)

    if not rows:
        return pl.DataFrame({
            'key_type': [],
            'key_value': [],
            'taxonomy_id': [],
            'mapped_identifier': [],
            'mapping_count': [],
        })

    combined = pl.concat(rows, how='vertical_relaxed')
    return (
        combined
        .group_by(['key_type', 'key_value', 'taxonomy_id'])
        .agg([
            pl.col('mapped_identifier').n_unique().alias('mapping_count'),
            pl.col('mapped_identifier').first().alias('mapped_identifier'),
        ])
        .sort(['key_type', 'key_value', 'taxonomy_id'])
    )


def build_uniprot_secondary_accession_map() -> pl.DataFrame:
    rows = [
        (secondary, primary)
        for secondary, primary in uniprot_input.get_uniprot_sec(organism=None)
    ]
    if not rows:
        return pl.DataFrame({
            'secondary_uniprot': [],
            'primary_uniprot': [],
        })
    return (
        pl.DataFrame(rows, schema=['secondary_uniprot', 'primary_uniprot'], orient='row')
        .unique()
        .sort(['secondary_uniprot', 'primary_uniprot'])
    )


def extract_chemical_reference_pairs_for_source(
    target_schema_root: str | Path,
    source: str,
) -> pl.DataFrame:
    identifiers = _read_identifiers(target_schema_root, source)
    grouped = (
        identifiers
        .filter(pl.col('identifier_type').is_in(('MI:2010:Standard Inchi', *CHEMICAL_REFERENCE_KEY_TYPES)))
        .group_by('entity_id')
        .agg(pl.struct(['identifier_type', 'identifier']).alias('pairs'))
    )

    rows: list[tuple[str, str, str, str]] = []
    for row in grouped.iter_rows(named=True):
        values: dict[str, set[str]] = defaultdict(set)
        for pair in row.get('pairs') or []:
            values[str(pair['identifier_type'])].add(str(pair['identifier']))

        inchis = values.get('MI:2010:Standard Inchi', set())
        if len(inchis) != 1:
            continue

        mapped_inchi = next(iter(inchis))
        for key_type in CHEMICAL_REFERENCE_KEY_TYPES:
            for key_value in values.get(key_type, set()):
                rows.append((key_type, key_value, mapped_inchi, source))

    if not rows:
        return _empty_df(CHEMICAL_STAGING_SCHEMA)

    return (
        pl.DataFrame(
            rows,
            schema=['key_type', 'key_value', 'mapped_identifier', 'reference_source'],
            orient='row',
        )
        .unique()
        .sort(['key_type', 'key_value', 'mapped_identifier'])
    )


def stage_chemical_reference_pairs(
    target_schema_root: str | Path,
    staging_dir: str | Path,
    reference_sources: Iterable[str] = DEFAULT_CHEMICAL_REFERENCE_SOURCES,
) -> dict[str, int]:
    staging_dir = Path(staging_dir)
    staging_dir.mkdir(parents=True, exist_ok=True)
    summary: dict[str, int] = {}

    for source in reference_sources:
        pairs = extract_chemical_reference_pairs_for_source(target_schema_root, source)
        pairs.write_parquet(staging_dir / f'{source}.parquet')
        summary[source] = int(pairs.height)

    return summary


def consolidate_staged_chemical_reference_pairs(staging_dir: str | Path) -> pl.DataFrame:
    staging_dir = Path(staging_dir)
    files = sorted(staging_dir.glob('*.parquet'))
    if not files:
        return pl.DataFrame({
            'key_type': [],
            'key_value': [],
            'mapped_identifier': [],
            'reference_sources': [],
            'mapping_count': [],
        })

    combined = pl.concat([pl.read_parquet(path) for path in files], how='vertical_relaxed')
    return (
        combined
        .group_by(['key_type', 'key_value'])
        .agg([
            pl.col('mapped_identifier').n_unique().alias('mapping_count'),
            pl.col('mapped_identifier').first().alias('mapped_identifier'),
            pl.col('reference_source').unique().sort().alias('reference_sources'),
        ])
        .sort(['key_type', 'key_value'])
    )


def build_chemical_reference_mappings(
    target_schema_root: str | Path,
    reference_sources: Iterable[str] = DEFAULT_CHEMICAL_REFERENCE_SOURCES,
) -> pl.DataFrame:
    temp_staging_dir = Path(target_schema_root) / '_mapping_tables' / '_staging_chemical_pairs'
    stage_chemical_reference_pairs(target_schema_root, temp_staging_dir, reference_sources)
    return consolidate_staged_chemical_reference_pairs(temp_staging_dir)


def materialize_mapping_tables(
    target_schema_root: str | Path,
    output_dir: str | Path,
    chemical_reference_sources: Iterable[str] = DEFAULT_CHEMICAL_REFERENCE_SOURCES,
) -> dict[str, int]:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    staging_dir = output_dir / 'staging' / 'chemical_reference_pairs'

    uniprot_reference = build_uniprot_reference_mappings(target_schema_root)
    uniprot_secondary = build_uniprot_secondary_accession_map()
    chemical_stage_summary = stage_chemical_reference_pairs(
        target_schema_root,
        staging_dir,
        reference_sources=chemical_reference_sources,
    )
    chemical_reference = consolidate_staged_chemical_reference_pairs(staging_dir)

    (output_dir / 'README.txt').write_text(
        'Materialized target-schema identifier mapping tables.\n'
        'Files:\n'
        '- uniprot_reference_mappings.parquet\n'
        '- uniprot_secondary_to_primary.parquet\n'
        '- chemical_reference_to_standard_inchi.parquet\n'
        'Staging:\n'
        '- staging/chemical_reference_pairs/<source>.parquet\n'
    )
    uniprot_reference.write_parquet(output_dir / 'uniprot_reference_mappings.parquet')
    uniprot_secondary.write_parquet(output_dir / 'uniprot_secondary_to_primary.parquet')
    chemical_reference.write_parquet(output_dir / 'chemical_reference_to_standard_inchi.parquet')

    return {
        'uniprot_reference_rows': int(uniprot_reference.height),
        'uniprot_secondary_rows': int(uniprot_secondary.height),
        'chemical_reference_rows': int(chemical_reference.height),
        'chemical_staging_rows': int(sum(chemical_stage_summary.values())),
    }
