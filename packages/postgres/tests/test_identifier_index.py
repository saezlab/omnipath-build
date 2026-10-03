"""Long published identifiers fit the bounded index without changing lookup values."""

import random
import uuid

import psycopg
from psycopg import sql
import pytest

from omnipath_postgres.compatibility.record_layout.indexes import _INDEXES
from omnipath_postgres.compatibility.record_layout.loader import load_release
from test_postgres import query, release, resource
from test_projection import ENTITY_A, ENTITY_B, fixture_rows

pytestmark = pytest.mark.integration
NS = "published_alias"


def _long_values():
    # Assigned supplementary CJK characters occupy four UTF-8 bytes apiece.
    # Random values resist PostgreSQL's compression, unlike repeated padding.
    rng = random.Random(873104)

    def characters(size):
        return "".join(chr(rng.randrange(0x20000, 0x2A6DF)) for _ in range(size))

    common = "SAMECASE" + characters(248)
    assert len(common) == 256
    return common, common + characters(3000), common + characters(3000)


@pytest.fixture
def loaded_long_identifiers(tmp_path, postgres_dsn):
    common, first, second = _long_values()
    rows = fixture_rows()
    rows[0][0]["identifiers"].extend(
        {"ns": NS, "id": value, "is_canonical": False, "source": "published"}
        for value in (first, None, "")
    )
    rows[0][1]["identifiers"] = [
        {"ns": NS, "id": second, "is_canonical": False, "source": "published"}
    ]
    resource(tmp_path, rows=rows)
    schema = "long_alias_" + uuid.uuid4().hex
    result = load_release(tmp_path, release(tmp_path), postgres_dsn, schema=schema)
    assert result.counts["identifiers"] == 6
    return {
        "dsn": postgres_dsn,
        "schema": schema,
        "common": common,
        "first": first,
        "second": second,
    }


def _nodes(plan):
    yield plan
    for child in plan.get("Plans", ()):
        yield from _nodes(child)


def test_long_unicode_identifiers_survive_load_view_and_exact_catalog_definition(
    loaded_long_identifiers,
):
    fixture = loaded_long_identifiers
    dsn, schema = fixture["dsn"], fixture["schema"]
    assert len(fixture["first"].encode("utf-8")) > 12_000
    assert query(
        dsn,
        schema,
        "SELECT entity_key,id FROM {s}.identifiers WHERE ns=%s AND id IS NOT NULL AND id<>'' ORDER BY entity_key",
        (NS,),
    ) == sorted([(ENTITY_A, fixture["first"]), (ENTITY_B, fixture["second"])])
    assert query(
        dsn,
        schema,
        "SELECT entity_id,id FROM {s}.entity_identifier_lookup WHERE ns=%s AND id IS NOT NULL AND id<>'' ORDER BY entity_id",
        (NS,),
    ) == sorted([(ENTITY_A, fixture["first"]), (ENTITY_B, fixture["second"])])
    assert query(
        dsn,
        schema,
        "SELECT id FROM {s}.entity_identifier_lookup WHERE ns=%s AND (id IS NULL OR id='') ORDER BY id NULLS FIRST",
        (NS,),
    ) == [(None,), ("",)]
    records = dict(query(dsn, schema, "SELECT entity_key,record_json FROM {s}.entities"))
    assert fixture["first"] in [item["id"] for item in records[ENTITY_A]["identifiers"]]
    assert fixture["second"] == records[ENTITY_B]["identifiers"][0]["id"]
    with psycopg.connect(dsn) as conn:
        indexes = conn.execute(
            "SELECT i.relname,x.indisvalid,x.indisready FROM pg_index x "
            "JOIN pg_class i ON i.oid=x.indexrelid "
            "JOIN pg_namespace n ON n.oid=i.relnamespace WHERE n.nspname=%s",
            (schema,),
        ).fetchall()
        valid_indexes = {name for name, valid, ready in indexes if valid and ready}
        assert {name for name, _table, _expression in _INDEXES} <= valid_indexes
        definition = conn.execute(
            "SELECT pg_get_indexdef(indexrelid) FROM pg_index WHERE indexrelid=%s::regclass",
            (schema + ".identifiers_lookup_idx",),
        ).fetchone()[0]
    assert 'ns, "left"(lower(id), 256) text_pattern_ops, entity_key' in definition


def test_legacy_unbounded_index_fails_for_same_published_values(loaded_long_identifiers):
    fixture = loaded_long_identifiers
    namespace = sql.Identifier(fixture["schema"])
    with psycopg.connect(fixture["dsn"]) as conn:
        conn.execute("SAVEPOINT legacy_index")
        conn.execute(sql.SQL("DROP INDEX {}.identifiers_lookup_idx").format(namespace))
        with pytest.raises(psycopg.errors.ProgramLimitExceeded):
            conn.execute(
                sql.SQL(
                    "CREATE INDEX identifiers_lookup_idx ON {}.identifiers "
                    "(ns,lower(id) text_pattern_ops,entity_key)"
                ).format(namespace)
            )
        # Restores the successful bounded index and leaves the load untouched.
        conn.execute("ROLLBACK TO SAVEPOINT legacy_index")
        conn.execute("RELEASE SAVEPOINT legacy_index")
        assert conn.execute(
            "SELECT indisvalid FROM pg_index WHERE indexrelid=%s::regclass",
            (fixture["schema"] + ".identifiers_lookup_idx",),
        ).fetchone() == (True,)


def test_same_256_character_prefix_is_disambiguated_by_full_exact_and_prefix_predicates(
    loaded_long_identifiers,
):
    fixture = loaded_long_identifiers
    schema = fixture["schema"]
    bounded = 'ns=%s AND "left"(lower(id),256)="left"(lower(%s),256)'
    assert set(
        query(
            fixture["dsn"],
            schema,
            "SELECT entity_key FROM {s}.identifiers WHERE " + bounded,
            (NS, fixture["first"]),
        )
    ) == {(ENTITY_A,), (ENTITY_B,)}
    exact = "SELECT entity_key FROM {s}.identifiers WHERE " + bounded + " AND lower(id)=lower(%s)"
    for key, value in ((ENTITY_A, fixture["first"]), (ENTITY_B, fixture["second"])):
        assert query(fixture["dsn"], schema, exact, (NS, value, value)) == [(key,)]
    # This prefix is longer than the stored index expression, so the full prefix
    # check is essential to separate the otherwise identical index candidates.
    prefix = fixture["first"][:270]
    assert len(prefix) > 256
    assert not fixture["second"].startswith(prefix)
    prefix_sql = (
        "SELECT entity_key FROM {s}.identifiers WHERE ns=%s "
        'AND "left"(lower(id),256) LIKE "left"(lower(%s),256)||\'%%\' '
        "AND lower(id) LIKE lower(%s)||'%%'"
    )
    assert query(fixture["dsn"], schema, prefix_sql, (NS, prefix, prefix)) == [(ENTITY_A,)]
    with psycopg.connect(fixture["dsn"]) as conn:
        # The small fixture rationally prefers a sequential scan; disabling it
        # here proves expression-index eligibility, without claiming speed.
        conn.execute("SET LOCAL enable_seqscan=off")
        for statement, params in (
            (exact, (NS, fixture["first"], fixture["first"])),
            (prefix_sql, (NS, prefix, prefix)),
        ):
            plan = conn.execute(
                sql.SQL("EXPLAIN (FORMAT JSON) " + statement).format(s=sql.Identifier(schema)),
                params,
            ).fetchone()[0][0]["Plan"]
            assert any(node.get("Index Name") == "identifiers_lookup_idx" for node in _nodes(plan))
