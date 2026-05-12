from __future__ import annotations

"""Entity candidate extraction from canonical silver tables.

This module consumes silver parquet tables with Polars. It avoids
reconstruction of nested rows and limits Python work to small scalar UDFs
needed for existing CV label formatting and fingerprint hashing.
"""

from pathlib import Path
from typing import Any

import polars as pl

from omnipath_build.gold.utils.cv_terms import format_cv_term
from omnipath_build.gold.utils.entity_extraction import compute_entity_fingerprint
from omnipath_build.gold.utils.schema import (
    CV_TERM_ENTITY_TYPE,
    EVIDENCE_IDENTIFIER_TERMS,
    INTERACTION_LIKE_TYPES,
    NAME_IDENTIFIER_TERMS,
    ONTOLOGY_IDENTIFIER_TERM,
    SYNONYM_IDENTIFIER_TERMS,
    TAXONOMY_IDENTIFIER_TERM,
    DEFINITION_HINT_TERMS,
    is_cv_term_accession,
    string_or_none,
)
from omnipath_build.silver.tables import has_silver_tables, silver_table_dir

_ATTR_DTYPE = pl.List(pl.Struct({
    'term': pl.Utf8,
    'value': pl.Utf8,
    'unit': pl.Utf8,
}))
_IDENTIFIER_DTYPE = pl.List(pl.Struct({
    'type': pl.Utf8,
    'value': pl.Utf8,
}))


def _empty_temp_entities() -> pl.DataFrame:
    return pl.DataFrame({
        'entity_pk': pl.Series([], dtype=pl.Int64),
        '_fingerprint': pl.Series([], dtype=pl.Utf8),
        'entity_type': pl.Series([], dtype=pl.Utf8),
        'taxonomy_id': pl.Series([], dtype=pl.Utf8),
        'entity_attributes': pl.Series([], dtype=_ATTR_DTYPE),
        'sources': pl.Series([], dtype=pl.List(pl.Utf8)),
    })


def _empty_temp_identifiers() -> pl.DataFrame:
    return pl.DataFrame({
        'entity_pk': pl.Series([], dtype=pl.Int64),
        'identifier': pl.Series([], dtype=pl.Utf8),
        'identifier_type': pl.Series([], dtype=pl.Utf8),
        'source': pl.Series([], dtype=pl.Utf8),
    })


def _empty_occurrence_map() -> pl.DataFrame:
    return pl.DataFrame({
        'occurrence_id': pl.Series([], dtype=pl.Utf8),
        '_fingerprint': pl.Series([], dtype=pl.Utf8),
    })


def _is_accession(value: Any) -> bool:
    text = string_or_none(value)
    return bool(text and is_cv_term_accession(text))


def _normalize_attr_term(value: Any) -> str | None:
    text = string_or_none(value)
    if text is None:
        return None
    if is_cv_term_accession(text):
        return format_cv_term(text)
    return text


def _fingerprint_from_row(row: dict[str, Any]) -> str:
    identifiers = row.get('identifiers') or []
    return compute_entity_fingerprint(row.get('entity_type'), identifiers)


def _ontology_term_rows_from_frames(
    ontology_occurrences: pl.DataFrame,
    ids_fmt: pl.DataFrame,
    anns: pl.DataFrame,
    source: str,
) -> list[dict[str, Any]]:
    """Build ontology term metadata rows from silver frames.

    Ontology-term metadata is comparatively small and still represented as a
    Python list by the downstream writer, but the heavy silver entity candidate
    extraction stays columnar.
    """
    if ontology_occurrences.is_empty():
        return []

    ontology_ids = ids_fmt.join(
        ontology_occurrences.select('occurrence_id'),
        on='occurrence_id',
        how='inner',
    )
    term_ids = (
        ontology_ids
        .filter(pl.col('type') == format_cv_term(ONTOLOGY_IDENTIFIER_TERM))
        .select(['occurrence_id', pl.col('value').alias('term_id')])
    )
    if term_ids.is_empty():
        return []

    labels = (
        ontology_ids
        .filter(pl.col('type').is_in([format_cv_term(t) for t in NAME_IDENTIFIER_TERMS]))
        .group_by('occurrence_id')
        .agg(pl.col('value').first().alias('label_from_id'))
    )
    synonyms = (
        ontology_ids
        .filter(pl.col('type').is_in([format_cv_term(t) for t in SYNONYM_IDENTIFIER_TERMS]))
        .group_by('occurrence_id')
        .agg(pl.col('value').unique().sort().alias('synonyms'))
    )
    definitions = (
        ontology_ids
        .filter(pl.col('type').is_in([format_cv_term(t) for t in DEFINITION_HINT_TERMS]))
        .group_by('occurrence_id')
        .agg(pl.col('value').first().alias('definition_from_id'))
    )
    ann_hints = (
        anns
        .join(ontology_occurrences.select('occurrence_id'), on='occurrence_id', how='inner')
        .filter(pl.col('value').is_not_null() & (pl.col('value') != ''))
        .with_columns([
            pl.col('value').map_elements(_is_accession, return_dtype=pl.Boolean).alias('_is_accession'),
            (pl.col('value').str.len_chars() > 80).alias('_is_long'),
        ])
        .group_by('occurrence_id')
        .agg([
            pl.col('value').filter(~pl.col('_is_accession')).first().alias('label_from_annotation'),
            pl.col('value').filter(pl.col('_is_long')).first().alias('definition_from_annotation'),
        ])
    )

    rows = (
        term_ids
        .join(labels, on='occurrence_id', how='left')
        .join(synonyms, on='occurrence_id', how='left')
        .join(definitions, on='occurrence_id', how='left')
        .join(ann_hints, on='occurrence_id', how='left')
        .select([
            'term_id',
            pl.when(pl.col('term_id').str.contains(':'))
            .then(pl.col('term_id').str.split_exact(':', 1).struct.field('field_0'))
            .otherwise(None)
            .alias('ontology_prefix'),
            pl.coalesce(['label_from_id', 'label_from_annotation']).alias('label'),
            pl.coalesce(['definition_from_id', 'definition_from_annotation']).alias('definition'),
            'synonyms',
            pl.lit(source).alias('source'),
        ])
        .to_dicts()
    )
    return rows


