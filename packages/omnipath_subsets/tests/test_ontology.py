"""Hierarchy axioms stay separate from interactions and preserve source scope."""

import re
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
    # Two resources publish the same resolved axiom. Global traversal deduplicates
    # the adjacency, while both original relation/evidence records remain intact.
    write_resource(
        tmp_path,
        "chemont_duplicate",
        [child, middle],
        [
            relation(
                child, "subclass_of", middle, source="chemont_duplicate", statement_kind="ontology"
            )
        ],
    )
    schema = "test_" + uuid.uuid4().hex
    load_release(
        tmp_path,
        write_release(tmp_path, ["chemont", "chemont_duplicate"]),
        postgres_dsn,
        schema=schema,
    )
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
    assert rows(
        postgres_dsn,
        schema,
        "SELECT resource,predicate FROM {s}.entity_ontology_relation "
        "WHERE child_entity_id=%s AND parent_entity_id=%s ORDER BY resource",
        (child["entity_key"], middle["entity_key"]),
    ) == [("chemont", "subclass_of"), ("chemont_duplicate", "subclass_of")]
    assert rows(
        postgres_dsn,
        schema,
        "SELECT COUNT(*) FROM {s}.evidence WHERE relation_key=%s",
        (relations[0]["relation_key"],),
    ) == [(2,)]
    with psycopg.connect(postgres_dsn) as conn, conn.transaction(force_rollback=True):
        tables = ("entity_ontology_relation", "ontology_closure", "ontology_ancestor")
        before = {
            table: set(
                conn.execute(
                    sql.SQL("SELECT * FROM {}.{}").format(
                        sql.Identifier(schema), sql.Identifier(table)
                    )
                ).fetchall()
            )
            for table in tables
        }
        for _ in range(2):
            ontology.rebuild_ontology(conn, schema)
            for table in tables:
                assert (
                    set(
                        conn.execute(
                            sql.SQL("SELECT * FROM {}.{}").format(
                                sql.Identifier(schema), sql.Identifier(table)
                            )
                        ).fetchall()
                    )
                    == before[table]
                )
            assert (
                conn.execute(
                    "SELECT relname FROM pg_class WHERE relnamespace=pg_my_temp_schema() "
                    "AND relname LIKE 'omnipath_ontology_%'"
                ).fetchall()
                == []
            )


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


