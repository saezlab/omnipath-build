from __future__ import annotations

import hashlib
import json
import shutil
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb

from omnipath_build.gold.utils.cv_terms import format_cv_term
from omnipath_build.gold.utils.schema import CV_TERM_ENTITY_TYPE, ONTOLOGY_IDENTIFIER_TERM
from omnipath_build.pipeline.progress import update_phase
from omnipath_build.rewrite.build_resources import (
    build_resources_parquet,
    build_resources_parquet_from_duckdb,
)


ONTOLOGY_ENTITY_TYPE_LABEL = format_cv_term(CV_TERM_ENTITY_TYPE)
ONTOLOGY_IDENTIFIER_TYPE_LABEL = format_cv_term(ONTOLOGY_IDENTIFIER_TERM)
_DEFAULT_BUILD_RESOURCES_PARQUET = build_resources_parquet


@dataclass(frozen=True)
class CombinedRewriteConfig:
    """Internal recompute and DuckDB runtime settings for rewrite combined."""

    bucket_count: int = 4096
    part_count: int = 16
    duckdb_memory_limit: str | None = None
    duckdb_threads: int | None = None
    duckdb_max_temp_directory_size: str | None = None
    row_group_size: int = 100_000

    def __post_init__(self) -> None:
        if self.bucket_count <= 0:
            raise ValueError('bucket_count must be positive')
        if self.part_count <= 0:
            raise ValueError('part_count must be positive')
        if self.bucket_count < self.part_count:
            raise ValueError('bucket_count must be >= part_count')
        if self.row_group_size <= 0:
            raise ValueError('row_group_size must be positive')


@dataclass(frozen=True)
class GoldSourceDir:
    source: str
    path: Path


@dataclass(frozen=True)
class SourceDatasets:
    entity: Path | None
    entity_evidence: Path | None
    entity_relation: Path | None
    entity_relation_evidence: Path | None


def build_combined_duckdb(
    *,
    gold_root: str | Path = 'data/gold',
    output_dir: str | Path = 'data/combined',
    reports_dir: str | Path | None = None,
    state_path: str | Path | None = None,
    source_state_paths: dict[str, str | Path] | None = None,
    use_source_scopes: bool = False,
    affected_entity_key_paths: list[str | Path] | None = None,
    affected_relation_key_paths: list[str | Path] | None = None,
    inputs_package: str = 'pypath.inputs_v2',
    freeze_monthly: bool = False,
    changed_source: str | None = None,
    entity_batch_size: int = 50_000,
    relation_batch_size: int = 50_000,
    config: CombinedRewriteConfig | None = None,
) -> dict[str, Any]:
    """Build/update combined artifacts through a bounded-memory DuckDB state store.

    The state database stores merged rows on disk. Expensive recomputations are
    scoped to entity/relation parts. Public rewrite artifacts are flat Parquet
    files under artifacts/combined/latest.
    """
    cfg = config or CombinedRewriteConfig()
    gold_root = Path(gold_root)
    output_dir = Path(output_dir)
    reports_dir = Path(reports_dir) if reports_dir is not None else output_dir / 'reports'
    combined_reports_dir = reports_dir / 'combined'
    output_dir.mkdir(parents=True, exist_ok=True)
    combined_reports_dir.mkdir(parents=True, exist_ok=True)
    _remove_stale_artifact_runs(output_dir)

    source_dirs = (
        _source_dirs_from_state_paths(source_state_paths)
        if source_state_paths is not None
        else (_discover_gold_source_dirs(gold_root) if gold_root.exists() else [])
    )
    if source_state_paths is not None:
        input_bytes = sum(source_dir.path.stat().st_size for source_dir in source_dirs)
    else:
        input_bytes = sum(_parquet_size_bytes(source_dir.path) for source_dir in source_dirs)
    if input_bytes == 0:
        _log(
            'skipping rewrite DuckDB combine; '
            f'no gold parquet input bytes found under {gold_root}'
        )
        return {
            'gold_root': str(gold_root),
            'output_dir': str(output_dir),
            'engine': 'duckdb',
            'mode': 'skipped',
            'skipped': 'empty_gold_input',
            'bucket_algorithm': 'stable_u64_sha256_mod_v1',
            'bucket_count': cfg.bucket_count,
            'part_count': 0,
            'requested_part_count': cfg.part_count,
            'partition_input_bytes': input_bytes,
            'updated_entity_parts': [],
            'updated_relation_parts': [],
            'sources': [{'source': item.source, 'path': str(item.path)} for item in source_dirs],
            'row_counts': {},
        }
    state_path = Path(state_path) if state_path is not None else output_dir / 'state.duckdb'
    state_path.parent.mkdir(parents=True, exist_ok=True)
    run_id = _new_run_id()
    incremental_requested = (
        affected_entity_key_paths is not None
        or affected_relation_key_paths is not None
        or use_source_scopes
    )

    con = duckdb.connect(str(state_path))
    try:
        _configure_duckdb(con, output_dir, cfg)
        _register_hash_functions(con)
        _ensure_state_schema(con)

        bootstrap_state = _state_is_empty(con) or not incremental_requested
        mode = 'bootstrap' if bootstrap_state else 'incremental'
        if bootstrap_state:
            _log(
                'starting rewrite DuckDB combine bootstrap '
                f'parts={cfg.part_count} input_bytes={input_bytes}'
            )
            _reset_state(con)
            entity_parts = set(range(cfg.part_count))
            relation_parts = set(range(cfg.part_count))
            affected_entity_count = 0
            affected_relation_count = 0
        else:
            if use_source_scopes:
                _create_affected_key_tables_from_source_scopes(
                    con,
                    source_dirs=source_dirs,
                    cfg=cfg,
                )
            else:
                _create_affected_key_tables(
                    con,
                    entity_paths=affected_entity_key_paths or [],
                    relation_paths=affected_relation_key_paths or [],
                    cfg=cfg,
                )
            affected_entity_count = _table_count(con, 'affected_entity_keys')
            affected_relation_count = _table_count(con, 'affected_relation_keys')
            if affected_entity_count == 0 and affected_relation_count == 0:
                _log('skipping rewrite DuckDB combine; no affected source-gold keys')
                return {
                    'gold_root': str(gold_root),
                    'output_dir': str(output_dir / 'latest'),
                    'state_path': str(state_path),
                    'engine': 'duckdb',
                    'mode': 'skipped',
                    'skipped': 'empty_affected_scope',
                    'run_id': run_id,
                    'bucket_algorithm': 'stable_u64_sha256_mod_v1',
                    'bucket_count': cfg.bucket_count,
                    'part_count': cfg.part_count,
                    'requested_part_count': cfg.part_count,
                    'partition_input_bytes': input_bytes,
                    'updated_entity_parts': [],
                    'updated_relation_parts': [],
                    'sources': [{'source': item.source, 'path': str(item.path)} for item in source_dirs],
                    'row_counts': _row_counts(con),
                }
            _log(
                'starting rewrite DuckDB combine incremental '
                f'entities={affected_entity_count} relations={affected_relation_count} '
                f'parts={cfg.part_count} input_bytes={input_bytes}'
            )
            entity_parts = _parts_for_table(con, 'affected_entity_keys', 'entity_part')
            _expand_relation_keys_for_affected_entities(con, cfg=cfg)
            affected_relation_count = _table_count(con, 'affected_relation_keys')
            relation_parts = _parts_for_table(con, 'affected_relation_keys', 'relation_part')

        if bootstrap_state:
            update_phase('entity parts')
            _apply_entity_parts(con, source_dirs, parts=entity_parts, cfg=cfg, full_build=True)
            update_phase('relation parts')
            _apply_relation_parts(con, source_dirs, parts=relation_parts, cfg=cfg, full_build=True)
        else:
            if affected_entity_count:
                update_phase('entity parts')
                _apply_entity_parts(con, source_dirs, parts=entity_parts, cfg=cfg, full_build=False)
            if affected_relation_count:
                update_phase('relation parts')
                _apply_relation_parts(con, source_dirs, parts=relation_parts, cfg=cfg, full_build=False)

        version_dir = output_dir / 'latest'
        started = time.perf_counter()
        update_phase('export latest')
        _log(f'exporting flat parquet artifacts to {version_dir}')
        _export_latest(
            con,
            version_dir,
            cfg=cfg,
            entity_parts=entity_parts if not bootstrap_state else set(range(cfg.part_count)),
            relation_parts=relation_parts if not bootstrap_state else set(range(cfg.part_count)),
            full_build=bootstrap_state,
        )
        _log(f'done parquet export in {time.perf_counter() - started:.1f}s')

        update_phase('relation annotation')
        relation_annotation_summary = _export_relation_annotation(
            con,
            version_dir,
            cfg=cfg,
            relation_parts=relation_parts if not bootstrap_state else set(range(cfg.part_count)),
            full_build=bootstrap_state,
        )
        (combined_reports_dir / 'relation_annotation_summary.json').write_text(
            json.dumps(relation_annotation_summary, indent=2) + '\n',
            encoding='utf-8',
        )

        update_phase('run artifacts')
        run_summary = _write_run_artifacts(
            con,
            reports_dir=combined_reports_dir,
            latest_dir=version_dir,
            run_id=run_id,
            mode=mode,
            changed_source=changed_source,
            affected_entity_count=affected_entity_count,
            affected_relation_count=affected_relation_count,
        )
        _record_combined_run(
            con,
            run_id=run_id,
            mode=mode,
            summary=run_summary,
        )

        update_phase('resources')
        if (
            source_state_paths is not None
            and build_resources_parquet is _DEFAULT_BUILD_RESOURCES_PARQUET
        ):
            resources_path, resources_row_count = build_resources_parquet_from_duckdb(
                con=con,
                source_state_paths=source_state_paths,
                output_path=version_dir / 'resources.parquet',
                inputs_package=inputs_package,
            )
        else:
            resources_path = build_resources_parquet(
                gold_root=gold_root,
                output_path=version_dir / 'resources.parquet',
                inputs_package=inputs_package,
            )
            resources_row_count = None

        row_counts = _row_counts(con)
        row_counts['relation_annotation_term.parquet'] = int(relation_annotation_summary['row_count'])
        if resources_row_count is not None:
            row_counts['resources.parquet'] = resources_row_count
        elif resources_path.exists():
            row_counts['resources.parquet'] = int(
                con.execute(f"select count(*) from read_parquet('{_sql_path(resources_path)}')").fetchone()[0]
            )

        summary = {
            'gold_root': str(gold_root),
            'output_dir': str(version_dir),
            'reports_dir': str(combined_reports_dir),
            'state_path': str(state_path),
            'engine': 'duckdb',
            'mode': mode,
            'run_id': run_id,
            'bucket_algorithm': 'stable_u64_sha256_mod_v1',
            'bucket_count': cfg.bucket_count,
            'part_count': cfg.part_count,
            'requested_part_count': cfg.part_count,
            'partition_input_bytes': input_bytes,
            'updated_entity_parts': sorted(entity_parts),
            'updated_relation_parts': sorted(relation_parts),
            'sources': [{'source': item.source, 'path': str(item.path)} for item in source_dirs],
            'row_counts': row_counts,
            'relation_annotation_summary': relation_annotation_summary,
            'run_summary': run_summary,
            'run_report': run_summary['run_report'],
            'resources_path': str(resources_path),
        }
        (combined_reports_dir / 'combined_build_summary.json').write_text(json.dumps(summary, indent=2) + '\n', encoding='utf-8')
        _append_build_manifest(
            combined_reports_dir,
            mode=mode,
            freeze_monthly=freeze_monthly,
            row_counts=row_counts,
            affected_entities=affected_entity_count,
            affected_relations=affected_relation_count,
            changed_source=changed_source,
            cfg=cfg,
        )

        if freeze_monthly:
            snapshot_dir = _freeze_monthly_snapshot(output_dir, version_dir)
            summary['monthly_snapshot'] = str(snapshot_dir)

        if use_source_scopes:
            _clear_consumed_source_scopes(source_dirs)

        return summary
    finally:
        con.close()


