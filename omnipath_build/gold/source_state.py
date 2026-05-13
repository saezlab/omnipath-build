from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb

from omnipath_build.gold.build_entities import (
    GoldPartitionConfig,
    _configure_duckdb,
    _copy_part_query,
    _register_hash_functions,
    _sql_path,
)

GOLD_SOURCE_STATE_FILE = 'state.duckdb'
GOLD_SOURCE_STATE_MANIFEST = 'state_manifest.json'


def gold_source_state_path(output_dir: Path) -> Path:
    return output_dir / GOLD_SOURCE_STATE_FILE


def gold_source_state_ready(output_dir: Path) -> bool:
    return gold_source_state_path(output_dir).exists()


def initialize_gold_source_state(
    *,
    source: str,
    output_dir: Path,
) -> dict[str, Any]:
    """Create a source-local DuckDB state DB from freshly built gold outputs."""
    cfg = _partition_config_from_entity_manifest(output_dir / 'entities' / 'manifest.json')
    state_path = gold_source_state_path(output_dir)
    if state_path.exists():
        state_path.unlink()

    con = duckdb.connect(str(state_path))
    try:
        _configure_duckdb(con, output_dir, cfg)
        _register_hash_functions(con)
        _create_state_from_outputs(con, output_dir)
        row_counts = _state_row_counts(con)
    finally:
        con.close()

    manifest = _write_state_manifest(
        output_dir=output_dir,
        source=source,
        mode='bootstrap',
        cfg=cfg,
        row_counts=row_counts,
    )
    return manifest


def stage_gold_source_state(output_dir: Path, staged_dir: Path) -> Path:
    source = gold_source_state_path(output_dir)
    if not source.exists():
        raise FileNotFoundError(f'missing gold source state: {source}')
    target = gold_source_state_path(staged_dir)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    return target


