from __future__ import annotations

import argparse
import json
import textwrap
from pathlib import Path
from typing import Any

import polars as pl
from id_resolver.resolve import (
    CHEMICAL_ENTITY_TYPES,
    PROTEIN_ENTITY_TYPES,
    RESOLUTION_STATUS_COLUMN,
    RESOLVED_ID_COLUMN,
    RESOLVED_ID_TYPE_COLUMN,
    STANDARD_INCHI_TYPE,
    TARGET_ENTITY_TYPES,
    UNIPROT_TYPE,
    resolve_identifier_frame,
)

from omnipath_build.gold.utils.canonicalization import (
    _aggregate_identifier_rows,
    _build_ambiguous_entity_report,
    _canonical_identifier_rows,
    _chemical_identifier_rows,
    _collect_ambiguous_entities,
    _entity_export_keys,
    _markdown_table,
    _protein_identifier_rows,
    _reduce_entities,
    _repair_protein_resolutions,
    _resolver_source_rows,
    ONTOLOGY_ENTITY_TYPE_LABEL,
    ONTOLOGY_IDENTIFIER_TYPE_LABEL,
)
from omnipath_build.gold.utils.silver_entity_extraction import extract_from_silver_tables
from omnipath_build.gold.utils.schema import string_or_none


def _build_ontology_terms(ontology_term_rows: list[dict[str, Any]]) -> pl.DataFrame | None:
    if not ontology_term_rows:
        return None

    index: dict[str, dict[str, Any]] = {}
    for row in ontology_term_rows:
        term_id = string_or_none(row.get('term_id'))
        if term_id is None:
            continue
        existing = index.get(term_id)
        incoming_synonyms = {str(v) for v in row.get('synonyms') or [] if v}
        if existing is None:
            index[term_id] = {
                'term_id': term_id,
                'ontology_prefix': string_or_none(row.get('ontology_prefix')),
                'label': string_or_none(row.get('label')),
                'definition': string_or_none(row.get('definition')),
                'synonyms': incoming_synonyms,
                'source': string_or_none(row.get('source')) or '',
            }
            continue
        if existing['label'] is None:
            existing['label'] = string_or_none(row.get('label'))
        if existing['definition'] is None:
            existing['definition'] = string_or_none(row.get('definition'))
        existing['synonyms'].update(incoming_synonyms)

    rows = []
    for term_row in sorted(index.values(), key=lambda r: r['term_id']):
        rows.append({
            'term_id': term_row['term_id'],
            'ontology_prefix': term_row['ontology_prefix'],
            'label': term_row['label'],
            'definition': term_row['definition'],
            'synonyms': sorted(term_row['synonyms']) or None,
            'source': term_row['source'],
        })

    return pl.DataFrame(rows)


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

    # 1. Extract entities and ontology terms from silver tables.
    (
        temp_entities,
        temp_identifiers,
        ontology_term_rows,
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
    if occurrence_fingerprint_map is not None:
        occurrence_map = (
            occurrence_fingerprint_map
            .join(fingerprint_map, on='_fingerprint', how='inner')
            .select(['occurrence_id', '_fingerprint', 'entity_pk'])
            .unique()
        )
        occurrence_map.write_parquet(output_dir / 'entity_occurrence_map.parquet')

    ontology_terms = _build_ontology_terms(ontology_term_rows)
    if ontology_terms is not None:
        ontology_terms.write_parquet(output_dir / 'ontology_term.parquet')

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
        'ontology_term_count': len(ontology_term_rows),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Build canonicalized entity.parquet from silver data.')
    parser.add_argument('--silver-dir', type=Path, required=True, help='Directory containing silver parquet files.')
    parser.add_argument('--mapping-dir', type=Path, default=Path('id_resolver/data'), help='Resolver mapping directory.')
    parser.add_argument('--output-dir', type=Path, required=True, help='Output directory for entity.parquet and entity_map.parquet.')
    parser.add_argument('--source-name', required=True, help='Source name for metadata.')
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    summary = build_entities(
        silver_dir=args.silver_dir,
        mapping_dir=args.mapping_dir,
        output_dir=args.output_dir,
        source_name=args.source_name,
    )
    print(f'Entity build complete: {summary}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
