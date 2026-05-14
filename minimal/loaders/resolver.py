from __future__ import annotations

from io import StringIO
import csv
import time
import re
from pathlib import Path
from dataclasses import dataclass

import pyarrow as pa
from psycopg2 import sql
import pyarrow.parquet as pq
import psycopg2.extensions

PROTEIN_TABLE = 'resolver_protein_identifier_lookup'
CHEMICAL_TABLE = 'resolver_chemical_identifier_lookup'

PROTEIN_COLUMNS = (
    'source',
    'key_type',
    'key_value',
    'taxonomy_id',
    'primary_uniprot',
    'mapping_type',
)
CHEMICAL_COLUMNS = (
    'source',
    'key_type',
    'key_value',
    'standard_inchi_key',
    'standard_inchi',
)


@dataclass(frozen=True)
class ResolverLoadStats:
    """Loaded resolver row counts."""

    protein_rows: int = 0
    chemical_rows: int = 0


def load_resolver_tables(
    conn: psycopg2.extensions.connection,
    *,
    schema: str = 'public',
    mapping_dir: str | Path = 'data',
    batch_size: int = 100_000,
    drop_existing: bool = True,
    indexes: bool = True,
) -> ResolverLoadStats:
    """Load resolver parquet lookup tables into PostgreSQL."""

    mapping_dir = Path(mapping_dir)
    protein_path = (
        mapping_dir / 'proteins' / 'protein_identifier_lookup.parquet'
    )
    chemical_path = (
        mapping_dir / 'chemicals' / 'chemical_identifier_lookup.parquet'
    )

    with conn.cursor() as cur:
        _ensure_schema(cur, schema)
        _ensure_tables(cur, schema=schema, drop_existing=drop_existing)
    conn.commit()

    protein_rows = 0
    chemical_rows = 0
    if protein_path.exists():
        protein_rows = _copy_parquet(
            conn,
            schema=schema,
            table=PROTEIN_TABLE,
            columns=PROTEIN_COLUMNS,
            path=protein_path,
            batch_size=batch_size,
            label='resolver.proteins',
        )
    if chemical_path.exists():
        chemical_rows = _copy_parquet(
            conn,
            schema=schema,
            table=CHEMICAL_TABLE,
            columns=CHEMICAL_COLUMNS,
            path=chemical_path,
            batch_size=batch_size,
            label='resolver.chemicals',
        )

    if indexes:
        started = time.monotonic()
        print('[resolver] creating indexes', flush=True)
        with conn.cursor() as cur:
            _create_indexes(cur, schema)
        conn.commit()
        print(
            f'[resolver] indexes ready in {time.monotonic() - started:.1f}s',
            flush=True,
        )

    return ResolverLoadStats(
        protein_rows=protein_rows,
        chemical_rows=chemical_rows,
    )


def _ensure_schema(cur: psycopg2.extensions.cursor, schema: str) -> None:
    cur.execute(
        sql.SQL('CREATE SCHEMA IF NOT EXISTS {}').format(sql.Identifier(schema))
    )


def _ensure_tables(
    cur: psycopg2.extensions.cursor,
    *,
    schema: str,
    drop_existing: bool,
) -> None:
    schema_id = sql.Identifier(schema)
    if drop_existing:
        for table in (PROTEIN_TABLE, CHEMICAL_TABLE):
            cur.execute(
                sql.SQL('DROP TABLE IF EXISTS {}.{} CASCADE').format(
                    schema_id,
                    sql.Identifier(table),
                )
            )

    cur.execute(
        sql.SQL(
            """
            CREATE TABLE IF NOT EXISTS {}.{} (
              source text NOT NULL,
              key_type text NOT NULL,
              key_value text NOT NULL,
              taxonomy_id text,
              primary_uniprot text NOT NULL,
              mapping_type text NOT NULL
            )
            """
        ).format(schema_id, sql.Identifier(PROTEIN_TABLE))
    )
    cur.execute(
        sql.SQL(
            """
            CREATE TABLE IF NOT EXISTS {}.{} (
              source text NOT NULL,
              key_type text NOT NULL,
              key_value text NOT NULL,
              standard_inchi_key text NOT NULL,
              standard_inchi text NOT NULL
            )
            """
        ).format(schema_id, sql.Identifier(CHEMICAL_TABLE))
    )
    cur.execute(
        sql.SQL(
            """
            ALTER TABLE {}.{}
            ADD COLUMN IF NOT EXISTS standard_inchi_key text
            """
        ).format(schema_id, sql.Identifier(CHEMICAL_TABLE))
    )
    if not drop_existing:
        for table in (PROTEIN_TABLE, CHEMICAL_TABLE):
            cur.execute(
                sql.SQL('TRUNCATE {}.{}').format(
                    schema_id,
                    sql.Identifier(table),
                )
            )


