from __future__ import annotations

from pathlib import Path

import polars as pl

UNIPROT_TYPE = 'MI:1097:Uniprot'
STANDARD_INCHI_TYPE = 'MI:2010:Standard Inchi'

PROTEIN_REFERENCE_TYPES: frozenset[str] = frozenset({
    'MI:0476:Ensembl',
    'MI:0477:Entrez',
    'MI:1097:Uniprot',
    'OM:0200:Gene Name Primary',
    'OM:0201:Gene Name Synonym',
    'OM:0221:Uniprot Entry Name',
})

CHEMICAL_ID_TYPE_TO_SOURCE: dict[str, str] = {
    'MI:0474:Chebi': 'chebi',
    'OM:0004:Hmdb': 'hmdb',
    'OM:0003:Lipidmaps': 'lipidmaps',
    'OM:0009:Swisslipids': 'swisslipids',
}

RESOLVED_ID_COLUMN = 'resolved_id'
RESOLVED_ID_TYPE_COLUMN = 'resolved_id_type'
RESOLUTION_STATUS_COLUMN = 'resolution_status'
RESOLUTION_SOURCE_COLUMN = 'resolution_source'


def _normalize_taxonomy(expr: pl.Expr) -> pl.Expr:
    return pl.when(expr.is_null() | (expr.cast(pl.Utf8) == '')).then(pl.lit(None, dtype=pl.Utf8)).otherwise(expr.cast(pl.Utf8))


def _empty_result(df: pl.DataFrame) -> pl.DataFrame:
    return df.with_columns([
        pl.lit(None, dtype=pl.Utf8).alias(RESOLVED_ID_COLUMN),
        pl.lit(None, dtype=pl.Utf8).alias(RESOLVED_ID_TYPE_COLUMN),
        pl.lit('unresolved').alias(RESOLUTION_STATUS_COLUMN),
        pl.lit(None, dtype=pl.Utf8).alias(RESOLUTION_SOURCE_COLUMN),
    ])


def _scan_protein_lookup(mapping_dir: Path) -> pl.LazyFrame:
    return pl.scan_parquet(mapping_dir / 'proteins' / 'protein_identifier_lookup.parquet').select([
        pl.col('key_type').cast(pl.Utf8),
        pl.col('key_value').cast(pl.Utf8),
        _normalize_taxonomy(pl.col('taxonomy_id')).alias('taxonomy_id'),
        pl.col('primary_uniprot').cast(pl.Utf8),
        pl.col('mapping_type').cast(pl.Utf8),
    ])


def _scan_primary_uniprots(mapping_dir: Path) -> pl.LazyFrame:
    return _scan_protein_lookup(mapping_dir).select('primary_uniprot').unique()


def _scan_uniprot_secondary(mapping_dir: Path) -> pl.LazyFrame:
    return _scan_protein_lookup(mapping_dir).filter(
        pl.col('mapping_type') == 'uniprot_secondary'
    ).select([
        pl.col('key_value').alias('id'),
        pl.col('primary_uniprot').alias('_secondary_uniprot'),
    ])


def _scan_protein_reference(mapping_dir: Path, protein_types: set[str]) -> pl.LazyFrame:
    return _scan_protein_lookup(mapping_dir).filter(
        pl.col('key_type').is_in(sorted(protein_types)) & (pl.col('mapping_type') != 'uniprot_secondary')
    ).select([
        'key_type',
        'key_value',
        'taxonomy_id',
        'primary_uniprot',
    ])


def _scan_chemical_reference(mapping_dir: Path, chemical_sources: set[str]) -> pl.LazyFrame:
    path = mapping_dir / 'chemicals' / 'chemical_identifier_lookup.parquet'
    if not path.exists():
        return pl.LazyFrame(
            schema={
                'key_type': pl.Utf8,
                'key_value': pl.Utf8,
                '_chemical_inchi': pl.Utf8,
                '_chemical_source': pl.Utf8,
            }
        )

    return pl.scan_parquet(path).filter(
        pl.col('source').is_in(sorted(chemical_sources))
    ).select([
        pl.col('key_type').cast(pl.Utf8),
        pl.col('key_value').cast(pl.Utf8),
        pl.col('standard_inchi').cast(pl.Utf8).alias('_chemical_inchi'),
        pl.col('source').cast(pl.Utf8).alias('_chemical_source'),
    ])