def test_first_build_plans_with_frontier_indexes_and_current_statistics(
    tmp_path, postgres_dsn, monkeypatch
):
    # Fourteen source rows produce 105 pairs. Observe the real traversal inputs:
    # the frontier shrinks each round instead of revisiting the accumulated pairs.
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
            text = query.as_string(self.cur)
            if "MIN(c.depth + 1)" in text:
                frontier = re.search(r'FROM "pg_temp"\."([^"]+)" c', text)[1]
                adjacency = re.search(r'JOIN "pg_temp"\."([^"]+)" e', text)[1]
                self.inspect_planning(frontier, adjacency)
            return self.cur.execute(query, *args, **kwargs)

        def inspect_planning(self, frontier, adjacency):
            cur, table = self.cur, self.table
            cur.execute(
                sql.SQL("SELECT COUNT(*), MIN(depth), MAX(depth) FROM pg_temp.{}").format(
                    sql.Identifier(frontier)
                )
            )
            actual, minimum, maximum = cur.fetchone()
            cur.execute(
                "SELECT reltuples FROM pg_class WHERE oid=%s::regclass",
                (f"pg_temp.{frontier}",),
            )
            estimated = cur.fetchone()[0]
            cur.execute(
                "SELECT reltuples FROM pg_class WHERE oid=%s::regclass",
                (f"pg_temp.{adjacency}",),
            )
            edge_estimate = cur.fetchone()[0]
            cur.execute(
                sql.SQL("SELECT COUNT(*) FROM {}.{}").format(
                    sql.Identifier(schema), sql.Identifier(table)
                )
            )
            accumulated = cur.fetchone()[0]
            scope = sql.SQL(
                "AND e.resource=c.resource AND e.version=c.version" if self.scoped else ""
            )
            cur.execute(
                sql.SQL("""
                EXPLAIN (FORMAT JSON)
                SELECT c.depth FROM pg_temp.{frontier} c
                JOIN pg_temp.{adjacency} e
                    ON e.child_entity_id=c.ancestor_entity_id
                    AND e.hierarchy_kind=c.hierarchy_kind {scope}
            """).format(
                    frontier=sql.Identifier(frontier),
                    adjacency=sql.Identifier(adjacency),
                    scope=scope,
                )
            )
            plan = cur.fetchone()[0][0]["Plan"]
            planned = [
                node["Plan Rows"]
                for node in plan_nodes(plan)
                if node.get("Relation Name") == frontier
            ]
            observed[table].append(
                (estimated, actual, minimum, maximum, edge_estimate, accumulated, planned)
            )
            if len(observed[table]) == 1:
                # Verify usable indexes on both narrow traversal inputs without
                # changing normal planner costing for the real expansion query.
                cur.execute("SET LOCAL enable_seqscan=off")
                try:
                    for name, column, value in (
                        (adjacency, "child_entity_id", nodes[0]["entity_key"]),
                        (frontier, "ancestor_entity_id", nodes[1]["entity_key"]),
                    ):
                        cur.execute(
                            sql.SQL("""
                            EXPLAIN (FORMAT JSON)
                            SELECT * FROM pg_temp.{name}
                            WHERE {column}=%s AND hierarchy_kind='subclass' {scope}
                        """).format(
                                name=sql.Identifier(name),
                                column=sql.Identifier(column),
                                scope=sql.SQL(
                                    "AND resource='chemont' AND version='1.0.0'"
                                    if self.scoped
                                    else ""
                                ),
                            ),
                            (value,),
                        )
                        index_plan = cur.fetchone()[0][0]["Plan"]
                        assert any(
                            node.get("Relation Name") == name and node.get("Index Name")
                            for node in plan_nodes(index_plan)
                        )
                finally:
                    cur.execute("SET LOCAL enable_seqscan=on")

    def inspect_closure(cur, schema, table, edge_query, *, scoped):
        return closure(PlanningCursor(cur, table, scoped), schema, table, edge_query, scoped=scoped)

    monkeypatch.setattr(ontology, "_closure", inspect_closure)
    load_release(tmp_path, write_release(tmp_path, ["chemont"]), postgres_dsn, schema=schema)
    for table, rounds in observed.items():
        assert len(rounds) == 14
        assert [item[1] for item in rounds] == list(range(14, 0, -1))
        assert rounds[-1][5] == 105
        # Each discovered pair is expanded once, rather than each cumulative
        # closure being expanded again. Every frontier has one BFS distance.
        assert sum(item[1] for item in rounds) == 105
        for level, (estimated, actual, minimum, maximum, edge_estimate, _, planned) in enumerate(
            rounds, 1
        ):
            assert estimated == actual
            assert minimum == maximum == level
            assert edge_estimate == 14
            assert planned == [actual]
        assert rows(
            postgres_dsn,
            schema,
            f"SELECT depth FROM {{s}}.{table} WHERE descendant_entity_id=%s AND ancestor_entity_id=%s",
            (nodes[0]["entity_key"], nodes[-1]["entity_key"]),
        ) == [(14,)]


def closure_fixture(conn, edges):
    """Exercise the SQL closure contract directly, without any resource build."""
    schema = "test_" + uuid.uuid4().hex
    conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    conn.execute(
        sql.SQL("""
        CREATE TABLE {s}.edges (
            resource text, version text, hierarchy_kind text,
            child_entity_id text, parent_entity_id text, provenance text
        )
    """).format(s=sql.Identifier(schema))
    )
    if edges:
        with conn.cursor() as cur:
            cur.executemany(
                sql.SQL("INSERT INTO {}.edges VALUES (%s,%s,%s,%s,%s,%s)").format(
                    sql.Identifier(schema)
                ),
                edges,
            )
    for table, dimensions, primary in (
        ("ontology_closure", "resource text NOT NULL, version text NOT NULL,", "resource,version,"),
        ("ontology_ancestor", "", ""),
    ):
        conn.execute(
            sql.SQL("""
            CREATE TABLE {s}.{table} (
                {dimensions}hierarchy_kind text NOT NULL,
                descendant_entity_id text NOT NULL, ancestor_entity_id text NOT NULL,
                depth integer NOT NULL CHECK(depth>0),
                PRIMARY KEY ({primary}hierarchy_kind,descendant_entity_id,ancestor_entity_id)
            )
        """).format(
                s=sql.Identifier(schema),
                table=sql.Identifier(table),
                dimensions=sql.SQL(dimensions),
                primary=sql.SQL(primary),
            )
        )
    return schema


