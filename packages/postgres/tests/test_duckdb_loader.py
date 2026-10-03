"""SQL/CSV COPY preserves published rows and rolls back partial release writes."""

from collections import defaultdict
from copy import deepcopy
import uuid

import duckdb
import psycopg
from psycopg import sql
import pytest

from omnipath_postgres.compatibility.record_layout import bulk, loader
from omnipath_postgres.compatibility.record_layout.projection import iter_resource_records
from test_postgres import exists, query, release, resource
from test_projection import QUANTITY, annotation, fixture_rows

pytestmark = pytest.mark.integration

_TEXT = 'comma, "quoted" \\path\r\nline\tα ≤ 😀'
_SORT_COLUMNS = {
    "entities": ("resource", "version", "entity_key"),
    "identifiers": ("resource", "version", "entity_key", "ordinal"),
    "relations": ("resource", "version", "relation_key"),
    "evidence": ("resource", "version", "relation_key", "ordinal"),
    "annotations": (
        "resource",
        "version",
        "owner_kind",
        "owner_key",
        "evidence_ordinal",
        "ordinal",
    ),
}


def _schema():
    return "duckdb_copy_" + uuid.uuid4().hex


def _order(table, row):
    return tuple(-1 if row[name] is None else row[name] for name in _SORT_COLUMNS[table])


def _adversarial_rows():
    """Three base rows per table; duplicates, nulls and measurements at all scopes."""
    entities, relations, payloads = fixture_rows()
    a, b, c = entities
    a.update(
        identifier=r"\N",
        namespace="",
        taxon="",
        label=_TEXT,
        has_hierarchy=None,
        parent_count=2**63 - 1,
        child_count=None,
    )
    a["identifiers"].extend(
        [
            {"ns": None, "id": "", "is_canonical": None, "source": r"\N"},
            {"ns": "", "id": r"\N", "is_canonical": False, "source": _TEXT},
            {"ns": _TEXT, "id": None, "is_canonical": True, "source": None},
        ]
    )
    for value in (None, "", r"\N", _TEXT):
        item = annotation("description", value=value)
        item.pop("scope")
        a["annotations"].append(item)
    for number in (1e-300, 1.2345678901234567, 0.0):
        quantity = {**QUANTITY, "has_numeric_value": number, "source_field": _TEXT}
        item = annotation(quantity=quantity)
        item.pop("scope")
        a["annotations"].append(item)
    # A present all-null quantity object must stay different from SQL NULL.
    item = annotation(quantity=dict.fromkeys(QUANTITY))
    item.pop("scope")
    a["annotations"].append(item)
    b.update(identifier=None, label="", namespace=None, taxon=None)
    c["label"] = r"\N"
    relation = relations[0]
    relation.update(
        subject_label=_TEXT,
        object_label=None,
        category="",
        interaction_class=None,
        sources=[None, "", r"\N", _TEXT],
    )
    first, second = relation["evidence"]
    first.update(source=None, dataset="", row_id=r"\N", upstream_id=_TEXT)
    first["annotations"].append(annotation("description", value=_TEXT, scope=r"\N"))
    first["annotations"][0].update(source=None, dataset="")
    first["annotations"][0]["quantity"]["has_numeric_value"] = 1e-300
    second.update(source="", dataset=None, row_id="", upstream_id=None, annotations=None)
    relation["evidence"].append(deepcopy(first))
    relation["evidence_count"] = 3
    relation["annotations"][0].update(value=r"\N", scope="")
    relation["annotations"][0]["quantity"]["has_numeric_value"] = 1.2345678901234567
    for suffix, sources, evidence, annotations in (
        ("/null-lists", None, None, []),
        ("/empty-lists", [], [], None),
    ):
        extra = deepcopy(relation)
        extra.update(
            relation_key=relation["relation_key"] + suffix,
            sources=sources,
            evidence=evidence,
            annotations=annotations,
            evidence_count=0,
            is_directed=None,
            taxon=None,
        )
        relations.append(extra)
    return entities, relations, payloads


