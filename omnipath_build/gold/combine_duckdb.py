from __future__ import annotations

import json
import time
import shutil
from typing import Any
from pathlib import Path
from datetime import UTC, datetime
from dataclasses import dataclass

import duckdb

from omnipath_build.gold.build_resources import build_resources_parquet
from omnipath_build.gold.utils.canonicalization import (
    ONTOLOGY_ENTITY_TYPE_LABEL,
    ONTOLOGY_IDENTIFIER_TYPE_LABEL,
)

@dataclass(frozen=True)
class GoldSourceDir:
    source: str
    path: Path


def build_combined_duckdb(
    *,
    gold_root: str | Path = 'data/gold',
    output_dir: str | Path = 'data/combined',
    affected_entity_keys: set[str] | None = None,
    affected_relation_keys: set[str] | None = None,
    inputs_package: str = 'pypath.inputs_v2',
    freeze_monthly: bool = False,
    changed_source: str | None = None,
    entity_batch_size: int = 50_000,
    relation_batch_size: int = 50_000,
) -> dict[str, Any]:
    """Build/update combined parquets through a DuckDB-backed keyed state store."""
    gold_root = Path(gold_root)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    source_dirs = _discover_gold_source_dirs(gold_root)
    state_path = output_dir / 'state.duckdb'
    incremental_requested = (
        affected_entity_keys is not None or affected_relation_keys is not None
    )
    affected_entity_keys = affected_entity_keys or set()
    affected_relation_keys = affected_relation_keys or set()
    run_id = _new_run_id()

    con = duckdb.connect(str(state_path))
    try:
        _configure_duckdb(con, output_dir)
        _ensure_state_schema(con)

        if _state_is_empty(con) and (output_dir / 'latest').exists():
            _log(f'importing existing combined latest from {output_dir / "latest"}')
            _import_existing_latest(con, output_dir / 'latest')

        bootstrap_state = _state_is_empty(con) or not incremental_requested
        if bootstrap_state:
            _log(
                'starting DuckDB combine bootstrap '
                f'entity_batch_size={entity_batch_size} '
                f'relation_batch_size={relation_batch_size}'
            )
            _reset_state(con)
            mode = 'bootstrap'
            affected_entities_count = 0
            affected_relations_count = 0
        else:
            _log(
                'starting incremental DuckDB combine '
                f'entities={len(affected_entity_keys)} '
                f'relations={len(affected_relation_keys)}'
            )
            mode = 'incremental'
            affected_entities_count = len(affected_entity_keys)
            affected_relations_count = len(affected_relation_keys)

        effective_entity_keys = set(affected_entity_keys)
        effective_relation_keys = set(affected_relation_keys)
        if bootstrap_state:
            _apply_bootstrap_batched(
                con,
                source_dirs,
                entity_batch_size=entity_batch_size,
                relation_batch_size=relation_batch_size,
            )
            effective_entity_keys = set()
            effective_relation_keys = set()
        else:
            effective_relation_keys = _apply_incremental_batched(
                con,
                source_dirs,
                affected_entity_keys=affected_entity_keys,
                affected_relation_keys=affected_relation_keys,
                entity_batch_size=entity_batch_size,
                relation_batch_size=relation_batch_size,
            )

        version_dir = output_dir / 'latest'
        started = time.perf_counter()
        _log(f'exporting parquet artifacts to {version_dir}')
        _export_latest(con, version_dir)
        _log(f'done parquet export in {time.perf_counter() - started:.1f}s')
        started = time.perf_counter()
        _log('exporting relation annotations')
        relation_annotation_summary = _export_relation_annotation(con, version_dir)
        _log(f'done relation annotations in {time.perf_counter() - started:.1f}s')
        started = time.perf_counter()
        _log(f'writing combine run artifacts run_id={run_id}')
        run_summary = _write_run_artifacts(
            con,
            output_dir=output_dir,
            latest_dir=version_dir,
            run_id=run_id,
            mode=mode,
            changed_source=changed_source,
            affected_entity_keys=effective_entity_keys,
            affected_relation_keys=effective_relation_keys,
        )
        _log(f'done combine run artifacts in {time.perf_counter() - started:.1f}s')
        started = time.perf_counter()
        _log('building resources metadata')
        resources_path = build_resources_parquet(
            gold_root=gold_root,
            output_path=version_dir / 'resources.parquet',
            inputs_package=inputs_package,
        )
        _log(f'done resources metadata in {time.perf_counter() - started:.1f}s')
        row_counts = _row_counts(con)
        row_counts['relation_annotation_term.parquet'] = int(
            relation_annotation_summary['row_count']
        )
        if resources_path.exists():
            row_counts['resources.parquet'] = int(
                con.execute(
                    f"select count(*) from read_parquet('{_sql_path(resources_path)}')"
                ).fetchone()[0]
            )

        summary = {
            'gold_root': str(gold_root),
            'output_dir': str(version_dir),
            'state_path': str(state_path),
            'engine': 'duckdb',
            'mode': mode,
            'run_id': run_id,
            'sources': [
                {'source': item.source, 'path': str(item.path)}
                for item in source_dirs
            ],
            'row_counts': row_counts,
            'relation_annotation_summary': relation_annotation_summary,
            'run_summary': run_summary,
            'run_dir': run_summary['run_dir'],
            'resources_path': str(resources_path),
        }
        (version_dir / 'combined_build_summary.json').write_text(
            json.dumps(summary, indent=2) + '\n',
            encoding='utf-8',
        )

        _append_build_manifest(
            version_dir,
            mode=mode,
            freeze_monthly=freeze_monthly,
            row_counts=row_counts,
            affected_entities=affected_entities_count,
            affected_relations=affected_relations_count,
            changed_source=changed_source,
        )

        if freeze_monthly:
            snapshot_dir = _freeze_monthly_snapshot(output_dir, version_dir)
            summary['monthly_snapshot'] = str(snapshot_dir)

        return summary
    finally:
        con.close()


