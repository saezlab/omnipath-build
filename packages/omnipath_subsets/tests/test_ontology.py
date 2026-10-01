"""Hierarchy axioms stay separate from interactions and preserve source scope."""

import uuid

import psycopg
from psycopg import sql
import pytest

from omnipath_postgres import ontology
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


def test_first_build_plans_with_traversal_indexes_and_current_statistics(
    tmp_path, postgres_dsn, monkeypatch
):
    # Fourteen source rows grow to 105 shortest pairs. Inspect the real planner
    # while expansion is happening, before the loader's final ANALYZE can mask
    # missing statistics or indexes on a fresh schema.
    nodes = [entity(f"CHEMONT:{i}", "ontology_class", "chemont") for i in range(15)]
    edges = [
        relation(
            child,
            "subclass_of",
            parent,
            source="chemont",
            row_id=str(i),
            statement_kind="ontology",
        )
        for i, (child, parent) in enumerate(zip(nodes, nodes[1:]))
    ]
    write_resource(tmp_path, "chemont", nodes, edges)
    schema = "test_" + uuid.uuid4().hex
    observed = {"ontology_closure": [], "ontology_ancestor": []}
    closure = ontology._closure

    def plan_nodes(plan):
        yield plan
        for child in plan.get("Plans", []):
            yield from plan_nodes(child)

    class PlanningCursor:
        def __init__(self, cur, table, scoped):
            self.cur, self.table, self.scoped = cur, table, scoped

        def __getattr__(self, name):
            return getattr(self.cur, name)

        def execute(self, query, *args, **kwargs):
            if " AS existing " in query.as_string(self.cur):
                self.inspect_planning()
            return self.cur.execute(query, *args, **kwargs)

        def inspect_planning(self):
            cur, table = self.cur, self.table
            cur.execute(
                "SELECT reltuples FROM pg_class WHERE oid=%s::regclass",
                (f"{schema}.{table}",),
            )
            estimated_rows = cur.fetchone()[0]
            cur.execute(
                sql.SQL("SELECT COUNT(*) FROM {}.{}").format(
                    sql.Identifier(schema), sql.Identifier(table)
                )
            )
            actual_rows = cur.fetchone()[0]
            cur.execute(
                "SELECT reltuples FROM pg_class WHERE oid IN "
                "(%s::regclass, 'omnipath_hierarchy_predicates'::regclass) ORDER BY reltuples",
                (f"{schema}.entity_ontology_relation",),
            )
            edge_and_predicate_rows = [row[0] for row in cur.fetchall()]
            cur.execute(
                sql.SQL("""
                EXPLAIN (FORMAT JSON)
                SELECT c.depth FROM {s}.{table} c
                JOIN {s}.entity_ontology_relation e
                    ON e.child_entity_id=c.ancestor_entity_id
                    AND e.hierarchy_kind=c.hierarchy_kind {scope}
            """).format(
                    s=sql.Identifier(schema),
                    table=sql.Identifier(table),
                    scope=sql.SQL(
                        "AND e.resource=c.resource AND e.version=c.version" if self.scoped else ""
                    ),
                )
            )
            plan = cur.fetchone()[0][0]["Plan"]
            planned_scan_rows = [
                node["Plan Rows"] for node in plan_nodes(plan) if node.get("Relation Name") == table
            ]
            observed[table].append(
                (estimated_rows, actual_rows, edge_and_predicate_rows, planned_scan_rows)
            )
            if len(observed[table]) == 1:
                # Force a selective index probe only for this EXPLAIN, then
                # restore normal costing for the actual closure expansion.
                cur.execute("SET LOCAL enable_seqscan=off")
                try:
                    cur.execute(
                        sql.SQL("""
                        EXPLAIN (FORMAT JSON)
                        SELECT * FROM {s}.entity_ontology_relation WHERE child_entity_id=%s
                    """).format(s=sql.Identifier(schema)),
                        (nodes[0]["entity_key"],),
                    )
                    edge_plan = cur.fetchone()[0][0]["Plan"]
                    assert any(
                        node.get("Index Name") == "ontology_edge_child_idx"
                        for node in plan_nodes(edge_plan)
                    )
                    cur.execute(
                        sql.SQL("""
                        EXPLAIN (FORMAT JSON)
                        SELECT * FROM {s}.{table}
                        WHERE ancestor_entity_id=%s AND hierarchy_kind='subclass'
                    """).format(s=sql.Identifier(schema), table=sql.Identifier(table)),
                        (nodes[1]["entity_key"],),
                    )
                    ancestor_plan = cur.fetchone()[0][0]["Plan"]
                    assert any(
                        node.get("Index Name") == f"{table}_ancestor_idx"
                        for node in plan_nodes(ancestor_plan)
                    )
                finally:
                    cur.execute("SET LOCAL enable_seqscan=on")

    def inspect_closure(cur, schema, table, edge_query, *, scoped):
        return closure(PlanningCursor(cur, table, scoped), schema, table, edge_query, scoped=scoped)

    monkeypatch.setattr(ontology, "_closure", inspect_closure)
    load_release(tmp_path, write_release(tmp_path, ["chemont"]), postgres_dsn, schema=schema)
    for table, rounds in observed.items():
        assert len(rounds) > 2
        assert rounds[0][1] == 14
        assert rounds[-1][1] == 105
        for estimated, actual, edge_and_predicate, planned in rounds:
            assert estimated == actual
            assert edge_and_predicate == [1, 14]
            assert planned == [actual]
        assert rows(
            postgres_dsn,
            schema,
            f"SELECT depth FROM {{s}}.{table} WHERE descendant_entity_id=%s AND ancestor_entity_id=%s",
            (nodes[0]["entity_key"], nodes[-1]["entity_key"]),
        ) == [(14,)]