def extract_from_silver_tables(
    silver_dir: str | Path,
    source: str,
) -> tuple[pl.DataFrame, pl.DataFrame, list[dict[str, Any]], pl.DataFrame, int]:
    """Build deduplicated entity frames from silver tables.

    Returns ``(temp_entities, temp_identifiers, ontology_term_rows,
    occurrence_fingerprint_map, entity_occurrences)``. ``temp_entities`` and
    ``temp_identifiers`` are ready to pass to canonicalization.
    """
    if not has_silver_tables(silver_dir):
        raise FileNotFoundError(f'silver tables not found under {silver_dir}')

    base = silver_table_dir(silver_dir)
    occ = _scan_silver_table(base, 'entity_occurrence')
    ids_raw = _scan_silver_table(base, 'entity_identifier')
    anns_raw = _scan_silver_table(base, 'entity_annotation')
    memberships = _scan_silver_table(base, 'membership')

    occurrence_count = occ.select(pl.len()).collect().item()
    if occurrence_count == 0:
        return _empty_temp_entities(), _empty_temp_identifiers(), [], _empty_occurrence_map(), 0

    ids_fmt = (
        ids_raw
        .filter(pl.col('identifier').is_not_null() & (pl.col('identifier') != ''))
        .select([
            'occurrence_id',
            pl.col('identifier_type').map_elements(format_cv_term, return_dtype=pl.Utf8).alias('type'),
            pl.col('identifier').alias('value'),
        ])
        .collect()
    )
    anns = (
        anns_raw
        .select([
            'occurrence_id',
            'term',
            'value',
            pl.col('unit').alias('units'),
        ])
        .with_row_index('_annotation_index')
        .with_columns(
            pl.col('_annotation_index').cum_count().over('occurrence_id').alias('_annotation_pos')
        )
        .collect()
    )
    occ_df = (
        occ
        .select([
            'occurrence_id',
            'entity_type',
            pl.col('entity_type').map_elements(format_cv_term, return_dtype=pl.Utf8).alias('entity_type_formatted'),
        ])
        .with_row_index('_occurrence_index')
        .collect()
    )
    membership_df = memberships.select(['parent_occurrence_id', 'member_occurrence_id']).collect()

    has_membership = (
        membership_df.select(pl.col('parent_occurrence_id').alias('occurrence_id')).unique()
        if not membership_df.is_empty()
        else pl.DataFrame({'occurrence_id': pl.Series([], dtype=pl.Utf8)})
    )
    has_ontology_backing = (
        ids_raw
        .filter(
            (pl.col('identifier_type') == ONTOLOGY_IDENTIFIER_TERM)
            & pl.col('identifier').is_not_null()
            & (pl.col('identifier') != '')
        )
        .select('occurrence_id')
        .unique()
        .collect()
    )

    occ_class = (
        occ_df
        .join(has_membership.with_columns(pl.lit(True).alias('has_membership')), on='occurrence_id', how='left')
        .join(has_ontology_backing.with_columns(pl.lit(True).alias('has_ontology_backing')), on='occurrence_id', how='left')
        .with_columns([
            pl.col('has_membership').fill_null(False),
            pl.col('has_ontology_backing').fill_null(False),
        ])
        .with_columns(
            pl.when(pl.col('entity_type').is_in(list(INTERACTION_LIKE_TYPES)) & pl.col('has_membership'))
            .then(pl.lit('interaction_relation'))
            .when(pl.col('entity_type') == CV_TERM_ENTITY_TYPE)
            .then(pl.lit('ontology_term_only'))
            .when(pl.col('has_ontology_backing'))
            .then(pl.lit('entity_with_ontology_backing'))
            .when(pl.col('has_membership'))
            .then(pl.lit('membership_relation'))
            .when(pl.col('entity_type').is_not_null())
            .then(pl.lit('entity_only'))
            .otherwise(pl.lit('ignored'))
            .alias('record_class')
        )
    )

    identifiers_by_occ = (
        ids_fmt
        .group_by('occurrence_id', maintain_order=True)
        .agg(pl.struct(['type', 'value']).alias('identifiers'))
    )

    taxonomy_from_ids = (
        ids_fmt
        .filter(pl.col('type') == format_cv_term(TAXONOMY_IDENTIFIER_TERM))
        .group_by('occurrence_id')
        .agg(pl.col('value').first().alias('taxonomy_from_id'))
    )
    taxonomy_from_anns = (
        anns
        .filter((pl.col('term') == TAXONOMY_IDENTIFIER_TERM) & pl.col('value').is_not_null() & (pl.col('value') != ''))
        .group_by('occurrence_id')
        .agg(pl.col('value').first().alias('taxonomy_from_annotation'))
    )
    direct_taxonomy = (
        occ_df.select('occurrence_id')
        .join(taxonomy_from_ids, on='occurrence_id', how='left')
        .join(taxonomy_from_anns, on='occurrence_id', how='left')
        .with_columns(pl.coalesce(['taxonomy_from_id', 'taxonomy_from_annotation']).alias('direct_taxonomy_id'))
        .select(['occurrence_id', 'direct_taxonomy_id'])
    )

    if membership_df.is_empty():
        member_taxonomy = pl.DataFrame({
            'occurrence_id': pl.Series([], dtype=pl.Utf8),
            'member_taxonomy_id': pl.Series([], dtype=pl.Utf8),
        })
    else:
        member_taxonomy = (
            membership_df
            .join(direct_taxonomy.rename({'occurrence_id': 'member_occurrence_id'}), on='member_occurrence_id', how='left')
            .filter(pl.col('direct_taxonomy_id').is_not_null() & (pl.col('direct_taxonomy_id') != ''))
            .group_by('parent_occurrence_id')
            .agg([
                pl.col('direct_taxonomy_id').n_unique().alias('_member_taxonomy_n'),
                pl.col('direct_taxonomy_id').first().alias('member_taxonomy_id'),
            ])
            .filter(pl.col('_member_taxonomy_n') == 1)
            .select([pl.col('parent_occurrence_id').alias('occurrence_id'), 'member_taxonomy_id'])
        )

    pure_ontology = (
        anns
        .filter((pl.col('term') == ONTOLOGY_IDENTIFIER_TERM) & pl.col('units').is_null())
        .filter(pl.col('value').is_not_null() & (pl.col('value') != ''))
        .join(occ_df.select(['occurrence_id', '_occurrence_index']), on='occurrence_id', how='left')
        .select([
            'occurrence_id',
            pl.col('value').alias('term_id'),
            ((pl.col('_occurrence_index') * 10_000) + pl.col('_annotation_pos')).alias('_candidate_order'),
        ])
    )

    attr_rows = (
        anns
        .with_columns([
            pl.col('value').map_elements(_is_accession, return_dtype=pl.Boolean).alias('_value_is_accession'),
        ])
        .filter(
            pl.col('term').is_not_null()
            & (pl.col('term') != TAXONOMY_IDENTIFIER_TERM)
            & (~pl.col('term').is_in(list(EVIDENCE_IDENTIFIER_TERMS)))
            & ~((pl.col('term') == ONTOLOGY_IDENTIFIER_TERM) & pl.col('units').is_null() & pl.col('_value_is_accession'))
            & ~((pl.col('term') == ONTOLOGY_IDENTIFIER_TERM) & pl.col('units').is_null())
        )
        .select([
            'occurrence_id',
            pl.col('term').map_elements(_normalize_attr_term, return_dtype=pl.Utf8).alias('term'),
            'value',
            pl.col('units').map_elements(_normalize_attr_term, return_dtype=pl.Utf8).alias('unit'),
        ])
    )
    attributes_by_occ = (
        attr_rows
        .group_by('occurrence_id', maintain_order=True)
        .agg(pl.struct(['term', 'value', 'unit']).alias('entity_attributes'))
    )

    materialized_occurrences = (
        occ_class
        .filter(~pl.col('record_class').is_in(['ignored', 'interaction_relation']))
        .join(identifiers_by_occ, on='occurrence_id', how='left')
        .join(direct_taxonomy, on='occurrence_id', how='left')
        .join(member_taxonomy, on='occurrence_id', how='left')
        .join(attributes_by_occ, on='occurrence_id', how='left')
        .with_columns([
            pl.coalesce(['direct_taxonomy_id', 'member_taxonomy_id']).alias('taxonomy_id'),
            pl.col('identifiers').cast(_IDENTIFIER_DTYPE),
            pl.col('entity_attributes').cast(_ATTR_DTYPE),
        ])
        .select([
            'occurrence_id',
            (pl.col('_occurrence_index') * 10_000).alias('_candidate_order'),
            pl.col('entity_type_formatted').alias('entity_type'),
            'taxonomy_id',
            'entity_attributes',
            'identifiers',
        ])
        .with_columns(
            pl.struct(['entity_type', 'identifiers'])
            .map_elements(_fingerprint_from_row, return_dtype=pl.Utf8)
            .alias('_fingerprint')
        )
    )

    ontology_annotation_entities = (
        pure_ontology
        .group_by('term_id', maintain_order=True)
        .agg(pl.col('_candidate_order').min().alias('_candidate_order'))
        .sort('_candidate_order')
        .with_columns([
            pl.lit(format_cv_term(CV_TERM_ENTITY_TYPE)).alias('entity_type'),
            pl.lit(None, dtype=pl.Utf8).alias('taxonomy_id'),
            pl.lit(None, dtype=_ATTR_DTYPE).alias('entity_attributes'),
            pl.struct([
                pl.lit(format_cv_term(ONTOLOGY_IDENTIFIER_TERM)).alias('type'),
                pl.col('term_id').alias('value'),
            ]).alias('_identifier'),
        ])
        .with_columns(pl.col('_identifier').implode().over('term_id').alias('identifiers'))
        .select(['_candidate_order', 'entity_type', 'taxonomy_id', 'entity_attributes', 'identifiers'])
        .with_columns(
            pl.struct(['entity_type', 'identifiers'])
            .map_elements(_fingerprint_from_row, return_dtype=pl.Utf8)
            .alias('_fingerprint')
        )
    )

    entity_candidates = pl.concat([
        materialized_occurrences.select(['_candidate_order', '_fingerprint', 'entity_type', 'taxonomy_id', 'entity_attributes', 'identifiers']),
        ontology_annotation_entities.select(['_candidate_order', '_fingerprint', 'entity_type', 'taxonomy_id', 'entity_attributes', 'identifiers']),
    ], how='vertical_relaxed')

    entity_occurrences = int(materialized_occurrences.height + pure_ontology.height)
    if entity_candidates.is_empty():
        ontology_only = occ_class.filter(pl.col('record_class') == 'ontology_term_only')
        ontology_terms = _ontology_term_rows_from_frames(ontology_only, ids_fmt, anns, source)
        return _empty_temp_entities(), _empty_temp_identifiers(), ontology_terms, _empty_occurrence_map(), entity_occurrences

    deduped = (
        entity_candidates
        .sort('_candidate_order')
        .unique(subset=['_fingerprint'], maintain_order=True)
        .with_row_index('entity_pk', offset=1)
        .with_columns([
            pl.col('entity_pk').cast(pl.Int64),
            pl.lit([source]).alias('sources'),
        ])
    )

    temp_entities = deduped.select([
        'entity_pk',
        '_fingerprint',
        'entity_type',
        'taxonomy_id',
        'entity_attributes',
        'sources',
    ])

    temp_identifiers = (
        deduped
        .select(['entity_pk', 'identifiers'])
        .explode('identifiers')
        .unnest('identifiers')
        .filter(pl.col('value').is_not_null() & (pl.col('value') != '') & pl.col('type').is_not_null() & (pl.col('type') != ''))
        .select([
            'entity_pk',
            pl.col('value').alias('identifier'),
            pl.col('type').alias('identifier_type'),
            pl.lit(source).alias('source'),
        ])
    )
    if temp_identifiers.is_empty():
        temp_identifiers = _empty_temp_identifiers()

    occurrence_fingerprint_map = materialized_occurrences.select(['occurrence_id', '_fingerprint']).unique()

    ontology_only = occ_class.filter(pl.col('record_class') == 'ontology_term_only')
    ontology_terms = _ontology_term_rows_from_frames(ontology_only, ids_fmt, anns, source)

    return temp_entities, temp_identifiers, ontology_terms, occurrence_fingerprint_map, entity_occurrences


def _scan_silver_table(base: Path, name: str) -> pl.LazyFrame:
    return pl.scan_parquet(base / name / '**' / '*.parquet')
