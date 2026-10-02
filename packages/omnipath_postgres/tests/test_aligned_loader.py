"""Aligned loader rejects invalid requests and preserves COPY failure boundaries."""

import csv
import io
from contextlib import closing
import uuid

import duckdb
import pytest
import psycopg2
from psycopg2 import sql

from omnipath_postgres import aligned_loader
from omnipath_postgres.aligned_projection import CopyQuery


def forbid_io(*args, **kwargs):
    pytest.fail("Invalid loader parameters must not read artifacts or connect")


@pytest.mark.parametrize("schema", ["public", "pg_temp", "", "bad-name", None])
def test_invalid_schema_before_io(monkeypatch, schema):
    monkeypatch.setattr(aligned_loader, "read_release", forbid_io)
    monkeypatch.setattr(aligned_loader.psycopg2, "connect", forbid_io)
    with pytest.raises(ValueError, match="schema"):
        aligned_loader.load_main_release("missing", "missing", "unused", schema=schema)


@pytest.mark.parametrize("threads", [0, -1, True, 1.5, None])
def test_invalid_duckdb_threads_before_io(monkeypatch, threads):
    monkeypatch.setattr(aligned_loader, "read_release", forbid_io)
    monkeypatch.setattr(aligned_loader.psycopg2, "connect", forbid_io)
    with pytest.raises(ValueError, match="duckdb_threads"):
        aligned_loader.load_main_release("missing", "missing", "unused", duckdb_threads=threads)


@pytest.mark.parametrize("limit", [None, "", 0, True, "0MB", "-1MB", "unlimited", "512MB; SELECT 1"])
def test_invalid_memory_limit_before_io(monkeypatch, limit):
    monkeypatch.setattr(aligned_loader, "read_release", forbid_io)
    monkeypatch.setattr(aligned_loader.psycopg2, "connect", forbid_io)
    with pytest.raises(ValueError, match="memory_limit"):
        aligned_loader.load_main_release("missing", "missing", "unused", memory_limit=limit)


@pytest.mark.parametrize("field", ["defer_constraints", "base_only", "retain_published_provenance"])
@pytest.mark.parametrize("value", [None, 0, 1, "true"])
def test_nonboolean_modes_before_io(monkeypatch, field, value):
    monkeypatch.setattr(aligned_loader, "read_release", forbid_io)
    monkeypatch.setattr(aligned_loader.psycopg2, "connect", forbid_io)
    with pytest.raises(ValueError, match=field):
        aligned_loader.load_main_release("missing", "missing", "unused", **{field: value})


@pytest.mark.parametrize("products", [("cosmos", "cosmos"), ("unknown",), "cosmos", None])
def test_invalid_products_before_io(monkeypatch, products):
    monkeypatch.setattr(aligned_loader, "read_release", forbid_io)
    monkeypatch.setattr(aligned_loader.psycopg2, "connect", forbid_io)
    with pytest.raises(ValueError, match="[Pp]roducts"):
        aligned_loader.load_main_release("missing", "missing", "unused", products=products)


@pytest.mark.parametrize("products", [("cosmos", "cosmos"), ("unknown",), "cosmos", None])
def test_invalid_finish_products_before_connect(monkeypatch, products):
    monkeypatch.setattr(aligned_loader.psycopg2, "connect", forbid_io)
    with pytest.raises(ValueError, match="[Pp]roducts"):
        aligned_loader.finish_main_release("unused", schema="fixture", products=products)


class Cursor:
    def __init__(self, connection):
        self.connection = connection
        self.rowcount = -1

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def copy_expert(self, statement, stream, **kwargs):
        self.connection.statements.append(statement)
        payload = stream.read()
        self.connection.payloads.append(payload)
        if self.connection.failure:
            raise RuntimeError("deliberate PostgreSQL COPY failure")
        parsed = list(csv.reader(io.StringIO(payload.decode())))
        self.rowcount = self.connection.rowcounts.pop(0) if self.connection.rowcounts else len(parsed)


class Connection:
    def __init__(self, *, rowcounts=(), failure=False):
        self.rowcounts = list(rowcounts)
        self.failure = failure
        self.payloads = []
        self.statements = []
        self.commits = 0

    def cursor(self):
        return Cursor(self)

    def commit(self):
        self.commits += 1


