"""Fresh COPY data gets narrow key statistics before transient owner lookups."""

import json
import re
from time import perf_counter
import uuid

import psycopg
from psycopg import sql
import pytest

from omnipath_postgres import loader
from omnipath_postgres.projection import PayloadReference
from test_postgres import release, resource
from test_reactions import reaction_fixture

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("reaction", [False, True])
def test_preliminary_statistics_are_scalar_and_ready_before_all_owner_batches(
    tmp_path, postgres_dsn, monkeypatch, reaction
):
    source = "rhea" if reaction else "signor"
    resource(tmp_path, source, **({"rows": reaction_fixture()} if reaction else {}))
    expected = loader._HASH_JOIN_COLUMNS if reaction else loader._OWNER_LOOKUP_COLUMNS
    analyses = []
    batches = []
    original_analyze = loader._analyze_tables
    original_owner = loader._validate_payload_owners

    def analyze(conn, schema, tables, *, columns=None):
        if columns is not None:
            analyses.append((tuple(tables), columns))
        return original_analyze(conn, schema, tables, columns=columns)

    def owners(conn, schema, source, version, references):
        # This executes on the actual uncommitted load transaction, before final
        # ANALYZE/derivations, so autovacuum cannot supply these statistics.
        actual = conn.execute(
            "SELECT tablename,attname FROM pg_stats WHERE schemaname=%s",
            (schema,),
        ).fetchall()
        assert set(actual) == {
            (table, column) for table, columns in expected.items() for column in columns
        }
        assert analyses == [(tuple(expected), expected)]
        batches.append(len(references))
        return original_owner(conn, schema, source, version, references)

    monkeypatch.setattr(loader, "_analyze_tables", analyze)
    monkeypatch.setattr(loader, "_validate_payload_owners", owners)
    schema = "owner_stats_" + uuid.uuid4().hex
    result = loader.load_release(
        tmp_path,
        release(tmp_path, {source: "1.0.0"}),
        postgres_dsn,
        schema=schema,
        batch_size=1,
        validate_source_records=True,
    )
    assert batches == [1, 1]
    assert result.phase_seconds[f"analyze_payload_lookup_{source}"] >= 0


def _nodes(plan):
    yield plan
    for child in plan.get("Plans", ()):
        yield from _nodes(child)


def _explain(conn, statement, resource_name, version, keys, mode):
    conn.execute(sql.SQL("SET LOCAL plan_cache_mode={}").format(sql.SQL(mode)))
    query = sql.SQL("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) EXECUTE {} ({},{},{})").format(
        sql.Identifier(statement),
        sql.Literal(resource_name),
        sql.Literal(version),
        sql.Literal(keys),
    )
    return conn.execute(query, prepare=False).fetchone()[0][0]