@pytest.mark.parametrize("threads", [1, 2])
def test_every_normalized_column_and_full_json_survives_csv_copy(
    tmp_path, postgres_dsn, monkeypatch, threads
):
    data_root = tmp_path / "quoted='data' α"
    directory = resource(data_root, rows=_adversarial_rows())
    expected = defaultdict(list)
    for record in iter_resource_records(directory, "signor", "1.0.0", batch_size=1):
        expected[record.table].append(record.values)
    # Split UTF-8, escaped CSV fields and JSON across many write calls.
    monkeypatch.setattr(bulk, "_COPY_BYTES", 17)
    spool = tmp_path / "private='staging'"
    spool.mkdir()
    marker = spool / "keep.txt"
    marker.write_text("unrelated staging-parent file")
    schema = _schema()
    result = loader.load_release(
        data_root,
        release(data_root),
        postgres_dsn,
        schema=schema,
        duckdb_threads=threads,
        memory_limit="64MB",
        temp_directory=spool,
    )
    assert result.counts == {table: len(expected[table]) for table in loader.COLUMNS}
    for table, columns in loader.COLUMNS.items():
        actual = [
            dict(zip(columns, row, strict=True))
            for row in query(
                postgres_dsn, schema, "SELECT " + ",".join(columns) + " FROM {s}." + table
            )
        ]
        assert sorted(actual, key=lambda row: _order(table, row)) == sorted(
            expected[table], key=lambda row: _order(table, row)
        ), table
        assert result.phase_seconds[f"stage_signor_{table}"] >= 0
        assert result.phase_seconds[f"copy_signor_{table}"] >= 0
    assert result.validate_source_records is False
    assert result.validated_payload_rows == {}
    assert query(postgres_dsn, schema, "SELECT to_regclass(%s)", (schema + ".payloads",)) == [
        (None,)
    ]
    assert list(spool.iterdir()) == [marker]


@pytest.mark.parametrize("scope", ["entity", "relation", "evidence"])
@pytest.mark.parametrize("number", [float("nan"), float("inf"), float("-inf")])
def test_nonfinite_measurements_fail_before_any_copy_and_rollback(
    tmp_path, postgres_dsn, monkeypatch, scope, number
):
    rows = fixture_rows()
    locations = {
        "entity": rows[0][0]["annotations"],
        "relation": rows[1][0]["annotations"],
        "evidence": rows[1][0]["evidence"][0]["annotations"],
    }
    locations[scope][0]["quantity"]["has_numeric_value"] = number
    resource(tmp_path, rows=rows)
    _assert_rejected_before_copy(tmp_path, postgres_dsn, monkeypatch, "finite")


@pytest.mark.parametrize(
    "field", ["entity_key", "relation_key", "subject_entity_key", "object_entity_key"]
)
@pytest.mark.parametrize("value", [None, "", " \t\n", "\xa0\u2003\u3000"])
def test_invalid_published_keys_fail_before_any_copy_and_rollback(
    tmp_path, postgres_dsn, monkeypatch, field, value
):
    rows = fixture_rows()
    row = rows[0][0] if field == "entity_key" else rows[1][0]
    row[field] = value
    resource(tmp_path, rows=rows)
    _assert_rejected_before_copy(tmp_path, postgres_dsn, monkeypatch, "nonempty")


@pytest.mark.parametrize(
    "location",
    ["identifier", "entity_annotation", "relation_annotation", "evidence", "evidence_annotation"],
)
def test_null_nested_structs_fail_before_any_copy_and_rollback(
    tmp_path, postgres_dsn, monkeypatch, location
):
    rows = fixture_rows()
    lists = {
        "identifier": rows[0][0]["identifiers"],
        "entity_annotation": rows[0][0]["annotations"],
        "relation_annotation": rows[1][0]["annotations"],
        "evidence": rows[1][0]["evidence"],
        "evidence_annotation": rows[1][0]["evidence"][0]["annotations"],
    }
    lists[location][0] = None
    resource(tmp_path, rows=rows)
    _assert_rejected_before_copy(tmp_path, postgres_dsn, monkeypatch, "Null nested struct")


