from __future__ import annotations

import json
from typing import Any
from pathlib import Path
import textwrap

import polars as pl

from id_resolver.resolve import (
    UNIPROT_TYPE,
    RESOLVED_ID_COLUMN,
    STANDARD_INCHI_TYPE,
    TARGET_ENTITY_TYPES,
    PROTEIN_ENTITY_TYPES,
    CHEMICAL_ENTITY_TYPES,
    RESOLVED_ID_TYPE_COLUMN,
    RESOLUTION_STATUS_COLUMN,
    resolve_identifier_frame,
)
from omnipath_build.gold.utils.keys import compute_entity_key
from omnipath_build.gold.utils.schema import ONTOLOGY_IDENTIFIER_TERM
from omnipath_build.gold.utils.table_schema import (
    EMPTY_IDENTIFIERS,
    ENTITY_EVIDENCE_SCHEMA,
)
from omnipath_build.gold.utils.canonicalization import (
    ONTOLOGY_ENTITY_TYPE_LABEL,
    ONTOLOGY_IDENTIFIER_TYPE_LABEL,
    _markdown_table,
    _reduce_entities,
    _entity_export_keys,
    _resolver_source_rows,
    _protein_identifier_rows,
    _chemical_identifier_rows,
    _aggregate_identifier_rows,
    _canonical_identifier_rows,
    _collect_ambiguous_entities,
    _repair_protein_resolutions,
    _build_ambiguous_entity_report,
)
from omnipath_build.gold.utils.entity_extraction import (
    extract_ontology_entity_description,
)
from omnipath_build.gold.utils.silver_entity_extraction import (
    extract_from_silver_tables,
)