def test_narrow_statistics_support_custom_and_generic_1024_key_index_probes(postgres_dsn, tmp_path):
    """Measure both plan modes on a fresh, wide uncommitted synthetic heap."""
    schema = "owner_plan_" + uuid.uuid4().hex
    size = 100_000
    resource_name, version = "bindingdb", "1"
    keys = [f"owner-{index:08d}" for index in range(1, size + 1, size // 1024)][:1024]
    evidence = {}
    with psycopg.connect(postgres_dsn, autocommit=True) as conn:
        conn.execute("BEGIN")
        try:
            conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
            for table, key in (("entities", "entity_key"), ("relations", "relation_key")):
                conn.execute(
                    sql.SQL(
                        "CREATE TABLE {}.{} (resource text NOT NULL,version text NOT NULL,"
                        "{} text NOT NULL,record_json jsonb NOT NULL,"
                        "PRIMARY KEY(resource,version,{}))"
                    ).format(
                        sql.Identifier(schema),
                        sql.Identifier(table),
                        sql.Identifier(key),
                        sql.Identifier(key),
                    )
                )
                conn.execute(
                    sql.SQL(
                        "INSERT INTO {}.{} SELECT %s,%s,'owner-'||lpad(n::text,8,'0'),"
                        "jsonb_build_object('padding',repeat(md5(n::text),12)) "
                        "FROM generate_series(1,%s) AS series(n)"
                    ).format(sql.Identifier(schema), sql.Identifier(table)),
                    (resource_name, version, size),
                )
                conn.execute(
                    sql.SQL(
                        "PREPARE {} (text,text,text[]) AS SELECT {} FROM {}.{} "
                        "WHERE resource=$1 AND version=$2 AND {}=ANY($3)"
                    ).format(
                        sql.Identifier("lookup_" + table),
                        sql.Identifier(key),
                        sql.Identifier(schema),
                        sql.Identifier(table),
                        sql.Identifier(key),
                    )
                )
            assert conn.execute(
                "SELECT count(*) FROM pg_stats WHERE schemaname=%s", (schema,)
            ).fetchone() == (0,)
            for stage in ("before", "after"):
                if stage == "after":
                    started = perf_counter()
                    loader._analyze_tables(
                        conn,
                        schema,
                        loader._OWNER_LOOKUP_COLUMNS,
                        columns=loader._OWNER_LOOKUP_COLUMNS,
                    )
                    evidence["analyze_seconds"] = perf_counter() - started
                for table, key in (("entities", "entity_key"), ("relations", "relation_key")):
                    for mode in ("force_custom_plan", "force_generic_plan"):
                        result = _explain(
                            conn, "lookup_" + table, resource_name, version, keys, mode
                        )
                        plan = result["Plan"]
                        evidence[f"{stage}_{table}_{mode}"] = {
                            "nodes": [node["Node Type"] for node in _nodes(plan)],
                            "estimated_rows": plan["Plan Rows"],
                            "actual_rows": plan["Actual Rows"],
                            "execution_ms": result["Execution Time"],
                            "shared_hit_blocks": plan.get("Shared Hit Blocks", 0),
                            "shared_read_blocks": plan.get("Shared Read Blocks", 0),
                            "index_conditions": [
                                node["Index Cond"] for node in _nodes(plan) if "Index Cond" in node
                            ],
                            "filter": plan.get("Filter"),
                            "rows_removed_by_filter": plan.get("Rows Removed by Filter", 0),
                            "exact_heap_blocks": plan.get("Exact Heap Blocks", 0),
                        }
                        assert plan["Actual Rows"] == len(keys)
                        if stage == "after":
                            nodes = list(_nodes(plan))
                            assert not any(
                                node["Node Type"] in {"Seq Scan", "Parallel Seq Scan"}
                                for node in nodes
                            )
                            probes = [node for node in nodes if "Index Cond" in node]
                            assert probes
                            assert any(
                                all(
                                    column in node["Index Cond"]
                                    for column in ("resource", "version", key)
                                )
                                for node in probes
                            )
            # Owner validation still sees all duplicate/scoped references and
            # rejects a missing key; no planner change weakens integrity.
            references = [
                PayloadReference(index, "entity", key, None, None, None, None)
                for index, key in enumerate(keys)
            ]
            loader._validate_payload_owners(conn, schema, resource_name, version, references)
            with pytest.raises(ValueError, match="absent entity owner"):
                loader._validate_payload_owners(
                    conn,
                    schema,
                    resource_name,
                    version,
                    references
                    + [PayloadReference(1024, "entity", "missing", None, None, None, None)],
                )
        finally:
            conn.rollback()
    # Keep the full conditions for review while omitting 1024 literal key values
    # from normal test output. Generic conditions remain exact parameter forms.
    artifact = tmp_path / "owner-lookup-plans.json"
    artifact.write_text(json.dumps(evidence, indent=2, sort_keys=True))
    summary = {}
    for label, details in evidence.items():
        if not isinstance(details, dict):
            summary[label] = details
            continue
        summary[label] = {
            **details,
            "index_conditions": [
                re.sub(r"'\{[^']*\}'::text\[\]", "'{1024 keys}'::text[]", condition)
                for condition in details["index_conditions"]
            ],
            "filter": re.sub(r"'\{[^']*\}'::text\[\]", "'{1024 keys}'::text[]", details["filter"])
            if details["filter"] is not None
            else None,
        }
    print(
        "owner_lookup_plans="
        + json.dumps({"exact_conditions_artifact": str(artifact), **summary}, sort_keys=True)
    )