def rebuild_closures(conn, schema):
    for table, scoped in (("ontology_closure", True), ("ontology_ancestor", False)):
        with conn.cursor() as cur:
            ontology._closure(
                cur,
                schema,
                table,
                sql.SQL("SELECT * FROM {}.edges").format(sql.Identifier(schema)),
                scoped=scoped,
            )
        assert (
            conn.execute(
                "SELECT relname FROM pg_class WHERE relnamespace=pg_my_temp_schema() "
                "AND relname LIKE 'omnipath_ontology_%'"
            ).fetchall()
            == []
        )


def shortest_pairs(edges, *, scoped):
    # An independent Floyd-Warshall oracle, rather than reproducing frontier SQL.
    groups = {}
    for resource, version, kind, child, parent, _ in edges:
        group = (resource, version, kind) if scoped else (kind,)
        groups.setdefault(group, []).append((child, parent))
    result = set()
    for group, pairs in groups.items():
        nodes = {node for pair in pairs for node in pair}
        distance = {(node, node): 0 for node in nodes}
        for child, parent in pairs:
            distance[child, parent] = min(distance.get((child, parent), float("inf")), 1)
        for middle in nodes:
            for child in nodes:
                for parent in nodes:
                    candidate = distance.get((child, middle), float("inf")) + distance.get(
                        (middle, parent), float("inf")
                    )
                    if candidate < distance.get((child, parent), float("inf")):
                        distance[child, parent] = candidate
        result.update(
            (*group, child, parent, depth)
            for (child, parent), depth in distance.items()
            if child != parent
        )
    return result


def test_frontier_shortest_pairs_scope_kinds_cycles_and_duplicate_provenance(postgres_dsn):
    edges = [
        ("first", "1", "subclass", "a", "b", "ab-first"),
        ("first", "1", "subclass", "b", "c", "bc"),
        ("first", "1", "subclass", "c", "d", "cd"),
        ("first", "1", "subclass", "a", "d", "shortcut"),
        ("first", "1", "subclass", "d", "a", "cycle"),
        ("first", "1", "subclass", "a", "b", "ab-duplicate"),
        ("first", "1", "part_of", "d", "e", "de-part"),
        ("first", "1", "part_of", "e", "f", "ef-part"),
        ("first", "2", "subclass", "b", "z", "other-version"),
        ("second", "1", "subclass", "d", "g", "other-resource"),
        ("first", "1", "subclass", "a", "a", "self"),
    ]
    with psycopg.connect(postgres_dsn) as conn, conn.transaction(force_rollback=True):
        schema = closure_fixture(conn, edges)
        for _ in range(2):
            rebuild_closures(conn, schema)
            for table, scoped in (("ontology_closure", True), ("ontology_ancestor", False)):
                actual = set(
                    conn.execute(
                        sql.SQL("SELECT * FROM {}.{}").format(
                            sql.Identifier(schema), sql.Identifier(table)
                        )
                    ).fetchall()
                )
                assert actual == shortest_pairs(edges, scoped=scoped)
            assert set(
                conn.execute(
                    sql.SQL("SELECT * FROM {}.edges").format(sql.Identifier(schema))
                ).fetchall()
            ) == set(edges)
        global_pairs = shortest_pairs(edges, scoped=False)
        assert ("subclass", "a", "d", 1) in global_pairs
        assert ("subclass", "a", "z", 2) in global_pairs
        assert ("subclass", "a", "g", 2) in global_pairs
        assert not any(child == "a" and parent == "e" for _, child, parent, _ in global_pairs)
        with psycopg.connect(postgres_dsn) as observer:
            assert (
                observer.execute(
                    "SELECT 1 FROM pg_namespace WHERE nspname=%s", (schema,)
                ).fetchall()
                == []
            )
    # Closure construction never committed the outer transaction.
    with psycopg.connect(postgres_dsn) as observer:
        assert (
            observer.execute("SELECT 1 FROM pg_namespace WHERE nspname=%s", (schema,)).fetchall()
            == []
        )