def _configure_duckdb(con: duckdb.DuckDBPyConnection, output_dir: Path) -> None:
    temp_dir = output_dir / '.duckdb_tmp'
    temp_dir.mkdir(parents=True, exist_ok=True)
    con.execute('set preserve_insertion_order = false')
    con.execute(f"set temp_directory = '{_sql_path(temp_dir)}'")


def _new_run_id() -> str:
    return datetime.now(UTC).strftime('%Y%m%dT%H%M%S%fZ')


def _discover_gold_source_dirs(gold_root: Path) -> list[GoldSourceDir]:
    if not gold_root.exists():
        raise FileNotFoundError(f'Gold root does not exist: {gold_root}')
    sources: list[GoldSourceDir] = []
    for source_dir in sorted(gold_root.iterdir()):
        if not source_dir.is_dir():
            continue
        if (source_dir / 'entities' / 'entity.parquet').exists():
            sources.append(GoldSourceDir(source=source_dir.name, path=source_dir))
    return sources


def _ensure_state_schema(con: duckdb.DuckDBPyConnection) -> None:
    con.execute("""
        create table if not exists entity_key_map (
            entity_key varchar primary key,
            entity_id bigint
        )
    """)
    con.execute("""
        create table if not exists relation_key_map (
            relation_key varchar primary key,
            relation_id bigint
        )
    """)
    con.execute("""
        create table if not exists entity (
            entity_id bigint,
            entity_key varchar,
            canonical_identifier varchar,
            canonical_identifier_type varchar,
            identifiers struct(identifier varchar, identifier_type varchar)[],
            entity_type varchar,
            taxonomy_id varchar,
            entity_attributes struct(term varchar, "value" varchar, unit varchar)[],
            sources varchar[]
        )
    """)
    con.execute("""
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
            sources varchar[]
        )
    """)
    con.execute("""
        create table if not exists entity_relation_evidence (
            relation_evidence_id bigint,
            relation_id bigint,
            relation_key varchar,
            source varchar,
            raw_record_id varchar,
            record_attributes struct(term varchar, "value" varchar, unit varchar)[],
            subject_attributes struct(term varchar, "value" varchar, unit varchar)[],
            object_attributes struct(term varchar, "value" varchar, unit varchar)[],
            evidence struct(term varchar, "value" varchar, unit varchar)[]
        )
    """)
    con.execute("""
        create table if not exists entity_evidence (
            source varchar,
            entity_key varchar,
            raw_record_ids varchar[],
            entity_type varchar,
            taxonomy_id varchar,
            identifiers struct(identifier varchar, identifier_type varchar)[],
            entity_attributes struct(term varchar, "value" varchar, unit varchar)[]
        )
    """)


def _reset_state(con: duckdb.DuckDBPyConnection) -> None:
    for table in [
        'entity_key_map',
        'relation_key_map',
        'entity',
        'entity_relation',
        'entity_relation_evidence',
        'entity_evidence',
    ]:
        con.execute(f'delete from {table}')


def _state_is_empty(con: duckdb.DuckDBPyConnection) -> bool:
    return int(con.execute('select count(*) from entity').fetchone()[0]) == 0


def _import_existing_latest(con: duckdb.DuckDBPyConnection, latest_dir: Path) -> None:
    imports = {
        'entity': latest_dir / 'entity.parquet',
        'entity_relation': latest_dir / 'entity_relation.parquet',
        'entity_relation_evidence': latest_dir / 'entity_relation_evidence.parquet',
        'entity_evidence': latest_dir / 'entity_evidence.parquet',
    }
    if not all(path.exists() for path in imports.values()):
        return

    for table, path in imports.items():
        con.execute(f'delete from {table}')
        con.execute(
            f"insert into {table} select * from read_parquet('{_sql_path(path)}')"
        )
    con.execute('delete from entity_key_map')
    con.execute("""
        insert into entity_key_map(entity_key, entity_id)
        select entity_key, entity_id from entity
    """)
    con.execute('delete from relation_key_map')
    con.execute("""
        insert into relation_key_map(relation_key, relation_id)
        select relation_key, relation_id from entity_relation
    """)


def _apply_bootstrap_batched(
    con: duckdb.DuckDBPyConnection,
    source_dirs: list[GoldSourceDir],
    *,
    entity_batch_size: int,
    relation_batch_size: int,
) -> None:
    started = time.perf_counter()
    _log('bootstrap: replaying entity keys source-by-source')
    for index, source_dir in enumerate(source_dirs, start=1):
        _apply_source_key_batches(
            con,
            source_dirs,
            source_dir,
            source_index=index,
            source_count=len(source_dirs),
            relative_path='entities/entity.parquet',
            key_column='entity_key',
            key_table='affected_entity_keys',
            batch_size=entity_batch_size,
            apply_batch=lambda: _apply_entity_batch(con, source_dirs),
        )
    _log(f'bootstrap: done entities in {time.perf_counter() - started:.1f}s')

    started = time.perf_counter()
    _log('bootstrap: replaying relation keys source-by-source')
    for index, source_dir in enumerate(source_dirs, start=1):
        _apply_source_key_batches(
            con,
            source_dirs,
            source_dir,
            source_index=index,
            source_count=len(source_dirs),
            relative_path='relations/entity_relation.parquet',
            key_column='relation_key',
            key_table='affected_relation_keys',
            batch_size=relation_batch_size,
            apply_batch=lambda: _apply_relation_batch(con, source_dirs),
        )
    _log(f'bootstrap: done relations/evidence in {time.perf_counter() - started:.1f}s')