def _canonicalize_entities(
    entities: pl.DataFrame,
    source_identifiers: pl.DataFrame,
    mapping_dir: Path,
    source_name: str,
) -> tuple[pl.DataFrame, pl.DataFrame, dict[str, Any], list[dict[str, Any]]]:
    """Canonicalize entities in-memory, preserving the original entity_pk column."""
    entities = entities.with_columns([
        pl.col('entity_pk').cast(pl.Int64),
        pl.col('entity_type').cast(pl.Utf8),
        pl.when(pl.col('taxonomy_id').is_null() | (pl.col('taxonomy_id').cast(pl.Utf8) == ''))
        .then(pl.lit(None, dtype=pl.Utf8))
        .otherwise(pl.col('taxonomy_id').cast(pl.Utf8))
        .alias('taxonomy_id'),
    ])
    source_identifiers = source_identifiers.with_columns([
        pl.col('entity_pk').cast(pl.Int64),
        pl.col('identifier').cast(pl.Utf8),
        pl.col('identifier_type').cast(pl.Utf8),
        pl.col('source').cast(pl.Utf8),
    ])

    ontology_type_label = ONTOLOGY_ENTITY_TYPE_LABEL
    ontology_entities = entities.filter(pl.col('entity_type') == ontology_type_label)
    non_ontology_entities = entities.filter(pl.col('entity_type') != ontology_type_label)

    eligible_entities = non_ontology_entities.filter(
        pl.col('entity_type').is_in(list(TARGET_ENTITY_TYPES))
    ).select(['entity_pk', 'entity_type', 'taxonomy_id'])

    ontology_canonical = (
        ontology_entities
        .select('entity_pk')
        .join(
            source_identifiers.filter(pl.col('identifier_type') == ONTOLOGY_IDENTIFIER_TYPE_LABEL),
            on='entity_pk',
            how='inner',
        )
        .select([
            'entity_pk',
            pl.col('identifier').alias('canonical_identifier'),
            pl.col('identifier_type').alias('canonical_identifier_type'),
        ])
    )

    if non_ontology_entities.is_empty() or source_identifiers.is_empty() or eligible_entities.is_empty():
        canonical_rows = (
            ontology_canonical
            if not ontology_canonical.is_empty()
            else pl.DataFrame({
                'entity_pk': pl.Series([], dtype=pl.Int64),
                'canonical_identifier': pl.Series([], dtype=pl.Utf8),
                'canonical_identifier_type': pl.Series([], dtype=pl.Utf8),
            })
        )
        ambiguous_entities: list[dict[str, Any]] = []
        entity_export_keys = _entity_export_keys(entities, canonical_rows, source_name=source_name)

        updated_entities = (
            entities
            .join(entity_export_keys, left_on='entity_pk', right_on='local_entity_pk', how='left')
            .select([
                'entity_pk',
                pl.col('export_entity_id').alias('entity_id'),
                pl.col('export_entity_id_type').alias('entity_id_type'),
                'entity_type',
                'entity_attributes',
                'taxonomy_id',
                'sources',
            ])
        )

        source_identifier_rows = (
            source_identifiers
            .join(entity_export_keys, left_on='entity_pk', right_on='local_entity_pk', how='inner')
            .with_columns([
                ((pl.col('identifier') == pl.col('export_entity_id')) & (pl.col('identifier_type') == pl.col('export_entity_id_type'))).alias('is_canonical'),
                pl.format('source:{}', pl.col('source')).alias('source_marker'),
            ])
            .select([
                pl.col('export_entity_id').alias('entity_id'),
                pl.col('export_entity_id_type').alias('entity_id_type'),
                'identifier',
                'identifier_type',
                'is_canonical',
                'source_marker',
            ])
        )

        fallback_rows = entity_export_keys.select([
            pl.col('export_entity_id').alias('entity_id'),
            pl.col('export_entity_id_type').alias('entity_id_type'),
            pl.col('export_entity_id').alias('identifier'),
            pl.col('export_entity_id_type').alias('identifier_type'),
            pl.lit(True).alias('is_canonical'),
            pl.lit('pipeline:unresolved_fallback').alias('source_marker'),
        ])

        updated_identifiers = _aggregate_identifier_rows(pl.concat([
            source_identifier_rows,
            fallback_rows,
        ], how='vertical_relaxed'))

        summary = {
            'entities_seen': int(entities.height),
            'eligible_entities': int(eligible_entities.height),
            'resolved_entities': 0,
            'ambiguous_entities': 0,
            'exact_conflicts': 0,
            'near_conflicts': 0,
            'identifier_rows_added': int(updated_identifiers.height),
            'entities_updated': 0,
        }
        return updated_entities, updated_identifiers, summary, ambiguous_entities

    resolver_input = (
        source_identifiers
        .join(eligible_entities, on='entity_pk', how='inner')
        .select([
            'entity_pk',
            'entity_type',
            'taxonomy_id',
            pl.col('identifier').alias('id'),
            pl.col('identifier_type').alias('id_type'),
        ])
        .filter(pl.col('id').is_not_null() & (pl.col('id') != ''))
        .filter(pl.col('id_type').is_not_null() & (pl.col('id_type') != ''))
        .unique()
    )

    resolved = resolve_identifier_frame(
        resolver_input,
        mapping_dir,
        id_column='id',
        id_type_column='id_type',
        taxonomy_column='taxonomy_id',
    )
    resolved = _repair_protein_resolutions(resolved, mapping_dir)

    resolvable = resolved.filter(pl.col(RESOLUTION_STATUS_COLUMN).is_in(['identity', 'mapped']))

    protein_resolvable = (
        resolvable
        .filter(pl.col('entity_type').is_in(list(PROTEIN_ENTITY_TYPES)))
        .filter(pl.col(RESOLVED_ID_TYPE_COLUMN) == UNIPROT_TYPE)
    )
    # Textual protein names are intentionally weak evidence: short aliases and
    # names can be shared between unrelated proteins, especially when taxonomy
    # is missing. They should enrich identifiers, but they should not veto a
    # unique accession/database-reference answer (UniProt/Ensembl/Entrez/etc.).
    weak_protein_name_type_prefixes = ['OM:0200', 'OM:0201', 'OM:0202', 'OM:0203']
    weak_protein_name_evidence = pl.col('id_type').cast(pl.Utf8).str.slice(0, 7).is_in(weak_protein_name_type_prefixes)
    protein_resolution_summary = (
        protein_resolvable
        .group_by('entity_pk')
        .agg([
            pl.col('taxonomy_id').drop_nulls().first().alias('entity_taxonomy_id'),
            pl.col(RESOLVED_ID_COLUMN).n_unique().alias('_all_resolved_count'),
            pl.col(RESOLVED_ID_COLUMN).first().alias('_all_primary_uniprot'),
            pl.col(RESOLVED_ID_COLUMN).filter(~weak_protein_name_evidence).n_unique().alias('_strong_resolved_count'),
            pl.col(RESOLVED_ID_COLUMN).filter(~weak_protein_name_evidence).first().alias('_strong_primary_uniprot'),
        ])
    )
    preferred_uniprots_from_strong_evidence = (
        protein_resolution_summary
        .filter(pl.col('_strong_resolved_count') == 1)
        .select([
            'entity_pk',
            'entity_taxonomy_id',
            pl.col('_strong_primary_uniprot').alias('primary_uniprot'),
        ])
    )
    preferred_uniprots = (
        protein_resolution_summary
        .with_columns([
            pl.when(pl.col('_strong_resolved_count') == 1)
            .then(pl.col('_strong_primary_uniprot'))
            .when(pl.col('_all_resolved_count') == 1)
            .then(pl.col('_all_primary_uniprot'))
            .otherwise(pl.lit(None, dtype=pl.Utf8))
            .alias('primary_uniprot'),
        ])
        .filter(pl.col('primary_uniprot').is_not_null())
        .select(['entity_pk', 'entity_taxonomy_id', 'primary_uniprot'])
    )

    resolved_for_conflicts = resolved.join(
        preferred_uniprots_from_strong_evidence.select([
            'entity_pk',
            pl.col('primary_uniprot').alias('_strong_primary_uniprot'),
        ]),
        on='entity_pk',
        how='left',
    ).filter(
        ~(
            pl.col('_strong_primary_uniprot').is_not_null()
            & pl.col('entity_type').is_in(list(PROTEIN_ENTITY_TYPES))
            & pl.col(RESOLUTION_STATUS_COLUMN).is_in(['identity', 'mapped'])
            & (pl.col(RESOLVED_ID_TYPE_COLUMN) == UNIPROT_TYPE)
            & weak_protein_name_evidence
            & (pl.col(RESOLVED_ID_COLUMN) != pl.col('_strong_primary_uniprot'))
        )
    ).drop('_strong_primary_uniprot')
    ambiguous_entities = _collect_ambiguous_entities(resolved_for_conflicts)

    preferred_inchis = (
        resolvable
        .filter(pl.col('entity_type').is_in(list(CHEMICAL_ENTITY_TYPES)))
        .filter(pl.col(RESOLVED_ID_TYPE_COLUMN) == STANDARD_INCHI_TYPE)
        .group_by('entity_pk')
        .agg([
            pl.col(RESOLVED_ID_COLUMN).n_unique().alias('_resolved_count'),
            pl.col(RESOLVED_ID_COLUMN).first().alias('standard_inchi'),
        ])
        .filter(pl.col('_resolved_count') == 1)
        .select(['entity_pk', 'standard_inchi'])
    )

    authoritative_identifiers = pl.concat([
        _protein_identifier_rows(preferred_uniprots, mapping_dir),
        _chemical_identifier_rows(preferred_inchis, mapping_dir),
    ], how='vertical_relaxed').unique()

    preferred_canonical_rows = pl.concat([
        preferred_uniprots.select([
            'entity_pk',
            pl.col('primary_uniprot').alias('canonical_identifier'),
            pl.lit(UNIPROT_TYPE).alias('canonical_identifier_type'),
        ]),
        preferred_inchis.select([
            'entity_pk',
            pl.col('standard_inchi').alias('canonical_identifier'),
            pl.lit(STANDARD_INCHI_TYPE).alias('canonical_identifier_type'),
        ]),
    ], how='vertical_relaxed')

    resolved_canonical_rows = pl.concat([
        preferred_canonical_rows,
        _canonical_identifier_rows(authoritative_identifiers)
        .join(preferred_canonical_rows.select('entity_pk'), on='entity_pk', how='anti'),
    ], how='vertical_relaxed').unique(subset=['entity_pk'], keep='first')

    canonical_rows = pl.concat([
        resolved_canonical_rows,
        ontology_canonical.join(resolved_canonical_rows.select('entity_pk'), on='entity_pk', how='anti'),
    ], how='vertical_relaxed').unique(subset=['entity_pk'], keep='first')

    entity_export_keys = _entity_export_keys(entities, canonical_rows, source_name=source_name)
    resolver_sources = _resolver_source_rows(resolvable, preferred_uniprots, preferred_inchis)

    updated_entities = (
        entities
        .join(entity_export_keys, left_on='entity_pk', right_on='local_entity_pk', how='left')
        .select([
            'entity_pk',
            pl.col('export_entity_id').alias('entity_id'),
            pl.col('export_entity_id_type').alias('entity_id_type'),
            'entity_type',
            'entity_attributes',
            'taxonomy_id',
            'sources',
        ])
    )

    source_identifier_rows = (
        source_identifiers
        .join(entity_export_keys, left_on='entity_pk', right_on='local_entity_pk', how='inner')
        .with_columns([
            ((pl.col('identifier') == pl.col('export_entity_id')) & (pl.col('identifier_type') == pl.col('export_entity_id_type'))).alias('is_canonical'),
            pl.format('source:{}', pl.col('source')).alias('source_marker'),
        ])
        .select([
            pl.col('export_entity_id').alias('entity_id'),
            pl.col('export_entity_id_type').alias('entity_id_type'),
            'identifier',
            'identifier_type',
            'is_canonical',
            'source_marker',
        ])
        .unique()
    )

    resolver_identifier_rows = (
        authoritative_identifiers
        .join(entity_export_keys, left_on='entity_pk', right_on='local_entity_pk', how='inner')
        .join(resolver_sources, on='entity_pk', how='left')
        .with_columns([
            ((pl.col('identifier') == pl.col('export_entity_id')) & (pl.col('identifier_type') == pl.col('export_entity_id_type'))).alias('is_canonical'),
            pl.coalesce([pl.col('source_marker'), pl.lit('resolver:canonicalization')]).alias('source_marker'),
        ])
        .select([
            pl.col('export_entity_id').alias('entity_id'),
            pl.col('export_entity_id_type').alias('entity_id_type'),
            'identifier',
            'identifier_type',
            'is_canonical',
            'source_marker',
        ])
        .unique()
    )

    unresolved_fallback_rows = (
        entity_export_keys
        .join(canonical_rows.select('entity_pk').rename({'entity_pk': 'local_entity_pk'}), on='local_entity_pk', how='anti')
        .select([
            pl.col('export_entity_id').alias('entity_id'),
            pl.col('export_entity_id_type').alias('entity_id_type'),
            pl.col('export_entity_id').alias('identifier'),
            pl.col('export_entity_id_type').alias('identifier_type'),
            pl.lit(True).alias('is_canonical'),
            pl.lit('pipeline:unresolved_fallback').alias('source_marker'),
        ])
    )

    updated_identifiers = _aggregate_identifier_rows(pl.concat([
        source_identifier_rows,
        resolver_identifier_rows,
        unresolved_fallback_rows,
    ], how='vertical_relaxed'))

    summary = {
        'entities_seen': int(entities.height),
        'eligible_entities': int(eligible_entities.height),
        'resolved_entities': int(
            pl.concat([
                preferred_uniprots.select('entity_pk'),
                preferred_inchis.select('entity_pk'),
            ], how='vertical_relaxed').unique().height
        ),
        'ambiguous_entities': len(ambiguous_entities),
        'exact_conflicts': sum(1 for item in ambiguous_entities if item.get('conflict_class') == 'exact'),
        'near_conflicts': sum(1 for item in ambiguous_entities if item.get('conflict_class') == 'near'),
        'identifier_rows_added': int(updated_identifiers.height),
        'entities_updated': int(canonical_rows.height),
    }

    return updated_entities, updated_identifiers, summary, ambiguous_entities