def test_frontier_deep_path_and_empty_self_only_rebuilds(postgres_dsn):
    edges = [("source", "1", "subclass", str(i), str(i + 1), str(i)) for i in range(25)]
    with psycopg.connect(postgres_dsn) as conn, conn.transaction(force_rollback=True):
        schema = closure_fixture(conn, edges)
        rebuild_closures(conn, schema)
        assert conn.execute(
            sql.SQL(
                "SELECT depth FROM {}.ontology_ancestor "
                "WHERE descendant_entity_id='0' AND ancestor_entity_id='25'"
            ).format(sql.Identifier(schema))
        ).fetchall() == [(25,)]
        assert (
            conn.execute(
                sql.SQL("SELECT COUNT(*) FROM {}.ontology_closure").format(sql.Identifier(schema))
            ).fetchone()[0]
            == 325
        )
        conn.execute(sql.SQL("TRUNCATE {}.edges").format(sql.Identifier(schema)))
        for self_only in (False, True):
            if self_only:
                conn.execute(
                    sql.SQL(
                        "INSERT INTO {}.edges VALUES ('source','1','subclass','a','a','self')"
                    ).format(sql.Identifier(schema))
                )
            rebuild_closures(conn, schema)
            for table in ("ontology_closure", "ontology_ancestor"):
                assert (
                    conn.execute(
                        sql.SQL("SELECT COUNT(*) FROM {}.{}").format(
                            sql.Identifier(schema), sql.Identifier(table)
                        )
                    ).fetchone()[0]
                    == 0
                )


def test_frontier_temporary_tables_do_not_touch_search_path_names(postgres_dsn, monkeypatch):
    token = uuid.UUID(int=0)
    monkeypatch.setattr(ontology, "uuid4", lambda: token)
    with psycopg.connect(postgres_dsn) as conn, conn.transaction(force_rollback=True):
        schema = closure_fixture(conn, [("source", "1", "subclass", "a", "b", "raw")])
        names = [
            f"omnipath_ontology_{role}_{token.hex}" for role in ("adjacency", "frontier", "next")
        ]
        for name in names:
            conn.execute(
                sql.SQL("CREATE TABLE {}.{} (marker text)").format(
                    sql.Identifier(schema), sql.Identifier(name)
                )
            )
            conn.execute(
                sql.SQL("INSERT INTO {}.{} VALUES ('untouched')").format(
                    sql.Identifier(schema), sql.Identifier(name)
                )
            )
        conn.execute(
            sql.SQL("SET LOCAL search_path TO {}, pg_catalog").format(sql.Identifier(schema))
        )
        rebuild_closures(conn, schema)
        rebuild_closures(conn, schema)
        for name in names:
            assert conn.execute(
                sql.SQL("SELECT marker FROM {}.{}").format(
                    sql.Identifier(schema), sql.Identifier(name)
                )
            ).fetchall() == [("untouched",)]


def test_frontier_failure_rolls_back_output_and_working_tables(postgres_dsn):
    with psycopg.connect(postgres_dsn) as conn, conn.transaction(force_rollback=True):
        schema = closure_fixture(
            conn,
            [
                ("source", "1", "subclass", "a", "b", "ab"),
                ("source", "1", "subclass", "b", "c", "bc"),
            ],
        )
        rebuild_closures(conn, schema)
        before = set(
            conn.execute(
                sql.SQL("SELECT * FROM {}.ontology_ancestor").format(sql.Identifier(schema))
            ).fetchall()
        )

        class FailingCursor:
            def __init__(self, cur):
                self.cur = cur

            def __getattr__(self, name):
                return getattr(self.cur, name)

            def execute(self, query, *args, **kwargs):
                if "MIN(c.depth + 1)" in query.as_string(self.cur):
                    raise RuntimeError("interrupted expansion")
                return self.cur.execute(query, *args, **kwargs)

        with pytest.raises(RuntimeError, match="interrupted expansion"):
            with conn.transaction(), conn.cursor() as cur:
                ontology._closure(
                    FailingCursor(cur),
                    schema,
                    "ontology_ancestor",
                    sql.SQL("SELECT * FROM {}.edges").format(sql.Identifier(schema)),
                    scoped=False,
                )
        assert (
            set(
                conn.execute(
                    sql.SQL("SELECT * FROM {}.ontology_ancestor").format(sql.Identifier(schema))
                ).fetchall()
            )
            == before
        )
        assert (
            conn.execute(
                "SELECT relname FROM pg_class WHERE relnamespace=pg_my_temp_schema() "
                "AND relname LIKE 'omnipath_ontology_%'"
            ).fetchall()
            == []
        )