# ---------------------------------------------------------------------------
# State schema.
# ---------------------------------------------------------------------------


def _ensure_state_schema(con: duckdb.DuckDBPyConnection) -> None:
    con.execute('''
        create table if not exists combined_run (
            combined_run_id varchar primary key,
            pipeline_run_id varchar,
            started_at timestamp,
            finished_at timestamp,
            status varchar,
            mode varchar,
            manifest_json varchar
        )
    ''')
    con.execute('''
        create table if not exists combined_run_scope_entity (
            combined_run_id varchar,
            source varchar,
            entity_key varchar,
            entity_id bigint,
            entity_part bigint,
            reason varchar
        )
    ''')
    con.execute('''
        create table if not exists combined_run_scope_relation (
            combined_run_id varchar,
            source varchar,
            relation_key varchar,
            relation_id bigint,
            relation_part bigint,
            reason varchar
        )
    ''')
    con.execute('''
        create table if not exists entity_key_map (
            entity_key varchar primary key,
            entity_id bigint,
            entity_bucket bigint,
            entity_part bigint
        )
    ''')
    con.execute('''
        create table if not exists relation_key_map (
            relation_key varchar primary key,
            relation_id bigint,
            relation_bucket bigint,
            relation_part bigint
        )
    ''')
    con.execute('''
        create table if not exists entity (
            entity_id bigint,
            entity_key varchar,
            canonical_identifier varchar,
            canonical_identifier_type varchar,
            identifiers struct(identifier varchar, identifier_type varchar)[],
            entity_type varchar,
            taxonomy_id varchar,
            entity_attributes struct(term varchar, "value" varchar, unit varchar)[],
            sources varchar[],
            source_count bigint,
            entity_bucket bigint,
            entity_part bigint
        )
    ''')
    con.execute('''
        create table if not exists entity_source (
            entity_key varchar,
            source varchar,
            evidence_count bigint,
            payload_hash varchar,
            active boolean,
            entity_bucket bigint,
            entity_part bigint
        )
    ''')
    con.execute('''
        create table if not exists entity_evidence (
            source varchar,
            entity_key varchar,
            raw_record_ids varchar[],
            entity_type varchar,
            taxonomy_id varchar,
            identifiers struct(identifier varchar, identifier_type varchar)[],
            entity_attributes struct(term varchar, "value" varchar, unit varchar)[],
            evidence struct(term varchar, "value" varchar, unit varchar)[],
            entity_bucket bigint,
            entity_part bigint
        )
    ''')
    con.execute('''
        create table if not exists entity_relation (
            relation_id bigint,
            relation_key varchar,
            subject_entity_id bigint,
            subject_entity_key varchar,
            predicate varchar,
            object_entity_id bigint,
            object_entity_key varchar,
            relation_category varchar,
            participant_types varchar[],
            evidence_count bigint,
            sources varchar[],
            source_count bigint,
            relation_bucket bigint,
            relation_part bigint
        )
    ''')
    con.execute('''
        create table if not exists relation_source (
            relation_key varchar,
            source varchar,
            evidence_count bigint,
            payload_hash varchar,
            active boolean,
            relation_bucket bigint,
            relation_part bigint
        )
    ''')
    con.execute('''
        create table if not exists entity_relation_evidence (
            relation_evidence_id bigint,
            relation_id bigint,
            relation_key varchar,
            source varchar,
            raw_record_id varchar,
            record_attributes struct(term varchar, "value" varchar, unit varchar)[],
            subject_attributes struct(term varchar, "value" varchar, unit varchar)[],
            object_attributes struct(term varchar, "value" varchar, unit varchar)[],
            evidence struct(term varchar, "value" varchar, unit varchar)[],
            relation_bucket bigint,
            relation_part bigint
        )
    ''')