def resolve_identifier_frame(
    df: pl.DataFrame,
    mapping_dir: str | Path,
    *,
    id_column: str = 'id',
    id_type_column: str = 'id_type',
    taxonomy_column: str | None = 'taxonomy_id',
) -> pl.DataFrame:
    if df.is_empty():
        return _empty_result(df)

    id_types = {
        str(value)
        for value in df.get_column(id_type_column).drop_nulls().unique().to_list()
    }
    needs_uniprot_identity = UNIPROT_TYPE in id_types
    protein_reference_types = id_types & PROTEIN_REFERENCE_TYPES
    chemical_sources = {
        CHEMICAL_ID_TYPE_TO_SOURCE[id_type]
        for id_type in id_types
        if id_type in CHEMICAL_ID_TYPE_TO_SOURCE
    }
    needs_standard_inchi_identity = STANDARD_INCHI_TYPE in id_types

    if not (needs_uniprot_identity or protein_reference_types or chemical_sources or needs_standard_inchi_identity):
        return _empty_result(df)

    mapping_dir = Path(mapping_dir)
    prepared = df.with_row_index('_resolver_row_idx').lazy().with_columns([
        pl.col(id_column).cast(pl.Utf8).alias(id_column),
        pl.col(id_type_column).cast(pl.Utf8).alias(id_type_column),
        (
            pl.lit(None, dtype=pl.Utf8)
            if taxonomy_column is None or taxonomy_column not in df.columns
            else _normalize_taxonomy(pl.col(taxonomy_column))
        ).alias('_resolver_taxonomy_id'),
        pl.when(pl.col(id_type_column) == UNIPROT_TYPE)
        .then(pl.col(id_column).str.replace(r'-\d+$', ''))
        .otherwise(pl.lit(None, dtype=pl.Utf8))
        .alias('_base_uniprot'),
    ])

    resolved = prepared

    if needs_uniprot_identity:
        primary_uniprots = _scan_primary_uniprots(mapping_dir)
        resolved = (
            resolved
            .join(
                primary_uniprots.select(pl.col('primary_uniprot').alias('_primary_identity')),
                left_on=id_column,
                right_on='_primary_identity',
                how='left',
            )
            .join(
                primary_uniprots.select(pl.col('primary_uniprot').alias('_primary_isoform')),
                left_on='_base_uniprot',
                right_on='_primary_isoform',
                how='left',
            )
            .join(_scan_uniprot_secondary(mapping_dir), left_on=id_column, right_on='id', how='left')
        )

    if protein_reference_types:
        protein_reference = _scan_protein_reference(mapping_dir, protein_reference_types)
        resolved = (
            resolved
            .join(
                protein_reference.select([
                    'key_type',
                    'key_value',
                    'taxonomy_id',
                    pl.col('primary_uniprot').alias('_protein_scoped'),
                ]),
                left_on=[id_type_column, id_column, '_resolver_taxonomy_id'],
                right_on=['key_type', 'key_value', 'taxonomy_id'],
                how='left',
            )
            .join(
                protein_reference.select([
                    'key_type',
                    'key_value',
                    pl.col('primary_uniprot').alias('_protein_global'),
                ]),
                left_on=[id_type_column, id_column],
                right_on=['key_type', 'key_value'],
                how='left',
            )
        )

    if chemical_sources:
        resolved = resolved.join(
            _scan_chemical_reference(mapping_dir, chemical_sources),
            left_on=[id_type_column, id_column],
            right_on=['key_type', 'key_value'],
            how='left',
        )

    for column_name in [
        '_primary_identity',
        '_primary_isoform',
        '_secondary_uniprot',
        '_protein_scoped',
        '_protein_global',
        '_chemical_inchi',
        '_chemical_source',
    ]:
        if column_name not in resolved.collect_schema().names():
            resolved = resolved.with_columns(pl.lit(None, dtype=pl.Utf8).alias(column_name))

    return (
        resolved
        .with_columns([
            pl.coalesce([
                pl.col('_primary_identity'),
                pl.when(pl.col('_base_uniprot') != pl.col(id_column)).then(pl.col('_primary_isoform')).otherwise(pl.lit(None, dtype=pl.Utf8)),
                pl.col('_secondary_uniprot'),
                pl.when(pl.col('_resolver_taxonomy_id').is_not_null()).then(pl.col('_protein_scoped')).otherwise(pl.lit(None, dtype=pl.Utf8)),
                pl.when(pl.col('_resolver_taxonomy_id').is_null()).then(pl.col('_protein_global')).otherwise(pl.lit(None, dtype=pl.Utf8)),
                pl.when(pl.col(id_type_column) == STANDARD_INCHI_TYPE).then(pl.col(id_column)).otherwise(pl.lit(None, dtype=pl.Utf8)),
                pl.col('_chemical_inchi'),
            ]).alias(RESOLVED_ID_COLUMN),
            pl.when(
                pl.col('_primary_identity').is_not_null()
                | pl.col('_primary_isoform').is_not_null()
                | pl.col('_secondary_uniprot').is_not_null()
                | pl.col('_protein_scoped').is_not_null()
                | pl.col('_protein_global').is_not_null()
            )
            .then(pl.lit(UNIPROT_TYPE))
            .when((pl.col(id_type_column) == STANDARD_INCHI_TYPE) | pl.col('_chemical_inchi').is_not_null())
            .then(pl.lit(STANDARD_INCHI_TYPE))
            .otherwise(pl.lit(None, dtype=pl.Utf8))
            .alias(RESOLVED_ID_TYPE_COLUMN),
        ])
        .with_columns([
            pl.when(pl.col('_primary_identity').is_not_null() | ((pl.col(id_type_column) == STANDARD_INCHI_TYPE) & pl.col(id_column).is_not_null()))
            .then(pl.lit('identity'))
            .when(pl.col(RESOLVED_ID_COLUMN).is_not_null())
            .then(pl.lit('mapped'))
            .otherwise(pl.lit('unresolved'))
            .alias(RESOLUTION_STATUS_COLUMN),
            pl.when(pl.col('_primary_identity').is_not_null())
            .then(pl.lit('uniprot_primary'))
            .when(pl.col('_primary_isoform').is_not_null())
            .then(pl.lit('uniprot_isoform'))
            .when(pl.col('_secondary_uniprot').is_not_null())
            .then(pl.lit('uniprot_secondary'))
            .when(pl.col('_protein_scoped').is_not_null() | pl.col('_protein_global').is_not_null())
            .then(pl.lit('uniprot_reference'))
            .when(pl.col(id_type_column) == STANDARD_INCHI_TYPE)
            .then(pl.lit('standard_inchi'))
            .when(pl.col('_chemical_inchi').is_not_null())
            .then(pl.col('_chemical_source'))
            .otherwise(pl.lit(None, dtype=pl.Utf8))
            .alias(RESOLUTION_SOURCE_COLUMN),
        ])
        .sort('_resolver_row_idx')
        .drop([
            '_resolver_row_idx',
            '_resolver_taxonomy_id',
            '_base_uniprot',
            '_primary_identity',
            '_primary_isoform',
            '_secondary_uniprot',
            '_protein_scoped',
            '_protein_global',
            '_chemical_inchi',
            '_chemical_source',
        ], strict=False)
        .collect()
    )


__all__ = [
    'UNIPROT_TYPE',
    'STANDARD_INCHI_TYPE',
    'PROTEIN_REFERENCE_TYPES',
    'CHEMICAL_ID_TYPE_TO_SOURCE',
    'RESOLVED_ID_COLUMN',
    'RESOLVED_ID_TYPE_COLUMN',
    'RESOLUTION_STATUS_COLUMN',
    'RESOLUTION_SOURCE_COLUMN',
    'resolve_identifier_frame',
]