def _apply_incremental_batched(
    con: duckdb.DuckDBPyConnection,
    source_dirs: list[GoldSourceDir],
    *,
    affected_entity_keys: set[str],
    affected_relation_keys: set[str],
    entity_batch_size: int,
    relation_batch_size: int,
) -> set[str]:
    expanded_relation_keys = set(affected_relation_keys)
    if affected_entity_keys:
        started = time.perf_counter()
        _log(
            'incremental: replaying affected entity keys '
            f'total={len(affected_entity_keys)}'
        )
        for batch_index, batch in enumerate(
            _iter_batches(sorted(affected_entity_keys), entity_batch_size),
            start=1,
        ):
            total_batches = _batch_count(len(affected_entity_keys), entity_batch_size)
            _log(
                'incremental entity batch '
                f'{batch_index}/{total_batches} keys={len(batch)}'
            )
            _create_key_table(con, 'affected_entity_keys', 'entity_key', set(batch))
            _apply_entity_batch(con, source_dirs)
        _log(f'incremental: done entities in {time.perf_counter() - started:.1f}s')

        expanded_relation_keys.update(_relation_keys_for_entity_keys(
            con,
            affected_entity_keys,
        ))

    if expanded_relation_keys:
        started = time.perf_counter()
        _log(
            'incremental: replaying affected relation keys '
            f'total={len(expanded_relation_keys)}'
        )
        for batch_index, batch in enumerate(
            _iter_batches(sorted(expanded_relation_keys), relation_batch_size),
            start=1,
        ):
            total_batches = _batch_count(len(expanded_relation_keys), relation_batch_size)
            _log(
                'incremental relation batch '
                f'{batch_index}/{total_batches} keys={len(batch)}'
            )
            _create_key_table(con, 'affected_relation_keys', 'relation_key', set(batch))
            _apply_relation_batch(con, source_dirs)
        _log(
            'incremental: done relations/evidence in '
            f'{time.perf_counter() - started:.1f}s'
        )
    return expanded_relation_keys


def _relation_keys_for_entity_keys(
    con: duckdb.DuckDBPyConnection,
    entity_keys: set[str],
) -> set[str]:
    if not entity_keys:
        return set()
    _create_key_table(con, 'affected_entity_relation_keys', 'entity_key', entity_keys)
    rows = con.execute("""
        select relation_key
        from entity_relation
        where subject_entity_key in (
            select entity_key from affected_entity_relation_keys
        )
        or object_entity_key in (
            select entity_key from affected_entity_relation_keys
        )
    """).fetchall()
    relation_keys = {row[0] for row in rows if row[0]}
    if relation_keys:
        _log(
            'incremental: expanded entity keys to relation keys '
            f'entities={len(entity_keys)} relations={len(relation_keys)}'
        )
    return relation_keys


def _apply_source_key_batches(
    con: duckdb.DuckDBPyConnection,
    source_dirs: list[GoldSourceDir],
    source_dir: GoldSourceDir,
    *,
    source_index: int,
    source_count: int,
    relative_path: str,
    key_column: str,
    key_table: str,
    batch_size: int,
    apply_batch,
) -> None:
    path = source_dir.path / relative_path
    if not path.exists():
        return

    pending_table = f'pending_{key_column}s'
    con.execute(f'drop table if exists {pending_table}')
    con.execute(f"""
        create temp table {pending_table} as
        select
            row_number() over(order by {key_column}) as rn,
            {key_column}
        from (
            select distinct try_cast({key_column} as varchar) as {key_column}
            from read_parquet('{_sql_path(path)}')
            where {key_column} is not null
        )
    """)
    total_keys = int(con.execute(f'select count(*) from {pending_table}').fetchone()[0])
    if total_keys == 0:
        _log(
            f'source {source_index}/{source_count} {source_dir.source}: '
            f'no {key_column}s'
        )
        return

    total_batches = _batch_count(total_keys, batch_size)
    for batch_index in range(1, total_batches + 1):
        low = (batch_index - 1) * batch_size
        high = batch_index * batch_size
        con.execute(f'drop table if exists {key_table}')
        con.execute(f"""
            create temp table {key_table} as
            select {key_column}
            from {pending_table}
            where rn > {low} and rn <= {high}
        """)
        batch_keys = int(con.execute(f'select count(*) from {key_table}').fetchone()[0])
        if batch_keys == 0:
            continue
        _log(
            f'source {source_index}/{source_count} {source_dir.source}: '
            f'{key_column} batch {batch_index}/{total_batches} keys={batch_keys}'
        )
        apply_batch()


def _apply_entity_batch(
    con: duckdb.DuckDBPyConnection,
    source_dirs: list[GoldSourceDir],
) -> None:
    con.execute('BEGIN TRANSACTION')
    try:
        _recompute_entities(con, source_dirs, full_build=False)
        _recompute_entity_evidence(con, source_dirs, full_build=False)
        con.execute('COMMIT')
    except Exception:
        con.execute('ROLLBACK')
        raise