def _reset_state(con: duckdb.DuckDBPyConnection) -> None:
    for table in [
        'combined_run_scope_entity',
        'combined_run_scope_relation',
        'entity_key_map',
        'relation_key_map',
        'entity',
        'entity_source',
        'entity_evidence',
        'entity_relation',
        'relation_source',
        'entity_relation_evidence',
    ]:
        con.execute(f'delete from {table}')


def _state_is_empty(con: duckdb.DuckDBPyConnection) -> bool:
    return int(con.execute('select count(*) from entity').fetchone()[0]) == 0


# ---------------------------------------------------------------------------
# Part recomputation.
# ---------------------------------------------------------------------------


def _apply_entity_parts(
    con: duckdb.DuckDBPyConnection,
    source_dirs: list[GoldSourceDir],
    *,
    parts: set[int],
    cfg: CombinedRewriteConfig,
    full_build: bool,
) -> None:
    for index, part in enumerate(sorted(parts), start=1):
        update_phase(f'entity part {index}/{len(parts)} part={part:05d}')
        _log(f'entity part {index}/{len(parts)} part={part:05d}')
        con.execute('begin transaction')
        try:
            _recompute_entity_part(con, source_dirs, entity_part=part, cfg=cfg, full_build=full_build)
            _recompute_entity_evidence_part(con, source_dirs, entity_part=part, cfg=cfg, full_build=full_build)
            con.execute('commit')
        except Exception:
            con.execute('rollback')
            raise


def _apply_relation_parts(
    con: duckdb.DuckDBPyConnection,
    source_dirs: list[GoldSourceDir],
    *,
    parts: set[int],
    cfg: CombinedRewriteConfig,
    full_build: bool,
) -> None:
    for index, part in enumerate(sorted(parts), start=1):
        update_phase(f'relation part {index}/{len(parts)} part={part:05d}')
        _log(f'relation part {index}/{len(parts)} part={part:05d}')
        con.execute('begin transaction')
        try:
            _recompute_relation_part(con, source_dirs, relation_part=part, cfg=cfg, full_build=full_build)
            _recompute_relation_evidence_part(con, source_dirs, relation_part=part, cfg=cfg, full_build=full_build)
            con.execute('commit')
        except Exception:
            con.execute('rollback')
            raise


def _recompute_entity_part(
    con: duckdb.DuckDBPyConnection,
    source_dirs: list[GoldSourceDir],
    *,
    entity_part: int,
    cfg: CombinedRewriteConfig,
    full_build: bool,
) -> None:
    columns = [
        'entity_key',
        'canonical_identifier',
        'canonical_identifier_type',
        'identifiers',
        'entity_type',
        'taxonomy_id',
        'entity_attributes',
        'sources',
        'entity_bucket',
        'entity_part',
    ]
    con.execute('drop table if exists source_entities')
    con.execute(f"""
        create temp table source_entities as
        {_source_union_sql(
            con,
            source_dirs,
            dataset_name='entity',
            columns=columns,
            part_column='entity_part',
            part_value=entity_part,
            key_column='entity_key',
            key_table='affected_entity_keys',
            full_build=full_build,
            cfg=cfg,
        )}
    """)

    con.execute(f"""
        create or replace temp view merged_entities as
        with ident_rows as (
            select entity_key, item.identifier as identifier, item.identifier_type as identifier_type
            from source_entities, unnest(identifiers) as t(item)
            union all
            select entity_key, canonical_identifier, canonical_identifier_type
            from source_entities
            where canonical_identifier is not null and canonical_identifier_type is not null
        ),
        ident_lists as (
            select entity_key, list_sort(list_distinct(list(struct_pack(identifier := identifier, identifier_type := identifier_type)))) as identifiers
            from ident_rows
            group by entity_key
        ),
        attr_rows as (
            select entity_key, item.term as term, item.value as value, item.unit as unit
            from source_entities, unnest(entity_attributes) as t(item)
        ),
        attr_lists as (
            select entity_key, list_distinct(list(struct_pack(term := term, value := value, unit := unit))) as entity_attributes
            from attr_rows
            group by entity_key
        ),
        source_rows as (
            select entity_key, coalesce(item, _source) as source
            from source_entities, unnest(sources) as t(item)
            union all
            select entity_key, _source as source from source_entities
        ),
        source_lists as (
            select entity_key, list_sort(list_distinct(list(source))) as sources
            from source_rows
            where source is not null
            group by entity_key
        ),
        base as (
            select
                entity_key,
                first(canonical_identifier order by _source, canonical_identifier nulls last) as canonical_identifier,
                first(canonical_identifier_type order by _source, canonical_identifier_type nulls last) as canonical_identifier_type,
                first(entity_type order by _source, entity_type nulls last) as entity_type,
                first(taxonomy_id order by _source, taxonomy_id nulls last) as taxonomy_id,
                coalesce(min(entity_bucket), stable_bucket(entity_key, {cfg.bucket_count}))::bigint as entity_bucket,
                coalesce(min(entity_part), stable_part(entity_key, {cfg.bucket_count}, {cfg.part_count}))::bigint as entity_part
            from source_entities
            group by entity_key
        )
        select
            b.entity_key,
            b.canonical_identifier,
            b.canonical_identifier_type,
            coalesce(il.identifiers, []::struct(identifier varchar, identifier_type varchar)[]) as identifiers,
            b.entity_type,
            b.taxonomy_id,
            coalesce(al.entity_attributes, []::struct(term varchar, "value" varchar, unit varchar)[]) as entity_attributes,
            coalesce(sl.sources, []::varchar[]) as sources,
            coalesce(array_length(sl.sources), 0)::bigint as source_count,
            b.entity_bucket,
            b.entity_part
        from base b
        left join ident_lists il using(entity_key)
        left join attr_lists al using(entity_key)
        left join source_lists sl using(entity_key)
    """)

    max_id = int(con.execute('select coalesce(max(entity_id), 0) from entity_key_map').fetchone()[0])
    con.execute(f"""
        insert into entity_key_map(entity_key, entity_id, entity_bucket, entity_part)
        select entity_key, {max_id} + row_number() over(order by entity_key), entity_bucket, entity_part
        from merged_entities
        where entity_key not in (select entity_key from entity_key_map)
    """)
    if full_build:
        con.execute(f'delete from entity where entity_part = {entity_part}')
        con.execute(f'delete from entity_source where entity_part = {entity_part}')
    else:
        con.execute(f"""
            delete from entity
            where entity_key in (select entity_key from affected_entity_keys)
              and stable_part(entity_key, {cfg.bucket_count}, {cfg.part_count}) = {entity_part}
        """)
        con.execute(f"""
            delete from entity_source
            where entity_key in (select entity_key from affected_entity_keys)
              and stable_part(entity_key, {cfg.bucket_count}, {cfg.part_count}) = {entity_part}
        """)

    con.execute("""
        insert into entity
        select
            m.entity_id,
            e.entity_key,
            e.canonical_identifier,
            e.canonical_identifier_type,
            e.identifiers,
            e.entity_type,
            e.taxonomy_id,
            e.entity_attributes,
            e.sources,
            e.source_count,
            e.entity_bucket,
            e.entity_part
        from merged_entities e
        join entity_key_map m using(entity_key)
    """)
    con.execute(f"""
        insert into entity_source
        select
            entity_key,
            source,
            count(*)::bigint as evidence_count,
            sha256(entity_key || '|' || source) as payload_hash,
            true as active,
            min(coalesce(entity_bucket, stable_bucket(entity_key, {cfg.bucket_count})))::bigint as entity_bucket,
            min(coalesce(entity_part, stable_part(entity_key, {cfg.bucket_count}, {cfg.part_count})))::bigint as entity_part
        from (
            select entity_key, _source as source, entity_bucket, entity_part
            from source_entities
            where _source is not null
        )
        group by entity_key, source
    """)


