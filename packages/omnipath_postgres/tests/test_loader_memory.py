"""COPY dependencies, immediate constraints and analyzed hash joins stay atomic."""

from copy import deepcopy
import uuid

import psycopg
from psycopg import sql
import pytest

from omnipath_postgres import loader
from test_postgres import exists, fixture_rows, query, release, resource
from test_reactions import reaction_fixture

pytestmark = pytest.mark.integration


def test_parent_tables_are_complete_before_copying_children(tmp_path, postgres_dsn, monkeypatch):
    rows = fixture_rows()
    resource(tmp_path, rows=rows)
    original = loader._copy_projected_table
    started = []

    def observed(conn, schema, table, con, statement, columns, spool_directory):
        started.append(table)
        # Check visibility on the actual load connection, before each child COPY.
        parents = {
            "identifiers": ("entities", 3),
            "relations": ("entities", 3),
            "evidence": ("relations", 1),
            "annotations": ("evidence", 2),
        }
        if table in parents:
            parent, count = parents[table]
            actual = conn.execute(
                sql.SQL("SELECT count(*) FROM {}.{}").format(
                    sql.Identifier(schema), sql.Identifier(parent)
                )
            ).fetchone()
            assert actual == (count,)
        return original(conn, schema, table, con, statement, columns, spool_directory)

    monkeypatch.setattr(loader, "_copy_projected_table", observed)
    schema = "copy_memory_" + uuid.uuid4().hex
    result = loader.load_release(
        tmp_path, release(tmp_path), postgres_dsn, schema=schema, batch_size=2
    )
    assert started == ["entities", "identifiers", "relations", "evidence", "annotations"]
    assert result.counts["identifiers"] == result.counts["evidence"] == 2
    assert dict(query(postgres_dsn, schema, "SELECT entity_key,record_json FROM {s}.entities")) == {
        row["entity_key"]: row for row in rows[0]
    }
    assert query(postgres_dsn, schema, "SELECT record_json FROM {s}.relations") == [(rows[1][0],)]


def test_endpoint_failure_prevents_child_copy_and_raw_validation(
    tmp_path, postgres_dsn, monkeypatch
):
    rows = fixture_rows()
    second = deepcopy(rows[1][0])
    second["relation_key"] += "/later"
    rows[1].append(second)
    rows[1][0]["object_entity_key"] = "absent endpoint"
    resource(tmp_path, rows=rows)
    original = loader._copy_projected_table
    started = []

    def observed(conn, schema, table, *args):
        started.append(table)
        return original(conn, schema, table, *args)

    def unexpected(*args, **kwargs):
        pytest.fail("An invalid endpoint must fail before raw validation")

    monkeypatch.setattr(loader, "_copy_projected_table", observed)
    monkeypatch.setattr(loader, "iter_validated_payloads", unexpected)
    schema = "copy_memory_" + uuid.uuid4().hex
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        loader.load_release(tmp_path, release(tmp_path), postgres_dsn, schema=schema, batch_size=1)
    assert started == ["entities", "identifiers", "relations"]
    assert not exists(postgres_dsn, schema)


def test_reaction_hash_queries_see_current_table_statistics(tmp_path, postgres_dsn, monkeypatch):
    resource(tmp_path, "rhea", rows=reaction_fixture())
    original = loader._validate_reaction_payloads
    batches = []

    def verify_statistics(conn, schema, source, version, references):
        with conn.cursor() as cur:
            cur.execute(
                "SELECT c.relname,c.reltuples FROM pg_class c "
                "JOIN pg_namespace n ON n.oid=c.relnamespace "
                "WHERE n.nspname=%s AND c.relname=ANY(%s)",
                (schema, ["entities", "relations", "evidence", "annotations"]),
            )
            estimates = dict(cur.fetchall())
            cur.execute(
                "SELECT tablename,attname FROM pg_stats WHERE schemaname=%s "
                "AND attname IN ('record_json','quantity')",
                (schema,),
            )
            assert cur.fetchall() == []  # Preliminary statistics avoid wide JSON.
        assert estimates.keys() == {"entities", "relations", "evidence", "annotations"}
        assert all(estimate > 0 for estimate in estimates.values())
        batches.append(len(references))
        original(conn, schema, source, version, references)

    monkeypatch.setattr(loader, "_validate_reaction_payloads", verify_statistics)
    schema = "copy_memory_" + uuid.uuid4().hex
    loader.load_release(
        tmp_path,
        release(tmp_path, {"rhea": "1.0.0"}),
        postgres_dsn,
        schema=schema,
        batch_size=1,
        validate_source_records=True,
    )
    assert batches == [1, 1]
    assert query(postgres_dsn, schema, "SELECT count(*) FROM {s}.reaction_context") == [(1,)]


def test_nonreaction_resource_avoids_inapplicable_hash_joins(tmp_path, postgres_dsn, monkeypatch):
    resource(tmp_path)

    def unexpected(*args, **kwargs):
        pytest.fail("Protein-only resources have no eligible reaction evidence")

    monkeypatch.setattr(loader, "_validate_reaction_payloads", unexpected)
    schema = "copy_memory_" + uuid.uuid4().hex
    result = loader.load_release(
        tmp_path, release(tmp_path), postgres_dsn, schema=schema, validate_source_records=True
    )
    assert result.validated_payload_rows == {"signor": 2}
    assert query(postgres_dsn, schema, "SELECT count(*) FROM {s}.evidence") == [(2,)]


def test_generic_pointer_integrity_is_not_skipped_for_nonreaction_resources(
    tmp_path, postgres_dsn, monkeypatch
):
    rows = fixture_rows()
    rows[2][0]["relation_key"] = "absent relation owner"
    resource(tmp_path, rows=rows)

    def unexpected(*args, **kwargs):
        pytest.fail("Protein-only resources have no eligible reaction evidence")

    monkeypatch.setattr(loader, "_validate_reaction_payloads", unexpected)
    schema = "copy_memory_" + uuid.uuid4().hex
    with pytest.raises(ValueError, match="Payload references an absent relation owner"):
        loader.load_release(
            tmp_path, release(tmp_path), postgres_dsn, schema=schema, validate_source_records=True
        )
    assert not exists(postgres_dsn, schema)