def _assert_rejected_before_copy(tmp_path, postgres_dsn, monkeypatch, message):
    def unexpected(*args, **kwargs):
        pytest.fail("Invalid projected input must fail before staging or COPY")

    monkeypatch.setattr(loader, "_copy_projected_table", unexpected)
    schema = _schema()
    with pytest.raises(ValueError, match=message):
        loader.load_release(tmp_path, release(tmp_path), postgres_dsn, schema=schema)
    assert not exists(postgres_dsn, schema)
    assert not list(tmp_path.glob(".postgres-stage-*"))


def test_failure_after_copying_second_resource_rolls_back_all_rows_and_cleans_staging(
    tmp_path, postgres_dsn, monkeypatch
):
    resource(tmp_path, "first")
    resource(tmp_path, "second")
    original = loader._copy_projected_table
    completed = []

    def fail_after_copy(conn, schema, table, *args):
        result = original(conn, schema, table, *args)
        completed.append(table)
        if completed.count("annotations") == 2:
            # Both resources' complete COPY data are visible in the same transaction.
            count = conn.execute("SELECT count(*) FROM " + schema + ".entities").fetchone()
            assert count == (6,)
            raise RuntimeError("interrupted after second resource COPY")
        return result

    monkeypatch.setattr(loader, "_copy_projected_table", fail_after_copy)
    schema = _schema()
    with pytest.raises(RuntimeError, match="interrupted after second"):
        loader.load_release(
            tmp_path,
            release(tmp_path, {"first": "1.0.0", "second": "1.0.0"}),
            postgres_dsn,
            schema=schema,
        )
    assert completed == ["entities", "identifiers", "relations", "evidence", "annotations"] * 2
    assert not exists(postgres_dsn, schema)
    assert not list(tmp_path.glob(".postgres-stage-*"))


def test_actual_csv_chunk_rotation_preserves_every_row_and_cleans_files(
    tmp_path, postgres_dsn, monkeypatch
):
    # About 10 MB of generated text exercises real sink rotation without resource
    # parsing. FILE_SIZE_BYTES may overshoot one vector, so require rotation only.
    monkeypatch.setattr(bulk, "_CSV_CHUNK_SIZE", "1MB")
    text = "x" * 980 + _TEXT
    statement = (
        "SELECT range AS ordinal, '"
        + text.replace("'", "''")
        + "'::VARCHAR AS value FROM range(10000)"
    )
    marker = tmp_path / "keep.txt"
    marker.write_text("unrelated staging-parent file")
    schema = _schema()
    with (
        duckdb.connect(config={"threads": 1, "memory_limit": "64MB"}) as con,
        psycopg.connect(postgres_dsn) as conn,
        conn.transaction(),
    ):
        conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        conn.execute(
            sql.SQL("CREATE TABLE {}.rotated (ordinal bigint PRIMARY KEY, value text)").format(
                sql.Identifier(schema)
            )
        )
        result = bulk.copy_projected_table(
            conn, schema, "rotated", con, statement, ("ordinal", "value"), tmp_path
        )
        assert result.rows == 10000
        assert result.chunks > 1
        assert result.bytes > 0
        # Unique integer ordinals covering this exact range prove full coverage;
        # compare the complete text for every row inside PostgreSQL.
        actual = conn.execute(
            sql.SQL(
                "SELECT count(*), min(ordinal), max(ordinal), count(DISTINCT ordinal), "
                "count(*) FILTER (WHERE value IS DISTINCT FROM %s) FROM {}.rotated"
            ).format(sql.Identifier(schema)),
            (text,),
        ).fetchone()
        assert actual == (10000, 0, 9999, 10000, 0)
        assert list(tmp_path.iterdir()) == [marker]