def _recompute_entity_evidence_part(
    con: duckdb.DuckDBPyConnection,
    source_dirs: list[GoldSourceDir],
    *,
    entity_part: int,
    cfg: CombinedRewriteConfig,
    full_build: bool,
) -> None:
    columns = [
        'source',
        'entity_key',
        'raw_record_id',
        'entity_type',
        'taxonomy_id',
        'identifiers',
        'entity_attributes',
        'evidence',
        'entity_bucket',
        'entity_part',
    ]
    con.execute('drop table if exists source_entity_evidence')
    con.execute(f"""
        create temp table source_entity_evidence as
        {_source_union_sql(
            con,
            source_dirs,
            dataset_name='entity_evidence',
            columns=columns,
            part_column='entity_part',
            part_value=entity_part,
            key_column='entity_key',
            key_table='affected_entity_keys',
            full_build=full_build,
            cfg=cfg,
        )}
    """)
    if full_build:
        con.execute(f'delete from entity_evidence where entity_part = {entity_part}')
    else:
        con.execute(f"""
            delete from entity_evidence
            where entity_key in (select entity_key from affected_entity_keys)
              and stable_part(entity_key, {cfg.bucket_count}, {cfg.part_count}) = {entity_part}
        """)
    con.execute(f"""
        insert into entity_evidence
        select
            coalesce(source, _source) as source,
            entity_key,
            list_sort(list_distinct(list(raw_record_id) filter (where raw_record_id is not null and raw_record_id != ''))) as raw_record_ids,
            first(entity_type order by entity_type nulls last) as entity_type,
            first(taxonomy_id order by taxonomy_id nulls last) as taxonomy_id,
            list_distinct(flatten(list(identifiers))) as identifiers,
            list_distinct(flatten(list(entity_attributes))) as entity_attributes,
            coalesce(
                list_distinct(flatten(list(evidence) filter (where evidence is not null))),
                []::struct(term varchar, "value" varchar, unit varchar)[]
            ) as evidence,
            coalesce(min(entity_bucket), stable_bucket(entity_key, {cfg.bucket_count}))::bigint as entity_bucket,
            coalesce(min(entity_part), stable_part(entity_key, {cfg.bucket_count}, {cfg.part_count}))::bigint as entity_part
        from source_entity_evidence
        group by coalesce(source, _source), entity_key
    """)


def _recompute_relation_part(
    con: duckdb.DuckDBPyConnection,
    source_dirs: list[GoldSourceDir],
    *,
    relation_part: int,
    cfg: CombinedRewriteConfig,
    full_build: bool,
) -> None:
    columns = [
        'relation_key',
        'subject_entity_key',
        'predicate',
        'object_entity_key',
        'relation_category',
        'evidence_count',
        'sources',
        'relation_bucket',
        'relation_part',
    ]
    con.execute('drop table if exists source_relations')
    con.execute(f"""
        create temp table source_relations as
        {_source_union_sql(
            con,
            source_dirs,
            dataset_name='entity_relation',
            columns=columns,
            part_column='relation_part',
            part_value=relation_part,
            key_column='relation_key',
            key_table='affected_relation_keys',
            full_build=full_build,
            cfg=cfg,
        )}
    """)
    con.execute("""
        create or replace temp view mapped_source_relations as
        select
            r.relation_key,
            r._source,
            sm.entity_id as subject_entity_id,
            r.subject_entity_key,
            r.predicate,
            om.entity_id as object_entity_id,
            r.object_entity_key,
            r.relation_category,
            r.evidence_count,
            r.sources,
            r.relation_bucket,
            r.relation_part
        from source_relations r
        join entity_key_map sm on sm.entity_key = r.subject_entity_key
        join entity_key_map om on om.entity_key = r.object_entity_key
    """)
    con.execute(f"""
        create or replace temp view merged_relations as
        with source_rows as (
            select relation_key, coalesce(item, _source) as source
            from mapped_source_relations, unnest(sources) as t(item)
            union all
            select relation_key, _source as source from source_relations
        ),
        source_lists as (
            select relation_key, list_sort(list_distinct(list(source))) as sources
            from source_rows
            where source is not null
            group by relation_key
        ),
        participant_rows as (
            select relation_key, subject_entity_id as entity_id from mapped_source_relations
            union all
            select relation_key, object_entity_id as entity_id from mapped_source_relations
        ),
        participant_type_lists as (
            select p.relation_key, list_sort(list_distinct(list(e.entity_type) filter (where e.entity_type is not null))) as participant_types
            from participant_rows p
            join entity e on e.entity_id = p.entity_id
            group by p.relation_key
        ),
        base as (
            select
                relation_key,
                first(subject_entity_id order by subject_entity_id nulls last) as subject_entity_id,
                first(subject_entity_key order by subject_entity_key nulls last) as subject_entity_key,
                first(predicate order by predicate nulls last) as predicate,
                first(object_entity_id order by object_entity_id nulls last) as object_entity_id,
                first(object_entity_key order by object_entity_key nulls last) as object_entity_key,
                first(relation_category order by relation_category nulls last) as relation_category,
                sum(evidence_count)::bigint as evidence_count,
                coalesce(min(relation_bucket), stable_bucket(relation_key, {cfg.bucket_count}))::bigint as relation_bucket,
                coalesce(min(relation_part), stable_part(relation_key, {cfg.bucket_count}, {cfg.part_count}))::bigint as relation_part
            from mapped_source_relations
            group by relation_key
        )
        select
            b.relation_key,
            b.subject_entity_id,
            b.subject_entity_key,
            b.predicate,
            b.object_entity_id,
            b.object_entity_key,
            b.relation_category,
            coalesce(ptl.participant_types, []::varchar[]) as participant_types,
            b.evidence_count,
            coalesce(sl.sources, []::varchar[]) as sources,
            coalesce(array_length(sl.sources), 0)::bigint as source_count,
            b.relation_bucket,
            b.relation_part
        from base b
        left join participant_type_lists ptl using(relation_key)
        left join source_lists sl using(relation_key)
    """)
    max_id = int(con.execute('select coalesce(max(relation_id), 0) from relation_key_map').fetchone()[0])
    con.execute(f"""
        insert into relation_key_map(relation_key, relation_id, relation_bucket, relation_part)
        select relation_key, {max_id} + row_number() over(order by relation_key), relation_bucket, relation_part
        from merged_relations
        where relation_key not in (select relation_key from relation_key_map)
    """)
    if full_build:
        con.execute(f'delete from entity_relation where relation_part = {relation_part}')
        con.execute(f'delete from relation_source where relation_part = {relation_part}')
    else:
        con.execute(f"""
            delete from entity_relation
            where relation_key in (select relation_key from affected_relation_keys)
              and stable_part(relation_key, {cfg.bucket_count}, {cfg.part_count}) = {relation_part}
        """)
        con.execute(f"""
            delete from relation_source
            where relation_key in (select relation_key from affected_relation_keys)
              and stable_part(relation_key, {cfg.bucket_count}, {cfg.part_count}) = {relation_part}
        """)
    con.execute("""
        insert into entity_relation
        select
            m.relation_id,
            r.relation_key,
            r.subject_entity_id,
            r.subject_entity_key,
            r.predicate,
            r.object_entity_id,
            r.object_entity_key,
            r.relation_category,
            r.participant_types,
            r.evidence_count,
            r.sources,
            r.source_count,
            r.relation_bucket,
            r.relation_part
        from merged_relations r
        join relation_key_map m using(relation_key)
    """)
    con.execute("""
        insert into relation_source
        select
            relation_key,
            source,
            count(*)::bigint as evidence_count,
            sha256(relation_key || '|' || source) as payload_hash,
            true as active,
            min(relation_bucket)::bigint as relation_bucket,
            min(relation_part)::bigint as relation_part
        from (
            select relation_key, _source as source, relation_bucket, relation_part
            from source_relations
            where _source is not null
        )
        group by relation_key, source
    """)