@pytest.fixture
def fake_copy_render(monkeypatch):
    # Only transport is mocked. DuckDB stages real CSV. Real PostgreSQL DDL,
    # identifiers and driver COPY are independently covered by the server pilot.
    monkeypatch.setattr(aligned_loader.sql.Composed, "as_string", lambda self, conn: "COPY fixture")


def test_zero_rows_copy_is_empty_success_and_no_hidden_commit(tmp_path, fake_copy_render):
    conn = Connection()
    with duckdb.connect() as con:
        item = CopyQuery("entity", ("entity_id",), "SELECT NULL::UUID WHERE false")
        result = aligned_loader._copy(conn, "fixture", item, con, tmp_path)
    assert result.rows == 0
    assert conn.commits == 0
    assert list(tmp_path.iterdir()) == []


def test_copy_null_literal_null_token_quotes_and_unicode_are_distinct(tmp_path, fake_copy_render):
    conn = Connection()
    with duckdb.connect() as con:
        con.execute("CREATE TABLE fixture(value VARCHAR)")
        con.executemany("INSERT INTO fixture VALUES (?)", [(None,), (r"\N",), ("α,'\"\nnewline",), ("",)])
        item = CopyQuery("annotation", ("value",), "SELECT value FROM fixture")
        result = aligned_loader._copy(conn, "fixture", item, con, tmp_path)
    assert result.rows == 4
    payload = b"".join(conn.payloads).decode()
    assert '\\N\n' in payload
    assert '"\\N"\n' in payload
    assert '""\n' in payload
    assert "α" in payload
    assert conn.commits == 0
    assert list(tmp_path.iterdir()) == []


def test_copy_count_mismatch_cleans_staging_and_does_not_commit(tmp_path, fake_copy_render):
    conn = Connection(rowcounts=(2,))
    with duckdb.connect() as con:
        item = CopyQuery("annotation", ("value",), "SELECT 'one'")
        with pytest.raises(ValueError, match="COPY count differs"):
            aligned_loader._copy(conn, "fixture", item, con, tmp_path)
    assert conn.commits == 0
    assert list(tmp_path.iterdir()) == []


def test_copy_transport_failure_cleans_staging_and_does_not_commit(tmp_path, fake_copy_render):
    conn = Connection(failure=True)
    with duckdb.connect() as con:
        item = CopyQuery("annotation", ("value",), "SELECT 'one'")
        with pytest.raises(RuntimeError, match="deliberate PostgreSQL COPY failure"):
            aligned_loader._copy(conn, "fixture", item, con, tmp_path)
    assert conn.commits == 0
    assert list(tmp_path.iterdir()) == []


class InvalidStaging:
    def __init__(self, result):
        self.result = result

    def execute(self, _):
        return self

    def fetchone(self):
        return self.result


@pytest.mark.parametrize("result", [None, (), (-1,), (True,), ("1",)])
def test_invalid_duckdb_stage_count_is_rejected(tmp_path, fake_copy_render, result):
    conn = Connection()
    item = CopyQuery("annotation", ("value",), "SELECT 'one'")
    with pytest.raises(ValueError, match="staged row count"):
        aligned_loader._copy(conn, "fixture", item, InvalidStaging(result), tmp_path)
    assert not conn.statements
    assert conn.commits == 0
    assert list(tmp_path.iterdir()) == []


def test_nonempty_staged_count_requires_csv_chunks(tmp_path, fake_copy_render):
    conn = Connection()
    item = CopyQuery("annotation", ("value",), "SELECT 'one'")
    with pytest.raises(ValueError, match="CSV chunks"):
        aligned_loader._copy(conn, "fixture", item, InvalidStaging((1,)), tmp_path)
    assert list(tmp_path.iterdir()) == []


