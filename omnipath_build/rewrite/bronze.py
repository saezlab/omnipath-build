from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import re
from typing import Any

import duckdb
import pyarrow as pa

from pypath.inputs_v2.raw_records import (
    METADATA_COLUMNS,
    PREPARSE_VERSION,
    RAW_RECORD_BUCKET_COUNT,
    RAW_RECORD_ID_BUCKET_STRIDE,
    RAW_RECORD_MIN_PART_SIZE_BYTES,
    RAW_RECORD_PART_COUNT,
    _clean_record,
    _effective_part_count,
    _record_hash,
    _stable_bucket,
    _stringify_if_unsupported,
)


@dataclass(frozen=True)
class BronzeRewriteSnapshot:
    source: str
    dataset: str
    snapshot_id: str
    source_state_path: Path


def source_state_path(data_root: str | Path, source: str) -> Path:
    return Path(data_root) / 'state' / 'sources' / f'{source}.duckdb'


def materialize_bronze_duckdb(
    *,
    records: Iterable[dict[str, Any]],
    source: str,
    dataset: str,
    data_root: str | Path = 'data_rewrite',
    batch_size: int = 50_000,
    download_fingerprint: dict[str, Any] | None = None,
    parser_contract: dict[str, Any] | None = None,
) -> BronzeRewriteSnapshot:
    """Materialize one raw dataset into the rewrite source DuckDB state."""
    data_root = Path(data_root)
    snapshot_id = datetime.now(UTC).strftime('%Y%m%dT%H%M%S%fZ')
    state_path = source_state_path(data_root, source)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    started_at = datetime.now(UTC).isoformat()

    con = duckdb.connect(str(state_path))
    try:
        _ensure_bronze_schema(con)
        previous_snapshot_id = _latest_snapshot_id(con, source, dataset)
        _create_staging_table(con)
        stats = _insert_staged_records(
            con,
            records=records,
            source=source,
            dataset=dataset,
            snapshot_id=snapshot_id,
            batch_size=batch_size,
        )
        input_bytes = int(stats.pop('_raw_record_input_bytes', 0))
        raw_record_part_count = _effective_part_count(
            input_bytes,
            max_part_count=RAW_RECORD_PART_COUNT,
            min_part_size_bytes=RAW_RECORD_MIN_PART_SIZE_BYTES,
        )
        _set_staged_parts(con, raw_record_part_count)
        id_stats = _assign_raw_record_ids(con, source=source, dataset=dataset)
        delta_stats = _write_change_state(
            con,
            source=source,
            dataset=dataset,
            snapshot_id=snapshot_id,
        )
        _replace_current_records(
            con,
            source=source,
            dataset=dataset,
            snapshot_id=snapshot_id,
        )
        _update_registry(
            con,
            source=source,
            dataset=dataset,
            snapshot_id=snapshot_id,
        )

        manifest = {
            'source': source,
            'dataset': dataset,
            'snapshot_id': snapshot_id,
            'previous_snapshot_id': previous_snapshot_id,
            'created_at': started_at,
            'completed_at': datetime.now(UTC).isoformat(),
            'preparse_version': PREPARSE_VERSION,
            'engine': 'duckdb_rewrite',
            'source_state_path': str(state_path),
            'typed_current_table': _typed_current_table_name(dataset),
            'bucket_algorithm': 'stable_u64_sha256_mod_v1',
            'raw_record_bucket_count': RAW_RECORD_BUCKET_COUNT,
            'raw_record_part_count': raw_record_part_count,
            'requested_raw_record_part_count': RAW_RECORD_PART_COUNT,
            'raw_record_min_part_size_bytes': RAW_RECORD_MIN_PART_SIZE_BYTES,
            'raw_record_partition_input_bytes': input_bytes,
            'download_fingerprint': download_fingerprint,
            'parser_contract': parser_contract,
            **stats,
            **id_stats,
            **delta_stats,
        }
        _record_snapshot(con, manifest)
    finally:
        con.close()

    return BronzeRewriteSnapshot(
        source=source,
        dataset=dataset,
        snapshot_id=snapshot_id,
        source_state_path=state_path,
    )