def _recompute_relation_evidence_part(
    con: duckdb.DuckDBPyConnection,
    source_dirs: list[GoldSourceDir],
    *,
    relation_part: int,
    cfg: CombinedRewriteConfig,
    full_build: bool,
) -> None:
    columns = [
        'source',
        'relation_key',
        'raw_record_id',
        'record_attributes',
        'subject_attributes',
        'object_attributes',
        'evidence',
        'relation_bucket',
        'relation_part',
    ]
    con.execute('drop table if exists source_relation_evidence')
    con.execute(f"""
        create temp table source_relation_evidence as
        {_source_union_sql(
            con,
            source_dirs,
            dataset_name='entity_relation_evidence',
            columns=columns,
            part_column='relation_part',
            part_value=relation_part,
            key_column='relation_key',
            key_table='affected_relation_keys',
            full_build=full_build,
            cfg=cfg,
        )}
    """)
    if full_build:
        con.execute(f'delete from entity_relation_evidence where relation_part = {relation_part}')
    else:
        con.execute(f"""
            delete from entity_relation_evidence
            where relation_key in (select relation_key from affected_relation_keys)
              and stable_part(relation_key, {cfg.bucket_count}, {cfg.part_count}) = {relation_part}
        """)
    max_id = int(con.execute('select coalesce(max(relation_evidence_id), 0) from entity_relation_evidence').fetchone()[0])
    con.execute(f"""
        insert into entity_relation_evidence
        select
            {max_id} + row_number() over(order by coalesce(e.source, e._source), m.relation_id, e.raw_record_id) as relation_evidence_id,
            m.relation_id,
            e.relation_key,
            coalesce(e.source, e._source) as source,
            e.raw_record_id,
            e.record_attributes,
            e.subject_attributes,
            e.object_attributes,
            e.evidence,
            coalesce(e.relation_bucket, stable_bucket(e.relation_key, {cfg.bucket_count}))::bigint as relation_bucket,
            coalesce(e.relation_part, stable_part(e.relation_key, {cfg.bucket_count}, {cfg.part_count}))::bigint as relation_part
        from source_relation_evidence e
        join relation_key_map m using(relation_key)
    """)


# ---------------------------------------------------------------------------
# Export.
# ---------------------------------------------------------------------------


def _export_latest(
    con: duckdb.DuckDBPyConnection,
    version_dir: Path,
    *,
    cfg: CombinedRewriteConfig,
    entity_parts: set[int],
    relation_parts: set[int],
    full_build: bool,
) -> None:
    del cfg, entity_parts, relation_parts, full_build

    tmp_dir = version_dir.parent / f'.{version_dir.name}.tmp'
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir)
    tmp_dir.mkdir(parents=True)

    update_phase('export entity parquet')
    _copy_query(
        con,
        '''
            select
                entity_id,
                canonical_identifier,
                canonical_identifier_type,
                identifiers,
                entity_type,
                taxonomy_id,
                entity_attributes,
                sources,
                source_count
            from entity
            order by entity_key
        ''',
        tmp_dir / 'entity.parquet',
    )
    _copy_query(
        con,
        '''
            select
                m.entity_id,
                e.source,
                e.raw_record_ids,
                e.entity_type,
                e.taxonomy_id,
                e.identifiers,
                e.entity_attributes,
                e.evidence
            from entity_evidence e
            join entity_key_map m using(entity_key)
            order by m.entity_id, e.source
        ''',
        tmp_dir / 'entity_evidence.parquet',
    )

    update_phase('export relation parquet')
    _copy_query(
        con,
        '''
            select
                relation_id,
                subject_entity_id,
                predicate,
                object_entity_id,
                relation_category,
                participant_types,
                evidence_count,
                sources,
                source_count
            from entity_relation
            order by relation_key
        ''',
        tmp_dir / 'entity_relation.parquet',
    )
    _copy_query(
        con,
        '''
            select
                relation_evidence_id,
                relation_id,
                source,
                raw_record_id,
                record_attributes,
                subject_attributes,
                object_attributes,
                evidence
            from entity_relation_evidence
            order by relation_id, source, raw_record_id
        ''',
        tmp_dir / 'entity_relation_evidence.parquet',
    )

    _replace_directory(tmp_dir, version_dir)


def _export_relation_annotation(
    con: duckdb.DuckDBPyConnection,
    output_dir: Path,
    *,
    cfg: CombinedRewriteConfig,
    relation_parts: set[int],
    full_build: bool,
) -> dict[str, Any]:
    del cfg, relation_parts, full_build

    output_path = output_dir / 'relation_annotation_term.parquet'
    stale_partitioned_dir = output_dir / 'relation_annotation_term'
    if stale_partitioned_dir.exists():
        shutil.rmtree(stale_partitioned_dir)

    term_entity_type = _sql_literal(ONTOLOGY_ENTITY_TYPE_LABEL)
    term_identifier_type = _sql_literal(ONTOLOGY_IDENTIFIER_TYPE_LABEL)
    update_phase('relation annotation parquet')
    query = f"""
            with term_entities as (
                select entity_id::bigint as term_entity_id, canonical_identifier::varchar as term_id
                from entity
                where entity_type = '{term_entity_type}'
                  and canonical_identifier_type = '{term_identifier_type}'
            ),
            interaction_relation_evidence as (
                select
                    r.relation_id::bigint as relation_id,
                    r.subject_entity_id::bigint as subject_entity_id,
                    r.object_entity_id::bigint as object_entity_id,
                    e.relation_evidence_id::bigint as relation_evidence_id,
                    e.source::varchar as source,
                    e.record_attributes
                from entity_relation r
                join entity_relation_evidence e using(relation_id)
                where r.relation_category = 'interaction'
            ),
            interaction_terms as (
                select distinct
                    i.relation_id,
                    i.relation_evidence_id,
                    i.source,
                    'relation'::varchar as scope,
                    t.term_entity_id
                from interaction_relation_evidence i,
                    unnest(i.record_attributes) as attr(item)
                join term_entities t
                  on t.term_id = coalesce(
                        nullif(regexp_extract(attr.item.term, '^([^:]+:[^:]+)$|^([^:]+:[^:]+):', 1), ''),
                        nullif(regexp_extract(attr.item.term, '^([^:]+:[^:]+)$|^([^:]+:[^:]+):', 2), '')
                    )
                where attr.item.term is not null
                  and regexp_matches(attr.item.term, '^[^:]+:[^:]+')
                  and attr.item.value is null
                  and attr.item.unit is null
            ),
            annotation_relations as (
                select r.subject_entity_id::bigint as subject_entity_id, r.object_entity_id::bigint as term_entity_id
                from entity_relation r
                join term_entities t on t.term_entity_id = r.object_entity_id
                where r.relation_category = 'association'
            ),
            participant_candidates as (
                select relation_id, relation_evidence_id, source, subject_entity_id as annotated_entity_id
                from interaction_relation_evidence
                union all
                select relation_id, relation_evidence_id, source, object_entity_id as annotated_entity_id
                from interaction_relation_evidence
            ),
            participant_terms as (
                select distinct
                    c.relation_id,
                    c.relation_evidence_id,
                    c.source,
                    'participants'::varchar as scope,
                    a.term_entity_id
                from participant_candidates c
                join annotation_relations a on a.subject_entity_id = c.annotated_entity_id
                join term_entities t on t.term_entity_id = a.term_entity_id
            )
            select
                relation_id,
                min(relation_evidence_id)::bigint as relation_evidence_id,
                source,
                scope,
                term_entity_id
            from (
                select * from interaction_terms
                union
                select * from participant_terms
            )
            group by relation_id, source, scope, term_entity_id
    """
    _copy_query(con, query, output_path)
    total = int(
        con.execute(
            f"select count(*) from read_parquet('{_sql_path(output_path)}')"
        ).fetchone()[0]
    )

    summary = {
        'output_dir': str(output_dir),
        'relation_annotation_path': str(output_path),
        'row_count': total,
        'missing_inputs': [],
        'engine': 'duckdb',
    }
    return summary