def _write_canonicalization_report(
    output_dir: Path,
    *,
    source_name: str,
    mapping_dir: Path,
    summary: dict[str, Any],
    ambiguous_entities: list[dict[str, Any]],
) -> None:
    source_label = source_name
    summary_rows = [
        ['entity occurrences (total extracted)', str(summary.get('entity_occurrences', summary['entities_seen']))],
        ['unique fingerprints (pre-deduped)', str(summary.get('unique_fingerprints', summary['entities_seen']))],
        ['eligible entities', str(summary['eligible_entities'])],
        ['resolved entities', str(summary['resolved_entities'])],
        ['ambiguous entities', str(summary['ambiguous_entities'])],
        ['backbone conflicts', str(summary.get('exact_conflicts', 0))],
        ['near conflicts', str(summary.get('near_conflicts', 0))],
        ['authoritative identifier rows written', str(summary['identifier_rows_added'])],
        ['entities with resolved identifiers', str(summary['entities_updated'])],
    ]
    report = textwrap.dedent(
        f"""\
        # Canonicalization report

        - Source: `{source_label}`
        - Gold directory: `{output_dir}`
        - Resolver mappings: `{mapping_dir}`

        ## What this step does

        - Reads raw source identifiers from silver data
        - Resolves supported identifiers to one canonical backbone per entity:
          - proteins -> UniProt
          - chemicals/lipids -> Standard InChI
        - Accepts an entity only when all supported evidence collapses to exactly one resolved backbone
        - Expands that backbone to the full authoritative identifier set
        - Marks the preferred canonical identifier via `is_canonical`

        ## Conflict policy

        - All resolver-supported source cross references are used as evidence
        - If multiple source identifiers agree on one resolved backbone, the entity is canonicalized
        - If they resolve to different backbones, the entity is left unresolved
        - For chemicals, conflicts are split into backbone conflicts vs near conflicts that differ only by stereo/protonation layers
        - Identifier types below are shown as labels, not accessions

        ## Summary

        """
    )
    report = report + _markdown_table(['metric', 'value'], summary_rows)
    report = report + '\n' + _build_ambiguous_entity_report(ambiguous_entities)
    (output_dir / 'canonicalization_report.md').write_text(report, encoding='utf-8')
    (output_dir / 'canonicalization_summary.json').write_text(
        json.dumps(summary, indent=2, sort_keys=True) + '\n',
        encoding='utf-8',
    )