def _ensure_bronze_schema(con: duckdb.DuckDBPyConnection) -> None:
    con.execute("""
        create table if not exists bronze_dataset_snapshot (
            snapshot_id varchar,
            source varchar,
            dataset varchar,
            source_run_id varchar,
            parser_contract_hash varchar,
            file_fingerprint varchar,
            records_hash varchar,
            status varchar,
            created_at varchar,
            manifest_json varchar
        )
    """)
    con.execute("""
        create table if not exists bronze_raw_record_registry (
            source varchar,
            dataset varchar,
            raw_record_key varchar,
            raw_record_id bigint,
            raw_record_bucket bigint,
            first_seen_snapshot_id varchar,
            last_seen_snapshot_id varchar,
            is_current boolean
        )
    """)
    con.execute("""
        create table if not exists bronze_raw_record_current (
            source varchar,
            dataset varchar,
            raw_record_key varchar,
            raw_record_id bigint,
            raw_record_bucket bigint,
            raw_record_part bigint,
            snapshot_id varchar
        )
    """)
    con.execute("""
        create table if not exists bronze_raw_record_change (
            source_run_id varchar,
            source varchar,
            dataset varchar,
            raw_record_key varchar,
            raw_record_id bigint,
            raw_record_bucket bigint,
            raw_record_part bigint,
            change_type varchar
        )
    """)


def _latest_snapshot_id(
    con: duckdb.DuckDBPyConnection,
    source: str,
    dataset: str,
) -> str | None:
    row = con.execute(
        """
        select snapshot_id
        from bronze_dataset_snapshot
        where source = ? and dataset = ? and status = 'accepted'
        order by created_at desc
        limit 1
        """,
        [source, dataset],
    ).fetchone()
    return str(row[0]) if row and row[0] else None


def _create_staging_table(con: duckdb.DuckDBPyConnection) -> None:
    con.execute('drop table if exists staged_bronze_raw_record')
    con.execute('drop table if exists staged_bronze_raw_record_typed')
    con.execute("""
        create temporary table staged_bronze_raw_record (
            source varchar,
            dataset varchar,
            raw_record_key varchar,
            raw_record_id bigint,
            raw_record_bucket bigint,
            raw_record_part bigint,
            snapshot_id varchar
        )
    """)
    con.execute("""
        create temporary table staged_bronze_raw_record_typed (
            _source varchar,
            _dataset varchar,
            _raw_record_key varchar,
            _raw_record_id bigint,
            raw_record_bucket bigint,
            raw_record_part bigint,
            snapshot_id varchar
        )
    """)


