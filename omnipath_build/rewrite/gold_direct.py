from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import duckdb
import pyarrow as pa

from omnipath_build.rewrite.gold_config import GoldPartitionConfig
from omnipath_build.rewrite.gold_sql import build_gold_temp_tables_sql as _build_gold_temp_tables_sql

ATTR_TYPE = pa.list_(pa.struct([
    pa.field('term', pa.string()),
    pa.field('value', pa.string()),
    pa.field('unit', pa.string()),
]))
SOURCE_IDENTIFIER_TYPE = pa.list_(pa.struct([
    pa.field('type', pa.string()),
    pa.field('value', pa.string()),
]))
GOLD_IDENTIFIER_TYPE = pa.list_(pa.struct([
    pa.field('identifier', pa.string()),
    pa.field('identifier_type', pa.string()),
]))
STRING_LIST_TYPE = pa.list_(pa.string())

TABLE_SCHEMAS: dict[str, pa.Schema] = {
    'gold_entity': pa.schema([
        pa.field('entity_pk', pa.int64()),
        pa.field('entity_key', pa.string()),
        pa.field('entity_bucket', pa.int64()),
        pa.field('entity_part', pa.int64()),
        pa.field('canonical_identifier', pa.string()),
        pa.field('canonical_identifier_type', pa.string()),
        pa.field('identifiers', GOLD_IDENTIFIER_TYPE),
        pa.field('entity_type', pa.string()),
        pa.field('taxonomy_id', pa.string()),
        pa.field('entity_attributes', ATTR_TYPE),
        pa.field('sources', STRING_LIST_TYPE),
    ]),
    'gold_entity_evidence': pa.schema([
        pa.field('entity_pk', pa.int64()),
        pa.field('source', pa.string()),
        pa.field('entity_key', pa.string()),
        pa.field('canonical_identifier', pa.string()),
        pa.field('canonical_identifier_type', pa.string()),
        pa.field('raw_record_id', pa.string()),
        pa.field('occurrence_id', pa.string()),
        pa.field('fingerprint', pa.string()),
        pa.field('entity_type', pa.string()),
        pa.field('taxonomy_id', pa.string()),
        pa.field('identifiers', GOLD_IDENTIFIER_TYPE),
        pa.field('entity_attributes', ATTR_TYPE),
        pa.field('evidence', ATTR_TYPE),
        pa.field('entity_bucket', pa.int64()),
        pa.field('entity_part', pa.int64()),
        pa.field('occ_bucket', pa.int64()),
        pa.field('occ_part', pa.int64()),
    ]),
    'gold_entity_map': pa.schema([
        pa.field('_fingerprint', pa.string()),
        pa.field('entity_pk', pa.int64()),
        pa.field('entity_key', pa.string()),
        pa.field('fingerprint_bucket', pa.int64()),
        pa.field('fingerprint_part', pa.int64()),
    ]),
    'gold_entity_occurrence_map': pa.schema([
        pa.field('occurrence_id', pa.string()),
        pa.field('_fingerprint', pa.string()),
        pa.field('entity_pk', pa.int64()),
        pa.field('entity_key', pa.string()),
        pa.field('occ_bucket', pa.int64()),
        pa.field('occ_part', pa.int64()),
    ]),
    'gold_entity_relation': pa.schema([
        pa.field('relation_pk', pa.int64()),
        pa.field('relation_key', pa.string()),
        pa.field('subject_entity_pk', pa.int64()),
        pa.field('subject_entity_key', pa.string()),
        pa.field('predicate', pa.string()),
        pa.field('object_entity_pk', pa.int64()),
        pa.field('object_entity_key', pa.string()),
        pa.field('relation_category', pa.string()),
        pa.field('evidence_count', pa.int64()),
        pa.field('sources', STRING_LIST_TYPE),
        pa.field('relation_bucket', pa.int64()),
        pa.field('relation_part', pa.int64()),
    ]),
    'gold_entity_relation_evidence': pa.schema([
        pa.field('relation_evidence_pk', pa.int64()),
        pa.field('relation_pk', pa.int64()),
        pa.field('relation_key', pa.string()),
        pa.field('source', pa.string()),
        pa.field('raw_record_id', pa.string()),
        pa.field('record_attributes', ATTR_TYPE),
        pa.field('subject_attributes', ATTR_TYPE),
        pa.field('object_attributes', ATTR_TYPE),
        pa.field('evidence', ATTR_TYPE),
        pa.field('subject_entity_key', pa.string()),
        pa.field('predicate', pa.string()),
        pa.field('object_entity_key', pa.string()),
        pa.field('relation_category', pa.string()),
        pa.field('relation_bucket', pa.int64()),
        pa.field('relation_part', pa.int64()),
    ]),
    'gold_entity_key_registry': pa.schema([
        pa.field('entity_key', pa.string()),
        pa.field('entity_pk', pa.int64()),
        pa.field('entity_bucket', pa.int64()),
        pa.field('entity_part', pa.int64()),
    ]),
    'gold_relation_key_registry': pa.schema([
        pa.field('relation_key', pa.string()),
        pa.field('relation_pk', pa.int64()),
        pa.field('relation_bucket', pa.int64()),
        pa.field('relation_part', pa.int64()),
    ]),
}

