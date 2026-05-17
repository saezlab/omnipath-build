"""Load resolver lookup tables used by canonicalization.

Resolver tables map source evidence identifiers to canonical identifiers. The
protein lookup is taxonomy-scoped and resolves UniProt, secondary UniProt, and
stable gene/protein cross-references to primary UniProt accessions. The
chemical lookup resolves supported chemical identifiers to standard InChI keys.

Tables can be loaded from materialized parquet files or streamed directly from
resolver source parsers. Canonicalization uses these lookup tables to rank and
choose entity resolution candidates.
"""

from __future__ import annotations

from io import StringIO
import csv
import time
from pathlib import Path
from dataclasses import dataclass

import pyarrow as pa
from psycopg2 import sql
import pyarrow.parquet as pq
import psycopg2.extensions

PROTEIN_TABLE = 'resolver_protein_identifier_lookup'
PROTEIN_AMBIGUOUS_TABLE = 'resolver_protein_identifier_lookup_ambiguous'
CHEMICAL_TABLE = 'resolver_chemical_identifier_lookup'
IDENTIFIER_TYPE_TABLE = 'vocab_identifier_type'

IDENTIFIER_TYPE_COLUMNS = (
    'identifier_type_id',
    'name',
)
PROTEIN_COLUMNS = (
    'key_identifier_type_id',
    'key_value',
    'taxonomy_id',
    'canonical_identifier_type_id',
    'canonical_identifier',
)
CHEMICAL_COLUMNS = (
    'key_identifier_type_id',
    'key_value',
    'canonical_identifier_type_id',
    'canonical_identifier',
)


@dataclass(frozen=True)
class ResolverLoadStats:
    """Loaded resolver row counts."""

    protein_rows: int = 0
    protein_ambiguous_rows: int = 0
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
    protein_type_path = mapping_dir / 'proteins' / 'identifier_type.parquet'
    chemical_path = (
        mapping_dir / 'chemicals' / 'chemical_identifier_lookup.parquet'
    )
    chemical_type_path = mapping_dir / 'chemicals' / 'identifier_type.parquet'

    with conn.cursor() as cur:
        _ensure_schema(cur, schema)
        _ensure_tables(cur, schema=schema, drop_existing=drop_existing)
    conn.commit()

    protein_rows = 0
    chemical_rows = 0
    for type_path in (protein_type_path, chemical_type_path):
        if type_path.exists():
            _copy_identifier_types(
                conn,
                schema=schema,
                path=type_path,
                batch_size=batch_size,
                label=f'resolver.{type_path.parent.name}.identifier_types',
            )
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
    conn.commit()

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