def merge_gold_source_state(
    *,
    source: str,
    output_dir: Path,
    staged_dir: Path,
    changed_entities_dir: Path,
    changed_relations_dir: Path,
    raw_record_ids_path: Path,
    raw_record_id_count: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Merge changed gold evidence into staged source state and export outputs."""
    if raw_record_id_count <= 0:
        raise ValueError('raw_record_id_count must be positive for incremental gold merge')

    cfg = _partition_config_from_state_manifest(output_dir / GOLD_SOURCE_STATE_MANIFEST)
    state_path = stage_gold_source_state(output_dir, staged_dir)
    con = duckdb.connect(str(state_path))
    try:
        _configure_duckdb(con, staged_dir, cfg)
        _register_hash_functions(con)
        con.execute('begin transaction')
        try:
            _create_raw_id_table(con, raw_record_ids_path)
            entity_counts = _merge_entity_state(
                con,
                changed_entities_dir=changed_entities_dir,
                cfg=cfg,
            )
            relation_counts = _merge_relation_state(
                con,
                changed_relations_dir=changed_relations_dir,
                cfg=cfg,
            )
            export_counts = _export_state_outputs(
                con,
                staged_dir=staged_dir,
                cfg=cfg,
            )
            con.execute('commit')
        except Exception:
            con.execute('rollback')
            raise
    finally:
        con.close()

    for extra in ('canonicalization_report.md', 'canonicalization_summary.json'):
        source_extra = output_dir / 'entities' / extra
        if source_extra.exists():
            shutil.copy2(source_extra, staged_dir / 'entities' / extra)

    row_counts = _state_row_counts_for_manifest(state_path, staged_dir, cfg)
    _write_state_manifest(
        output_dir=staged_dir,
        source=source,
        mode='incremental',
        cfg=cfg,
        row_counts=row_counts,
    )
    _write_entity_manifest(
        output_dir=staged_dir / 'entities',
        source=source,
        cfg=cfg,
        row_counts={
            'entity': export_counts['entity'],
            'entity_evidence': export_counts['entity_evidence'],
            'entity_occurrence_map': export_counts['entity_occurrence_map'],
            'entity_map': export_counts['entity_map'],
        },
        summary={
            'incremental': True,
            'changed_raw_record_count': raw_record_id_count,
            'entity_count': row_counts['entity'],
        },
    )
    _write_relation_manifest(
        output_dir=staged_dir / 'relations',
        source=source,
        cfg=cfg,
        row_counts={
            'entity_relation': export_counts['entity_relation'],
            'entity_relation_evidence': export_counts['entity_relation_evidence'],
        },
    )

    entity_summary = {
        'incremental': True,
        'changed_raw_record_count': raw_record_id_count,
        'entity_count': row_counts['entity'],
        'entity_evidence_count': row_counts['entity_evidence'],
        'affected_entity_count': entity_counts['affected_entity_count'],
    }
    relation_summary = {
        'incremental': True,
        'changed_raw_record_count': raw_record_id_count,
        'relation_count': row_counts['entity_relation'],
        'relation_evidence_count': row_counts['entity_relation_evidence'],
        'affected_relation_count': relation_counts['affected_relation_count'],
    }
    return entity_summary, relation_summary


def publish_staged_gold_state(*, staged_dir: Path, output_dir: Path) -> None:
    staged_state = gold_source_state_path(staged_dir)
    if staged_state.exists():
        shutil.copy2(staged_state, gold_source_state_path(output_dir))
    staged_manifest = staged_dir / GOLD_SOURCE_STATE_MANIFEST
    if staged_manifest.exists():
        shutil.copy2(staged_manifest, output_dir / GOLD_SOURCE_STATE_MANIFEST)


def _create_state_from_outputs(con: duckdb.DuckDBPyConnection, output_dir: Path) -> None:
    tables = {
        'entity': output_dir / 'entities' / 'entity',
        'entity_evidence': output_dir / 'entities' / 'entity_evidence',
        'entity_occurrence_map': output_dir / 'entities' / 'entity_occurrence_map',
        'entity_map': output_dir / 'entities' / 'entity_map',
        'entity_relation': output_dir / 'relations' / 'entity_relation',
        'entity_relation_evidence': output_dir / 'relations' / 'entity_relation_evidence',
    }
    for table, path in tables.items():
        con.execute(f'drop table if exists {table}')
        con.execute(f'create table {table} as select * from {_read_dataset_sql(path)}')

    con.execute('drop table if exists entity_key_registry')
    con.execute('''
        create table entity_key_registry as
        select distinct
            try_cast(entity_key as varchar) as entity_key,
            try_cast(entity_pk as bigint) as entity_pk,
            try_cast(entity_bucket as bigint) as entity_bucket,
            try_cast(entity_part as bigint) as entity_part
        from entity
        where entity_key is not null
    ''')
    con.execute('drop table if exists relation_key_registry')
    con.execute('''
        create table relation_key_registry as
        select distinct
            try_cast(relation_key as varchar) as relation_key,
            try_cast(relation_pk as bigint) as relation_pk,
            try_cast(relation_bucket as bigint) as relation_bucket,
            try_cast(relation_part as bigint) as relation_part
        from entity_relation
        where relation_key is not null
    ''')
    _create_state_indexes(con)


def _merge_entity_state(
    con: duckdb.DuckDBPyConnection,
    *,
    changed_entities_dir: Path,
    cfg: GoldPartitionConfig,
) -> dict[str, int]:
    changed_evidence_sql = _read_dataset_sql(changed_entities_dir / 'entity_evidence')

    con.execute('drop table if exists changed_entity_evidence_input')
    con.execute(f'''
        create temp table changed_entity_evidence_input as
        select *
        from {changed_evidence_sql}
        where raw_record_id is not null
    ''')
    con.execute('drop table if exists changed_input_fingerprints')
    con.execute('''
        create temp table changed_input_fingerprints as
        select distinct try_cast(fingerprint as varchar) as fingerprint
        from changed_entity_evidence_input
        where fingerprint is not null
    ''')
    con.execute('drop table if exists affected_entity_keys')
    con.execute('''
        create temp table affected_entity_keys as
        select distinct e.entity_key
        from entity_evidence e
        join changed_raw_record_ids r on r.raw_record_id = try_cast(e.raw_record_id as varchar)
        where e.entity_key is not null
    ''')
    con.execute('drop table if exists affected_occurrence_ids')
    con.execute('''
        create temp table affected_occurrence_ids as
        select distinct try_cast(e.occurrence_id as varchar) as occurrence_id
        from entity_evidence e
        join changed_raw_record_ids r on r.raw_record_id = try_cast(e.raw_record_id as varchar)
        where e.occurrence_id is not null
    ''')
    con.execute('drop table if exists affected_fingerprints')
    con.execute('''
        create temp table affected_fingerprints as
        select distinct try_cast(e.fingerprint as varchar) as fingerprint
        from entity_evidence e
        join changed_raw_record_ids r on r.raw_record_id = try_cast(e.raw_record_id as varchar)
        where e.fingerprint is not null
        union
        select fingerprint
        from changed_input_fingerprints
    ''')
    con.execute('drop table if exists previous_fingerprint_identity')
    con.execute('''
        create temp table previous_fingerprint_identity as
        select
            try_cast(fingerprint as varchar) as fingerprint,
            first(entity_key order by entity_key nulls last) as entity_key,
            first(canonical_identifier order by canonical_identifier nulls last) as canonical_identifier,
            first(canonical_identifier_type order by canonical_identifier_type nulls last) as canonical_identifier_type
        from entity_evidence
        where try_cast(fingerprint as varchar) in (
            select fingerprint from changed_input_fingerprints
        )
        group by try_cast(fingerprint as varchar)
    ''')
    con.execute('drop table if exists changed_entity_key_remap')
    con.execute('''
        create temp table changed_entity_key_remap as
        select distinct
            c.entity_key as old_entity_key,
            coalesce(p.entity_key, c.entity_key) as new_entity_key
        from changed_entity_evidence_input c
        left join previous_fingerprint_identity p
          on p.fingerprint = try_cast(c.fingerprint as varchar)
        where c.entity_key is not null
          and coalesce(p.entity_key, c.entity_key) is not null
    ''')
    con.execute('drop table if exists changed_entity_evidence_stable')
    con.execute(f'''
        create temp table changed_entity_evidence_stable as
        with stable as (
            select
                c.source,
                coalesce(p.entity_key, c.entity_key) as entity_key,
                coalesce(p.canonical_identifier, c.canonical_identifier) as canonical_identifier,
                coalesce(p.canonical_identifier_type, c.canonical_identifier_type) as canonical_identifier_type,
                try_cast(c.raw_record_id as varchar) as raw_record_id,
                try_cast(c.occurrence_id as varchar) as occurrence_id,
                try_cast(c.fingerprint as varchar) as fingerprint,
                c.entity_type,
                c.taxonomy_id,
                c.identifiers,
                c.entity_attributes,
                c.evidence
            from changed_entity_evidence_input c
            left join previous_fingerprint_identity p
              on p.fingerprint = try_cast(c.fingerprint as varchar)
        )
        select
            source,
            entity_key,
            canonical_identifier,
            canonical_identifier_type,
            raw_record_id,
            occurrence_id,
            fingerprint,
            entity_type,
            taxonomy_id,
            identifiers,
            entity_attributes,
            evidence,
            stable_bucket(entity_key, {cfg.bucket_count})::bigint as entity_bucket,
            stable_part(entity_key, {cfg.bucket_count}, {cfg.part_count})::bigint as entity_part,
            stable_bucket(occurrence_id, {cfg.bucket_count})::bigint as occ_bucket,
            stable_part(occurrence_id, {cfg.bucket_count}, {cfg.part_count})::bigint as occ_part
        from stable
        where entity_key is not null
    ''')
    con.execute('''
        insert into affected_entity_keys
        select distinct entity_key
        from changed_entity_evidence_stable
        where entity_key is not null
    ''')
    con.execute('''
        insert into affected_occurrence_ids
        select distinct occurrence_id
        from changed_entity_evidence_stable
        where occurrence_id is not null
    ''')
    con.execute('''
        insert into affected_fingerprints
        select distinct fingerprint
        from changed_entity_evidence_stable
        where fingerprint is not null
    ''')
    _deduplicate_temp_table(con, 'affected_entity_keys', 'entity_key')
    _deduplicate_temp_table(con, 'affected_occurrence_ids', 'occurrence_id')
    _deduplicate_temp_table(con, 'affected_fingerprints', 'fingerprint')

    max_pk = int(con.execute('select coalesce(max(entity_pk), 0) from entity_key_registry').fetchone()[0])
    con.execute('drop table if exists new_entity_keys')
    con.execute('''
        create temp table new_entity_keys as
        select distinct entity_key, entity_bucket, entity_part
        from changed_entity_evidence_stable
        where entity_key not in (select entity_key from entity_key_registry)
    ''')
    con.execute(f'''
        insert into entity_key_registry(entity_key, entity_pk, entity_bucket, entity_part)
        select
            entity_key,
            {max_pk} + row_number() over(order by entity_key) as entity_pk,
            entity_bucket,
            entity_part
        from new_entity_keys
    ''')
    con.execute('''
        delete from entity_evidence
        where try_cast(raw_record_id as varchar) in (select raw_record_id from changed_raw_record_ids)
    ''')
    con.execute('''
        insert into entity_evidence
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
            e.evidence,
            e.entity_bucket,
            e.entity_part,
            e.occ_bucket,
            e.occ_part
        from changed_entity_evidence_stable e
        join entity_key_registry r using(entity_key)
    ''')
    con.execute('delete from entity where entity_key in (select entity_key from affected_entity_keys)')
    con.execute('''
        insert into entity
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
        from entity_evidence e
        join entity_key_registry r using(entity_key)
        where e.entity_key in (select entity_key from affected_entity_keys)
        group by r.entity_pk, e.entity_key, r.entity_bucket, r.entity_part
    ''')
    con.execute('delete from entity_occurrence_map where occurrence_id in (select occurrence_id from affected_occurrence_ids)')
    con.execute('''
        insert into entity_occurrence_map
        select distinct
            e.occurrence_id,
            e.fingerprint as _fingerprint,
            e.entity_pk,
            e.entity_key,
            e.occ_bucket,
            e.occ_part
        from entity_evidence e
        where e.occurrence_id in (select occurrence_id from affected_occurrence_ids)
          and e.occurrence_id is not null
    ''')
    con.execute('delete from entity_map where _fingerprint in (select fingerprint from affected_fingerprints)')
    con.execute(f'''
        insert into entity_map
        select distinct
            e.fingerprint as _fingerprint,
            e.entity_pk,
            e.entity_key,
            stable_bucket(e.fingerprint, {cfg.bucket_count})::bigint as fingerprint_bucket,
            stable_part(e.fingerprint, {cfg.bucket_count}, {cfg.part_count})::bigint as fingerprint_part
        from entity_evidence e
        where e.fingerprint in (select fingerprint from affected_fingerprints)
          and e.fingerprint is not null
    ''')
    count = int(con.execute('select count(*) from affected_entity_keys').fetchone()[0])
    return {'affected_entity_count': count}


def _merge_relation_state(
    con: duckdb.DuckDBPyConnection,
    *,
    changed_relations_dir: Path,
    cfg: GoldPartitionConfig,
) -> dict[str, int]:
    changed_evidence_sql = _read_dataset_sql(changed_relations_dir / 'entity_relation_evidence')

    con.execute('drop table if exists affected_relation_keys')
    con.execute('''
        create temp table affected_relation_keys as
        select distinct e.relation_key
        from entity_relation_evidence e
        join changed_raw_record_ids r on r.raw_record_id = try_cast(e.raw_record_id as varchar)
        where e.relation_key is not null
    ''')
    con.execute('drop table if exists changed_relation_evidence_stable')
    con.execute(f'''
        create temp table changed_relation_evidence_stable as
        with changed as (
            select * from {changed_evidence_sql}
            where raw_record_id is not null
        ),
        remapped as (
            select
                c.source,
                try_cast(c.raw_record_id as varchar) as raw_record_id,
                c.record_attributes,
                c.subject_attributes,
                c.object_attributes,
                c.evidence,
                coalesce(sm.new_entity_key, c.subject_entity_key) as subject_entity_key,
                c.predicate,
                coalesce(om.new_entity_key, c.object_entity_key) as object_entity_key,
                c.relation_category
            from changed c
            left join changed_entity_key_remap sm on sm.old_entity_key = c.subject_entity_key
            left join changed_entity_key_remap om on om.old_entity_key = c.object_entity_key
        ),
        keyed as (
            select
                source,
                sha256(
                    coalesce(subject_entity_key, '') || '|' ||
                    coalesce(predicate, '') || '|' ||
                    coalesce(object_entity_key, '') || '|' ||
                    coalesce(relation_category, '')
                ) as relation_key,
                raw_record_id,
                record_attributes,
                subject_attributes,
                object_attributes,
                evidence,
                subject_entity_key,
                predicate,
                object_entity_key,
                relation_category
            from remapped
            where subject_entity_key is not null
              and object_entity_key is not null
        )
        select
            source,
            relation_key,
            raw_record_id,
            record_attributes,
            subject_attributes,
            object_attributes,
            evidence,
            subject_entity_key,
            predicate,
            object_entity_key,
            relation_category,
            stable_bucket(relation_key, {cfg.bucket_count})::bigint as relation_bucket,
            stable_part(relation_key, {cfg.bucket_count}, {cfg.part_count})::bigint as relation_part
        from keyed
    ''')
    con.execute('''
        insert into affected_relation_keys
        select distinct relation_key
        from changed_relation_evidence_stable
        where relation_key is not null
    ''')
    _deduplicate_temp_table(con, 'affected_relation_keys', 'relation_key')

    max_pk = int(con.execute('select coalesce(max(relation_pk), 0) from relation_key_registry').fetchone()[0])
    con.execute('drop table if exists new_relation_keys')
    con.execute('''
        create temp table new_relation_keys as
        select distinct relation_key, relation_bucket, relation_part
        from changed_relation_evidence_stable
        where relation_key not in (select relation_key from relation_key_registry)
    ''')
    con.execute(f'''
        insert into relation_key_registry(relation_key, relation_pk, relation_bucket, relation_part)
        select
            relation_key,
            {max_pk} + row_number() over(order by relation_key) as relation_pk,
            relation_bucket,
            relation_part
        from new_relation_keys
    ''')
    con.execute('''
        delete from entity_relation_evidence
        where try_cast(raw_record_id as varchar) in (select raw_record_id from changed_raw_record_ids)
    ''')
    evidence_offset = int(con.execute('select coalesce(max(relation_evidence_pk), 0) from entity_relation_evidence').fetchone()[0])
    con.execute(f'''
        insert into entity_relation_evidence
        select
            {evidence_offset} + row_number() over(order by e.source, e.relation_key, e.raw_record_id) as relation_evidence_pk,
            r.relation_pk,
            e.relation_key,
            e.source,
            e.raw_record_id,
            e.record_attributes,
            e.subject_attributes,
            e.object_attributes,
            e.evidence,
            e.subject_entity_key,
            e.predicate,
            e.object_entity_key,
            e.relation_category,
            e.relation_bucket,
            e.relation_part
        from changed_relation_evidence_stable e
        join relation_key_registry r using(relation_key)
    ''')
    con.execute('delete from entity_relation where relation_key in (select relation_key from affected_relation_keys)')
    con.execute('''
        insert into entity_relation
        with grouped as (
            select
                r.relation_pk,
                e.relation_key,
                first(e.subject_entity_key order by e.subject_entity_key nulls last) as subject_entity_key,
                first(e.predicate order by e.predicate nulls last) as predicate,
                first(e.object_entity_key order by e.object_entity_key nulls last) as object_entity_key,
                first(e.relation_category order by e.relation_category nulls last) as relation_category,
                count(*)::bigint as evidence_count,
                list_sort(list_distinct(list(e.source) filter (where e.source is not null))) as sources,
                r.relation_bucket,
                r.relation_part
            from entity_relation_evidence e
            join relation_key_registry r using(relation_key)
            where e.relation_key in (select relation_key from affected_relation_keys)
            group by r.relation_pk, e.relation_key, r.relation_bucket, r.relation_part
        )
        select
            g.relation_pk,
            g.relation_key,
            sm.entity_pk as subject_entity_pk,
            g.subject_entity_key,
            g.predicate,
            om.entity_pk as object_entity_pk,
            g.object_entity_key,
            g.relation_category,
            g.evidence_count,
            g.sources,
            g.relation_bucket,
            g.relation_part
        from grouped g
        left join entity_key_registry sm on sm.entity_key = g.subject_entity_key
        left join entity_key_registry om on om.entity_key = g.object_entity_key
    ''')
    count = int(con.execute('select count(*) from affected_relation_keys').fetchone()[0])
    return {'affected_relation_count': count}


def _export_state_outputs(
    con: duckdb.DuckDBPyConnection,
    *,
    staged_dir: Path,
    cfg: GoldPartitionConfig,
) -> dict[str, int]:
    entities_dir = staged_dir / 'entities'
    relations_dir = staged_dir / 'relations'
    for root in [
        entities_dir / 'entity',
        entities_dir / 'entity_evidence',
        entities_dir / 'entity_occurrence_map',
        entities_dir / 'entity_map',
        relations_dir / 'entity_relation',
        relations_dir / 'entity_relation_evidence',
    ]:
        if root.exists():
            shutil.rmtree(root)
        root.mkdir(parents=True, exist_ok=True)

    counts = {
        'entity': 0,
        'entity_evidence': 0,
        'entity_occurrence_map': 0,
        'entity_map': 0,
        'entity_relation': 0,
        'entity_relation_evidence': 0,
    }
    for part in range(cfg.part_count):
        counts['entity'] += _copy_part_query(
            con,
            f'select * from entity where entity_part = {part} order by entity_key',
            entities_dir / 'entity',
            part,
            cfg,
        )
        counts['entity_evidence'] += _copy_part_query(
            con,
            f'select * from entity_evidence where entity_part = {part} order by entity_key, source, raw_record_id, occurrence_id',
            entities_dir / 'entity_evidence',
            part,
            cfg,
        )
        counts['entity_occurrence_map'] += _copy_part_query(
            con,
            f'select * from entity_occurrence_map where occ_part = {part} order by occurrence_id',
            entities_dir / 'entity_occurrence_map',
            part,
            cfg,
        )
        counts['entity_map'] += _copy_part_query(
            con,
            f'select * from entity_map where fingerprint_part = {part} order by _fingerprint',
            entities_dir / 'entity_map',
            part,
            cfg,
        )
        counts['entity_relation'] += _copy_part_query(
            con,
            f'select * from entity_relation where relation_part = {part} order by relation_key',
            relations_dir / 'entity_relation',
            part,
            cfg,
        )
        counts['entity_relation_evidence'] += _copy_part_query(
            con,
            f'select * from entity_relation_evidence where relation_part = {part} order by relation_key, source, raw_record_id',
            relations_dir / 'entity_relation_evidence',
            part,
            cfg,
        )
    return counts


def _create_raw_id_table(con: duckdb.DuckDBPyConnection, raw_record_ids_path: Path) -> None:
    con.execute('drop table if exists changed_raw_record_ids')
    con.execute(f"""
        create temp table changed_raw_record_ids as
        select distinct try_cast(raw_record_id as varchar) as raw_record_id
        from read_parquet('{_sql_path(raw_record_ids_path)}', union_by_name=true)
        where raw_record_id is not null
          and try_cast(raw_record_id as varchar) <> ''
    """)


def _deduplicate_temp_table(con: duckdb.DuckDBPyConnection, table: str, column: str) -> None:
    con.execute(f'''
        create or replace temp table {table} as
        select distinct {column}
        from {table}
        where {column} is not null
    ''')


def _create_state_indexes(con: duckdb.DuckDBPyConnection) -> None:
    index_statements = [
        'create index if not exists idx_entity_evidence_raw on entity_evidence(raw_record_id)',
        'create index if not exists idx_entity_evidence_key on entity_evidence(entity_key)',
        'create index if not exists idx_entity_evidence_fingerprint on entity_evidence(fingerprint)',
        'create index if not exists idx_relation_evidence_raw on entity_relation_evidence(raw_record_id)',
        'create index if not exists idx_relation_evidence_key on entity_relation_evidence(relation_key)',
        'create index if not exists idx_entity_key_registry_key on entity_key_registry(entity_key)',
        'create index if not exists idx_relation_key_registry_key on relation_key_registry(relation_key)',
    ]
    for statement in index_statements:
        con.execute(statement)


def _state_row_counts(con: duckdb.DuckDBPyConnection) -> dict[str, int]:
    return {
        table: int(con.execute(f'select count(*) from {table}').fetchone()[0])
        for table in [
            'entity',
            'entity_evidence',
            'entity_occurrence_map',
            'entity_map',
            'entity_relation',
            'entity_relation_evidence',
        ]
    }


def _state_row_counts_for_manifest(
    state_path: Path,
    temp_dir: Path,
    cfg: GoldPartitionConfig,
) -> dict[str, int]:
    con = duckdb.connect(str(state_path))
    try:
        _configure_duckdb(con, temp_dir, cfg)
        return _state_row_counts(con)
    finally:
        con.close()


def _partition_config_from_state_manifest(path: Path) -> GoldPartitionConfig:
    if not path.exists():
        raise FileNotFoundError(f'missing gold source state manifest: {path}')
    manifest = json.loads(path.read_text(encoding='utf-8'))
    return _partition_config_from_manifest_data(manifest)


def _partition_config_from_entity_manifest(path: Path) -> GoldPartitionConfig:
    if not path.exists():
        raise FileNotFoundError(f'missing gold entity manifest: {path}')
    manifest = json.loads(path.read_text(encoding='utf-8'))
    return _partition_config_from_manifest_data(manifest)


def _partition_config_from_manifest_data(manifest: dict[str, Any]) -> GoldPartitionConfig:
    return GoldPartitionConfig(
        bucket_count=int(manifest.get('bucket_count', 4096)),
        part_count=int(manifest.get('part_count', manifest.get('entity_part_count', 128))),
        min_part_size_bytes=int(manifest.get('min_part_size_bytes', 200 * 1024 * 1024)),
    )


def _write_state_manifest(
    *,
    output_dir: Path,
    source: str,
    mode: str,
    cfg: GoldPartitionConfig,
    row_counts: dict[str, int],
) -> dict[str, Any]:
    manifest = {
        'layer': 'gold',
        'kind': 'source_state',
        'source': source,
        'mode': mode,
        'created_at': datetime.now(UTC).isoformat(),
        'state_path': str(gold_source_state_path(output_dir)),
        'bucket_algorithm': 'stable_u64_sha256_mod_v1',
        'bucket_count': cfg.bucket_count,
        'part_count': cfg.part_count,
        'min_part_size_bytes': cfg.min_part_size_bytes,
        'row_counts': row_counts,
    }
    (output_dir / GOLD_SOURCE_STATE_MANIFEST).write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + '\n',
        encoding='utf-8',
    )
    return manifest


def _write_entity_manifest(
    *,
    output_dir: Path,
    source: str,
    cfg: GoldPartitionConfig,
    row_counts: dict[str, int],
    summary: dict[str, Any],
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        'layer': 'gold',
        'kind': 'entities',
        'source': source,
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


def _write_relation_manifest(
    *,
    output_dir: Path,
    source: str,
    cfg: GoldPartitionConfig,
    row_counts: dict[str, int],
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        'layer': 'gold',
        'kind': 'relations',
        'source': source,
        'bucket_algorithm': 'stable_u64_sha256_mod_v1',
        'relation_key_algorithm': 'sha256_v1',
        'bucket_count': cfg.bucket_count,
        'part_count': cfg.part_count,
        'min_part_size_bytes': cfg.min_part_size_bytes,
        'relation_bucket_count': cfg.bucket_count,
        'relation_part_count': cfg.part_count,
        'parent_bucket_count': cfg.bucket_count,
        'parent_part_count': cfg.part_count,
        'outputs': {
            'entity_relation': 'entity_relation/',
            'entity_relation_evidence': 'entity_relation_evidence/',
        },
        'row_counts': row_counts,
    }
    (output_dir / 'manifest.json').write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + '\n',
        encoding='utf-8',
    )


def _read_dataset_sql(path: Path) -> str:
    if path.is_dir():
        return (
            "read_parquet("
            f"'{_sql_path(path / '**' / '*.parquet')}', "
            "union_by_name=true, hive_partitioning=false)"
        )
    return f"read_parquet('{_sql_path(path)}', union_by_name=true)"