def _insert_staged_records(
    con: duckdb.DuckDBPyConnection,
    *,
    records: Iterable[dict[str, Any]],
    source: str,
    dataset: str,
    snapshot_id: str,
    batch_size: int,
) -> dict[str, int]:
    batch: list[tuple[str, str, str, None, int, None, str]] = []
    typed_batch: list[dict[str, Any]] = []
    typed_schema_names: list[str] | None = None
    typed_schema: pa.Schema | None = None
    rows = 0
    raw_record_input_bytes = 0

    def flush() -> None:
        nonlocal typed_schema_names, typed_schema
        if not batch:
            return
        con.executemany(
            """
            insert into staged_bronze_raw_record
            values (?, ?, ?, ?, ?, ?, ?)
            """,
            batch,
        )
        if typed_schema_names is None:
            seen: dict[str, None] = {}
            for row in typed_batch:
                for name in row:
                    seen.setdefault(name, None)
            typed_schema_names = list(seen)
        normalized = [
            {
                name: _stringify_if_unsupported(row.get(name))
                for name in typed_schema_names
            }
            for row in typed_batch
        ]
        table = pa.Table.from_pylist(normalized)
        if typed_schema is None:
            typed_schema = table.schema
            con.register('bronze_typed_batch', table)
            try:
                con.execute('drop table staged_bronze_raw_record_typed')
                con.execute("""
                    create temporary table staged_bronze_raw_record_typed as
                    select * from bronze_typed_batch where false
                """)
                _normalize_typed_staging_schema(con)
            finally:
                con.unregister('bronze_typed_batch')
        else:
            table = table.cast(typed_schema, safe=False)
        con.register('bronze_typed_batch', table)
        try:
            con.execute("""
                insert into staged_bronze_raw_record_typed
                select * from bronze_typed_batch
            """)
        finally:
            con.unregister('bronze_typed_batch')
        batch.clear()
        typed_batch.clear()

    for record in records:
        clean = _clean_typed_record(record)
        key = _record_hash(clean)
        bucket = _stable_bucket(key, RAW_RECORD_BUCKET_COUNT)
        raw_record_input_bytes += _approx_record_size_bytes(clean)
        typed_row = {
            **clean,
            '_source': source,
            '_dataset': dataset,
            '_raw_record_key': key,
            '_raw_record_id': None,
            'raw_record_bucket': bucket,
            'raw_record_part': None,
            'snapshot_id': snapshot_id,
        }
        if typed_schema_names is not None:
            missing = set(typed_row) - set(typed_schema_names)
            if missing:
                raise ValueError(
                    'Raw parser emitted new columns after first batch: '
                    f'{sorted(missing)}. Use a stable parser schema.'
                )
        batch.append(
            (
                source,
                dataset,
                key,
                None,
                bucket,
                None,
                snapshot_id,
            )
        )
        typed_batch.append(typed_row)
        rows += 1
        if len(batch) >= batch_size:
            flush()
    flush()
    duplicate_key_count, duplicate_row_count = con.execute("""
        with counts as (
            select raw_record_key, count(*) as n
            from staged_bronze_raw_record
            group by raw_record_key
        )
        select
            count(*) filter (where n > 1),
            coalesce(sum(n - 1) filter (where n > 1), 0)
        from counts
    """).fetchone()
    return {
        'rows': rows,
        'duplicate_key_count': int(duplicate_key_count or 0),
        'duplicate_row_count': int(duplicate_row_count or 0),
        '_raw_record_input_bytes': raw_record_input_bytes,
    }


def _set_staged_parts(con: duckdb.DuckDBPyConnection, part_count: int) -> None:
    con.execute(
        f"""
        update staged_bronze_raw_record
        set raw_record_part = floor(
            raw_record_bucket * {int(part_count)} / {RAW_RECORD_BUCKET_COUNT}
        )::bigint
        """
    )
    con.execute(
        f"""
        update staged_bronze_raw_record_typed
        set raw_record_part = floor(
            raw_record_bucket * {int(part_count)} / {RAW_RECORD_BUCKET_COUNT}
        )::bigint
        """
    )