def _build_entity_evidence(
    silver_dir: str | Path,
    occurrence_map: pl.DataFrame | None,
    fingerprint_map: pl.DataFrame | None,
    final_entities: pl.DataFrame,
    output_dir: Path,
    source_name: str,
) -> pl.DataFrame:
    """Build source-local entity evidence keyed by raw records and occurrences."""
    silver_base = Path(silver_dir)
    entity_occurrence_path = silver_base / 'entity_occurrence.parquet'
    if not entity_occurrence_path.exists():
        empty_evidence = pl.DataFrame({
            name: pl.Series([], dtype=dtype)
            for name, dtype in ENTITY_EVIDENCE_SCHEMA.items()
        })
        empty_evidence.write_parquet(output_dir / 'entity_evidence.parquet')
        return empty_evidence

    # occurrence_id -> raw_record_id from silver
    raw_records = pl.read_parquet(entity_occurrence_path).select([
        'occurrence_id',
        pl.col('record_id').cast(pl.Utf8).alias('raw_record_id'),
    ]).filter(pl.col('raw_record_id').is_not_null() & (pl.col('raw_record_id') != ''))

    if raw_records.is_empty():
        empty_evidence = pl.DataFrame({
            name: pl.Series([], dtype=dtype)
            for name, dtype in ENTITY_EVIDENCE_SCHEMA.items()
        })
        empty_evidence.write_parquet(output_dir / 'entity_evidence.parquet')
        return empty_evidence

    occurrence_evidence = (
        _empty_entity_evidence_index()
        if occurrence_map is None or occurrence_map.is_empty()
        else (
            occurrence_map
            .join(raw_records, on='occurrence_id', how='inner')
            .select(['entity_pk', 'raw_record_id', 'occurrence_id', '_fingerprint'])
        )
    )
    ontology_evidence = _build_ontology_entity_evidence(
        silver_base=silver_base,
        raw_records=raw_records,
        fingerprint_map=fingerprint_map,
        source_name=source_name,
    )
    evidence_index = pl.concat(
        [occurrence_evidence, ontology_evidence],
        how='vertical_relaxed',
    ).unique()

    evidence = (
        final_entities
        .select([
            'entity_pk',
            'entity_key',
            'canonical_identifier',
            'canonical_identifier_type',
            'entity_type',
            'taxonomy_id',
            'identifiers',
            'entity_attributes',
        ])
        .join(evidence_index, on='entity_pk', how='inner')
        .select([
            pl.lit(source_name).alias('source'),
            'entity_key',
            'canonical_identifier',
            'canonical_identifier_type',
            'raw_record_id',
            'occurrence_id',
            pl.col('_fingerprint').alias('fingerprint'),
            'entity_type',
            'taxonomy_id',
            'identifiers',
            'entity_attributes',
        ])
    )

    if evidence.is_empty():
        empty_evidence = pl.DataFrame({
            name: pl.Series([], dtype=dtype)
            for name, dtype in ENTITY_EVIDENCE_SCHEMA.items()
        })
        empty_evidence.write_parquet(output_dir / 'entity_evidence.parquet')
        return empty_evidence

    evidence.write_parquet(output_dir / 'entity_evidence.parquet')
    return evidence