def _apply_relation_batch(
    con: duckdb.DuckDBPyConnection,
    source_dirs: list[GoldSourceDir],
) -> None:
    con.execute('BEGIN TRANSACTION')
    try:
        _recompute_relations(con, source_dirs, full_build=False)
        _recompute_relation_evidence(con, source_dirs, full_build=False)
        con.execute('COMMIT')
    except Exception:
        con.execute('ROLLBACK')
        raise


def _iter_batches(items: list[str], batch_size: int):
    if batch_size <= 0:
        raise ValueError('Batch size must be greater than zero')
    for start in range(0, len(items), batch_size):
        yield items[start:start + batch_size]


def _batch_count(total: int, batch_size: int) -> int:
    if batch_size <= 0:
        raise ValueError('Batch size must be greater than zero')
    return (total + batch_size - 1) // batch_size


def _create_key_table(
    con: duckdb.DuckDBPyConnection,
    table: str,
    column: str,
    values: set[str],
) -> None:
    con.execute(f'drop table if exists {table}')
    con.execute(f'create temp table {table}({column} varchar)')
    if values:
        con.executemany(
            f'insert into {table} values (?)',
            [(value,) for value in sorted(values)],
        )


def _source_union_sql(
    source_dirs: list[GoldSourceDir],
    relative_path: str,
    columns: list[str],
    *,
    full_build: bool,
    key_column: str,
    key_table: str,
) -> str:
    selects: list[str] = []
    selected_columns = ', '.join(_column_expr(column) for column in columns)
    for source_dir in source_dirs:
        path = source_dir.path / relative_path
        if not path.exists():
            continue
        where = ''
        if not full_build:
            where = f' where {key_column} in (select {key_column} from {key_table})'
        selects.append(
            "select "
            f"'{_sql_literal(source_dir.source)}' as _source, "
            f'{selected_columns} '
            f"from read_parquet('{_sql_path(path)}')"
            f'{where}'
        )
    if selects:
        return '\nunion all\n'.join(selects)
    return _empty_source_sql(columns)


def _empty_source_sql(columns: list[str]) -> str:
    typed_columns = {
        'entity_pk': 'null::bigint as entity_pk',
        'entity_key': 'null::varchar as entity_key',
        'canonical_identifier': 'null::varchar as canonical_identifier',
        'canonical_identifier_type': 'null::varchar as canonical_identifier_type',
        'identifiers': '[]::struct(identifier varchar, identifier_type varchar)[] as identifiers',
        'entity_type': 'null::varchar as entity_type',
        'taxonomy_id': 'null::varchar as taxonomy_id',
        'entity_attributes': '[]::struct(term varchar, "value" varchar, unit varchar)[] as entity_attributes',
        'sources': '[]::varchar[] as sources',
        'relation_pk': 'null::bigint as relation_pk',
        'relation_key': 'null::varchar as relation_key',
        'subject_entity_pk': 'null::bigint as subject_entity_pk',
        'subject_entity_key': 'null::varchar as subject_entity_key',
        'predicate': 'null::varchar as predicate',
        'object_entity_pk': 'null::bigint as object_entity_pk',
        'object_entity_key': 'null::varchar as object_entity_key',
        'relation_category': 'null::varchar as relation_category',
        'evidence_count': 'null::bigint as evidence_count',
        'relation_evidence_pk': 'null::bigint as relation_evidence_pk',
        'raw_record_id': 'null::varchar as raw_record_id',
        'source': 'null::varchar as source',
        'record_attributes': '[]::struct(term varchar, "value" varchar, unit varchar)[] as record_attributes',
        'subject_attributes': '[]::struct(term varchar, "value" varchar, unit varchar)[] as subject_attributes',
        'object_attributes': '[]::struct(term varchar, "value" varchar, unit varchar)[] as object_attributes',
        'evidence': '[]::struct(term varchar, "value" varchar, unit varchar)[] as evidence',
        'raw_record_ids': '[]::varchar[] as raw_record_ids',
    }
    selected = ', '.join(typed_columns[column] for column in columns)
    return f'select null::varchar as _source, {selected} where false'


def _column_expr(column: str) -> str:
    casts = {
        'entity_pk': 'bigint',
        'relation_pk': 'bigint',
        'relation_evidence_pk': 'bigint',
        'subject_entity_pk': 'bigint',
        'object_entity_pk': 'bigint',
        'evidence_count': 'bigint',
        'entity_key': 'varchar',
        'canonical_identifier': 'varchar',
        'canonical_identifier_type': 'varchar',
        'entity_type': 'varchar',
        'taxonomy_id': 'varchar',
        'relation_key': 'varchar',
        'subject_entity_key': 'varchar',
        'predicate': 'varchar',
        'object_entity_key': 'varchar',
        'relation_category': 'varchar',
        'source': 'varchar',
        'raw_record_id': 'varchar',
        'identifiers': 'struct(identifier varchar, identifier_type varchar)[]',
        'entity_attributes': 'struct(term varchar, "value" varchar, unit varchar)[]',
        'sources': 'varchar[]',
        'record_attributes': 'struct(term varchar, "value" varchar, unit varchar)[]',
        'subject_attributes': 'struct(term varchar, "value" varchar, unit varchar)[]',
        'object_attributes': 'struct(term varchar, "value" varchar, unit varchar)[]',
        'evidence': 'struct(term varchar, "value" varchar, unit varchar)[]',
        'raw_record_ids': 'varchar[]',
    }
    if column not in casts:
        return column
    return f'try_cast({column} as {casts[column]}) as {column}'