def _assign_raw_record_ids(
    con: duckdb.DuckDBPyConnection,
    *,
    source: str,
    dataset: str,
) -> dict[str, int]:
    con.execute(
        f"""
        create or replace temporary table staged_bronze_key as
        with
        new_keys as (
            select distinct
                raw_record_key,
                raw_record_bucket,
                raw_record_part
            from staged_bronze_raw_record
        ),
        old_map as (
            select
                raw_record_key,
                raw_record_id,
                raw_record_bucket
            from bronze_raw_record_registry
            where source = ? and dataset = ?
        ),
        max_old as (
            select
                raw_record_bucket,
                max(raw_record_id % {RAW_RECORD_ID_BUCKET_STRIDE}) as max_local_id
            from old_map
            group by raw_record_bucket
        ),
        added_map as (
            select
                n.raw_record_key,
                (
                    n.raw_record_bucket * {RAW_RECORD_ID_BUCKET_STRIDE}
                    + coalesce(m.max_local_id, 0)
                    + row_number() over (
                        partition by n.raw_record_bucket
                        order by n.raw_record_key
                    )
                )::bigint as raw_record_id,
                n.raw_record_bucket,
                n.raw_record_part
            from new_keys n
            left join old_map o using (raw_record_key)
            left join max_old m
              on m.raw_record_bucket = n.raw_record_bucket
            where o.raw_record_key is null
        )
        select
            n.raw_record_key,
            coalesce(o.raw_record_id, a.raw_record_id)::bigint as raw_record_id,
            n.raw_record_bucket as raw_record_bucket,
            n.raw_record_part as raw_record_part
        from new_keys n
        left join old_map o using (raw_record_key)
        left join added_map a using (raw_record_key)
        """,
        [source, dataset],
    )
    con.execute("""
        update staged_bronze_raw_record as r
        set raw_record_id = k.raw_record_id
        from staged_bronze_key k
        where r.raw_record_key = k.raw_record_key
    """)
    con.execute("""
        update staged_bronze_raw_record_typed as r
        set
            _raw_record_id = k.raw_record_id,
            raw_record_part = k.raw_record_part
        from staged_bronze_key k
        where r._raw_record_key = k.raw_record_key
    """)
    min_id, max_id, distinct_ids = con.execute("""
        select
            min(raw_record_id),
            max(raw_record_id),
            count(distinct raw_record_id)
        from staged_bronze_raw_record
    """).fetchone()
    return {
        'min_raw_record_id': int(min_id or 0),
        'max_raw_record_id': int(max_id or 0),
        'distinct_raw_record_ids': int(distinct_ids or 0),
    }


def _write_change_state(
    con: duckdb.DuckDBPyConnection,
    *,
    source: str,
    dataset: str,
    snapshot_id: str,
) -> dict[str, dict[str, int]]:
    con.execute(
        """
        delete from bronze_raw_record_change
        where source_run_id = ? and source = ? and dataset = ?
        """,
        [snapshot_id, source, dataset],
    )
    con.execute(
        """
        insert into bronze_raw_record_change
        with
        old_keys as (
            select
                raw_record_key,
                raw_record_id,
                raw_record_bucket,
                raw_record_part
            from bronze_raw_record_current
            where source = ? and dataset = ?
            group by all
        ),
        new_keys as (
            select
                raw_record_key,
                raw_record_id,
                raw_record_bucket,
                raw_record_part
            from staged_bronze_raw_record
            group by all
        )
        select
            ? as source_run_id,
            ? as source,
            ? as dataset,
            raw_record_key,
            raw_record_id,
            raw_record_bucket,
            raw_record_part,
            'added' as change_type
        from new_keys
        where raw_record_key not in (select raw_record_key from old_keys)
        union all
        select
            ? as source_run_id,
            ? as source,
            ? as dataset,
            raw_record_key,
            raw_record_id,
            raw_record_bucket,
            raw_record_part,
            'removed' as change_type
        from old_keys
        where raw_record_key not in (select raw_record_key from new_keys)
        """,
        [
            source,
            dataset,
            snapshot_id,
            source,
            dataset,
            snapshot_id,
            source,
            dataset,
        ],
    )
    rows = con.execute(
        """
        select change_type, count(distinct raw_record_key)
        from bronze_raw_record_change
        where source_run_id = ? and source = ? and dataset = ?
        group by change_type
        order by change_type
        """,
        [snapshot_id, source, dataset],
    ).fetchall()
    return {'delta_keys_by_type': {str(kind): int(count) for kind, count in rows}}


def _replace_current_records(
    con: duckdb.DuckDBPyConnection,
    *,
    source: str,
    dataset: str,
    snapshot_id: str,
) -> None:
    con.execute(
        'delete from bronze_raw_record_current where source = ? and dataset = ?',
        [source, dataset],
    )
    con.execute(
        """
        insert into bronze_raw_record_current
        select
            source,
            dataset,
            raw_record_key,
            raw_record_id,
            raw_record_bucket,
            raw_record_part,
            ? as snapshot_id
        from staged_bronze_raw_record
        """,
        [snapshot_id],
    )
    typed_table = _quote_identifier(_typed_current_table_name(dataset))
    con.execute(f'drop table if exists {typed_table}')
    con.execute(f"""
        create table {typed_table} as
        select *
        from staged_bronze_raw_record_typed
    """)