def _build_ontology_entity_evidence(
    *,
    silver_base: Path,
    raw_records: pl.DataFrame,
    fingerprint_map: pl.DataFrame | None,
    source_name: str,
) -> pl.DataFrame:
    empty = _empty_entity_evidence_index()
    if fingerprint_map is None or fingerprint_map.is_empty():
        return empty
    annotation_path = silver_base / 'entity_annotation.parquet'
    if not annotation_path.exists():
        return empty

    annotations = (
        pl.read_parquet(annotation_path)
        .filter(
            (pl.col('term') == ONTOLOGY_IDENTIFIER_TERM)
            & pl.col('value').is_not_null()
            & (pl.col('value') != '')
            & pl.col('unit').is_null()
        )
        .select(['occurrence_id', 'value'])
    )
    if annotations.is_empty():
        return empty

    fingerprint_rows = []
    for row in annotations.unique().iter_rows(named=True):
        desc = extract_ontology_entity_description(
            {'value': row['value']},
            source_name,
        )
        if desc is None:
            continue
        fingerprint_rows.append({
            'occurrence_id': row['occurrence_id'],
            '_fingerprint': desc['_fingerprint'],
        })
    if not fingerprint_rows:
        return empty

    ontology_occurrences = pl.DataFrame(fingerprint_rows)
    return (
        ontology_occurrences
        .join(raw_records, on='occurrence_id', how='inner')
        .join(fingerprint_map, on='_fingerprint', how='inner')
        .select(['entity_pk', 'raw_record_id', 'occurrence_id', '_fingerprint'])
        .unique()
    )