def _replace_directory(tmp_dir: Path, target_dir: Path) -> None:
    if target_dir.exists():
        backup_dir = target_dir.parent / f'.{target_dir.name}.previous'
        if backup_dir.exists():
            shutil.rmtree(backup_dir)
        target_dir.replace(backup_dir)
        tmp_dir.replace(target_dir)
        shutil.rmtree(backup_dir)
    else:
        tmp_dir.replace(target_dir)


def _remove_stale_artifact_runs(output_dir: Path) -> None:
    stale_runs_dir = output_dir / 'runs'
    if stale_runs_dir.exists():
        shutil.rmtree(stale_runs_dir)


# ---------------------------------------------------------------------------
# Run artifacts.
# ---------------------------------------------------------------------------


def _write_run_artifacts(
    con: duckdb.DuckDBPyConnection,
    *,
    reports_dir: Path,
    latest_dir: Path,
    run_id: str,
    mode: str,
    changed_source: str | None,
    affected_entity_count: int,
    affected_relation_count: int,
) -> dict[str, Any]:
    run_path = reports_dir / 'runs' / f'{run_id}.json'
    run_path.parent.mkdir(parents=True, exist_ok=True)

    manifest = {
        'layer': 'combined',
        'run_id': run_id,
        'mode': mode,
        'created_at': datetime.now(UTC).isoformat(),
        'changed_source': changed_source,
        'latest_dir': str(latest_dir),
        'run_report': str(run_path),
        'affected_entity_count': int(con.execute('select count(*) from entity').fetchone()[0]) if mode == 'bootstrap' else affected_entity_count,
        'affected_relation_count': int(con.execute('select count(*) from entity_relation').fetchone()[0]) if mode == 'bootstrap' else affected_relation_count,
    }
    run_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    (reports_dir / 'latest.json').write_text(
        json.dumps({'run_id': run_id, 'path': str(run_path)}, indent=2, sort_keys=True) + '\n',
        encoding='utf-8',
    )
    return manifest


def _record_combined_run(
    con: duckdb.DuckDBPyConnection,
    *,
    run_id: str,
    mode: str,
    summary: dict[str, Any],
) -> None:
    now = datetime.now(UTC)
    con.execute(
        '''
        insert or replace into combined_run(
            combined_run_id,
            pipeline_run_id,
            started_at,
            finished_at,
            status,
            mode,
            manifest_json
        )
        values (?, null, ?, ?, 'success', ?, ?)
        ''',
        [run_id, now, now, mode, json.dumps(summary, sort_keys=True)],
    )
    con.execute('delete from combined_run_scope_entity where combined_run_id = ?', [run_id])
    con.execute('delete from combined_run_scope_relation where combined_run_id = ?', [run_id])
    if mode == 'bootstrap':
        con.execute(
            '''
            insert into combined_run_scope_entity
            select ?, null::varchar, entity_key, entity_id, entity_part, 'bootstrap'
            from entity
            ''',
            [run_id],
        )
        con.execute(
            '''
            insert into combined_run_scope_relation
            select ?, null::varchar, relation_key, relation_id, relation_part, 'bootstrap'
            from entity_relation
            ''',
            [run_id],
        )
    else:
        con.execute(
            '''
            insert into combined_run_scope_entity
            select
                ?,
                a.source,
                a.entity_key,
                m.entity_id,
                a.entity_part,
                'source_scope'
            from affected_entity_keys a
            left join entity_key_map m using(entity_key)
            ''',
            [run_id],
        )
        con.execute(
            '''
            insert into combined_run_scope_relation
            select
                ?,
                a.source,
                a.relation_key,
                m.relation_id,
                a.relation_part,
                'source_scope'
            from affected_relation_keys a
            left join relation_key_map m using(relation_key)
            ''',
            [run_id],
        )


# ---------------------------------------------------------------------------
# Source discovery and SQL helpers.
# ---------------------------------------------------------------------------


def _discover_gold_source_dirs(gold_root: Path) -> list[GoldSourceDir]:
    if not gold_root.exists():
        raise FileNotFoundError(f'Gold root does not exist: {gold_root}')
    sources: list[GoldSourceDir] = []
    for source_dir in sorted(gold_root.iterdir()):
        if not source_dir.is_dir():
            continue
        datasets = _source_datasets(source_dir)
        if datasets.entity is not None:
            sources.append(GoldSourceDir(source=source_dir.name, path=source_dir))
    return sources


def _source_dirs_from_state_paths(paths: dict[str, str | Path]) -> list[GoldSourceDir]:
    sources: list[GoldSourceDir] = []
    for source, path_value in sorted(paths.items()):
        path = Path(path_value)
        if not path.exists():
            raise FileNotFoundError(f'Source state database does not exist: {path}')
        sources.append(GoldSourceDir(source=source, path=path))
    return sources


def _source_datasets(source_dir: Path) -> SourceDatasets:
    return SourceDatasets(
        entity=_first_existing(source_dir / 'entities' / 'entity', source_dir / 'entities' / 'entity.parquet'),
        entity_evidence=_first_existing(source_dir / 'entities' / 'entity_evidence', source_dir / 'entities' / 'entity_evidence.parquet'),
        entity_relation=_first_existing(source_dir / 'relations' / 'entity_relation', source_dir / 'relations' / 'entity_relation.parquet'),
        entity_relation_evidence=_first_existing(source_dir / 'relations' / 'entity_relation_evidence', source_dir / 'relations' / 'entity_relation_evidence.parquet'),
    )


def _source_union_sql(
    con: duckdb.DuckDBPyConnection,
    source_dirs: list[GoldSourceDir],
    *,
    dataset_name: str,
    columns: list[str],
    part_column: str,
    part_value: int,
    key_column: str,
    key_table: str,
    full_build: bool,
    cfg: CombinedRewriteConfig,
) -> str:
    selects: list[str] = []
    for source_dir in source_dirs:
        source_sql: str | None
        if _is_source_state_path(source_dir.path):
            source_sql = _source_state_table_sql(con, source_dir)
            source_sql = f'{source_sql}.gold_{dataset_name}'
            existing_columns = _relation_columns(con, source_sql)
            if not existing_columns:
                continue
        else:
            datasets = _source_datasets(source_dir.path)
            path = getattr(datasets, dataset_name)
            if path is None:
                continue
            source_sql = _read_dataset_sql(path)
            existing_columns = _relation_columns(con, source_sql)
        selected_columns = ', '.join(_column_expr(column, existing_columns, key_column, cfg) for column in columns)
        part_filter = (
            f'stable_part(try_cast({key_column} as varchar), '
            f'{cfg.bucket_count}, {cfg.part_count}) = {part_value}'
        )
        filters = [part_filter, f'{key_column} is not null']
        if not full_build:
            filters.append(f'try_cast({key_column} as varchar) in (select {key_column} from {key_table})')
        selects.append(
            "select "
            f"'{_sql_literal(source_dir.source)}' as _source, "
            f'{selected_columns} '
            f'from {source_sql} '
            f"where {' and '.join(filters)}"
        )
    if selects:
        return '\nunion all\n'.join(selects)
    return _empty_source_sql(columns)