def _update_registry(
    con: duckdb.DuckDBPyConnection,
    *,
    source: str,
    dataset: str,
    snapshot_id: str,
) -> None:
    con.execute(
        """
        update bronze_raw_record_registry
        set is_current = false
        where source = ? and dataset = ?
        """,
        [source, dataset],
    )
    con.execute(
        """
        insert into bronze_raw_record_registry
        select
            ? as source,
            ? as dataset,
            k.raw_record_key,
            k.raw_record_id,
            k.raw_record_bucket,
            ? as first_seen_snapshot_id,
            ? as last_seen_snapshot_id,
            true as is_current
        from staged_bronze_key k
        left join bronze_raw_record_registry r
          on r.source = ?
         and r.dataset = ?
         and r.raw_record_key = k.raw_record_key
        where r.raw_record_key is null
        """,
        [source, dataset, snapshot_id, snapshot_id, source, dataset],
    )
    con.execute(
        """
        update bronze_raw_record_registry as r
        set
            last_seen_snapshot_id = ?,
            is_current = true
        from staged_bronze_key k
        where r.source = ?
          and r.dataset = ?
          and r.raw_record_key = k.raw_record_key
        """,
        [snapshot_id, source, dataset],
    )


def _record_snapshot(
    con: duckdb.DuckDBPyConnection,
    manifest: dict[str, Any],
) -> None:
    manifest_json = json.dumps(manifest, sort_keys=True, separators=(',', ':'))
    con.execute(
        """
        insert into bronze_dataset_snapshot
        values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            manifest['snapshot_id'],
            manifest['source'],
            manifest['dataset'],
            manifest['snapshot_id'],
            None,
            json.dumps(manifest.get('download_fingerprint'), sort_keys=True),
            None,
            'accepted',
            manifest['created_at'],
            manifest_json,
        ],
    )


def _clean_typed_record(record: dict[str, Any]) -> dict[str, Any]:
    clean = _clean_record(record)
    reserved = set(METADATA_COLUMNS) | {'snapshot_id'}
    out: dict[str, Any] = {}
    for key, value in clean.items():
        name = key if key not in reserved else f'raw{key}'
        out[name] = value
    return out


def _approx_record_size_bytes(record: dict[str, Any]) -> int:
    size = 0
    for key, value in record.items():
        size += len(str(key).encode('utf-8', errors='surrogatepass'))
        if value is None:
            continue
        if isinstance(value, bytes):
            size += len(value)
        else:
            size += len(str(value).encode('utf-8', errors='surrogatepass'))
    return size


def _normalize_typed_staging_schema(con: duckdb.DuckDBPyConnection) -> None:
    for column, data_type in (
        ('_source', 'varchar'),
        ('_dataset', 'varchar'),
        ('_raw_record_key', 'varchar'),
        ('_raw_record_id', 'bigint'),
        ('raw_record_bucket', 'bigint'),
        ('raw_record_part', 'bigint'),
        ('snapshot_id', 'varchar'),
    ):
        con.execute(
            f'alter table staged_bronze_raw_record_typed '
            f'alter column {_quote_identifier(column)} type {data_type}'
        )


def _typed_current_table_name(dataset: str) -> str:
    slug = re.sub(r'[^0-9A-Za-z_]+', '_', dataset).strip('_').lower()
    if not slug:
        slug = 'dataset'
    if slug[0].isdigit():
        slug = f'dataset_{slug}'
    suffix = hashlib.sha1(dataset.encode('utf-8')).hexdigest()[:8]
    return f'bronze_raw_record_current__{slug}__{suffix}'


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'
