"""Network serving and registration require a real published release stamp."""

import uuid

import psycopg
from psycopg import sql
import pytest

from omnipath_postgres.compatibility.record_layout.loader import load_release
from omnipath_subsets.compatibility.record_layout import network_views
from omnipath_subsets.compatibility.record_layout.network_views import _query
from omnipath_subsets.compatibility.record_layout.network_views._registry import register
from release_fixture import entity, relation, write_release, write_resource

pytestmark = pytest.mark.integration


@pytest.fixture
def published_schema(tmp_path, postgres_dsn):
    subject = entity("P1", "protein", "uniprot")
    object_ = entity("P2", "protein", "uniprot")
    edge = relation(subject, "interacts_with", object_, source="connectomedb2025")
    write_resource(tmp_path, "connectomedb2025", [subject, object_], [edge])
    schema = "ready_" + uuid.uuid4().hex
    load_release(
        tmp_path, write_release(tmp_path, ["connectomedb2025"]), postgres_dsn, schema=schema
    )
    return schema, edge


def unpublish(conn, schema, state):
    namespace = sql.Identifier(schema)
    if state == "missing":
        conn.execute(sql.SQL("DROP TABLE {}.release_metadata").format(namespace))
    elif state == "empty":
        # This is the publication shape of a staged base checkpoint. Do not
        # fabricate a publication stamp to make manually created tables ready.
        conn.execute(sql.SQL("DELETE FROM {}.release_metadata").format(namespace))
    else:
        conn.execute(
            sql.SQL("""
                INSERT INTO {s}.release_metadata
                    (release_id, manifest_json, manifest_text, manifest_sha256,
                     input_manifest_sha256)
                SELECT release_id || '-duplicate', manifest_json, manifest_text,
                    manifest_sha256, input_manifest_sha256
                FROM {s}.release_metadata
            """).format(s=namespace)
        )


@pytest.mark.parametrize("state", ["missing", "empty", "multiple"])
@pytest.mark.parametrize("entrypoint", ["register", "rebuild", "iter_records", "query"])
def test_unpublished_or_ambiguous_release_fails_before_registration_or_streaming(
    postgres_dsn, published_schema, monkeypatch, state, entrypoint
):
    schema, _ = published_schema

    def unexpected_stream(*args, **kwargs):
        pytest.fail("An unpublished release must fail before reading network records")

    monkeypatch.setattr(_query, "_binary_rows", unexpected_stream)
    monkeypatch.setattr(_query, "_reaction_rows", unexpected_stream)
    with psycopg.connect(postgres_dsn) as conn:
        unpublish(conn, schema, state)
        with pytest.raises(ValueError, match="exactly one published release"):
            if entrypoint == "register":
                register(conn, schema, ())
            elif entrypoint == "rebuild":
                network_views.rebuild(conn, schema)
            elif entrypoint == "iter_records":
                list(network_views.iter_records(conn, schema, "liana"))
            else:
                network_views.query(conn, schema, "liana")
        # The guard is read-only and raises before creating a registry. Missing
        # publication tables do not leave the caller transaction aborted.
        assert conn.execute(
            "SELECT to_regclass(%s)", (schema + ".network_registry",)
        ).fetchone() == (None,)
        conn.rollback()


def test_published_release_registers_and_streams_normally(postgres_dsn, published_schema):
    schema, edge = published_schema
    with psycopg.connect(postgres_dsn) as conn:
        before = conn.execute(
            sql.SQL("SELECT release_id,manifest_sha256 FROM {}.release_metadata").format(
                sql.Identifier(schema)
            )
        ).fetchall()
        assert network_views.rebuild(conn, schema)["presets"] == 3
        records = list(network_views.iter_records(conn, schema, "liana"))
        assert len(records) == 1
        assert records[0]["relation_key"] == edge["relation_key"]
        assert network_views.query(conn, schema, "liana")["source_records"] == 1
        assert (
            conn.execute(
                sql.SQL("SELECT release_id,manifest_sha256 FROM {}.release_metadata").format(
                    sql.Identifier(schema)
                )
            ).fetchall()
            == before
        )
        conn.rollback()
        assert conn.execute(
            "SELECT to_regclass(%s)", (schema + ".network_registry",)
        ).fetchone() == (None,)


def test_unpublishing_preserves_an_existing_registry(postgres_dsn, published_schema):
    schema, _ = published_schema
    with psycopg.connect(postgres_dsn) as conn:
        network_views.rebuild(conn, schema)
        before = conn.execute(
            sql.SQL("SELECT * FROM {}.network_registry ORDER BY name").format(
                sql.Identifier(schema)
            )
        ).fetchall()
        unpublish(conn, schema, "empty")
        with pytest.raises(ValueError, match="exactly one published release"):
            network_views.rebuild(conn, schema)
        assert (
            conn.execute(
                sql.SQL("SELECT * FROM {}.network_registry ORDER BY name").format(
                    sql.Identifier(schema)
                )
            ).fetchall()
            == before
        )
        conn.rollback()