GOLD_TABLES = tuple(TABLE_SCHEMAS)
GOLD_TEMP_TABLES = {
    'gold_entity': '_gold_entity_out',
    'gold_entity_evidence': '_gold_entity_evidence_out',
    'gold_entity_map': '_gold_entity_map_out',
    'gold_entity_occurrence_map': '_gold_entity_occurrence_map_out',
    'gold_entity_relation': '_relation_out',
    'gold_entity_relation_evidence': '_relation_evidence_out',
    'gold_entity_key_registry': '_entity_registry_out',
    'gold_relation_key_registry': '_relation_registry_out',
}


@dataclass(frozen=True)
class DirectGoldBuildResult:
    changed: bool
    rows_by_table: dict[str, int]


@dataclass(frozen=True)
class SourceGoldScope:
    raw_record_ids: set[int]
    occurrence_ids: set[str]

    @property
    def is_empty(self) -> bool:
        return not self.raw_record_ids and not self.occurrence_ids


def build_gold_source_duckdb(
    con: duckdb.DuckDBPyConnection,
    *,
    source: str,
    mapping_dir: str | Path,
    cfg: GoldPartitionConfig,
) -> DirectGoldBuildResult:
    has_current_gold = _current_gold_state_exists(con)
    scope = _load_source_scope(con, source=source, bootstrap=not has_current_gold)
    if scope.is_empty:
        _clear_scope_tables(con)
        return DirectGoldBuildResult(False, _current_gold_row_counts(con))

    # A changed source run invalidates source-local canonicalization and relation
    # numbering. Rebuilding that source in DuckDB keeps the new rewrite path out
    # of the old Parquet->Polars->DuckDB loop while preserving deterministic rows.
    full_scope = _load_all_source_scope(con, source=source)
    _build_gold_temp_tables(con=con, source=source, mapping_dir=Path(mapping_dir), cfg=cfg, scope=full_scope)
    changed = not has_current_gold or _gold_temp_tables_changed(con)
    if changed:
        _replace_gold_tables_from_temp(con)
        _refresh_scope_tables_from_gold(con)
        rows_by_table = _current_gold_row_counts(con)
    else:
        _clear_scope_tables(con)
        rows_by_table = _read_gold_counts(con)
    return DirectGoldBuildResult(changed=changed, rows_by_table=rows_by_table)


def _build_gold_temp_tables(
    *,
    con: duckdb.DuckDBPyConnection,
    source: str,
    mapping_dir: Path,
    cfg: GoldPartitionConfig,
    scope: SourceGoldScope,
) -> None:
    _build_gold_temp_tables_sql(con=con, source=source, mapping_dir=mapping_dir, cfg=cfg, scope=scope)




def _replace_gold_tables_from_temp(con: duckdb.DuckDBPyConnection) -> None:
    for table in GOLD_TABLES:
        temp = GOLD_TEMP_TABLES[table]
        con.execute(f'drop table if exists {_quote_identifier(table)}')
        columns = ', '.join(_quote_identifier(name) for name in TABLE_SCHEMAS[table].names)
        con.execute(
            f'create table {_quote_identifier(table)} as '
            f'select {columns} from {_quote_identifier(temp)}'
        )


def _gold_temp_tables_changed(con: duckdb.DuckDBPyConnection) -> bool:
    for table in GOLD_TABLES:
        if not _table_exists(con, table):
            return True
        temp = GOLD_TEMP_TABLES[table]
        columns = ', '.join(_quote_identifier(name) for name in TABLE_SCHEMAS[table].names)
        diff_count = int(con.execute(f'''
            select count(*) from (
                (select {columns} from {_quote_identifier(table)}
                 except all
                 select {columns} from {_quote_identifier(temp)})
                union all
                (select {columns} from {_quote_identifier(temp)}
                 except all
                 select {columns} from {_quote_identifier(table)})
            )
        ''').fetchone()[0] or 0)
        if diff_count:
            return True
    return False



def _read_gold_counts(con: duckdb.DuckDBPyConnection) -> dict[str, int]:
    return _current_gold_row_counts(con)