def _recompute_entities(
    con: duckdb.DuckDBPyConnection,
    source_dirs: list[GoldSourceDir],
    *,
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
    ]
    con.execute('drop table if exists source_entities')
    con.execute(f"""
        create temp table source_entities as
        {_source_union_sql(
            source_dirs,
            'entities/entity.parquet',
            columns,
            full_build=full_build,
            key_column='entity_key',
            key_table='affected_entity_keys',
        )}
    """)
    con.execute("""
        create or replace temp view merged_entities as
        with ident_rows as (
            select entity_key, item.identifier as identifier, item.identifier_type as identifier_type
            from source_entities, unnest(identifiers) as t(item)
            union all
            select entity_key, canonical_identifier, canonical_identifier_type
            from source_entities
            where canonical_identifier is not null
              and canonical_identifier_type is not null
        ),
        ident_lists as (
            select
                entity_key,
                list_sort(list_distinct(list(
                    struct_pack(identifier := identifier, identifier_type := identifier_type)
                ))) as identifiers
            from ident_rows
            group by entity_key
        ),
        attr_rows as (
            select entity_key, item.term as term, item.value as value, item.unit as unit
            from source_entities, unnest(entity_attributes) as t(item)
        ),
        attr_lists as (
            select
                entity_key,
                list_distinct(list(struct_pack(term := term, value := value, unit := unit))) as entity_attributes
            from attr_rows
            group by entity_key
        ),
        source_rows as (
            select entity_key, item as source
            from source_entities, unnest(sources) as t(item)
        ),
        source_lists as (
            select entity_key, list_sort(list_distinct(list(source))) as sources
            from source_rows
            group by entity_key
        )
        select
            se.entity_key,
            first(canonical_identifier order by _source, canonical_identifier nulls last) as canonical_identifier,
            first(canonical_identifier_type order by _source, canonical_identifier_type nulls last) as canonical_identifier_type,
            coalesce(il.identifiers, []::struct(identifier varchar, identifier_type varchar)[]) as identifiers,
            first(entity_type order by _source, entity_type nulls last) as entity_type,
            first(taxonomy_id order by _source, taxonomy_id nulls last) as taxonomy_id,
            coalesce(al.entity_attributes, []::struct(term varchar, "value" varchar, unit varchar)[]) as entity_attributes,
            coalesce(sl.sources, []::varchar[]) as sources
        from source_entities se
        left join ident_lists il using(entity_key)
        left join attr_lists al using(entity_key)
        left join source_lists sl using(entity_key)
        group by se.entity_key, il.identifiers, al.entity_attributes, sl.sources
    """)
    max_id = int(con.execute(
        'select coalesce(max(entity_id), 0) from entity_key_map'
    ).fetchone()[0])
    con.execute(f"""
        insert into entity_key_map(entity_key, entity_id)
        select entity_key, {max_id} + row_number() over(order by entity_key) as entity_id
        from merged_entities
        where entity_key not in (select entity_key from entity_key_map)
    """)
    if full_build:
        con.execute('delete from entity')
    else:
        con.execute("""
            delete from entity
            where entity_key in (select entity_key from affected_entity_keys)
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
            e.sources
        from merged_entities e
        join entity_key_map m using(entity_key)
    """)


def _recompute_relations(
    con: duckdb.DuckDBPyConnection,
    source_dirs: list[GoldSourceDir],
    *,
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
    ]
    con.execute('drop table if exists source_relations')
    con.execute(f"""
        create temp table source_relations as
        {_source_union_sql(
            source_dirs,
            'relations/entity_relation.parquet',
            columns,
            full_build=full_build,
            key_column='relation_key',
            key_table='affected_relation_keys',
        )}
    """)
    con.execute("""
        create or replace temp view mapped_source_relations as
        select
            r.relation_key,
            sm.entity_id as subject_entity_id,
            r.subject_entity_key,
            r.predicate,
            om.entity_id as object_entity_id,
            r.object_entity_key,
            r.relation_category,
            r.evidence_count,
            r.sources
        from source_relations r
        join entity_key_map sm on sm.entity_key = r.subject_entity_key
        join entity_key_map om on om.entity_key = r.object_entity_key
    """)
    con.execute("""
        create or replace temp view merged_relations as
        with source_rows as (
            select relation_key, item as source
            from mapped_source_relations, unnest(sources) as t(item)
        ),
        source_lists as (
            select relation_key, list_sort(list_distinct(list(source))) as sources
            from source_rows
            group by relation_key
        ),
        participant_rows as (
            select relation_key, subject_entity_id as entity_id
            from mapped_source_relations
            union all
            select relation_key, object_entity_id as entity_id
            from mapped_source_relations
        ),
        participant_type_lists as (
            select
                p.relation_key,
                list_sort(list_distinct(list(e.entity_type) filter (where e.entity_type is not null))) as participant_types
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
                sum(evidence_count)::bigint as evidence_count
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
            ptl.participant_types,
            b.evidence_count,
            coalesce(sl.sources, []::varchar[]) as sources
        from base b
        left join participant_type_lists ptl using(relation_key)
        left join source_lists sl using(relation_key)
        group by
            b.relation_key,
            b.subject_entity_id,
            b.subject_entity_key,
            b.predicate,
            b.object_entity_id,
            b.object_entity_key,
            b.relation_category,
            ptl.participant_types,
            b.evidence_count,
            sl.sources
    """)
    max_id = int(con.execute(
        'select coalesce(max(relation_id), 0) from relation_key_map'
    ).fetchone()[0])
    con.execute(f"""
        insert into relation_key_map(relation_key, relation_id)
        select relation_key, {max_id} + row_number() over(order by relation_key) as relation_id
        from merged_relations
        where relation_key not in (select relation_key from relation_key_map)
    """)
    if full_build:
        con.execute('delete from entity_relation')
    else:
        con.execute("""
            delete from entity_relation
            where relation_key in (select relation_key from affected_relation_keys)
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
            coalesce(r.participant_types, []::varchar[]),
            r.evidence_count,
            r.sources
        from merged_relations r
        join relation_key_map m using(relation_key)
    """)


