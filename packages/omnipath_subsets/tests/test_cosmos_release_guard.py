"""COSMOS must reject staged releases before creating or truncating products."""

import uuid

import psycopg
from psycopg import sql
import pytest

from omnipath_postgres.loader import load_release
from omnipath_postgres.reactions import rebuild_reactions
from omnipath_subsets import cosmos
from release_fixture import (
    entity,
    relation,
    source_context_annotations,
    write_release,
    write_resource,
)

pytestmark = pytest.mark.integration


@pytest.fixture
def published_reactions(tmp_path, postgres_dsn):
    event = entity("RX1", "molecular_activity", "rhea")
    input_ = entity("1")
    output = entity("2")
    raw = {"direction": "LEFT-TO-RIGHT"}
    edges = [
        relation(
            event,
            predicate,
            participant,
            source="rhea",
            dataset="reactions",
            annotations=source_context_annotations(raw, source="rhea", dataset="reactions"),
        )
        for predicate, participant in (("has_input", input_), ("has_output", output))
    ]
    write_resource(tmp_path, "rhea", [event, input_, output], edges)
    schema = "cosmos_ready_" + uuid.uuid4().hex
    load_release(tmp_path, write_release(tmp_path, ["rhea"]), postgres_dsn, schema=schema)
    return schema


def unpublish(conn, schema, state):
    namespace = sql.Identifier(schema)
    if state == "empty":
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


@pytest.mark.parametrize("state", ["empty", "multiple"])
def test_unpublished_release_does_not_create_cosmos_tables(
    postgres_dsn, published_reactions, state
):
    schema = published_reactions
    with psycopg.connect(postgres_dsn) as conn:
        unpublish(conn, schema, state)
        with pytest.raises(ValueError, match="exactly one loaded release"):
            cosmos.rebuild(conn, schema)
        for table in ("cosmos_edge", "cosmos_label"):
            assert conn.execute("SELECT to_regclass(%s)", (schema + "." + table,)).fetchone() == (
                None,
            )
        conn.rollback()


@pytest.mark.parametrize("state", ["empty", "multiple"])
def test_unpublished_release_does_not_truncate_existing_cosmos_products(
    postgres_dsn, published_reactions, state
):
    schema = published_reactions
    namespace = sql.Identifier(schema)
    with psycopg.connect(postgres_dsn) as conn:
        assert cosmos.rebuild(conn, schema)["edges"] == 3
        conn.commit()
        before = {
            table: conn.execute(
                sql.SQL("SELECT * FROM {}.{} ORDER BY 1").format(namespace, sql.Identifier(table))
            ).fetchall()
            for table in ("cosmos_edge", "cosmos_label")
        }
        assert all(before.values())
        unpublish(conn, schema, state)
        with pytest.raises(ValueError, match="exactly one loaded release"):
            cosmos.rebuild(conn, schema)
        # Inspect before rolling back: a caller catching ValueError must not
        # inherit truncated products or reset sequence state from _ensure_tables.
        for table in before:
            assert (
                conn.execute(
                    sql.SQL("SELECT * FROM {}.{} ORDER BY 1").format(
                        namespace, sql.Identifier(table)
                    )
                ).fetchall()
                == before[table]
            )
        conn.rollback()


def test_raw_reaction_derivation_remains_available_before_publication(
    postgres_dsn, published_reactions
):
    schema = published_reactions
    with psycopg.connect(postgres_dsn) as conn:
        unpublish(conn, schema, "empty")
        stats = rebuild_reactions(conn, schema)
        assert stats["contexts"] == 1
        assert stats["participants"] == 2
        with pytest.raises(ValueError, match="exactly one loaded release"):
            cosmos.rebuild(conn, schema)
        conn.rollback()
