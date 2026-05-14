from __future__ import annotations

from io import StringIO
import csv
from pathlib import Path
from dataclasses import dataclass
from collections.abc import Iterable, Iterator

import pyarrow as pa
from psycopg2 import sql
import pyarrow.dataset as ds
import psycopg2.extensions

@dataclass(frozen=True)
class SourceSnapshotSyncStats:
    """Summary of source-row lineage updates from a raw snapshot delta."""

    current_rows: int = 0
    removed_rows: int = 0


def sync_source_snapshot(
    conn: psycopg2.extensions.connection,
    *,
    schema: str,
    source: str,
    dataset: str,
    snapshot_id: str,
    records_path: str | Path,
    delta_path: str | Path,
) -> SourceSnapshotSyncStats:
    """Apply raw snapshot membership to row-scoped evidence tables.

    The preparse layer owns raw-record hash comparison. This function receives
    the resulting stable row IDs: all current row IDs for lineage updates and
    removed row IDs for evidence deletion.
    """

    with conn.cursor() as cur:
        _create_staging_tables(cur)
        current_rows = _load_current_rows(
            cur,
            source=source,
            dataset=dataset,
            snapshot_id=snapshot_id,
            records_path=Path(records_path),
        )
        removed_rows = _load_removed_rows(
            cur,
            source=source,
            dataset=dataset,
            delta_path=Path(delta_path),
        )
        if removed_rows:
            _delete_removed_source_row_evidence(cur, schema=schema)
        _upsert_current_source_rows(cur, schema=schema)
        _update_current_evidence_snapshot(cur, schema=schema)

    conn.commit()
    return SourceSnapshotSyncStats(
        current_rows=current_rows,
        removed_rows=removed_rows,
    )


def _create_staging_tables(cur: psycopg2.extensions.cursor) -> None:
    cur.execute('DROP TABLE IF EXISTS stg_current_source_row')
    cur.execute('DROP TABLE IF EXISTS stg_removed_source_row')
    cur.execute(
        """
        CREATE TEMP TABLE stg_current_source_row (
          source text NOT NULL,
          dataset text NOT NULL,
          row_id bigint NOT NULL,
          snapshot_id text
        ) ON COMMIT DROP
        """
    )
    cur.execute(
        """
        CREATE TEMP TABLE stg_removed_source_row (
          source text NOT NULL,
          dataset text NOT NULL,
          row_id bigint NOT NULL
        ) ON COMMIT DROP
        """
    )


def _load_current_rows(
    cur: psycopg2.extensions.cursor,
    *,
    source: str,
    dataset: str,
    snapshot_id: str,
    records_path: Path,
) -> int:
    return _copy_record_ids(
        cur,
        table='stg_current_source_row',
        rows=_iter_current_row_ids(records_path),
        source=source,
        dataset=dataset,
        snapshot_id=snapshot_id,
    )


def _load_removed_rows(
    cur: psycopg2.extensions.cursor,
    *,
    source: str,
    dataset: str,
    delta_path: Path,
) -> int:
    return _copy_record_ids(
        cur,
        table='stg_removed_source_row',
        rows=_iter_removed_row_ids(delta_path),
        source=source,
        dataset=dataset,
        snapshot_id=None,
    )


def _iter_current_row_ids(records_path: Path) -> Iterator[int]:
    if not records_path.exists():
        return
    dataset = ds.dataset(records_path, format='parquet')
    for batch in dataset.to_batches(
        columns=['_raw_record_id'],
        batch_size=100_000,
    ):
        for value in batch.column('_raw_record_id').to_pylist():
            if value is not None:
                yield int(value)


def _iter_removed_row_ids(delta_path: Path) -> Iterator[int]:
    if not delta_path.exists():
        return
    dataset = ds.dataset(delta_path, format='parquet')
    for batch in dataset.to_batches(
        columns=['_raw_record_id', '_change_type'],
        batch_size=100_000,
    ):
        table = pa.Table.from_batches([batch])
        for row in table.to_pylist():
            if row.get('_change_type') == 'removed':
                value = row.get('_raw_record_id')
                if value is not None:
                    yield int(value)