def _recompute_relation_evidence(
    con: duckdb.DuckDBPyConnection,
    source_dirs: list[GoldSourceDir],
    *,
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
    ]
    con.execute('drop table if exists source_relation_evidence')
    con.execute(f"""
        create temp table source_relation_evidence as
        {_source_union_sql(
            source_dirs,
            'relations/entity_relation_evidence.parquet',
            columns,
            full_build=full_build,
            key_column='relation_key',
            key_table='affected_relation_keys',
        )}
    """)
    if full_build:
        con.execute('delete from entity_relation_evidence')
    else:
        con.execute("""
            delete from entity_relation_evidence
            where relation_key in (select relation_key from affected_relation_keys)
        """)
    max_id = int(con.execute("""
        select coalesce(max(relation_evidence_id), 0) from entity_relation_evidence
    """).fetchone()[0])
    con.execute(f"""
        insert into entity_relation_evidence
        select
            {max_id} + row_number() over(order by e.source, m.relation_id, e.raw_record_id) as relation_evidence_id,
            m.relation_id,
            e.relation_key,
            e.source,
            e.raw_record_id,
            e.record_attributes,
            e.subject_attributes,
            e.object_attributes,
            e.evidence
        from source_relation_evidence e
        join relation_key_map m using(relation_key)
    """)


def _recompute_entity_evidence(
    con: duckdb.DuckDBPyConnection,
    source_dirs: list[GoldSourceDir],
    *,
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
    ]
    con.execute('drop table if exists source_entity_evidence')
    con.execute(f"""
        create temp table source_entity_evidence as
        {_source_union_sql(
            source_dirs,
            'entities/entity_evidence.parquet',
            columns,
            full_build=full_build,
            key_column='entity_key',
            key_table='affected_entity_keys',
        )}
    """)
    if full_build:
        con.execute('delete from entity_evidence')
    else:
        con.execute("""
            delete from entity_evidence
            where entity_key in (select entity_key from affected_entity_keys)
        """)
    con.execute("""
        insert into entity_evidence
        select
            source,
            entity_key,
            list_sort(list_distinct(list(raw_record_id) filter (where raw_record_id is not null and raw_record_id != ''))) as raw_record_ids,
            first(entity_type order by entity_type nulls last) as entity_type,
            first(taxonomy_id order by taxonomy_id nulls last) as taxonomy_id,
            list_distinct(flatten(list(identifiers))) as identifiers,
            list_distinct(flatten(list(entity_attributes))) as entity_attributes
        from source_entity_evidence
        group by source, entity_key
    """)


def _export_latest(con: duckdb.DuckDBPyConnection, version_dir: Path) -> None:
    tmp_dir = version_dir.parent / f'.{version_dir.name}.tmp'
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir)
    tmp_dir.mkdir(parents=True)

    exports = {
        'entity.parquet': """
            select
                entity_id,
                entity_key,
                canonical_identifier,
                canonical_identifier_type,
                identifiers,
                entity_type,
                taxonomy_id,
                entity_attributes,
                sources
            from entity
        """,
        'entity_relation.parquet': """
            select
                relation_id,
                relation_key,
                subject_entity_id,
                subject_entity_key,
                predicate,
                object_entity_id,
                object_entity_key,
                relation_category,
                participant_types,
                evidence_count,
                sources
            from entity_relation
        """,
        'entity_relation_evidence.parquet': """
            select
                relation_evidence_id,
                relation_id,
                relation_key,
                source,
                raw_record_id,
                record_attributes,
                subject_attributes,
                object_attributes,
                evidence
            from entity_relation_evidence
        """,
        'entity_evidence.parquet': """
            select
                source,
                entity_key,
                raw_record_ids,
                entity_type,
                taxonomy_id,
                identifiers,
                entity_attributes
            from entity_evidence
        """,
    }
    for file_name, query in exports.items():
        con.execute(
            f"copy ({query}) to '{_sql_path(tmp_dir / file_name)}' (format parquet)"
        )

    if version_dir.exists():
        backup_dir = version_dir.parent / f'.{version_dir.name}.previous'
        if backup_dir.exists():
            shutil.rmtree(backup_dir)
        version_dir.replace(backup_dir)
        tmp_dir.replace(version_dir)
        shutil.rmtree(backup_dir)
    else:
        tmp_dir.replace(version_dir)


