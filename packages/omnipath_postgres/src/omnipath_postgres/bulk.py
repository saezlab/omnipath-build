"""DuckDB SQL projection sinks and bounded PostgreSQL COPY transport.

No projected row is converted to a Python dictionary. DuckDB writes CSV chunks;
Python forwards their bytes through the caller's PostgreSQL transaction. Chunk
rotation bounds each COPY statement's immediate foreign-key trigger queue.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter

from psycopg import sql


_COPY_BYTES = 1024 * 1024
_CSV_CHUNK_SIZE = "16MB"


@dataclass(frozen=True)
class BulkCopyResult:
    rows: int
    stage_seconds: float
    copy_seconds: float
    chunks: int
    bytes: int


def _literal(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def copy_projected_table(conn, schema, table, con, query, columns, spool_directory):
    """Stage one SQL projection and COPY its chunks through the existing connection.

    DuckDB's FILE_SIZE_BYTES rotation is a threshold, not a strict byte ceiling:
    the last vector or a large record can exceed it. Only one table is staged at
    a time; temporary files are removed on success or exception. JSON text, CSV
    quoting and SQL-null encoding stay in DuckDB/PostgreSQL, outside Python.
    """
    with TemporaryDirectory(prefix=f"{table}-", dir=spool_directory) as directory:
        output = Path(directory) / "csv"
        started = perf_counter()
        result = con.execute(
            f"COPY ({query}) TO {_literal(output)} (FORMAT CSV, HEADER false, "
            "DELIMITER ',', QUOTE '\"', ESCAPE '\"', NULL '\\N', "
            f"PER_THREAD_OUTPUT true, FILE_SIZE_BYTES '{_CSV_CHUNK_SIZE}', "
            "FILENAME_PATTERN 'part_{i}')"
        ).fetchone()
        if not result or not isinstance(result[0], int) or result[0] < 0:
            raise ValueError(f"DuckDB returned no valid staged row count for {table}")
        staged = result[0]
        stage_seconds = perf_counter() - started
        files = sorted(output.glob("*.csv"))
        if staged and not files:
            raise ValueError(f"DuckDB produced no CSV chunks for {table}")
        statement = sql.SQL("COPY {}.{} ({}) FROM STDIN WITH (FORMAT CSV, NULL '\\N')").format(
            sql.Identifier(schema),
            sql.Identifier(table),
            sql.SQL(", ").join(map(sql.Identifier, columns)),
        )
        copied = size = 0
        started = perf_counter()
        for path in files:
            size += path.stat().st_size
            with conn.cursor() as cur, path.open("rb") as stream:
                with cur.copy(statement) as copy:
                    while block := stream.read(_COPY_BYTES):
                        copy.write(block)
                if cur.rowcount < 0:
                    raise ValueError(f"PostgreSQL returned no COPY count for {table}")
                copied += cur.rowcount
        copy_seconds = perf_counter() - started
        if copied != staged:
            raise ValueError(
                f"COPY count differs from DuckDB projection for {table}: {copied}/{staged}"
            )
        return BulkCopyResult(staged, stage_seconds, copy_seconds, len(files), size)