def _is_source_state_path(path: Path) -> bool:
    return path.is_file() and path.suffix == '.duckdb'


def _source_state_table_sql(
    con: duckdb.DuckDBPyConnection,
    source_dir: GoldSourceDir,
) -> str:
    alias = _source_state_alias(source_dir.source)
    if not _attached_database_exists(con, alias):
        con.execute(
            f"attach '{_sql_path(source_dir.path)}' as {_quote_identifier(alias)} (read_only)"
        )
    return _quote_identifier(alias)


def _source_state_alias(source: str) -> str:
    cleaned = ''.join(ch if ch.isalnum() else '_' for ch in source.lower())
    return f'source_state_{cleaned}'


def _attached_database_exists(con: duckdb.DuckDBPyConnection, alias: str) -> bool:
    rows = con.execute('select database_name from duckdb_databases()').fetchall()
    return alias in {str(row[0]) for row in rows}


def _column_expr(column: str, existing_columns: set[str], key_column: str, cfg: CombinedRewriteConfig) -> str:
    casts = _column_casts()
    if column.endswith('_bucket'):
        return f'stable_bucket(try_cast({key_column} as varchar), {cfg.bucket_count})::bigint as {column}'
    if column.endswith('_part'):
        return f'stable_part(try_cast({key_column} as varchar), {cfg.bucket_count}, {cfg.part_count})::bigint as {column}'
    if column in existing_columns:
        return f'try_cast({column} as {casts[column]}) as {column}'
    return _empty_column_expr(column)


def _empty_source_sql(columns: list[str]) -> str:
    selected = ', '.join(_empty_column_expr(column) for column in columns)
    return f'select null::varchar as _source, {selected} where false'


def _empty_column_expr(column: str) -> str:
    typed = {
        'entity_key': 'null::varchar as entity_key',
        'canonical_identifier': 'null::varchar as canonical_identifier',
        'canonical_identifier_type': 'null::varchar as canonical_identifier_type',
        'identifiers': '[]::struct(identifier varchar, identifier_type varchar)[] as identifiers',
        'entity_type': 'null::varchar as entity_type',
        'taxonomy_id': 'null::varchar as taxonomy_id',
        'entity_attributes': '[]::struct(term varchar, "value" varchar, unit varchar)[] as entity_attributes',
        'sources': '[]::varchar[] as sources',
        'entity_bucket': 'null::bigint as entity_bucket',
        'entity_part': 'null::bigint as entity_part',
        'source': 'null::varchar as source',
        'raw_record_id': 'null::varchar as raw_record_id',
        'raw_record_ids': '[]::varchar[] as raw_record_ids',
        'relation_key': 'null::varchar as relation_key',
        'subject_entity_key': 'null::varchar as subject_entity_key',
        'predicate': 'null::varchar as predicate',
        'object_entity_key': 'null::varchar as object_entity_key',
        'relation_category': 'null::varchar as relation_category',
        'evidence_count': 'null::bigint as evidence_count',
        'relation_bucket': 'null::bigint as relation_bucket',
        'relation_part': 'null::bigint as relation_part',
        'record_attributes': '[]::struct(term varchar, "value" varchar, unit varchar)[] as record_attributes',
        'subject_attributes': '[]::struct(term varchar, "value" varchar, unit varchar)[] as subject_attributes',
        'object_attributes': '[]::struct(term varchar, "value" varchar, unit varchar)[] as object_attributes',
        'evidence': '[]::struct(term varchar, "value" varchar, unit varchar)[] as evidence',
    }
    return typed[column]


def _column_casts() -> dict[str, str]:
    return {
        'entity_key': 'varchar',
        'canonical_identifier': 'varchar',
        'canonical_identifier_type': 'varchar',
        'identifiers': 'struct(identifier varchar, identifier_type varchar)[]',
        'entity_type': 'varchar',
        'taxonomy_id': 'varchar',
        'entity_attributes': 'struct(term varchar, "value" varchar, unit varchar)[]',
        'sources': 'varchar[]',
        'entity_bucket': 'bigint',
        'entity_part': 'bigint',
        'source': 'varchar',
        'raw_record_id': 'varchar',
        'raw_record_ids': 'varchar[]',
        'relation_key': 'varchar',
        'subject_entity_key': 'varchar',
        'predicate': 'varchar',
        'object_entity_key': 'varchar',
        'relation_category': 'varchar',
        'evidence_count': 'bigint',
        'relation_bucket': 'bigint',
        'relation_part': 'bigint',
        'record_attributes': 'struct(term varchar, "value" varchar, unit varchar)[]',
        'subject_attributes': 'struct(term varchar, "value" varchar, unit varchar)[]',
        'object_attributes': 'struct(term varchar, "value" varchar, unit varchar)[]',
        'evidence': 'struct(term varchar, "value" varchar, unit varchar)[]',
    }


def _read_dataset_sql(path: Path) -> str:
    if path.is_dir():
        return f"read_parquet('{_sql_path(path / '**' / '*.parquet')}', union_by_name=true, hive_partitioning=true)"
    return f"read_parquet('{_sql_path(path)}', union_by_name=true)"


def _relation_columns(con: duckdb.DuckDBPyConnection, relation_sql: str) -> set[str]:
    try:
        rows = con.execute(f'describe select * from {relation_sql} limit 0').fetchall()
        return {row[0] for row in rows}
    except Exception:
        return set()


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _first_existing(*paths: Path) -> Path | None:
    for path in paths:
        if path.exists():
            return path
    return None


# ---------------------------------------------------------------------------
# Incremental helpers.
# ---------------------------------------------------------------------------


def _create_affected_key_tables(
    con: duckdb.DuckDBPyConnection,
    *,
    entity_paths: list[str | Path],
    relation_paths: list[str | Path],
    cfg: CombinedRewriteConfig,
) -> None:
    entity_sql = _read_affected_paths_sql(entity_paths, 'entity_key')
    relation_sql = _read_affected_paths_sql(relation_paths, 'relation_key')
    con.execute('drop table if exists affected_entity_keys')
    con.execute(f'''
        create temp table affected_entity_keys as
        select distinct
            null::varchar as source,
            entity_key,
            stable_part(entity_key, {cfg.bucket_count}, {cfg.part_count})::bigint as entity_part
        from ({entity_sql})
        where entity_key is not null
          and entity_key <> ''
    ''')
    con.execute('drop table if exists affected_relation_keys')
    con.execute(f'''
        create temp table affected_relation_keys as
        select distinct
            null::varchar as source,
            relation_key,
            stable_part(relation_key, {cfg.bucket_count}, {cfg.part_count})::bigint as relation_part
        from ({relation_sql})
        where relation_key is not null
          and relation_key <> ''
    ''')


def _create_affected_key_tables_from_source_scopes(
    con: duckdb.DuckDBPyConnection,
    *,
    source_dirs: list[GoldSourceDir],
    cfg: CombinedRewriteConfig,
) -> None:
    entity_selects: list[str] = []
    relation_selects: list[str] = []
    for source_dir in source_dirs:
        if not _is_source_state_path(source_dir.path):
            continue
        schema = _source_state_table_sql(con, source_dir)
        entity_scope = f'{schema}.source_run_scope_entity'
        relation_scope = f'{schema}.source_run_scope_relation'
        if _relation_columns(con, entity_scope):
            entity_selects.append(
                f"select '{_sql_path(source_dir.source)}'::varchar as source, "
                f'entity_key::varchar as entity_key from {entity_scope}'
            )
        if _relation_columns(con, relation_scope):
            relation_selects.append(
                f"select '{_sql_path(source_dir.source)}'::varchar as source, "
                f'relation_key::varchar as relation_key from {relation_scope}'
            )

    entity_sql = (
        '\nunion all\n'.join(entity_selects)
        if entity_selects
        else 'select null::varchar as source, null::varchar as entity_key where false'
    )
    relation_sql = (
        '\nunion all\n'.join(relation_selects)
        if relation_selects
        else 'select null::varchar as source, null::varchar as relation_key where false'
    )
    con.execute('drop table if exists affected_entity_keys')
    con.execute(f'''
        create temp table affected_entity_keys as
        select distinct
            source,
            entity_key,
            stable_part(entity_key, {cfg.bucket_count}, {cfg.part_count})::bigint as entity_part
        from ({entity_sql})
        where entity_key is not null
          and entity_key <> ''
    ''')
    con.execute('drop table if exists affected_relation_keys')
    con.execute(f'''
        create temp table affected_relation_keys as
        select distinct
            source,
            relation_key,
            stable_part(relation_key, {cfg.bucket_count}, {cfg.part_count})::bigint as relation_part
        from ({relation_sql})
        where relation_key is not null
          and relation_key <> ''
    ''')