def _create_indexes(cur: psycopg2.extensions.cursor, schema: str) -> None:
    schema_id = sql.Identifier(schema)
    specs = [
        (
            'resolver_protein_lookup_key_tax_idx',
            PROTEIN_TABLE,
            ('key_type', 'key_value', 'taxonomy_id'),
            'btree',
        ),
        (
            'resolver_protein_lookup_key_idx',
            PROTEIN_TABLE,
            ('key_type', 'key_value'),
            'btree',
        ),
        (
            'resolver_protein_lookup_primary_idx',
            PROTEIN_TABLE,
            ('primary_uniprot',),
            'btree',
        ),
        (
            'resolver_protein_lookup_mapping_type_idx',
            PROTEIN_TABLE,
            ('mapping_type',),
            'btree',
        ),
        (
            'resolver_chemical_lookup_key_idx',
            CHEMICAL_TABLE,
            ('key_type', 'key_value'),
            'btree',
        ),
        (
            'resolver_chemical_lookup_inchi_idx',
            CHEMICAL_TABLE,
            ('standard_inchi',),
            'hash',
        ),
        (
            'resolver_chemical_lookup_inchi_key_idx',
            CHEMICAL_TABLE,
            ('standard_inchi_key',),
            'btree',
        ),
    ]
    for name, table, columns, index_method in specs:
        method_sql = (
            sql.SQL(' USING HASH') if index_method == 'hash' else sql.SQL('')
        )
        cur.execute(
            sql.SQL('CREATE INDEX IF NOT EXISTS {} ON {}.{}{} ({})').format(
                sql.Identifier(name),
                schema_id,
                sql.Identifier(table),
                method_sql,
                sql.SQL(', ').join(
                    sql.Identifier(column) for column in columns
                ),
            )
        )


def _copy_parquet(
    conn: psycopg2.extensions.connection,
    *,
    schema: str,
    table: str,
    columns: tuple[str, ...],
    path: Path,
    batch_size: int,
    label: str,
) -> int:
    parquet = pq.ParquetFile(path)
    missing_columns = [column for column in columns if column not in parquet.schema.names]
    if missing_columns:
        raise ValueError(
            f'{path} is missing required resolver column(s): '
            f'{", ".join(missing_columns)}. '
            'Build minimal resolver tables with `minimal.cli build-resolver`.'
        )
    total = 0
    started = time.monotonic()
    with conn.cursor() as cur:
        for batch in parquet.iter_batches(
            batch_size=batch_size,
            columns=list(columns),
        ):
            _copy_batch(
                cur, schema, table, columns, pa.Table.from_batches([batch])
            )
            total += batch.num_rows
            elapsed = time.monotonic() - started
            rate = total / elapsed if elapsed else 0.0
            print(
                f'[{label}] loaded rows={total:,} rate={rate:,.0f}/s',
                flush=True,
            )
    conn.commit()
    return total


def _copy_batch(
    cur: psycopg2.extensions.cursor,
    schema: str,
    table: str,
    columns: tuple[str, ...],
    batch: pa.Table,
) -> None:
    buffer = StringIO()
    writer = csv.writer(buffer, lineterminator='\n')
    arrays = [batch.column(column).to_pylist() for column in columns]
    for row in zip(*arrays, strict=True):
        writer.writerow(
            [
                _copy_value(_normalize_key_type(value) if column == 'key_type' else value)
                for column, value in zip(columns, row, strict=True)
            ]
        )
    buffer.seek(0)
    cur.copy_expert(
        sql.SQL("COPY {}.{} ({}) FROM STDIN WITH (FORMAT CSV, NULL '\\N')")
        .format(
            sql.Identifier(schema),
            sql.Identifier(table),
            sql.SQL(', ').join(sql.Identifier(column) for column in columns),
        )
        .as_string(cur.connection),
        buffer,
    )


def _copy_value(value: object) -> str:
    if value is None:
        return '\\N'
    return str(value)


_ACCESSION_LABEL_RE = re.compile(r'^([A-Z][A-Z0-9_]*:\d+):(.+)$')


def _normalize_key_type(value: object) -> object:
    if value is None:
        return None
    text = str(value)
    match = _ACCESSION_LABEL_RE.match(text)
    if not match:
        return value
    accession, label = match.groups()
    return f'{label}:{accession}'
