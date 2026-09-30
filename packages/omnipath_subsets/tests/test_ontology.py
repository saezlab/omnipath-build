"""Hierarchy axioms stay separate from interactions and preserve source scope."""

import uuid

import psycopg
from psycopg import sql
import pytest

from omnipath_postgres.loader import load_release
from release_fixture import annotation, entity, relation, write_release, write_resource

pytestmark = pytest.mark.integration


def rows(dsn, schema, query, params=None):
    with psycopg.connect(dsn) as conn:
        return conn.execute(sql.SQL(query).format(s=sql.Identifier(schema)), params).fetchall()


def test_hierarchy_closure_cycles_inverse_edges_and_graph_counts(tmp_path, postgres_dsn):
    child = entity(
        "CHEMONT:0001",
        "ontology_class",
        "chemont",
        aliases=[("synonym", "Child synonym")],
        annotations=[annotation("description", "Chemical class definition")],
    )
    middle = entity("CHEMONT:0002", "ontology_class", "chemont")
    root = entity("CHEMONT:0003", "ontology_class", "chemont")
    part = entity("CHEMONT:0004", "ontology_class", "chemont")
    protein = entity("P04637", "protein", "uniprot")
    chemical = entity("15377")
    entities = [child, middle, root, part, protein, chemical]
    relations = [
        relation(
            child, "subclass_of", middle, source="chemont", row_id="1", statement_kind="ontology"
        ),
        relation(
            middle, "subclass_of", root, source="chemont", row_id="2", statement_kind="ontology"
        ),
        relation(
            root, "subclass_of", child, source="chemont", row_id="3", statement_kind="ontology"
        ),
        relation(root, "has_part", part, source="chemont", row_id="4", statement_kind="ontology"),
        relation(chemical, "interacts_with", protein, source="chemont", row_id="5"),
    ]
    write_resource(tmp_path, "chemont", entities, relations)
    schema = "test_" + uuid.uuid4().hex
    load_release(tmp_path, write_release(tmp_path, ["chemont"]), postgres_dsn, schema=schema)
    assert rows(postgres_dsn, schema, "SELECT COUNT(*) FROM {s}.statement") == [(5,)]
    assert rows(postgres_dsn, schema, "SELECT COUNT(*) FROM {s}.relation") == [(1,)]
    counts = dict(
        rows(
            postgres_dsn, schema, "SELECT entity_id,relation_count FROM {s}.entity_relation_counts"
        )
    )
    assert counts[child["entity_key"]] == 0
    assert counts[protein["entity_key"]] == counts[chemical["entity_key"]] == 1
    assert rows(
        postgres_dsn,
        schema,
        "SELECT depth FROM {s}.ontology_closure WHERE descendant_entity_id=%s AND ancestor_entity_id=%s AND hierarchy_kind='subclass'",
        (child["entity_key"], root["entity_key"]),
    ) == [(2,)]
    assert rows(
        postgres_dsn,
        schema,
        "SELECT COUNT(*) FROM {s}.ontology_closure WHERE descendant_entity_id=ancestor_entity_id",
    ) == [(0,)]
    assert rows(
        postgres_dsn,
        schema,
        "SELECT child_entity_id,parent_entity_id FROM {s}.entity_ontology_relation WHERE predicate='has_part'",
    ) == [
        (part["entity_key"], root["entity_key"]),
    ]
    summary = rows(
        postgres_dsn,
        schema,
        "SELECT definition,synonyms,child_count,parent_count FROM {s}.entity_ontology_term WHERE term_entity_id=%s",
        (child["entity_key"],),
    )[0]
    assert summary == ("Chemical class definition", ["Child synonym"], 1, 1)
    assert rows(
        postgres_dsn,
        schema,
        "SELECT has_hierarchy,parent_count,child_count FROM {s}.entity WHERE entity_id=%s",
        (root["entity_key"],),
    ) == [(True, 1, 2)]


def test_release_wide_closure_bridges_resources_without_changing_scoped_closure(
    tmp_path, postgres_dsn
):
    a, b, c = [entity("CHEMONT:" + str(i), "ontology_class", "chemont") for i in range(3)]
    write_resource(
        tmp_path,
        "first",
        [a, b, c],
        [relation(a, "subclass_of", b, source="first", statement_kind="ontology")],
    )
    write_resource(
        tmp_path,
        "second",
        [a, b, c],
        [relation(b, "subclass_of", c, source="second", statement_kind="ontology")],
    )
    schema = "test_" + uuid.uuid4().hex
    load_release(
        tmp_path, write_release(tmp_path, ["first", "second"]), postgres_dsn, schema=schema
    )
    assert rows(
        postgres_dsn,
        schema,
        "SELECT depth FROM {s}.ontology_ancestor WHERE descendant_entity_id=%s AND ancestor_entity_id=%s",
        (a["entity_key"], c["entity_key"]),
    ) == [(2,)]
    assert (
        rows(
            postgres_dsn,
            schema,
            "SELECT depth FROM {s}.ontology_closure WHERE descendant_entity_id=%s AND ancestor_entity_id=%s",
            (a["entity_key"], c["entity_key"]),
        )
        == []
    )
    assert rows(
        postgres_dsn,
        schema,
        "SELECT overlap FROM {s}.resource_overlap_summary WHERE content_kind='entity'",
    ) == [(3,)]
    assert rows(
        postgres_dsn,
        schema,
        "SELECT source_count,source_list FROM {s}.entity_source_count WHERE entity_id=%s",
        (a["entity_key"],),
    ) == [(2, ["first", "second"])]