def _empty_entity_evidence_index() -> pl.DataFrame:
    return pl.DataFrame({
        'entity_pk': pl.Series([], dtype=pl.Int64),
        'raw_record_id': pl.Series([], dtype=pl.Utf8),
        'occurrence_id': pl.Series([], dtype=pl.Utf8),
        '_fingerprint': pl.Series([], dtype=pl.Utf8),
    })


def reduce_entities_from_evidence(
    entity_evidence: pl.DataFrame,
    *,
    entity_pk_map: pl.DataFrame | None = None,
) -> pl.DataFrame:
    """Project final source entities from source entity evidence facts."""
    if entity_evidence.is_empty():
        return pl.DataFrame({
            'entity_pk': pl.Series([], dtype=pl.Int64),
            'entity_key': pl.Series([], dtype=pl.Utf8),
            'canonical_identifier': pl.Series([], dtype=pl.Utf8),
            'canonical_identifier_type': pl.Series([], dtype=pl.Utf8),
            'identifiers': pl.Series([], dtype=pl.List(pl.Struct({
                'identifier': pl.Utf8,
                'identifier_type': pl.Utf8,
            }))),
            'entity_type': pl.Series([], dtype=pl.Utf8),
            'taxonomy_id': pl.Series([], dtype=pl.Utf8),
            'entity_attributes': pl.Series([], dtype=pl.List(pl.Struct({
                'term': pl.Utf8,
                'value': pl.Utf8,
                'unit': pl.Utf8,
            }))),
            'sources': pl.Series([], dtype=pl.List(pl.Utf8)),
        })

    reduced = (
        entity_evidence
        .group_by([
            'entity_key',
            'canonical_identifier',
            'canonical_identifier_type',
            'entity_type',
            'taxonomy_id',
        ])
        .agg([
            pl.col('identifiers').explode().drop_nulls().unique(maintain_order=True).alias('identifiers'),
            pl.col('entity_attributes').explode().drop_nulls().unique(maintain_order=True).alias('entity_attributes'),
            pl.col('source').drop_nulls().unique().sort().alias('sources'),
        ])
        .with_columns(
            pl.when(pl.col('identifiers').is_null())
            .then(EMPTY_IDENTIFIERS)
            .otherwise(pl.col('identifiers'))
            .alias('identifiers')
        )
        .sort(['canonical_identifier_type', 'canonical_identifier', 'entity_key'])
    )

    if entity_pk_map is not None and not entity_pk_map.is_empty():
        reduced = reduced.join(
            entity_pk_map.select(['entity_key', 'entity_pk']),
            on='entity_key',
            how='left',
        )
    if 'entity_pk' not in reduced.columns:
        reduced = reduced.with_row_index('entity_pk', offset=1)
    elif reduced['entity_pk'].null_count() > 0:
        max_pk = int(reduced['entity_pk'].max() or 0)
        reduced = (
            reduced
            .sort(['canonical_identifier_type', 'canonical_identifier', 'entity_key'])
            .with_row_index('_new_entity_pk', offset=max_pk + 1)
            .with_columns(
                pl.coalesce(['entity_pk', '_new_entity_pk']).cast(pl.Int64).alias('entity_pk')
            )
            .drop('_new_entity_pk')
        )

    return reduced.select([
        'entity_pk',
        'entity_key',
        'canonical_identifier',
        'canonical_identifier_type',
        'identifiers',
        'entity_type',
        'taxonomy_id',
        'entity_attributes',
        'sources',
    ])