def _current_gold_row_counts(con: duckdb.DuckDBPyConnection) -> dict[str, int]:
    return {
        'entity': _table_count(con, 'gold_entity'),
        'entity_evidence': _table_count(con, 'gold_entity_evidence'),
        'entity_map': _table_count(con, 'gold_entity_map'),
        'entity_occurrence_map': _table_count(con, 'gold_entity_occurrence_map'),
        'entity_relation': _table_count(con, 'gold_entity_relation'),
        'entity_relation_evidence': _table_count(con, 'gold_entity_relation_evidence'),
    }


def _load_source_scope(con: duckdb.DuckDBPyConnection, *, source: str, bootstrap: bool) -> SourceGoldScope:
    raw_record_ids: set[int] = set()
    occurrence_ids: set[str] = set()
    if _table_exists(con, 'source_run_scope_raw_record'):
        rows = con.execute('select distinct raw_record_id from source_run_scope_raw_record where raw_record_id is not null').fetchall()
        raw_record_ids.update(int(row[0]) for row in rows)
    if _table_exists(con, 'source_run_scope_occurrence'):
        rows = con.execute('select distinct raw_record_id, occurrence_id from source_run_scope_occurrence').fetchall()
        raw_record_ids.update(int(row[0]) for row in rows if row[0] is not None)
        occurrence_ids.update(str(row[1]) for row in rows if row[1] is not None)
    if bootstrap and not raw_record_ids:
        return _load_all_source_scope(con, source=source)
    return SourceGoldScope(raw_record_ids=raw_record_ids, occurrence_ids=occurrence_ids)


def _load_all_source_scope(con: duckdb.DuckDBPyConnection, *, source: str) -> SourceGoldScope:
    rows = con.execute('''
        select distinct _raw_record_id, occurrence_id
        from silver_entity_occurrence
        where source = ?
          and _raw_record_id is not null
    ''', [source]).fetchall()
    return SourceGoldScope(
        raw_record_ids={int(row[0]) for row in rows if row[0] is not None},
        occurrence_ids={str(row[1]) for row in rows if row[1] is not None},
    )


def _current_gold_state_exists(con: duckdb.DuckDBPyConnection) -> bool:
    return all(_table_exists(con, table) for table in GOLD_TABLES)


def _table_exists(con: duckdb.DuckDBPyConnection, table: str) -> bool:
    return bool(con.execute(
        """
        select count(*)
        from information_schema.tables
        where table_schema = 'main'
          and table_name = ?
        """,
        [table],
    ).fetchone()[0])


def _table_count(con: duckdb.DuckDBPyConnection, table: str) -> int:
    if not _table_exists(con, table):
        return 0
    return int(con.execute(f'select count(*) from {_quote_identifier(table)}').fetchone()[0] or 0)



def _refresh_scope_tables_from_gold(con: duckdb.DuckDBPyConnection) -> None:
    con.execute('drop table if exists source_run_scope_entity')
    con.execute('''
        create table source_run_scope_entity as
        with scope_run as (
            select max(source_run_id) as source_run_id, max(source) as source
            from (
                select source_run_id, source from source_run_scope_raw_record
                union all
                select source_run_id, source from source_run_scope_occurrence
            )
        )
        select
            coalesce(nullif(scope_run.source_run_id, ''), 'rewrite') as source_run_id,
            scope_run.source,
            e.entity_key,
            e.entity_bucket,
            e.entity_part,
            'source_gold_rebuild'::varchar as reason
        from gold_entity e
        cross join scope_run
    ''')
    con.execute('drop table if exists source_run_scope_relation')
    con.execute('''
        create table source_run_scope_relation as
        with scope_run as (
            select max(source_run_id) as source_run_id, max(source) as source
            from (
                select source_run_id, source from source_run_scope_raw_record
                union all
                select source_run_id, source from source_run_scope_occurrence
            )
        )
        select
            coalesce(nullif(scope_run.source_run_id, ''), 'rewrite') as source_run_id,
            scope_run.source,
            r.relation_key,
            r.relation_bucket,
            r.relation_part,
            'source_gold_rebuild'::varchar as reason
        from gold_entity_relation r
        cross join scope_run
    ''')


def _clear_scope_tables(con: duckdb.DuckDBPyConnection) -> None:
    con.execute('drop table if exists source_run_scope_entity')
    con.execute('''
        create table source_run_scope_entity(
            source_run_id varchar,
            source varchar,
            entity_key varchar,
            entity_bucket bigint,
            entity_part bigint,
            reason varchar
        )
    ''')
    con.execute('drop table if exists source_run_scope_relation')
    con.execute('''
        create table source_run_scope_relation(
            source_run_id varchar,
            source varchar,
            relation_key varchar,
            relation_bucket bigint,
            relation_part bigint,
            reason varchar
        )
    ''')



def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'