def _export_relation_annotation(
    con: duckdb.DuckDBPyConnection,
    output_dir: Path,
) -> dict[str, Any]:
    relation_annotation_path = output_dir / 'relation_annotation_term.parquet'
    term_entity_type = _sql_literal(ONTOLOGY_ENTITY_TYPE_LABEL)
    term_identifier_type = _sql_literal(ONTOLOGY_IDENTIFIER_TYPE_LABEL)
    query = f"""
        with term_entities as (
            select
                entity_id::bigint as term_entity_id,
                canonical_identifier::varchar as term_id
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
            select
                r.subject_entity_id::bigint as subject_entity_id,
                r.object_entity_id::bigint as term_entity_id
            from entity_relation r
            join term_entities t on t.term_entity_id = r.object_entity_id
            where r.relation_category = 'association'
        ),
        participant_candidates as (
            select
                relation_id,
                relation_evidence_id,
                source,
                subject_entity_id as annotated_entity_id
            from interaction_relation_evidence
            union all
            select
                relation_id,
                relation_evidence_id,
                source,
                object_entity_id as annotated_entity_id
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
            join annotation_relations a
              on a.subject_entity_id = c.annotated_entity_id
            join term_entities t
              on t.term_entity_id = a.term_entity_id
        )
        select
            relation_id,
            relation_evidence_id,
            source,
            scope,
            term_entity_id
        from (
            select * from interaction_terms
            union
            select * from participant_terms
        )
    """
    con.execute(
        f"copy ({query}) to '{_sql_path(relation_annotation_path)}' (format parquet)"
    )
    row_count = int(con.execute(f'select count(*) from ({query})').fetchone()[0])
    summary = {
        'output_dir': str(output_dir),
        'relation_annotation_path': str(relation_annotation_path),
        'row_count': row_count,
        'missing_inputs': [],
        'engine': 'duckdb',
    }
    (output_dir / 'relation_annotation_summary.json').write_text(
        json.dumps(summary, indent=2) + '\n',
        encoding='utf-8',
    )
    return summary


def _write_run_artifacts(
    con: duckdb.DuckDBPyConnection,
    *,
    output_dir: Path,
    latest_dir: Path,
    run_id: str,
    mode: str,
    changed_source: str | None,
    affected_entity_keys: set[str],
    affected_relation_keys: set[str],
) -> dict[str, Any]:
    run_dir = output_dir / 'runs' / run_id
    affected_dir = run_dir / 'affected'
    delta_dir = run_dir / 'delta'
    affected_dir.mkdir(parents=True, exist_ok=True)
    delta_dir.mkdir(parents=True, exist_ok=True)

    full_build = mode == 'bootstrap'
    if full_build:
        _write_full_build_run_artifacts(
            con,
            affected_dir=affected_dir,
            delta_dir=delta_dir,
            changed_source=changed_source,
            latest_dir=latest_dir,
        )
    else:
        _create_key_table(con, 'run_affected_entity_keys', 'entity_key', affected_entity_keys)
        _create_key_table(con, 'run_affected_relation_keys', 'relation_key', affected_relation_keys)
        _write_incremental_run_artifacts(
            con,
            affected_dir=affected_dir,
            delta_dir=delta_dir,
            changed_source=changed_source,
            latest_dir=latest_dir,
        )

    delta_counts = _run_delta_counts(con, delta_dir)
    manifest = {
        'layer': 'combined',
        'run_id': run_id,
        'mode': mode,
        'created_at': datetime.now(UTC).isoformat(),
        'changed_source': changed_source,
        'latest_dir': str(latest_dir),
        'run_dir': str(run_dir),
        'affected_entity_count': (
            int(con.execute('select count(*) from entity').fetchone()[0])
            if full_build else len(affected_entity_keys)
        ),
        'affected_relation_count': (
            int(con.execute('select count(*) from entity_relation').fetchone()[0])
            if full_build else len(affected_relation_keys)
        ),
        'delta_counts': delta_counts,
    }
    (run_dir / 'manifest.json').write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + '\n',
        encoding='utf-8',
    )
    (output_dir / 'runs' / 'latest.json').write_text(
        json.dumps({
            'run_id': run_id,
            'path': str(run_dir),
            'manifest': str(run_dir / 'manifest.json'),
        }, indent=2, sort_keys=True) + '\n',
        encoding='utf-8',
    )
    return manifest


def _write_full_build_run_artifacts(
    con: duckdb.DuckDBPyConnection,
    *,
    affected_dir: Path,
    delta_dir: Path,
    changed_source: str | None,
    latest_dir: Path,
) -> None:
    source_value = _nullable_sql_literal(changed_source)
    _copy_query(con, f"""
        select
            {source_value} as source,
            entity_key,
            'added'::varchar as change_type,
            'bootstrap'::varchar as reason
        from entity
    """, affected_dir / 'entity_keys.parquet')
    _copy_query(con, f"""
        select
            {source_value} as source,
            relation_key,
            'added'::varchar as change_type,
            'bootstrap'::varchar as reason
        from entity_relation
    """, affected_dir / 'relation_keys.parquet')
    _copy_empty_like(con, 'entity_delete', delta_dir / 'entity_delete.parquet')
    _copy_empty_like(con, 'entity_relation_delete', delta_dir / 'entity_relation_delete.parquet')
    _copy_empty_like(con, 'entity_evidence_delete', delta_dir / 'entity_evidence_delete.parquet')
    _copy_empty_like(
        con,
        'relation_annotation_term_delete',
        delta_dir / 'relation_annotation_term_delete.parquet',
    )
    _copy_query(con, 'select * from entity', delta_dir / 'entity_upsert.parquet')
    _copy_query(con, 'select * from entity_relation', delta_dir / 'entity_relation_upsert.parquet')
    _copy_query(
        con,
        'select * from entity_relation_evidence',
        delta_dir / 'entity_relation_evidence_upsert.parquet',
    )
    _copy_query(con, 'select * from entity_evidence', delta_dir / 'entity_evidence_upsert.parquet')
    _copy_query(
        con,
        f"select * from read_parquet('{_sql_path(latest_dir / 'relation_annotation_term.parquet')}')",
        delta_dir / 'relation_annotation_term_upsert.parquet',
    )