def _copy_record_ids(
    cur: psycopg2.extensions.cursor,
    *,
    table: str,
    rows: Iterable[int],
    source: str,
    dataset: str,
    snapshot_id: str | None,
) -> int:
    buffer = StringIO()
    writer = csv.writer(buffer)
    count = 0
    for row_id in rows:
        if snapshot_id is None:
            writer.writerow([source, dataset, row_id])
        else:
            writer.writerow([source, dataset, row_id, snapshot_id])
        count += 1
    if count == 0:
        return 0
    buffer.seek(0)
    columns = (
        '(source, dataset, row_id)'
        if snapshot_id is None
        else '(source, dataset, row_id, snapshot_id)'
    )
    cur.copy_expert(
        f'COPY {table} {columns} FROM STDIN WITH (FORMAT CSV)',
        buffer,
    )
    return count


def _delete_removed_source_row_evidence(
    cur: psycopg2.extensions.cursor,
    *,
    schema: str,
) -> None:
    schema_id = sql.Identifier(schema)

    cur.execute('DROP TABLE IF EXISTS _removed_relation')
    cur.execute(
        sql.SQL(
            """
            CREATE TEMP TABLE _removed_relation ON COMMIT DROP AS
            SELECT DISTINCT rer.relation_id
            FROM {}.relation_evidence_relation rer
            JOIN {}.relation_evidence re
              ON re.relation_evidence_id = rer.relation_evidence_id
            JOIN stg_removed_source_row rm
              ON rm.source = re.source
             AND rm.dataset = re.dataset
             AND rm.row_id = re.row_id
            """
        ).format(schema_id, schema_id)
    )

    cur.execute('DROP TABLE IF EXISTS _removed_entity')
    cur.execute(
        sql.SQL(
            """
            CREATE TEMP TABLE _removed_entity ON COMMIT DROP AS
            SELECT DISTINCT r.entity_id
            FROM {}.entity_evidence_resolution r
            JOIN {}.entity_evidence ee
              ON ee.entity_evidence_id = r.entity_evidence_id
            JOIN stg_removed_source_row rm
              ON rm.source = ee.source
             AND rm.dataset = ee.dataset
             AND rm.row_id = ee.row_id
            WHERE r.entity_id IS NOT NULL
            UNION
            SELECT DISTINCT re.subject_entity_id AS entity_id
            FROM {}.relation_evidence re
            JOIN stg_removed_source_row rm
              ON rm.source = re.source
             AND rm.dataset = re.dataset
             AND rm.row_id = re.row_id
            WHERE re.subject_entity_id IS NOT NULL
            UNION
            SELECT DISTINCT re.object_entity_id AS entity_id
            FROM {}.relation_evidence re
            JOIN stg_removed_source_row rm
              ON rm.source = re.source
             AND rm.dataset = re.dataset
             AND rm.row_id = re.row_id
            WHERE re.object_entity_id IS NOT NULL
            """
        ).format(schema_id, schema_id, schema_id, schema_id)
    )

    cur.execute('DROP TABLE IF EXISTS _removed_identifier')
    cur.execute(
        sql.SQL(
            """
            CREATE TEMP TABLE _removed_identifier ON COMMIT DROP AS
            SELECT DISTINCT eei.identifier_id
            FROM {}.entity_evidence_identifier eei
            JOIN {}.entity_evidence ee
              ON ee.entity_evidence_id = eei.entity_evidence_id
            JOIN stg_removed_source_row rm
              ON rm.source = ee.source
             AND rm.dataset = ee.dataset
             AND rm.row_id = ee.row_id
            """
        ).format(schema_id, schema_id)
    )

    cur.execute(
        sql.SQL(
            """
            DELETE FROM {}.annotation a
            USING {}.relation_evidence re, stg_removed_source_row rm
            WHERE a.relation_evidence_id = re.relation_evidence_id
              AND rm.source = re.source
              AND rm.dataset = re.dataset
              AND rm.row_id = re.row_id
            """
        ).format(schema_id, schema_id)
    )
    cur.execute(
        sql.SQL(
            """
            DELETE FROM {}.annotation a
            USING {}.entity_evidence ee, stg_removed_source_row rm
            WHERE a.entity_evidence_id = ee.entity_evidence_id
              AND rm.source = ee.source
              AND rm.dataset = ee.dataset
              AND rm.row_id = ee.row_id
            """
        ).format(schema_id, schema_id)
    )
    cur.execute(
        sql.SQL(
            """
            DELETE FROM {}.relation_evidence re
            USING stg_removed_source_row rm
            WHERE rm.source = re.source
              AND rm.dataset = re.dataset
              AND rm.row_id = re.row_id
            """
        ).format(schema_id)
    )
    cur.execute(
        sql.SQL(
            """
            DELETE FROM {}.entity_evidence_identifier eei
            USING {}.entity_evidence ee, stg_removed_source_row rm
            WHERE eei.entity_evidence_id = ee.entity_evidence_id
              AND rm.source = ee.source
              AND rm.dataset = ee.dataset
              AND rm.row_id = ee.row_id
            """
        ).format(schema_id, schema_id)
    )
    cur.execute(
        sql.SQL(
            """
            DELETE FROM {}.identifier i
            USING _removed_identifier ri
            WHERE i.identifier_id = ri.identifier_id
              AND NOT EXISTS (
                SELECT 1
                FROM {}.entity_evidence_identifier eei
                WHERE eei.identifier_id = i.identifier_id
              )
            """
        ).format(schema_id, schema_id)
    )
    cur.execute(
        sql.SQL(
            """
            DELETE FROM {}.entity_evidence ee
            USING stg_removed_source_row rm
            WHERE rm.source = ee.source
              AND rm.dataset = ee.dataset
              AND rm.row_id = ee.row_id
            """
        ).format(schema_id)
    )
    cur.execute(
        sql.SQL(
            """
            DELETE FROM {}.source_row sr
            USING stg_removed_source_row rm
            WHERE rm.source = sr.source
              AND rm.dataset = sr.dataset
              AND rm.row_id = sr.row_id
            """
        ).format(schema_id)
    )
    cur.execute(
        sql.SQL(
            """
            DELETE FROM {}.relation r
            USING _removed_relation rr
            WHERE r.relation_id = rr.relation_id
              AND NOT EXISTS (
                SELECT 1
                FROM {}.relation_evidence_relation rer
                WHERE rer.relation_id = r.relation_id
              )
            """
        ).format(schema_id, schema_id)
    )
    cur.execute(
        sql.SQL(
            """
            DELETE FROM {}.entity e
            USING _removed_entity re
            WHERE e.entity_id = re.entity_id
              AND NOT EXISTS (
                SELECT 1
                FROM {}.entity_evidence_resolution r
                WHERE r.entity_id = e.entity_id
              )
              AND NOT EXISTS (
                SELECT 1
                FROM {}.relation rel
                WHERE rel.subject_entity_id = e.entity_id
                   OR rel.object_entity_id = e.entity_id
              )
              AND NOT EXISTS (
                SELECT 1
                FROM {}.annotation a
                WHERE a.entity_id = e.entity_id
              )
            """
        ).format(schema_id, schema_id, schema_id, schema_id)
    )