def build_entities(
    silver_dir: str | Path,
    mapping_dir: str | Path,
    output_dir: str | Path,
    source_name: str,
) -> dict[str, Any]:
    """Extract entities from silver, canonicalize, deduplicate, and write outputs."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    mapping_dir = Path(mapping_dir)

    # 1. Extract entities from silver tables.
    (
        temp_entities,
        temp_identifiers,
        _ontology_term_rows,
        occurrence_fingerprint_map,
        entity_occurrences,
    ) = extract_from_silver_tables(silver_dir, source_name)
    unique_fingerprint_count = int(temp_entities.height)

    # 4. Canonicalize
    canonicalized_entities, canonical_identifiers, summary, ambiguous_entities = _canonicalize_entities(
        temp_entities, temp_identifiers, mapping_dir, source_name
    )

    # Inject occurrence counts into summary for reporting
    summary['entity_occurrences'] = entity_occurrences
    summary['unique_fingerprints'] = unique_fingerprint_count

    # 5. Dedup by canonical ID
    final_entities, entity_key_map = _reduce_entities(canonicalized_entities, canonical_identifiers)

    # Ensure consistent schema even when all unit values are null
    final_entities = final_entities.with_columns([
        pl.col('entity_attributes').cast(pl.List(pl.Struct({
            'term': pl.Utf8,
            'value': pl.Utf8,
            'unit': pl.Utf8,
        }))),
    ])

    # Add stable entity_key
    final_entities = final_entities.with_columns([
        pl.struct(['canonical_identifier', 'canonical_identifier_type', 'taxonomy_id'])
        .map_elements(
            lambda row: compute_entity_key(row['canonical_identifier'], row['canonical_identifier_type'], row['taxonomy_id']),
            return_dtype=pl.Utf8,
        )
        .alias('entity_key'),
    ]).select([
        'entity_pk',
        'entity_key',
        'canonical_identifier',
        'canonical_identifier_type',
        'identifiers',
        'entity_type',
        'taxonomy_id',
        'entity_attributes',
        'sources',
    ])

    # 6. Build fingerprint -> final PK map
    fingerprint_map = (
        temp_entities.select(['entity_pk', '_fingerprint'])
        .join(
            canonicalized_entities.select(['entity_pk', 'entity_id', 'entity_id_type']),
            on='entity_pk',
            how='inner',
        )
        .join(
            entity_key_map.rename({'entity_pk': 'final_entity_pk'}),
            on=['entity_id', 'entity_id_type'],
            how='inner',
        )
        .select(['_fingerprint', 'final_entity_pk'])
        .rename({'final_entity_pk': 'entity_pk'})
        .unique()
    )

    # 7. Write outputs
    final_entities.write_parquet(output_dir / 'entity.parquet')
    fingerprint_map.write_parquet(output_dir / 'entity_map.parquet')
    occurrence_map = None
    if occurrence_fingerprint_map is not None:
        occurrence_map = (
            occurrence_fingerprint_map
            .join(fingerprint_map, on='_fingerprint', how='inner')
            .select(['occurrence_id', '_fingerprint', 'entity_pk'])
            .unique()
        )
        occurrence_map.write_parquet(output_dir / 'entity_occurrence_map.parquet')

    # Build granular source entity evidence and project final entity rows from it.
    entity_evidence = _build_entity_evidence(
        silver_dir=silver_dir,
        occurrence_map=occurrence_map,
        fingerprint_map=fingerprint_map,
        final_entities=final_entities,
        output_dir=output_dir,
        source_name=source_name,
    )
    reduced_final_entities = reduce_entities_from_evidence(
        entity_evidence,
        entity_pk_map=final_entities.select(['entity_key', 'entity_pk']),
    )
    reduced_final_entities.write_parquet(output_dir / 'entity.parquet')

    _write_canonicalization_report(
        output_dir,
        source_name=source_name,
        mapping_dir=mapping_dir,
        summary=summary,
        ambiguous_entities=ambiguous_entities,
    )

    return {
        **summary,
        'entity_count': int(final_entities.height),
    }