def _write_incremental_run_artifacts(
    con: duckdb.DuckDBPyConnection,
    *,
    affected_dir: Path,
    delta_dir: Path,
    changed_source: str | None,
    latest_dir: Path,
) -> None:
    source_value = _nullable_sql_literal(changed_source)
    _copy_query(con, f"""
        select
            {source_value} as source,
            entity_key,
            null::varchar as change_type,
            'gold_delta'::varchar as reason
        from run_affected_entity_keys
    """, affected_dir / 'entity_keys.parquet')
    _copy_query(con, f"""
        select
            {source_value} as source,
            relation_key,
            null::varchar as change_type,
            'gold_delta'::varchar as reason
        from run_affected_relation_keys
    """, affected_dir / 'relation_keys.parquet')

    _copy_query(con, """
        select entity_id, entity_key
        from entity_key_map
        where entity_key in (select entity_key from run_affected_entity_keys)
    """, delta_dir / 'entity_delete.parquet')
    _copy_query(con, """
        select relation_id, relation_key
        from relation_key_map
        where relation_key in (select relation_key from run_affected_relation_keys)
    """, delta_dir / 'entity_relation_delete.parquet')
    _copy_query(con, """
        select null::varchar as source, entity_key
        from run_affected_entity_keys
    """, delta_dir / 'entity_evidence_delete.parquet')
    _copy_query(con, """
        select relation_id
        from relation_key_map
        where relation_key in (select relation_key from run_affected_relation_keys)
    """, delta_dir / 'relation_annotation_term_delete.parquet')

    _copy_query(con, """
        select *
        from entity
        where entity_key in (select entity_key from run_affected_entity_keys)
    """, delta_dir / 'entity_upsert.parquet')
    _copy_query(con, """
        select *
        from entity_relation
        where relation_key in (select relation_key from run_affected_relation_keys)
    """, delta_dir / 'entity_relation_upsert.parquet')
    _copy_query(con, """
        select *
        from entity_relation_evidence
        where relation_key in (select relation_key from run_affected_relation_keys)
    """, delta_dir / 'entity_relation_evidence_upsert.parquet')
    _copy_query(con, """
        select *
        from entity_evidence
        where entity_key in (select entity_key from run_affected_entity_keys)
    """, delta_dir / 'entity_evidence_upsert.parquet')
    annotation_path = latest_dir / 'relation_annotation_term.parquet'
    _copy_query(con, f"""
        select rat.*
        from read_parquet('{_sql_path(annotation_path)}') rat
        join relation_key_map rkm on rkm.relation_id = rat.relation_id
        where rkm.relation_key in (select relation_key from run_affected_relation_keys)
    """, delta_dir / 'relation_annotation_term_upsert.parquet')


def _copy_empty_like(
    con: duckdb.DuckDBPyConnection,
    schema_name: str,
    output_path: Path,
) -> None:
    schemas = {
        'entity_delete': 'select null::bigint as entity_id, null::varchar as entity_key where false',
        'entity_relation_delete': 'select null::bigint as relation_id, null::varchar as relation_key where false',
        'entity_evidence_delete': 'select null::varchar as source, null::varchar as entity_key where false',
        'relation_annotation_term_delete': 'select null::bigint as relation_id where false',
    }
    _copy_query(con, schemas[schema_name], output_path)


def _run_delta_counts(con: duckdb.DuckDBPyConnection, delta_dir: Path) -> dict[str, int]:
    counts: dict[str, int] = {}
    for path in sorted(delta_dir.glob('*.parquet')):
        counts[path.name] = int(con.execute(
            f"select count(*) from read_parquet('{_sql_path(path)}')"
        ).fetchone()[0])
    return counts


def _copy_query(
    con: duckdb.DuckDBPyConnection,
    query: str,
    output_path: Path,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    con.execute(
        f"copy ({query}) to '{_sql_path(output_path)}' (format parquet)"
    )


def _row_counts(con: duckdb.DuckDBPyConnection) -> dict[str, int]:
    tables = {
        'entity.parquet': 'entity',
        'entity_relation.parquet': 'entity_relation',
        'entity_relation_evidence.parquet': 'entity_relation_evidence',
        'entity_evidence.parquet': 'entity_evidence',
    }
    return {
        file_name: int(con.execute(f'select count(*) from {table}').fetchone()[0])
        for file_name, table in tables.items()
    }


def _append_build_manifest(
    output_dir: Path,
    *,
    mode: str,
    freeze_monthly: bool,
    row_counts: dict[str, int],
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


def _sql_path(path: Path) -> str:
    return str(path).replace("'", "''")


def _sql_literal(value: str) -> str:
    return value.replace("'", "''")


def _nullable_sql_literal(value: str | None) -> str:
    if value is None:
        return 'null::varchar'
    return f"'{_sql_literal(value)}'::varchar"


def _log(message: str) -> None:
    print(f'[combine:duckdb] {message}', flush=True)