def _upsert_current_source_rows(
    cur: psycopg2.extensions.cursor,
    *,
    schema: str,
) -> None:
    schema_id = sql.Identifier(schema)
    cur.execute(
        sql.SQL(
            """
            INSERT INTO {}.source_row (source, dataset, row_id, snapshot_id)
            SELECT DISTINCT source, dataset, row_id, snapshot_id
            FROM stg_current_source_row
            ON CONFLICT (source, dataset, row_id)
            DO UPDATE SET snapshot_id = EXCLUDED.snapshot_id
            """
        ).format(schema_id)
    )


def _update_current_evidence_snapshot(
    cur: psycopg2.extensions.cursor,
    *,
    schema: str,
) -> None:
    schema_id = sql.Identifier(schema)
    cur.execute(
        sql.SQL(
            """
            UPDATE {}.entity_evidence ee
            SET snapshot_id = current.snapshot_id
            FROM stg_current_source_row current
            WHERE current.source = ee.source
              AND current.dataset = ee.dataset
              AND current.row_id = ee.row_id
              AND ee.snapshot_id IS DISTINCT FROM current.snapshot_id
            """
        ).format(schema_id)
    )
    cur.execute(
        sql.SQL(
            """
            UPDATE {}.relation_evidence re
            SET snapshot_id = current.snapshot_id
            FROM stg_current_source_row current
            WHERE current.source = re.source
              AND current.dataset = re.dataset
              AND current.row_id = re.row_id
              AND re.snapshot_id IS DISTINCT FROM current.snapshot_id
            """
        ).format(schema_id)
    )