def _clear_consumed_source_scopes(source_dirs: list[GoldSourceDir]) -> None:
    """Clear source-local scopes after a successful combined update consumes them."""
    scope_tables = (
        'source_run_scope_raw_record',
        'source_run_scope_occurrence',
        'source_run_scope_entity',
        'source_run_scope_relation',
    )
    for source_dir in source_dirs:
        if not _is_source_state_path(source_dir.path):
            continue
        source_con = duckdb.connect(str(source_dir.path))
        try:
            for table in scope_tables:
                exists = source_con.execute(
                    """
                    select count(*)
                    from information_schema.tables
                    where table_schema = 'main'
                      and table_name = ?
                    """,
                    [table],
                ).fetchone()[0]
                if exists:
                    source_con.execute(f'delete from {_quote_identifier(table)}')
        finally:
            source_con.close()


def _expand_relation_keys_for_affected_entities(
    con: duckdb.DuckDBPyConnection,
    *,
    cfg: CombinedRewriteConfig,
) -> None:
    entity_count = _table_count(con, 'affected_entity_keys')
    if entity_count == 0:
        return
    before_count = _table_count(con, 'affected_relation_keys')
    con.execute(f'''
        insert into affected_relation_keys(source, relation_key, relation_part)
        select distinct
            null::varchar as source,
            relation_key,
            stable_part(relation_key, {cfg.bucket_count}, {cfg.part_count})::bigint as relation_part
        from entity_relation
        where relation_key is not null
          and (
            subject_entity_key in (select entity_key from affected_entity_keys)
            or object_entity_key in (select entity_key from affected_entity_keys)
          )
    ''')
    con.execute('''
        create or replace temp table affected_relation_keys as
        select distinct source, relation_key, relation_part
        from affected_relation_keys
        where relation_key is not null
    ''')
    after_count = _table_count(con, 'affected_relation_keys')
    added_count = after_count - before_count
    if added_count > 0:
        _log(
            'incremental: expanded entity keys to relation keys '
            f'entities={entity_count} added_relations={added_count}'
        )


def _parts_for_table(con: duckdb.DuckDBPyConnection, table: str, column: str) -> set[int]:
    rows = con.execute(f'''
        select distinct try_cast({column} as bigint)
        from {table}
        where {column} is not null
        order by 1
    ''').fetchall()
    return {int(row[0]) for row in rows if row[0] is not None}


def _table_count(con: duckdb.DuckDBPyConnection, table: str) -> int:
    return int(con.execute(f'select count(*) from {table}').fetchone()[0])


def _read_affected_paths_sql(paths: list[str | Path], column: str) -> str:
    existing = [Path(path) for path in paths if Path(path).exists()]
    if not existing:
        return f'select null::varchar as {column} where false'
    values = ', '.join("'" + _sql_path(path).replace("'", "''") + "'" for path in existing)
    return f'''
        select try_cast({column} as varchar) as {column}
        from read_parquet([{values}], union_by_name=true)
    '''


# ---------------------------------------------------------------------------
# Misc.
# ---------------------------------------------------------------------------


def _row_counts(con: duckdb.DuckDBPyConnection) -> dict[str, int]:
    tables = {
        'entity.parquet': 'entity',
        'entity_relation.parquet': 'entity_relation',
        'entity_relation_evidence.parquet': 'entity_relation_evidence',
        'entity_evidence.parquet': 'entity_evidence',
    }
    return {file_name: int(con.execute(f'select count(*) from {table}').fetchone()[0]) for file_name, table in tables.items()}


def _configure_duckdb(
    con: duckdb.DuckDBPyConnection,
    output_dir: Path,
    cfg: CombinedRewriteConfig,
) -> None:
    temp_dir = output_dir / '.duckdb_tmp'
    temp_dir.mkdir(parents=True, exist_ok=True)
    con.execute('set preserve_insertion_order = false')
    con.execute(f"set temp_directory = '{_sql_path(temp_dir)}'")
    if cfg.duckdb_memory_limit:
        con.execute(f"set memory_limit = '{cfg.duckdb_memory_limit}'")
    if cfg.duckdb_max_temp_directory_size:
        con.execute(f"set max_temp_directory_size = '{cfg.duckdb_max_temp_directory_size}'")
    if cfg.duckdb_threads:
        con.execute(f"set threads = {cfg.duckdb_threads}")


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


def _copy_query(
    con: duckdb.DuckDBPyConnection,
    query: str,
    output_path: Path,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    con.execute(
        f"copy ({query}) to '{_sql_path(output_path)}' "
        '(format parquet, compression zstd)'
    )


def _parquet_size_bytes(path: str | Path) -> int:
    path = Path(path)
    if not path.exists():
        return 0
    if path.is_file():
        return path.stat().st_size if path.suffix == '.parquet' else 0
    return sum(file.stat().st_size for file in path.rglob('*.parquet') if file.is_file())


def _append_build_manifest(
    output_dir: Path,
    *,
    mode: str,
    freeze_monthly: bool,
    row_counts: dict[str, int],
    cfg: CombinedRewriteConfig,
    affected_entities: int = 0,
    affected_relations: int = 0,
    changed_source: str | None = None,
) -> None:
    manifest_path = output_dir / 'build_manifest.jsonl'
    entry = {
        'timestamp': datetime.now(UTC).isoformat().replace('+00:00', 'Z'),
        'mode': mode,
        'freeze_monthly': freeze_monthly,
        'changed_source': changed_source,
        'affected_entities': affected_entities,
        'affected_relations': affected_relations,
        'row_counts': row_counts,
        'engine': 'duckdb',
        'bucket_algorithm': 'stable_u64_sha256_mod_v1',
        'bucket_count': cfg.bucket_count,
        'part_count': cfg.part_count,
        'entity_bucket_count': cfg.bucket_count,
        'entity_part_count': cfg.part_count,
        'relation_bucket_count': cfg.bucket_count,
        'relation_part_count': cfg.part_count,
    }
    with manifest_path.open('a', encoding='utf-8') as handle:
        handle.write(json.dumps(entry, sort_keys=True) + '\n')


def _freeze_monthly_snapshot(output_dir: Path, source_dir: Path) -> Path:
    snapshot_name = datetime.now(UTC).strftime('%Y-%m')
    snapshot_dir = output_dir / snapshot_name
    if snapshot_dir.exists():
        shutil.rmtree(snapshot_dir)
    shutil.copytree(source_dir, snapshot_dir)
    return snapshot_dir


def _new_run_id() -> str:
    return datetime.now(UTC).strftime('%Y%m%dT%H%M%S%fZ')


def _sql_literal(value: str) -> str:
    return value.replace("'", "''")


def _sql_path(path: str | Path) -> str:
    return str(path).replace("'", "''")


def _log(message: str) -> None:
    print(f'[combine:duckdb] {message}', flush=True)