@pytest.fixture
def checkpoint_database(postgres_dsn):
    """Tiny disposable publication schema; no resource build or source data."""
    schema = "checkpoint_" + uuid.uuid4().hex
    with closing(psycopg2.connect(postgres_dsn)) as connection:
        with connection.cursor() as cursor:
            cursor.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
            cursor.execute(sql.SQL("""CREATE TABLE {}.parquet_release (
                singleton boolean PRIMARY KEY, version text,manifest_sha256 text,
                status text,phase_seconds jsonb NOT NULL DEFAULT '{{}}')""").format(sql.Identifier(schema)))
            cursor.execute(sql.SQL("""CREATE TABLE {}.parquet_phase (
                phase text PRIMARY KEY,seconds double precision,result jsonb,
                completed_at timestamptz DEFAULT now())""").format(sql.Identifier(schema)))
            cursor.execute(sql.SQL("CREATE TABLE {}.product_contents(value text)").format(sql.Identifier(schema)))
            cursor.execute(sql.SQL("INSERT INTO {}.parquet_release VALUES(true,'release','digest','derived','{{}}')").format(
                sql.Identifier(schema)
            ))
        connection.commit()
        try:
            yield postgres_dsn, schema, connection
        finally:
            connection.rollback()
            with connection.cursor() as cursor:
                cursor.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
            connection.commit()


@pytest.mark.parametrize("wrapped", [True, False])
@pytest.mark.parametrize("observer_fails", [False, True])
def test_product_checkpoint_commits_tables_and_both_metadata_interfaces_before_observer(
    checkpoint_database, wrapped, observer_fails,
):
    dsn, schema, connection = checkpoint_database
    stats = {"rows": 1, "sources": ["fixture"]}
    outcome = {"phase_seconds": {"projection": 0.25}, "result": stats} if wrapped else stats
    with connection.cursor() as cursor:
        cursor.execute(sql.SQL("INSERT INTO {}.product_contents VALUES('complete product')").format(sql.Identifier(schema)))
    observed = []

    def observer(event, **fields):
        # A separate connection can see only actually committed publication.
        with closing(psycopg2.connect(dsn)) as reader, reader.cursor() as cursor:
            cursor.execute(sql.SQL("SELECT value FROM {}.product_contents").format(sql.Identifier(schema)))
            assert cursor.fetchall() == [("complete product",)]
            cursor.execute(sql.SQL("SELECT product,release_id,manifest_sha256,stats FROM {}.subset_build_metadata").format(
                sql.Identifier(schema)
            ))
            assert cursor.fetchall() == [("cosmos", "release", "digest", stats)]
            cursor.execute(sql.SQL("SELECT phase,seconds,result FROM {}.parquet_phase").format(sql.Identifier(schema)))
            assert cursor.fetchall() == [("cosmos", 1.5, outcome)]
            cursor.execute(sql.SQL("SELECT status,phase_seconds FROM {}.parquet_release").format(sql.Identifier(schema)))
            assert cursor.fetchall() == [("cosmos", {"cosmos": 1.5})]
        observed.append((event, fields))
        if observer_fails:
            raise OSError("deliberate observer failure")

    if observer_fails:
        with pytest.raises(OSError, match="observer failure"):
            aligned_loader._checkpoint(connection, schema, "cosmos", 1.5, outcome,
                                       ("release", "digest"), observer)
    else:
        aligned_loader._checkpoint(connection, schema, "cosmos", 1.5, outcome,
                                   ("release", "digest"), observer)
    assert observed == [("phase_committed", {"phase": "cosmos", "seconds": 1.5, "result": outcome})]
    # Caller rollback after an observer exception must retain published work.
    connection.rollback()
    with connection.cursor() as cursor:
        cursor.execute(sql.SQL("SELECT count(*) FROM {}.product_contents").format(sql.Identifier(schema)))
        assert cursor.fetchone()[0] == 1


def test_checkpoint_identity_change_never_commits_pending_product(checkpoint_database):
    dsn, schema, connection = checkpoint_database
    with connection.cursor() as cursor:
        cursor.execute(sql.SQL("INSERT INTO {}.product_contents VALUES('unfinished product')").format(sql.Identifier(schema)))
    with pytest.raises(ValueError, match="identity changed"):
        aligned_loader._checkpoint(connection, schema, "cosmos", 1.5, {"result": {}},
                                   ("wrong release", "wrong digest"), None)
    with closing(psycopg2.connect(dsn)) as reader, reader.cursor() as cursor:
        cursor.execute(sql.SQL("SELECT count(*) FROM {}.product_contents").format(sql.Identifier(schema)))
        assert cursor.fetchone()[0] == 0
        cursor.execute(sql.SQL("SELECT count(*) FROM {}.parquet_phase").format(sql.Identifier(schema)))
        assert cursor.fetchone()[0] == 0
