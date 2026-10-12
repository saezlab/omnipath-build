"""The DuckDB database of an installation: views over its Parquet files, or native
tables copied from them.

Either way the database has one table per published and lookup table over every
installed resource (``entity``, ``relation``, ...), the same tables per resource in a
schema named after it (``signor.relation``), and ``resources`` listing what it holds.
"""

from __future__ import annotations

import os
from pathlib import Path
import tempfile

import duckdb

from .contracts import SERVING, TABLES, Installation, ResourceRecord

DATABASE = "omnipath.duckdb"
_PAYLOADS = "evidence_payloads"


def _literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _name(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _tables(record: ResourceRecord) -> list[str]:
    """The tables a resource has: raw evidence only when it was installed."""
    files = {f["name"] for f in record["files"]}
    return [t for t in TABLES + SERVING if t != _PAYLOADS or f"{_PAYLOADS}.parquet" in files]


def _source(root: Path, record: ResourceRecord, table: str) -> str:
    """One resource's table read from its file, with its provenance columns."""
    path = root / "resources" / record["resource_id"] / record["version"] / f"{table}.parquet"
    return (
        f"SELECT *, {_literal(record['resource_id'])} AS _resource, "
        f"{_literal(record['version'])} AS _resource_version "
        f"FROM read_parquet({_literal(str(path))})"
    )


def _state(records: list[ResourceRecord]) -> list[tuple[str, str, bool]]:
    return sorted((r["resource_id"], r["version"], _PAYLOADS in _tables(r)) for r in records)


def _describe(db, root: Path, materialized: bool, records: list[ResourceRecord]) -> None:
    db.execute(
        "CREATE OR REPLACE TABLE database_info AS SELECT ?::VARCHAR AS data_dir, "
        "?::BOOLEAN AS materialized",
        [str(root), materialized],
    )
    db.execute(
        "CREATE OR REPLACE TABLE resources "
        "(resource_id VARCHAR, version VARCHAR, has_evidence BOOLEAN)"
    )
    for row in _state(records):
        db.execute("INSERT INTO resources VALUES (?, ?, ?)", row)


def _resource_schema(db, root: Path, record: ResourceRecord, materialized: bool) -> None:
    schema = _name(record["resource_id"])
    db.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
    for table in _tables(record):
        source = (
            f"SELECT * FROM main.{table} WHERE _resource = {_literal(record['resource_id'])}"
            if materialized
            else _source(root, record, table)
        )
        db.execute(f"CREATE OR REPLACE VIEW {schema}.{table} AS {source}")


def _configure(db, root: Path, memory_limit: str) -> None:
    db.execute("SET memory_limit = ?", [memory_limit])
    db.execute("SET temp_directory = ?", [str(root / ".duckdb-tmp")])


def _rebuild(root: Path, records: list[ResourceRecord], materialized: bool, memory_limit: str):
    """Write the database anew and put it in place when complete."""
    staging = Path(tempfile.mkdtemp(dir=root, prefix=".database-")) / DATABASE
    try:
        db = duckdb.connect(str(staging))
        try:
            _configure(db, root, memory_limit)
            present = [t for t in TABLES + SERVING if any(t in _tables(r) for r in records)]
            if materialized:
                for table in present:
                    union = " UNION ALL BY NAME ".join(
                        _source(root, r, table) for r in records if table in _tables(r)
                    )
                    db.execute(f"CREATE TABLE {table} AS {union}")
            for record in records:
                _resource_schema(db, root, record, materialized)
            if not materialized:
                for table in present:
                    union = " UNION ALL BY NAME ".join(
                        f"SELECT * FROM {_name(r['resource_id'])}.{table}"
                        for r in records
                        if table in _tables(r)
                    )
                    db.execute(f"CREATE VIEW {table} AS {union}")
            _describe(db, root, materialized, records)
        finally:
            db.close()
        os.replace(staging, root / DATABASE)
    finally:
        for leftover in staging.parent.iterdir():
            leftover.unlink()
        staging.parent.rmdir()


def _update(root: Path, records: list[ResourceRecord], memory_limit: str) -> bool:
    """Replace the rows of added, changed and removed resources in native tables, in
    one transaction. False when the database has to be written anew instead."""
    try:
        db = duckdb.connect(str(root / DATABASE))
    except duckdb.Error:
        return False
    try:
        _configure(db, root, memory_limit)
        if db.execute("SELECT materialized FROM database_info").fetchone() != (True,):
            return False
        current = set(db.execute("SELECT * FROM resources").fetchall())
        stale = sorted({row[0] for row in current ^ set(_state(records))})
        by_name = {r["resource_id"]: r for r in records}
        existing = {row[0] for row in db.execute("SHOW TABLES").fetchall()}
        db.execute("BEGIN")
        for table in TABLES + SERVING:
            fresh = [by_name[n] for n in stale if n in by_name and table in _tables(by_name[n])]
            if table not in existing:
                if fresh:
                    return False
                continue
            db.execute(f"DELETE FROM {table} WHERE _resource IN (SELECT unnest(?))", [stale])
            for record in fresh:
                db.execute(f"INSERT INTO {table} BY NAME {_source(root, record, table)}")
        for name in stale:
            if name in by_name:
                _resource_schema(db, root, by_name[name], True)
            else:
                db.execute(f"DROP SCHEMA IF EXISTS {_name(name)} CASCADE")
        _describe(db, root, True, records)
        db.execute("COMMIT")
        return True
    except duckdb.Error:
        # A resource with a new column or type: the tables take their schema anew.
        return False
    finally:
        db.close()


def in_sync(root: Path, installation: Installation) -> bool:
    """Whether the database describes exactly the installed resources, in the
    installation's mode, at this location."""
    path = root / DATABASE
    if not path.is_file():
        return False
    try:
        db = duckdb.connect(str(path), read_only=True)
    except duckdb.Error:
        return False
    try:
        data_dir, materialized = db.execute("SELECT * FROM database_info").fetchone()
        state = db.execute("SELECT * FROM resources ORDER BY ALL").fetchall()
    except duckdb.Error:
        return False
    finally:
        db.close()
    return (
        materialized == installation["materialize"]
        # Views name their files; native tables do not depend on the location.
        and (materialized or data_dir == str(root))
        and state == _state(installation["resources"])
    )


def synchronize(root: Path, installation: Installation, memory_limit: str = "2GB") -> None:
    """Bring the database in line with the installation; nothing when it already is."""
    if in_sync(root, installation):
        return
    records = installation["resources"]
    try:
        if not (installation["materialize"] and _update(root, records, memory_limit)):
            _rebuild(root, records, installation["materialize"], memory_limit)
    finally:
        temporary = root / ".duckdb-tmp"
        if temporary.is_dir() and not any(temporary.iterdir()):
            temporary.rmdir()
