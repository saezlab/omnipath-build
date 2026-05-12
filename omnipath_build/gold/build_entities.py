from __future__ import annotations

import json
import shutil
import hashlib
import textwrap
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Iterable

import duckdb
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
from omnipath_build.silver.tables import silver_table_dir, has_silver_tables
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

DEFAULT_MIN_PART_SIZE_BYTES = 200 * 1024 * 1024


@dataclass(frozen=True)
class GoldPartitionConfig:
    """Physical layout and bounded-memory knobs for source-level gold entities.

    Buckets are the deterministic logical unit. Parts are the compact physical
    Parquet unit. Final public outputs are written as one Parquet file per part.
    Temporary work may contain more files, but is removed at the end of the task.
    """

    bucket_count: int = 4096
    part_count: int = 128
    duckdb_memory_limit: str | None = None
    duckdb_threads: int | None = None
    duckdb_max_temp_directory_size: str | None = None
    duckdb_partitioned_write_max_open_files: int = 64
    row_group_size: int = 100_000
    min_part_size_bytes: int = DEFAULT_MIN_PART_SIZE_BYTES

    def __post_init__(self) -> None:
        if self.bucket_count <= 0:
            raise ValueError('bucket_count must be positive')
        if self.part_count <= 0:
            raise ValueError('part_count must be positive')
        if self.bucket_count < self.part_count:
            raise ValueError('bucket_count must be >= part_count')
        if self.duckdb_partitioned_write_max_open_files <= 0:
            raise ValueError('duckdb_partitioned_write_max_open_files must be positive')
        if self.min_part_size_bytes < 0:
            raise ValueError('min_part_size_bytes must be non-negative')

    def effective_for_input_bytes(self, input_bytes: int) -> GoldPartitionConfig:
        """Return a config whose physical part count respects the target size.

        ``part_count`` is treated as an upper bound. When the input is smaller
        than ``part_count * min_part_size_bytes``, use fewer physical parts so
        we do not create many tiny Parquet files. The last part may be smaller.
        """
        if self.min_part_size_bytes <= 0 or input_bytes <= 0:
            return self
        max_parts_by_size = max(1, input_bytes // self.min_part_size_bytes)
        effective_part_count = max(1, min(self.part_count, int(max_parts_by_size)))
        if effective_part_count == self.part_count:
            return self
        return replace(self, part_count=effective_part_count)


def parquet_size_bytes(path: str | Path) -> int:
    """Return total bytes for a Parquet file or directory dataset."""
    path = Path(path)
    if not path.exists():
        return 0
    if path.is_file():
        return path.stat().st_size if path.suffix == '.parquet' else 0
    return sum(file.stat().st_size for file in path.rglob('*.parquet') if file.is_file())


def effective_partition_config_for_paths(
    cfg: GoldPartitionConfig,
    paths: Iterable[str | Path],
) -> tuple[GoldPartitionConfig, int]:
    input_bytes = sum(parquet_size_bytes(path) for path in paths)
    return cfg.effective_for_input_bytes(input_bytes), input_bytes


# ---------------------------------------------------------------------------
# Existing canonicalization implementation, intentionally kept intact.
# The rewrite below calls it on filtered silver parts instead of whole sources.
# ---------------------------------------------------------------------------


def _canonicalize_entities(
    entities: pl.DataFrame,
    source_identifiers: pl.DataFrame,
    mapping_dir: Path,
    source_name: str,
) -> tuple[pl.DataFrame, pl.DataFrame, dict[str, Any], list[dict[str, Any]]]:
    """Canonicalize entities in-memory, preserving the original entity_pk column.

    This function is unchanged semantically from the previous implementation;
    the caller now supplies only a bounded occurrence-part slice.
    """
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


# ---------------------------------------------------------------------------
# Scalable source-level entity build.
# ---------------------------------------------------------------------------


def build_entities(
    silver_dir: str | Path,
    mapping_dir: str | Path,
    output_dir: str | Path,
    source_name: str,
    *,
    partition_config: GoldPartitionConfig | None = None,
) -> dict[str, Any]:
    """Extract, canonicalize, deduplicate, and write source-level gold entities.

    The previous implementation built the whole source in memory. This version
    filters silver into deterministic occurrence parts, canonicalizes one part at
    a time, writes normalized evidence, and finally reduces by entity-key part.
    Final public outputs are compact partitioned Parquet datasets:

        entity/part=00000/data.parquet
        entity_evidence/part=00000/data.parquet
        entity_map/part=00000/data.parquet
        entity_occurrence_map/part=00000/data.parquet
    """
    cfg = partition_config or GoldPartitionConfig()
    silver_dir = Path(silver_dir)
    mapping_dir = Path(mapping_dir)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    if not has_silver_tables(silver_dir):
        raise FileNotFoundError(f'silver tables not found under {silver_dir}')

    work_dir = output_dir / '_work_entities'
    if work_dir.exists():
        shutil.rmtree(work_dir)
    work_dir.mkdir(parents=True)

    con = duckdb.connect()
    try:
        _configure_duckdb(con, output_dir, cfg)
        _register_hash_functions(con)

        silver_base = silver_table_dir(silver_dir)
        requested_part_count = cfg.part_count
        cfg, input_bytes = effective_partition_config_for_paths(cfg, [silver_base])
        started_at = time.perf_counter()
        _log_entities(
            source_name,
            f'start silver={silver_dir} output={output_dir} '
            f'parts={cfg.part_count}/{requested_part_count} '
            f'min_part_size={cfg.min_part_size_bytes} input_bytes={input_bytes}',
        )
        summaries: list[dict[str, Any]] = []
        ambiguous_entities: list[dict[str, Any]] = []
        entity_occurrences = 0
        unique_fingerprints = 0

        for occ_part in range(cfg.part_count):
            part_started_at = time.perf_counter()
            part_dir = work_dir / 'silver_parts' / f'occ_part={occ_part:05d}'
            _log_entities(
                source_name,
                f'occ_part {occ_part + 1}/{cfg.part_count} filter start',
            )
            occurrence_count = _write_filtered_silver_occurrence_part(
                con,
                silver_base=silver_base,
                part_state_dir=part_dir,
                occ_part=occ_part,
                cfg=cfg,
            )
            if occurrence_count == 0:
                _log_entities(
                    source_name,
                    f'occ_part {occ_part + 1}/{cfg.part_count} empty '
                    f'in {_elapsed(part_started_at)}',
                )
                continue

            _log_entities(
                source_name,
                f'occ_part {occ_part + 1}/{cfg.part_count} canonicalize '
                f'occurrences={occurrence_count}',
            )
            part_result = _canonicalize_occurrence_part(
                silver_part_dir=part_dir,
                mapping_dir=mapping_dir,
                source_name=source_name,
                output_dir=work_dir,
                occ_part=occ_part,
                cfg=cfg,
            )
            if part_result is None:
                _log_entities(
                    source_name,
                    f'occ_part {occ_part + 1}/{cfg.part_count} no entities '
                    f'in {_elapsed(part_started_at)}',
                )
                continue
            summaries.append(part_result['summary'])
            ambiguous_entities.extend(part_result['ambiguous_entities'])
            entity_occurrences += int(part_result.get('entity_occurrences', 0))
            unique_fingerprints += int(part_result.get('unique_fingerprints', 0))
            _log_entities(
                source_name,
                f'occ_part {occ_part + 1}/{cfg.part_count} done '
                f'entities_seen={part_result["summary"].get("entities_seen", 0)} '
                f'occurrences_total={entity_occurrences} '
                f'in {_elapsed(part_started_at)}',
            )

        _log_entities(source_name, 'finalize partitioned outputs start')
        finalize_started_at = time.perf_counter()
        row_counts = _finalize_entity_outputs(
            con,
            output_dir=output_dir,
            work_dir=work_dir,
            source_name=source_name,
            cfg=cfg,
        )
        _log_entities(
            source_name,
            f'finalize done rows={row_counts} in {_elapsed(finalize_started_at)}',
        )

        summary = _merge_canonicalization_summaries(summaries)
        summary['entity_occurrences'] = entity_occurrences
        summary['unique_fingerprints'] = unique_fingerprints
        summary['entity_count'] = row_counts['entity']

        _write_canonicalization_report(
            output_dir,
            source_name=source_name,
            mapping_dir=mapping_dir,
            summary=summary,
            ambiguous_entities=ambiguous_entities,
        )
        _write_gold_entity_manifest(
            output_dir,
            source_name=source_name,
            cfg=cfg,
            row_counts=row_counts,
            summary=summary,
        )
        _log_entities(
            source_name,
            f'done entity_count={summary["entity_count"]} '
            f'occurrences={entity_occurrences} in {_elapsed(started_at)}',
        )
        return summary
    finally:
        con.close()
        shutil.rmtree(work_dir, ignore_errors=True)


def _canonicalize_occurrence_part(
    *,
    silver_part_dir: Path,
    mapping_dir: Path,
    source_name: str,
    output_dir: Path,
    occ_part: int,
    cfg: GoldPartitionConfig,
) -> dict[str, Any] | None:
    (
        temp_entities,
        temp_identifiers,
        _ontology_term_rows,
        occurrence_fingerprint_map,
        entity_occurrences,
    ) = extract_from_silver_tables(silver_part_dir, source_name)

    if temp_entities.is_empty():
        return None

    unique_fingerprint_count = int(temp_entities.height)
    canonicalized_entities, canonical_identifiers, summary, ambiguous_entities = _canonicalize_entities(
        temp_entities,
        temp_identifiers,
        mapping_dir,
        source_name,
    )
    final_entities, entity_key_map = _reduce_entities(canonicalized_entities, canonical_identifiers)
    if final_entities.is_empty():
        return None

    final_entities = final_entities.with_columns([
        pl.col('entity_attributes').cast(pl.List(pl.Struct({
            'term': pl.Utf8,
            'value': pl.Utf8,
            'unit': pl.Utf8,
        }))),
        pl.struct(['canonical_identifier', 'canonical_identifier_type', 'taxonomy_id'])
        .map_elements(
            lambda row: compute_entity_key(
                row['canonical_identifier'],
                row['canonical_identifier_type'],
                row['taxonomy_id'],
            ),
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

    part_output_dir = output_dir / 'entity_part_outputs' / f'occ_part={occ_part:05d}'
    part_output_dir.mkdir(parents=True, exist_ok=True)

    occurrence_map = None
    if occurrence_fingerprint_map is not None and not occurrence_fingerprint_map.is_empty():
        occurrence_map = (
            occurrence_fingerprint_map
            .join(fingerprint_map, on='_fingerprint', how='inner')
            .select(['occurrence_id', '_fingerprint', 'entity_pk'])
            .unique()
        )

    entity_evidence = _build_entity_evidence(
        silver_dir=silver_part_dir,
        occurrence_map=occurrence_map,
        fingerprint_map=fingerprint_map,
        final_entities=final_entities,
        output_dir=part_output_dir,
        source_name=source_name,
    )
    if not entity_evidence.is_empty():
        entity_evidence = _add_entity_part_columns(entity_evidence, cfg)
        (output_dir / 'canonical_entity_evidence').mkdir(parents=True, exist_ok=True)
        _write_frame_partition_files(
            entity_evidence,
            output_dir / 'canonical_entity_evidence',
            part_column='entity_part',
            part_count=cfg.part_count,
            filename=f'occ_part={occ_part:05d}.parquet',
        )

    if occurrence_map is not None and not occurrence_map.is_empty():
        occurrence_entity_keys = (
            occurrence_map
            .join(final_entities.select(['entity_pk', 'entity_key']), on='entity_pk', how='inner')
            .drop('entity_pk')
            .unique()
        )
        occurrence_entity_keys = _add_occurrence_part_columns(occurrence_entity_keys, cfg)
        (output_dir / 'occurrence_entity_keys').mkdir(parents=True, exist_ok=True)
        _write_frame_partition_files(
            occurrence_entity_keys,
            output_dir / 'occurrence_entity_keys',
            part_column='occ_part',
            part_count=cfg.part_count,
            filename=f'occ_part={occ_part:05d}.parquet',
        )

    if not fingerprint_map.is_empty():
        fingerprint_entity_keys = (
            fingerprint_map
            .join(final_entities.select(['entity_pk', 'entity_key']), on='entity_pk', how='inner')
            .drop('entity_pk')
            .unique()
        )
        fingerprint_entity_keys = _add_fingerprint_part_columns(fingerprint_entity_keys, cfg)
        (output_dir / 'fingerprint_entity_keys').mkdir(parents=True, exist_ok=True)
        _write_frame_partition_files(
            fingerprint_entity_keys,
            output_dir / 'fingerprint_entity_keys',
            part_column='fingerprint_part',
            part_count=cfg.part_count,
            filename=f'occ_part={occ_part:05d}.parquet',
        )

    summary['entity_occurrences'] = int(entity_occurrences)
    summary['unique_fingerprints'] = unique_fingerprint_count
    return {
        'summary': summary,
        'ambiguous_entities': ambiguous_entities,
        'entity_occurrences': int(entity_occurrences),
        'unique_fingerprints': unique_fingerprint_count,
    }


def _finalize_entity_outputs(
    con: duckdb.DuckDBPyConnection,
    *,
    output_dir: Path,
    work_dir: Path,
    source_name: str,
    cfg: GoldPartitionConfig,
) -> dict[str, int]:
    evidence_glob = _glob_or_none(work_dir / 'canonical_entity_evidence')
    occurrence_glob = _glob_or_none(work_dir / 'occurrence_entity_keys')
    fingerprint_glob = _glob_or_none(work_dir / 'fingerprint_entity_keys')

    # Replace public outputs for this source build, but load the old registry
    # before rewriting it so source-local entity_pk values remain stable.
    _prepare_output_dataset_dirs(output_dir, [
        'entity',
        'entity_evidence',
        'entity_occurrence_map',
        'entity_map',
    ])
    (output_dir / '_state' / 'entity_key_registry').mkdir(parents=True, exist_ok=True)

    if evidence_glob is None:
        _write_empty_entity_outputs(con, output_dir, cfg)
        return {'entity': 0, 'entity_evidence': 0, 'entity_occurrence_map': 0, 'entity_map': 0}

    _load_or_create_entity_registry(con, output_dir)
    for entity_part in range(cfg.part_count):
        _create_part_temp_table(
            con,
            table_name='entity_evidence_part',
            root=work_dir / 'canonical_entity_evidence',
            fallback_glob=evidence_glob,
            part_column='entity_part',
            part=entity_part,
            extra_filter='entity_key is not null',
        )
        max_pk = int(con.execute('select coalesce(max(entity_pk), 0) from entity_key_registry').fetchone()[0])
        con.execute('drop table if exists _new_entity_keys')
        con.execute("""
            create temp table _new_entity_keys as
            select distinct entity_key, entity_bucket, entity_part
            from entity_evidence_part
            where entity_key not in (select entity_key from entity_key_registry)
        """)
        con.execute(f"""
            insert into entity_key_registry(entity_key, entity_pk, entity_bucket, entity_part)
            select
                entity_key,
                {max_pk} + row_number() over(order by entity_key) as entity_pk,
                entity_bucket,
                entity_part
            from _new_entity_keys
        """)

    _write_parts(
        con,
        root=output_dir / '_state' / 'entity_key_registry',
        part_count=cfg.part_count,
        part_column='entity_part',
        query="""
            select entity_key, entity_pk, entity_bucket, entity_part
            from entity_key_registry
        """,
        cfg=cfg,
    )

    entity_count = 0
    evidence_count = 0
    for entity_part in range(cfg.part_count):
        _create_part_temp_table(
            con,
            table_name='entity_evidence_part',
            root=work_dir / 'canonical_entity_evidence',
            fallback_glob=evidence_glob,
            part_column='entity_part',
            part=entity_part,
            extra_filter='entity_key is not null',
        )
        entity_query = f"""
            select
                r.entity_pk,
                e.entity_key,
                first(e.canonical_identifier order by e.canonical_identifier nulls last) as canonical_identifier,
                first(e.canonical_identifier_type order by e.canonical_identifier_type nulls last) as canonical_identifier_type,
                list_distinct(flatten(list(e.identifiers))) as identifiers,
                first(e.entity_type order by e.entity_type nulls last) as entity_type,
                first(e.taxonomy_id order by e.taxonomy_id nulls last) as taxonomy_id,
                list_distinct(flatten(list(e.entity_attributes))) as entity_attributes,
                list_sort(list_distinct(list(e.source) filter (where e.source is not null))) as sources,
                r.entity_bucket,
                r.entity_part
            from entity_evidence_part e
            join entity_key_registry r using(entity_key)
            group by r.entity_pk, e.entity_key, r.entity_bucket, r.entity_part
            order by e.entity_key
        """
        entity_count += _copy_part_query(con, entity_query, output_dir / 'entity', entity_part, cfg)

        evidence_query = f"""
            select
                r.entity_pk,
                e.source,
                e.entity_key,
                e.canonical_identifier,
                e.canonical_identifier_type,
                e.raw_record_id,
                e.occurrence_id,
                e.fingerprint,
                e.entity_type,
                e.taxonomy_id,
                e.identifiers,
                e.entity_attributes,
                e.entity_bucket,
                e.entity_part,
                e.occ_bucket,
                e.occ_part
            from entity_evidence_part e
            join entity_key_registry r using(entity_key)
            order by e.entity_key, e.source, e.raw_record_id, e.occurrence_id
        """
        evidence_count += _copy_part_query(con, evidence_query, output_dir / 'entity_evidence', entity_part, cfg)

    occurrence_count = 0
    if occurrence_glob is not None:
        for occ_part in range(cfg.part_count):
            _create_part_temp_table(
                con,
                table_name='occurrence_entity_keys_part',
                root=work_dir / 'occurrence_entity_keys',
                fallback_glob=occurrence_glob,
                part_column='occ_part',
                part=occ_part,
            )
            query = f"""
                select
                    o.occurrence_id,
                    o._fingerprint,
                    r.entity_pk,
                    o.entity_key,
                    o.occ_bucket,
                    o.occ_part
                from occurrence_entity_keys_part o
                join entity_key_registry r using(entity_key)
                order by o.occurrence_id
            """
            occurrence_count += _copy_part_query(con, query, output_dir / 'entity_occurrence_map', occ_part, cfg)

    entity_map_count = 0
    if fingerprint_glob is not None:
        for fingerprint_part in range(cfg.part_count):
            _create_part_temp_table(
                con,
                table_name='fingerprint_entity_keys_part',
                root=work_dir / 'fingerprint_entity_keys',
                fallback_glob=fingerprint_glob,
                part_column='fingerprint_part',
                part=fingerprint_part,
            )
            query = f"""
                select
                    f._fingerprint,
                    r.entity_pk,
                    f.entity_key,
                    f.fingerprint_bucket,
                    f.fingerprint_part
                from fingerprint_entity_keys_part f
                join entity_key_registry r using(entity_key)
                order by f._fingerprint
            """
            entity_map_count += _copy_part_query(con, query, output_dir / 'entity_map', fingerprint_part, cfg)

    return {
        'entity': entity_count,
        'entity_evidence': evidence_count,
        'entity_occurrence_map': occurrence_count,
        'entity_map': entity_map_count,
    }


# ---------------------------------------------------------------------------
# Evidence helpers retained from the previous implementation.
# ---------------------------------------------------------------------------


def _build_entity_evidence(
    silver_dir: str | Path,
    occurrence_map: pl.DataFrame | None,
    fingerprint_map: pl.DataFrame | None,
    final_entities: pl.DataFrame,
    output_dir: Path,
    source_name: str,
) -> pl.DataFrame:
    silver_base = Path(silver_dir)
    entity_occurrence_path = _silver_table_path(silver_base, 'entity_occurrence')
    if not entity_occurrence_path.exists():
        empty_evidence = pl.DataFrame({
            name: pl.Series([], dtype=dtype)
            for name, dtype in ENTITY_EVIDENCE_SCHEMA.items()
        })
        empty_evidence.write_parquet(output_dir / 'entity_evidence.parquet')
        return empty_evidence

    raw_records = _read_silver_polars_table(entity_occurrence_path).select([
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
    annotation_path = _silver_table_path(silver_base, 'entity_annotation')
    if not annotation_path.exists():
        return empty

    annotations = (
        _read_silver_polars_table(annotation_path)
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


def _read_silver_polars_table(path: Path) -> pl.DataFrame:
    return pl.read_parquet(str(path / '**' / '*.parquet'), hive_partitioning=False)


def reduce_entities_from_evidence(
    entity_evidence: pl.DataFrame,
    *,
    entity_pk_map: pl.DataFrame | None = None,
) -> pl.DataFrame:
    """Compatibility helper for tests and small in-memory fixtures."""
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


# ---------------------------------------------------------------------------
# Silver filtering and Parquet output helpers.
# ---------------------------------------------------------------------------


def _write_filtered_silver_occurrence_part(
    con: duckdb.DuckDBPyConnection,
    *,
    silver_base: Path,
    part_state_dir: Path,
    occ_part: int,
    cfg: GoldPartitionConfig,
) -> int:
    if part_state_dir.exists():
        shutil.rmtree(part_state_dir)
    part_state_dir.mkdir(parents=True, exist_ok=True)

    occurrence_path = _silver_table_path(silver_base, 'entity_occurrence')
    if not occurrence_path.exists():
        raise FileNotFoundError(f'missing silver entity_occurrence table: {occurrence_path}')

    con.execute('drop table if exists _part_occurrence_ids')
    con.execute(f"""
        create temp table _part_occurrence_ids as
        select distinct try_cast(occurrence_id as varchar) as occurrence_id
        from {_read_parquet_dataset_sql(occurrence_path)}
        where occurrence_id is not null
          and stable_part(try_cast(occurrence_id as varchar), {cfg.bucket_count}, {cfg.part_count}) = {occ_part}
    """)
    row_count = int(con.execute('select count(*) from _part_occurrence_ids').fetchone()[0])
    if row_count == 0:
        return 0

    _copy_silver_dataset_query(con, f"""
        select *
        from {_read_parquet_dataset_sql(occurrence_path)}
        where try_cast(occurrence_id as varchar) in (select occurrence_id from _part_occurrence_ids)
    """, part_state_dir / 'entity_occurrence')

    _copy_silver_table_filtered_by_occurrence(
        con,
        _silver_table_path(silver_base, 'entity_identifier'),
        part_state_dir / 'entity_identifier',
    )
    _copy_silver_table_filtered_by_occurrence(
        con,
        _silver_table_path(silver_base, 'entity_annotation'),
        part_state_dir / 'entity_annotation',
    )

    membership_path = _silver_table_path(silver_base, 'membership')
    if membership_path.exists():
        con.execute('drop table if exists _part_membership_ids')
        _copy_silver_dataset_query(con, f"""
            select *
            from {_read_parquet_dataset_sql(membership_path)}
            where try_cast(parent_occurrence_id as varchar) in (select occurrence_id from _part_occurrence_ids)
               or try_cast(member_occurrence_id as varchar) in (select occurrence_id from _part_occurrence_ids)
        """, part_state_dir / 'membership')
        con.execute(f"""
            create temp table _part_membership_ids as
            select distinct try_cast(membership_id as varchar) as membership_id
            from {_read_parquet_dataset_sql(part_state_dir / 'membership')}
            where membership_id is not null
        """)
    else:
        _copy_silver_dataset_query(con, "select null::varchar as membership_id, null::varchar as parent_occurrence_id, null::varchar as member_occurrence_id where false", part_state_dir / 'membership')
        con.execute('drop table if exists _part_membership_ids')
        con.execute('create temp table _part_membership_ids(membership_id varchar)')

    membership_annotation_path = _silver_table_path(silver_base, 'membership_annotation')
    if membership_annotation_path.exists():
        _copy_silver_dataset_query(con, f"""
            select *
            from {_read_parquet_dataset_sql(membership_annotation_path)}
            where try_cast(membership_id as varchar) in (select membership_id from _part_membership_ids)
        """, part_state_dir / 'membership_annotation')
    else:
        _copy_silver_dataset_query(con, "select null::varchar as membership_id, null::varchar as term, null::varchar as value, null::varchar as unit where false", part_state_dir / 'membership_annotation')

    return row_count


def _copy_silver_table_filtered_by_occurrence(
    con: duckdb.DuckDBPyConnection,
    source_path: Path,
    output_path: Path,
) -> None:
    if source_path.exists():
        _copy_silver_dataset_query(con, f"""
            select *
            from {_read_parquet_dataset_sql(source_path)}
            where try_cast(occurrence_id as varchar) in (select occurrence_id from _part_occurrence_ids)
        """, output_path)
    else:
        _copy_silver_dataset_query(con, "select null::varchar as occurrence_id where false", output_path)


def _copy_silver_dataset_query(
    con: duckdb.DuckDBPyConnection,
    query: str,
    output_dir: Path,
) -> None:
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    _copy_query(con, query, output_dir / 'part=00000.parquet')


def _silver_table_path(silver_base: Path, name: str) -> Path:
    return silver_base / name


def _read_parquet_dataset_sql(path: Path) -> str:
    return (
        "read_parquet("
        f"'{_sql_path(path / '**' / '*.parquet')}', "
        "union_by_name=true, hive_partitioning=true)"
    )


def _write_empty_entity_outputs(
    con: duckdb.DuckDBPyConnection,
    output_dir: Path,
    cfg: GoldPartitionConfig,
) -> None:
    for part in range(cfg.part_count):
        _copy_part_query(con, f"""
            select
                null::bigint as entity_pk,
                null::varchar as entity_key,
                null::varchar as canonical_identifier,
                null::varchar as canonical_identifier_type,
                []::struct(identifier varchar, identifier_type varchar)[] as identifiers,
                null::varchar as entity_type,
                null::varchar as taxonomy_id,
                []::struct(term varchar, "value" varchar, unit varchar)[] as entity_attributes,
                []::varchar[] as sources,
                null::bigint as entity_bucket,
                {part}::bigint as entity_part
            where false
        """, output_dir / 'entity', part, cfg)
        _copy_part_query(con, f"""
            select
                null::bigint as entity_pk,
                null::varchar as source,
                null::varchar as entity_key,
                null::varchar as canonical_identifier,
                null::varchar as canonical_identifier_type,
                null::varchar as raw_record_id,
                null::varchar as occurrence_id,
                null::varchar as fingerprint,
                null::varchar as entity_type,
                null::varchar as taxonomy_id,
                []::struct(identifier varchar, identifier_type varchar)[] as identifiers,
                []::struct(term varchar, "value" varchar, unit varchar)[] as entity_attributes,
                null::bigint as entity_bucket,
                {part}::bigint as entity_part,
                null::bigint as occ_bucket,
                null::bigint as occ_part
            where false
        """, output_dir / 'entity_evidence', part, cfg)


# ---------------------------------------------------------------------------
# Reporting and schema helpers.
# ---------------------------------------------------------------------------


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
        ['entity occurrences (total extracted)', str(summary.get('entity_occurrences', summary.get('entities_seen', 0)))],
        ['unique fingerprints (pre-deduped)', str(summary.get('unique_fingerprints', summary.get('entities_seen', 0)))],
        ['eligible entities', str(summary.get('eligible_entities', 0))],
        ['resolved entities', str(summary.get('resolved_entities', 0))],
        ['ambiguous entities', str(summary.get('ambiguous_entities', 0))],
        ['backbone conflicts', str(summary.get('exact_conflicts', 0))],
        ['near conflicts', str(summary.get('near_conflicts', 0))],
        ['authoritative identifier rows written', str(summary.get('identifier_rows_added', 0))],
        ['entities with resolved identifiers', str(summary.get('entities_updated', 0))],
    ]
    report = textwrap.dedent(
        f"""\
        # Canonicalization report

        - Source: `{source_label}`
        - Gold directory: `{output_dir}`
        - Resolver mappings: `{mapping_dir}`

        ## What this step does

        - Reads silver data in occurrence-key parts
        - Resolves supported identifiers to one canonical backbone per entity
        - Writes normalized source-level entity evidence
        - Reduces entities by stable `entity_key` in compact Parquet parts

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


def _write_gold_entity_manifest(
    output_dir: Path,
    *,
    source_name: str,
    cfg: GoldPartitionConfig,
    row_counts: dict[str, int],
    summary: dict[str, Any],
) -> None:
    manifest = {
        'layer': 'gold',
        'kind': 'entities',
        'source': source_name,
        'bucket_algorithm': 'stable_u64_sha256_mod_v1',
        'entity_key_algorithm': 'sha256_v1',
        'bucket_count': cfg.bucket_count,
        'part_count': cfg.part_count,
        'min_part_size_bytes': cfg.min_part_size_bytes,
        'entity_bucket_count': cfg.bucket_count,
        'entity_part_count': cfg.part_count,
        'occ_bucket_count': cfg.bucket_count,
        'occ_part_count': cfg.part_count,
        'outputs': {
            'entity': 'entity/',
            'entity_evidence': 'entity_evidence/',
            'entity_map': 'entity_map/',
            'entity_occurrence_map': 'entity_occurrence_map/',
        },
        'row_counts': row_counts,
        'summary': summary,
    }
    (output_dir / 'manifest.json').write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + '\n',
        encoding='utf-8',
    )


def _merge_canonicalization_summaries(summaries: Iterable[dict[str, Any]]) -> dict[str, Any]:
    keys = [
        'entities_seen',
        'eligible_entities',
        'resolved_entities',
        'ambiguous_entities',
        'exact_conflicts',
        'near_conflicts',
        'identifier_rows_added',
        'entities_updated',
    ]
    out = {key: 0 for key in keys}
    for summary in summaries:
        for key in keys:
            out[key] += int(summary.get(key, 0) or 0)
    return out


# ---------------------------------------------------------------------------
# Hashing, DuckDB, and filesystem helpers.
# ---------------------------------------------------------------------------


def _stable_u64(value: str | None) -> int | None:
    if value is None:
        return None
    digest = hashlib.sha256(str(value).encode('utf-8')).digest()
    return int.from_bytes(digest[:8], 'big', signed=False)


def _stable_bucket_py(value: str | None, bucket_count: int) -> int | None:
    hashed = _stable_u64(value)
    if hashed is None:
        return None
    return int(hashed % bucket_count)


def _stable_part_py(value: str | None, bucket_count: int, part_count: int) -> int | None:
    bucket = _stable_bucket_py(value, bucket_count)
    if bucket is None:
        return None
    return int(bucket * part_count // bucket_count)


def _register_hash_functions(con: duckdb.DuckDBPyConnection) -> None:
    try:
        con.create_function('stable_bucket', _stable_bucket_py, return_type='BIGINT')
    except Exception:
        pass
    try:
        con.create_function('stable_part', _stable_part_py, return_type='BIGINT')
    except Exception:
        pass


def _add_entity_part_columns(frame: pl.DataFrame, cfg: GoldPartitionConfig) -> pl.DataFrame:
    return frame.with_columns([
        pl.col('entity_key').map_elements(lambda v: _stable_bucket_py(v, cfg.bucket_count), return_dtype=pl.Int64).alias('entity_bucket'),
        pl.col('entity_key').map_elements(lambda v: _stable_part_py(v, cfg.bucket_count, cfg.part_count), return_dtype=pl.Int64).alias('entity_part'),
        pl.col('occurrence_id').map_elements(lambda v: _stable_bucket_py(v, cfg.bucket_count), return_dtype=pl.Int64).alias('occ_bucket'),
        pl.col('occurrence_id').map_elements(lambda v: _stable_part_py(v, cfg.bucket_count, cfg.part_count), return_dtype=pl.Int64).alias('occ_part'),
    ])


def _add_occurrence_part_columns(frame: pl.DataFrame, cfg: GoldPartitionConfig) -> pl.DataFrame:
    return frame.with_columns([
        pl.col('occurrence_id').map_elements(lambda v: _stable_bucket_py(v, cfg.bucket_count), return_dtype=pl.Int64).alias('occ_bucket'),
        pl.col('occurrence_id').map_elements(lambda v: _stable_part_py(v, cfg.bucket_count, cfg.part_count), return_dtype=pl.Int64).alias('occ_part'),
    ])


def _add_fingerprint_part_columns(frame: pl.DataFrame, cfg: GoldPartitionConfig) -> pl.DataFrame:
    return frame.with_columns([
        pl.col('_fingerprint').map_elements(lambda v: _stable_bucket_py(v, cfg.bucket_count), return_dtype=pl.Int64).alias('fingerprint_bucket'),
        pl.col('_fingerprint').map_elements(lambda v: _stable_part_py(v, cfg.bucket_count, cfg.part_count), return_dtype=pl.Int64).alias('fingerprint_part'),
    ])


def _configure_duckdb(con: duckdb.DuckDBPyConnection, output_dir: Path, cfg: GoldPartitionConfig) -> None:
    temp_dir = output_dir / '.duckdb_tmp'
    temp_dir.mkdir(parents=True, exist_ok=True)
    con.execute('set preserve_insertion_order = false')
    con.execute(f"set temp_directory = '{_sql_path(temp_dir)}'")
    _try_duckdb_setting(
        con,
        f'set partitioned_write_max_open_files = {cfg.duckdb_partitioned_write_max_open_files}',
    )
    if cfg.duckdb_memory_limit:
        con.execute(f"set memory_limit = '{cfg.duckdb_memory_limit}'")
    if cfg.duckdb_max_temp_directory_size:
        con.execute(f"set max_temp_directory_size = '{cfg.duckdb_max_temp_directory_size}'")
    if cfg.duckdb_threads:
        con.execute(f"set threads = {cfg.duckdb_threads}")


def _try_duckdb_setting(con: duckdb.DuckDBPyConnection, statement: str) -> None:
    try:
        con.execute(statement)
    except duckdb.Error:
        pass


def _load_or_create_entity_registry(con: duckdb.DuckDBPyConnection, output_dir: Path) -> None:
    con.execute('drop table if exists entity_key_registry')
    registry_glob = _glob_or_none(output_dir / '_state' / 'entity_key_registry')
    if registry_glob is None:
        con.execute('''
            create temp table entity_key_registry(
                entity_key varchar,
                entity_pk bigint,
                entity_bucket bigint,
                entity_part bigint
            )
        ''')
    else:
        con.execute(f"""
            create temp table entity_key_registry as
            select
                try_cast(entity_key as varchar) as entity_key,
                try_cast(entity_pk as bigint) as entity_pk,
                try_cast(entity_bucket as bigint) as entity_bucket,
                try_cast(entity_part as bigint) as entity_part
            from read_parquet('{_sql_path(registry_glob)}', union_by_name=true)
        """)


def _prepare_output_dataset_dirs(output_dir: Path, names: Iterable[str]) -> None:
    for name in names:
        path = output_dir / name
        if path.exists():
            shutil.rmtree(path)
        path.mkdir(parents=True, exist_ok=True)


def _write_parts(
    con: duckdb.DuckDBPyConnection,
    *,
    root: Path,
    part_count: int,
    part_column: str,
    query: str,
    cfg: GoldPartitionConfig,
) -> int:
    total = 0
    for part in range(part_count):
        total += _copy_part_query(
            con,
            f"select * from ({query}) where {part_column} = {part}",
            root,
            part,
            cfg,
        )
    return total


def _copy_part_query(
    con: duckdb.DuckDBPyConnection,
    query: str,
    root: Path,
    part: int,
    cfg: GoldPartitionConfig,
) -> int:
    part_dir = root / f'part={part:05d}'
    tmp_dir = root / f'.part={part:05d}.tmp'
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir)
    tmp_dir.mkdir(parents=True, exist_ok=True)
    output_path = tmp_dir / 'data.parquet'
    con.execute(
        f"copy ({query}) to '{_sql_path(output_path)}' "
        f"(format parquet, compression zstd, row_group_size {cfg.row_group_size})"
    )
    count = int(con.execute(f"select count(*) from read_parquet('{_sql_path(output_path)}')").fetchone()[0])
    if part_dir.exists():
        shutil.rmtree(part_dir)
    tmp_dir.replace(part_dir)
    return count


def _copy_query(con: duckdb.DuckDBPyConnection, query: str, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    con.execute(f"copy ({query}) to '{_sql_path(output_path)}' (format parquet, compression zstd)")


def _write_frame_partition_files(
    frame: pl.DataFrame,
    root: Path,
    *,
    part_column: str,
    part_count: int,
    filename: str,
) -> None:
    for part in range(part_count):
        part_frame = frame.filter(pl.col(part_column) == part)
        if part_frame.is_empty():
            continue
        part_dir = root / f'{part_column}={part:05d}'
        part_dir.mkdir(parents=True, exist_ok=True)
        part_frame.write_parquet(part_dir / filename)


def _partition_glob_or_none(root: Path, part_column: str, part: int) -> str | None:
    return _glob_or_none(root / f'{part_column}={part:05d}')


def _has_partition_dirs(root: Path, part_column: str) -> bool:
    if not root.exists() or not root.is_dir():
        return False
    prefix = f'{part_column}='
    return any(path.is_dir() and path.name.startswith(prefix) for path in root.iterdir())


def _create_part_temp_table(
    con: duckdb.DuckDBPyConnection,
    *,
    table_name: str,
    root: Path,
    fallback_glob: str,
    part_column: str,
    part: int,
    extra_filter: str = 'true',
) -> None:
    con.execute(f'drop table if exists {table_name}')
    part_glob = _partition_glob_or_none(root, part_column, part)
    if part_glob is None and _has_partition_dirs(root, part_column):
        con.execute(f"""
            create temp table {table_name} as
            select *
            from read_parquet('{_sql_path(fallback_glob)}', union_by_name=true)
            where false
        """)
        return

    read_glob = part_glob or fallback_glob
    con.execute(f"""
        create temp table {table_name} as
        select *
        from read_parquet('{_sql_path(read_glob)}', union_by_name=true)
        where {extra_filter}
          and {part_column} = {part}
    """)


def _glob_or_none(root: Path) -> str | None:
    if not root.exists():
        return None
    if root.is_file():
        return str(root)
    files = list(root.rglob('*.parquet'))
    if not files:
        return None
    return str(root / '**' / '*.parquet')


def _sql_path(path: str | Path) -> str:
    return str(path).replace("'", "''")


def _elapsed(started_at: float) -> str:
    elapsed = time.perf_counter() - started_at
    if elapsed < 60:
        return f'{elapsed:.1f}s'
    minutes, seconds = divmod(elapsed, 60)
    return f'{int(minutes)}m {seconds:.0f}s'


def _log_entities(source_name: str, message: str) -> None:
    print(f'[gold:entities:{source_name}] {message}', flush=True)
