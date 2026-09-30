"""COPY dependencies, immediate constraints and analyzed hash joins stay atomic."""

from copy import deepcopy
import uuid

import psycopg
import pytest

from omnipath_postgres import loader
from test_postgres import exists, fixture_rows, query, release, resource
from test_reactions import reaction_fixture

pytestmark = pytest.mark.integration


def test_child_batch_can_fill_before_its_buffered_parent(tmp_path, postgres_dsn):
    # Two identifiers and two evidence rows fill batches while their one parent
    # remains partial; immediate FK checks require that parent to be copied first.
    rows = fixture_rows()
    resource(tmp_path, rows=rows)
    schema = "copy_memory_" + uuid.uuid4().hex
    result = loader.load_release(
        tmp_path, release(tmp_path), postgres_dsn, schema=schema, batch_size=2
    )
    assert result.counts["identifiers"] == result.counts["evidence"] == 2
    assert dict(query(postgres_dsn, schema, "SELECT entity_key,record_json FROM {s}.entities")) == {
        row["entity_key"]: row for row in rows[0]
    }
    assert query(postgres_dsn, schema, "SELECT record_json FROM {s}.relations") == [(rows[1][0],)]


def test_foreign_keys_fail_before_the_remaining_resource_is_consumed(
    tmp_path, postgres_dsn, monkeypatch
):
    rows = fixture_rows()
    second = deepcopy(rows[1][0])
    second["relation_key"] += "/later"
    rows[1].append(second)
    rows[1][0]["object_entity_key"] = "absent endpoint"
    resource(tmp_path, rows=rows)
    original = loader.iter_resource_records
    consumed = []

    def observed(*args, **kwargs):
        for record in original(*args, **kwargs):
            if record.table == "relations":
                consumed.append(record.values["relation_key"])
            yield record

    monkeypatch.setattr(loader, "iter_resource_records", observed)
    schema = "copy_memory_" + uuid.uuid4().hex
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        loader.load_release(tmp_path, release(tmp_path), postgres_dsn, schema=schema, batch_size=1)
    assert consumed == [rows[1][0]["relation_key"]]
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
    )
    assert batches == [1, 1]
    assert query(postgres_dsn, schema, "SELECT count(*) FROM {s}.reaction_context") == [(1,)]


def test_nonreaction_resource_avoids_inapplicable_hash_joins(tmp_path, postgres_dsn, monkeypatch):
    resource(tmp_path)

    def unexpected(*args, **kwargs):
        pytest.fail("Protein-only resources have no eligible reaction evidence")

    monkeypatch.setattr(loader, "_validate_reaction_payloads", unexpected)
    schema = "copy_memory_" + uuid.uuid4().hex
    result = loader.load_release(tmp_path, release(tmp_path), postgres_dsn, schema=schema)
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
        loader.load_release(tmp_path, release(tmp_path), postgres_dsn, schema=schema)
    assert not exists(postgres_dsn, schema)