def load_resolver_sources(
    conn: psycopg2.extensions.connection,
    *,
    sources: list[str],
    schema: str = 'public',
    batch_size: int = 100_000,
    drop_existing: bool = True,
    indexes: bool = True,
    taxonomy_ids: list[int | str] | None = None,
    max_records: int | None = None,
    pubchem_url: str | Path | None = None,
) -> ResolverLoadStats:
    """Stream resolver source rows directly into PostgreSQL."""

    with conn.cursor() as cur:
        _ensure_schema(cur, schema)
        _ensure_tables(cur, schema=schema, drop_existing=drop_existing)
        _insert_static_identifier_types(cur, schema)
    conn.commit()

    selected = set(sources)
    protein_rows = 0
    protein_ambiguous_rows = 0
    chemical_rows = 0

    if 'uniprot' in selected:
        protein_rows, protein_ambiguous_rows = _load_protein_sources(
            conn,
            schema=schema,
            batch_size=batch_size,
            taxonomy_ids=taxonomy_ids,
        )

    chemical_sources = [source for source in sources if source != 'uniprot']
    if chemical_sources:
        chemical_rows = _load_chemical_sources(
            conn,
            schema=schema,
            sources=chemical_sources,
            batch_size=batch_size,
            max_records=max_records,
            pubchem_url=pubchem_url,
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
        protein_ambiguous_rows=protein_ambiguous_rows,
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
        for table in (
            PROTEIN_TABLE,
            PROTEIN_AMBIGUOUS_TABLE,
            CHEMICAL_TABLE,
            IDENTIFIER_TYPE_TABLE,
        ):
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
              identifier_type_id bigint PRIMARY KEY,
              name text NOT NULL UNIQUE
            )
            """
        ).format(schema_id, sql.Identifier(IDENTIFIER_TYPE_TABLE))
    )
    cur.execute(
        sql.SQL(
            """
            CREATE TABLE IF NOT EXISTS {}.{} (
              key_identifier_type_id bigint NOT NULL
                REFERENCES {}.{}(identifier_type_id),
              key_value text NOT NULL,
              taxonomy_id text,
              canonical_identifier_type_id bigint NOT NULL
                REFERENCES {}.{}(identifier_type_id),
              canonical_identifier text NOT NULL
            )
            """
        ).format(
            schema_id,
            sql.Identifier(PROTEIN_TABLE),
            schema_id,
            sql.Identifier(IDENTIFIER_TYPE_TABLE),
            schema_id,
            sql.Identifier(IDENTIFIER_TYPE_TABLE),
        )
    )
    cur.execute(
        sql.SQL(
            """
            CREATE TABLE IF NOT EXISTS {}.{} (
              key_identifier_type_id bigint NOT NULL
                REFERENCES {}.{}(identifier_type_id),
              key_value text NOT NULL,
              taxonomy_id text,
              canonical_identifier_type_id bigint NOT NULL
                REFERENCES {}.{}(identifier_type_id),
              canonical_identifier text NOT NULL
            )
            """
        ).format(
            schema_id,
            sql.Identifier(PROTEIN_AMBIGUOUS_TABLE),
            schema_id,
            sql.Identifier(IDENTIFIER_TYPE_TABLE),
            schema_id,
            sql.Identifier(IDENTIFIER_TYPE_TABLE),
        )
    )
    cur.execute(
        sql.SQL(
            """
            CREATE TABLE IF NOT EXISTS {}.{} (
              key_identifier_type_id bigint NOT NULL
                REFERENCES {}.{}(identifier_type_id),
              key_value text NOT NULL,
              canonical_identifier_type_id bigint NOT NULL
                REFERENCES {}.{}(identifier_type_id),
              canonical_identifier text NOT NULL
            )
            """
        ).format(
            schema_id,
            sql.Identifier(CHEMICAL_TABLE),
            schema_id,
            sql.Identifier(IDENTIFIER_TYPE_TABLE),
            schema_id,
            sql.Identifier(IDENTIFIER_TYPE_TABLE),
        )
    )
    if not drop_existing:
        cur.execute(
            sql.SQL('TRUNCATE {}, {}, {}, {}').format(
                sql.SQL('{}.{}').format(schema_id, sql.Identifier(PROTEIN_TABLE)),
                sql.SQL('{}.{}').format(
                    schema_id, sql.Identifier(PROTEIN_AMBIGUOUS_TABLE)
                ),
                sql.SQL('{}.{}').format(schema_id, sql.Identifier(CHEMICAL_TABLE)),
                sql.SQL('{}.{}').format(
                    schema_id, sql.Identifier(IDENTIFIER_TYPE_TABLE)
                ),
            )
        )


def _create_indexes(cur: psycopg2.extensions.cursor, schema: str) -> None:
    schema_id = sql.Identifier(schema)
    specs = [
        (
            'resolver_protein_lookup_key_tax_idx',
            PROTEIN_TABLE,
            ('key_identifier_type_id', 'key_value', 'taxonomy_id'),
            'btree',
        ),
        (
            'resolver_protein_lookup_key_idx',
            PROTEIN_TABLE,
            ('key_identifier_type_id', 'key_value'),
            'btree',
        ),
        (
            'resolver_protein_lookup_canonical_idx',
            PROTEIN_TABLE,
            ('canonical_identifier_type_id', 'canonical_identifier'),
            'btree',
        ),
        (
            'resolver_protein_ambiguous_key_tax_idx',
            PROTEIN_AMBIGUOUS_TABLE,
            ('key_identifier_type_id', 'key_value', 'taxonomy_id'),
            'btree',
        ),
        (
            'resolver_chemical_lookup_key_idx',
            CHEMICAL_TABLE,
            ('key_identifier_type_id', 'key_value'),
            'btree',
        ),
        (
            'resolver_chemical_lookup_canonical_idx',
            CHEMICAL_TABLE,
            ('canonical_identifier_type_id', 'canonical_identifier'),
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
            'Build omnipath_build resolver tables with `omnipath_build.cli build-resolver`.'
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
    return total


def _load_protein_sources(
    conn: psycopg2.extensions.connection,
    *,
    schema: str,
    batch_size: int,
    taxonomy_ids: list[int | str] | None,
) -> tuple[int, int]:
    from omnipath_build.resolver.sources.proteins import _protein_identifier_rows

    started = time.monotonic()
    with conn.cursor() as cur:
        _create_staging_table(cur, 'stg_resolver_protein', PROTEIN_COLUMNS)
        _copy_dict_rows(
            cur,
            rows=_normalized_protein_rows(
                _protein_identifier_rows(taxonomy_ids=taxonomy_ids)
            ),
            table='stg_resolver_protein',
            columns=PROTEIN_COLUMNS,
            batch_size=batch_size,
            label='resolver.proteins',
            started=started,
        )
        protein_rows, ambiguous_rows = _insert_split_protein_rows(cur, schema)
    conn.commit()
    return protein_rows, ambiguous_rows


def _load_chemical_sources(
    conn: psycopg2.extensions.connection,
    *,
    schema: str,
    sources: list[str],
    batch_size: int,
    max_records: int | None,
    pubchem_url: str | Path | None,
) -> int:
    from omnipath_build.resolver.sources.chemicals import _chemical_identifier_rows

    started = time.monotonic()
    with conn.cursor() as cur:
        _create_staging_table(cur, 'stg_resolver_chemical', CHEMICAL_COLUMNS)
        _copy_dict_rows(
            cur,
            rows=_normalized_chemical_rows(
                _chemical_identifier_rows(
                    sources,
                    max_records=max_records,
                    pubchem_url=pubchem_url,
                )
            ),
            table='stg_resolver_chemical',
            columns=CHEMICAL_COLUMNS,
            batch_size=batch_size,
            label='resolver.chemicals',
            started=started,
        )
        chemical_rows = _insert_chemical_rows(cur, schema)
    conn.commit()
    return chemical_rows


def _insert_static_identifier_types(
    cur: psycopg2.extensions.cursor,
    schema: str,
) -> None:
    from omnipath_build.resolver.identifier_types import identifier_type_rows

    cur.executemany(
        sql.SQL(
            """
            INSERT INTO {}.{} (identifier_type_id, name)
            VALUES (%s, %s)
            ON CONFLICT (identifier_type_id) DO UPDATE
            SET name = EXCLUDED.name
            """
        )
        .format(sql.Identifier(schema), sql.Identifier(IDENTIFIER_TYPE_TABLE))
        .as_string(cur.connection),
        [
            (row['identifier_type_id'], row['name'])
            for row in identifier_type_rows()
        ],
    )


def _normalized_protein_rows(rows: object) -> object:
    from omnipath_build.resolver.identifier_types import identifier_type_id
    from omnipath_build.resolver.sources.proteins import UNIPROT_TYPE

    canonical_identifier_type_id = identifier_type_id(UNIPROT_TYPE)
    for row in rows:
        key_type = row.get('key_type')
        key_value = row.get('key_value')
        canonical_identifier = row.get('primary_uniprot')
        if not key_type or not key_value or not canonical_identifier:
            continue
        yield {
            'key_identifier_type_id': identifier_type_id(str(key_type)),
            'key_value': key_value,
            'taxonomy_id': row.get('taxonomy_id'),
            'canonical_identifier_type_id': canonical_identifier_type_id,
            'canonical_identifier': canonical_identifier,
        }


def _normalized_chemical_rows(rows: object) -> object:
    from omnipath_build.resolver.identifier_types import identifier_type_id
    from omnipath_build.resolver.sources.chemicals import STANDARD_INCHI_KEY_TYPE

    canonical_identifier_type_id = identifier_type_id(STANDARD_INCHI_KEY_TYPE)
    for row in rows:
        key_type = row.get('key_type')
        key_value = row.get('key_value')
        standard_inchi_key = row.get('standard_inchi_key')
        if not key_type or not key_value or not standard_inchi_key:
            continue
        yield {
            'key_identifier_type_id': identifier_type_id(str(key_type)),
            'key_value': key_value,
            'canonical_identifier_type_id': canonical_identifier_type_id,
            'canonical_identifier': standard_inchi_key,
        }


def _create_staging_table(
    cur: psycopg2.extensions.cursor,
    table: str,
    columns: tuple[str, ...],
) -> None:
    cur.execute(sql.SQL('DROP TABLE IF EXISTS {}').format(sql.Identifier(table)))
    cur.execute(
        sql.SQL('CREATE TEMP TABLE {} ({}) ON COMMIT DROP').format(
            sql.Identifier(table),
            sql.SQL(', ').join(
                sql.SQL('{} text').format(sql.Identifier(column))
                for column in columns
            ),
        )
    )


def _copy_dict_rows(
    cur: psycopg2.extensions.cursor,
    *,
    rows: object,
    table: str,
    columns: tuple[str, ...],
    batch_size: int,
    label: str,
    started: float,
) -> int:
    total = 0
    chunk: list[dict] = []
    for row in rows:
        chunk.append(row)
        if len(chunk) >= batch_size:
            _copy_dict_batch(cur, table, columns, chunk)
            total += len(chunk)
            _print_load_progress(label, total, started)
            chunk = []
    if chunk:
        _copy_dict_batch(cur, table, columns, chunk)
        total += len(chunk)
        _print_load_progress(label, total, started)
    return total


def _copy_dict_batch(
    cur: psycopg2.extensions.cursor,
    table: str,
    columns: tuple[str, ...],
    rows: list[dict],
) -> None:
    buffer = StringIO()
    writer = csv.writer(buffer, lineterminator='\n')
    for row in rows:
        writer.writerow([_copy_value(row.get(column)) for column in columns])
    buffer.seek(0)
    cur.copy_expert(
        _copy_sql(None, table, columns).as_string(cur.connection),
        buffer,
    )


def _print_load_progress(label: str, total: int, started: float) -> None:
    elapsed = time.monotonic() - started
    rate = total / elapsed if elapsed else 0.0
    print(f'[{label}] loaded rows={total:,} rate={rate:,.0f}/s', flush=True)


def _insert_split_protein_rows(
    cur: psycopg2.extensions.cursor,
    schema: str,
) -> tuple[int, int]:
    cur.execute(
        sql.SQL(
            """
            WITH ambiguous_keys AS MATERIALIZED (
              SELECT
                key_identifier_type_id,
                key_value,
                taxonomy_id,
                canonical_identifier_type_id
              FROM stg_resolver_protein
              GROUP BY
                key_identifier_type_id,
                key_value,
                taxonomy_id,
                canonical_identifier_type_id
              HAVING COUNT(DISTINCT canonical_identifier) > 1
            ),
            inserted_protein AS (
              INSERT INTO {}.{} (
                key_identifier_type_id,
                key_value,
                taxonomy_id,
                canonical_identifier_type_id,
                canonical_identifier
              )
              SELECT DISTINCT
                p.key_identifier_type_id::bigint,
                p.key_value,
                NULLIF(p.taxonomy_id, ''),
                p.canonical_identifier_type_id::bigint,
                p.canonical_identifier
              FROM stg_resolver_protein p
              LEFT JOIN ambiguous_keys a
                ON a.key_identifier_type_id = p.key_identifier_type_id
               AND a.key_value = p.key_value
               AND a.taxonomy_id IS NOT DISTINCT FROM p.taxonomy_id
               AND a.canonical_identifier_type_id =
                   p.canonical_identifier_type_id
              WHERE a.key_identifier_type_id IS NULL
              RETURNING 1
            ),
            inserted_ambiguous AS (
              INSERT INTO {}.{} (
                key_identifier_type_id,
                key_value,
                taxonomy_id,
                canonical_identifier_type_id,
                canonical_identifier
              )
              SELECT DISTINCT
                p.key_identifier_type_id::bigint,
                p.key_value,
                NULLIF(p.taxonomy_id, ''),
                p.canonical_identifier_type_id::bigint,
                p.canonical_identifier
              FROM stg_resolver_protein p
              JOIN ambiguous_keys a
                ON a.key_identifier_type_id = p.key_identifier_type_id
               AND a.key_value = p.key_value
               AND a.taxonomy_id IS NOT DISTINCT FROM p.taxonomy_id
               AND a.canonical_identifier_type_id =
                   p.canonical_identifier_type_id
              RETURNING 1
            )
            SELECT
              (SELECT COUNT(*) FROM inserted_protein),
              (SELECT COUNT(*) FROM inserted_ambiguous)
            """
        ).format(
            sql.Identifier(schema),
            sql.Identifier(PROTEIN_TABLE),
            sql.Identifier(schema),
            sql.Identifier(PROTEIN_AMBIGUOUS_TABLE),
        )
    )
    protein_rows, ambiguous_rows = cur.fetchone()
    return int(protein_rows), int(ambiguous_rows)


def _insert_chemical_rows(
    cur: psycopg2.extensions.cursor,
    schema: str,
) -> int:
    cur.execute(
        sql.SQL(
            """
            INSERT INTO {}.{} (
              key_identifier_type_id,
              key_value,
              canonical_identifier_type_id,
              canonical_identifier
            )
            SELECT DISTINCT
              key_identifier_type_id::bigint,
              key_value,
              canonical_identifier_type_id::bigint,
              canonical_identifier
            FROM stg_resolver_chemical
            """
        ).format(sql.Identifier(schema), sql.Identifier(CHEMICAL_TABLE))
    )
    return int(cur.rowcount)


def _copy_identifier_types(
    conn: psycopg2.extensions.connection,
    *,
    schema: str,
    path: Path,
    batch_size: int,
    label: str,
) -> int:
    with conn.cursor() as cur:
        cur.execute('DROP TABLE IF EXISTS stg_identifier_type')
        cur.execute(
            """
            CREATE TEMP TABLE stg_identifier_type (
              identifier_type_id bigint,
              name text
            ) ON COMMIT DROP
            """
        )
    row_count = _copy_parquet(
        conn,
        schema=None,
        table='stg_identifier_type',
        columns=IDENTIFIER_TYPE_COLUMNS,
        path=path,
        batch_size=batch_size,
        label=label,
    )
    with conn.cursor() as cur:
        cur.execute(
            sql.SQL(
                """
                INSERT INTO {}.{} (identifier_type_id, name)
                SELECT DISTINCT identifier_type_id, name
                FROM stg_identifier_type
                ON CONFLICT (identifier_type_id) DO UPDATE
                SET name = EXCLUDED.name
                """
            ).format(sql.Identifier(schema), sql.Identifier(IDENTIFIER_TYPE_TABLE))
        )
    conn.commit()
    return row_count


def _copy_batch(
    cur: psycopg2.extensions.cursor,
    schema: str | None,
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
                _copy_value(value)
                for column, value in zip(columns, row, strict=True)
            ]
        )
    buffer.seek(0)
    cur.copy_expert(
        _copy_sql(schema, table, columns).as_string(cur.connection),
        buffer,
    )


def _copy_sql(
    schema: str | None,
    table: str,
    columns: tuple[str, ...],
) -> sql.Composed:
    table_sql = (
        sql.Identifier(table)
        if schema is None
        else sql.SQL('{}.{}').format(sql.Identifier(schema), sql.Identifier(table))
    )
    return sql.SQL("COPY {} ({}) FROM STDIN WITH (FORMAT CSV, NULL '\\N')").format(
        table_sql,
        sql.SQL(', ').join(sql.Identifier(column) for column in columns),
    )


def _copy_value(value: object) -> str:
    if value is None:
        return '\\N'
    return str(value)
