"""Bulk SQL projection matches the trusted row projector on bounded fixtures."""

from collections import Counter, defaultdict
from copy import deepcopy
import json

import duckdb
import pytest

from omnipath_postgres.duckdb_projection import (
    JSON_COLUMNS,
    PROJECTION_COLUMNS,
    projection_query,
    validate_resource,
)
from omnipath_postgres.loader import COLUMNS
from omnipath_postgres.projection import iter_resource_records
from test_projection import QUANTITY, annotation, fixture_rows, write_fixture


def canonical(row):
    return json.dumps(row, sort_keys=True, ensure_ascii=False, allow_nan=False)


def sql_rows(con, directory, resource, version, table):
    result = con.execute(projection_query(directory, resource, version, table))
    columns = tuple(column[0] for column in result.description)
    assert columns == COLUMNS[table]
    rows = []
    # Materializing tiny equivalence fixtures is deliberately test-only. The
    # production API returns SELECT text for DuckDB COPY rather than these rows.
    for values in result.fetchall():
        row = dict(zip(columns, values, strict=True))
        for column in JSON_COLUMNS.intersection(row):
            if row[column] is not None:
                assert isinstance(row[column], str)
                row[column] = json.loads(row[column])
        rows.append(row)
    return rows


def assert_equivalent(con, directory, resource="fixture", version="1"):
    expected = defaultdict(list)
    for record in iter_resource_records(directory, resource, version, batch_size=1):
        expected[record.table].append(record.values)
    counts = {}
    for table in PROJECTION_COLUMNS:
        actual = sql_rows(con, directory, resource, version, table)
        assert Counter(map(canonical, actual)) == Counter(map(canonical, expected[table]))
        counts[table] = len(actual)
    validated = validate_resource(con, directory)
    assert validated.counts == counts
    return validated


def test_explicit_column_contract_and_all_nested_occurrences_match_reference(tmp_path):
    write_fixture(tmp_path)
    assert PROJECTION_COLUMNS == COLUMNS
    with duckdb.connect() as con:
        validated = assert_equivalent(con, tmp_path)
        assert validated.counts == {
            "entities": 3,
            "identifiers": 2,
            "relations": 1,
            "evidence": 2,
            "annotations": 9,
        }
        assert not validated.has_activity
        assert not validated.has_participant_relation


def test_null_lists_empty_lists_null_struct_fields_and_duplicate_positions_are_lossless(tmp_path):
    entities, relations, payloads = fixture_rows()
    entities[0]["identifiers"].append(
        {"ns": None, "id": None, "is_canonical": None, "source": None}
    )
    entities[0]["annotations"].append(
        {
            "term": None,
            "value": None,
            "quantity": dict.fromkeys(QUANTITY),
            "source": None,
            "dataset": None,
        }
    )
    entities[1]["identifiers"] = None
    entities[1]["annotations"] = None
    entities[2]["identifiers"] = []
    entities[2]["annotations"] = []
    relations[0]["sources"] = None
    relations[0]["annotations"].append(annotation(value="", quantity=None, scope=None))
    relations[0]["evidence"][0]["annotations"] = None
    relations[0]["evidence"][1]["annotations"].append(annotation(value="null", scope=None))
    write_fixture(tmp_path, entities=entities, relations=relations, payloads=payloads)
    with duckdb.connect() as con:
        assert_equivalent(con, tmp_path)
        rows = sql_rows(con, tmp_path, "fixture", "1", "annotations")
        quantities = [row["quantity"] for row in rows]
        assert None in quantities
        assert dict.fromkeys(QUANTITY) in quantities
        assert {row["scope"] for row in rows if row["owner_kind"] == "entity"} == {None}
        assert sorted(row["ordinal"] for row in rows if row["owner_kind"] == "evidence") == [
            0,
            1,
            2,
        ]
        relation = con.execute(projection_query(tmp_path, "fixture", "1", "relations")).fetchone()
        assert relation[COLUMNS["relations"].index("sources")] is None


def test_literal_json_null_string_is_distinct_from_sql_null(tmp_path):
    entities, relations, _ = fixture_rows()
    entities[0]["annotations"][0]["value"] = "null"
    relations[0]["sources"] = [None, "null", ""]
    relations[0]["evidence"][0]["annotations"][0]["quantity"] = None
    write_fixture(tmp_path, entities=entities, relations=relations)
    with duckdb.connect() as con:
        assert_equivalent(con, tmp_path)
        sources = sql_rows(con, tmp_path, "source", "1", "relations")[0]["sources"]
        assert sources == [None, "null", ""]
        rows = sql_rows(con, tmp_path, "source", "1", "annotations")
        assert any(row["value"] == "null" for row in rows)
        assert any(row["value"] is None for row in rows)


def test_path_resource_version_and_source_strings_are_safely_quoted_without_hive_inference(
    tmp_path,
):
    directory = tmp_path / "resource='ignore'; SELECT 1 -- α"
    entities, relations, _ = fixture_rows()
    text = "quotes:'\" tabs:\t lines:\n\\N,backslash:\\ unicode:α😀"
    entities[0]["label"] = text
    entities[0]["annotations"][1]["value"] = text
    relations[0]["evidence"][0]["row_id"] = text
    relations[0]["sources"] = [text, "", None, text]
    write_fixture(directory, entities=entities, relations=relations)
    resource = "res'; DROP TABLE marker; -- α"
    version = "version'\\\n"
    with duckdb.connect() as con:
        con.execute("CREATE TABLE marker (id INTEGER)")
        assert_equivalent(con, directory, resource, version)
        assert con.execute("SELECT count(*) FROM marker").fetchone() == (0,)


def test_empty_typed_files_produce_zero_rows_and_false_flags(tmp_path):
    write_fixture(tmp_path, entities=[], relations=[], payloads=[])
    with duckdb.connect() as con:
        validated = assert_equivalent(con, tmp_path)
        assert validated.counts == dict.fromkeys(COLUMNS, 0)
        assert not validated.has_activity
        assert not validated.has_participant_relation


@pytest.mark.parametrize("predicate", ["has_input", "has_output", "enabled_by", "associated_with"])
@pytest.mark.parametrize("kind", ["relation", "ontology_axiom"])
def test_activity_and_participant_flags_use_actual_typed_records(tmp_path, predicate, kind):
    entities, relations, _ = fixture_rows()
    entities[0]["entity_type"] = "molecular_activity"
    relations[0].update(predicate=predicate, statement_kind=kind)
    write_fixture(tmp_path, entities=entities, relations=relations)
    with duckdb.connect() as con:
        validated = assert_equivalent(con, tmp_path)
    assert validated.has_activity
    assert validated.has_participant_relation == (
        kind == "relation" and predicate in {"has_input", "has_output", "enabled_by"}
    )


@pytest.mark.parametrize(
    "field", ["entity_key", "relation_key", "subject_entity_key", "object_entity_key"]
)
@pytest.mark.parametrize("value", [None, "", "\t\n", "\u2007\xa0\x85"])
def test_missing_and_whitespace_resolved_keys_are_rejected(tmp_path, field, value):
    entities, relations, _ = fixture_rows()
    if field == "entity_key":
        entities[0][field] = value
    else:
        relations[0][field] = value
    write_fixture(tmp_path, entities=entities, relations=relations)
    with duckdb.connect() as con, pytest.raises(ValueError, match="nonempty"):
        validate_resource(con, tmp_path)


@pytest.mark.parametrize("scope", ["entity", "relation", "evidence"])
@pytest.mark.parametrize("number", [float("nan"), float("inf"), float("-inf")])
def test_nonfinite_quantities_are_rejected_at_every_annotation_scope(tmp_path, scope, number):
    entities, relations, _ = fixture_rows()
    if scope == "entity":
        target = entities[0]["annotations"][0]
    elif scope == "relation":
        target = relations[0]["annotations"][0]
    else:
        target = relations[0]["evidence"][1]["annotations"][0]
    target["quantity"]["has_numeric_value"] = number
    write_fixture(tmp_path, entities=entities, relations=relations)
    with duckdb.connect() as con, pytest.raises(ValueError, match="quantity must be finite"):
        validate_resource(con, tmp_path)


@pytest.mark.parametrize(
    "scope",
    ["identifier", "entity_annotation", "relation_annotation", "evidence", "evidence_annotation"],
)
def test_null_nested_struct_entries_are_rejected_before_copy(tmp_path, scope):
    entities, relations, _ = fixture_rows()
    if scope == "identifier":
        entities[0]["identifiers"].append(None)
    elif scope == "entity_annotation":
        entities[0]["annotations"].append(None)
    elif scope == "relation_annotation":
        relations[0]["annotations"].append(None)
    elif scope == "evidence":
        relations[0]["evidence"].append(None)
    else:
        relations[0]["evidence"][0]["annotations"].append(None)
    write_fixture(tmp_path, entities=entities, relations=relations)
    with duckdb.connect() as con, pytest.raises(ValueError, match="Null nested struct"):
        validate_resource(con, tmp_path)


def test_multiple_evidence_arrays_keep_parent_ordinals_and_all_original_fields(tmp_path):
    entities, relations, _ = fixture_rows()
    other = deepcopy(relations[0])
    other["relation_key"] += ":other"
    other["sources"] = []
    other["evidence"][0]["annotations"] = []
    other["evidence"][1]["annotations"] = [annotation(value="third", scope="object")]
    other["evidence"].append(
        {
            "source": None,
            "dataset": None,
            "row_id": "",
            "upstream_id": "up-3",
            "annotations": [annotation(value="third", scope="subject")],
        }
    )
    other["evidence_count"] = 3
    relations.append(other)
    write_fixture(tmp_path, entities=entities, relations=relations)
    with duckdb.connect() as con:
        validated = assert_equivalent(con, tmp_path)
        assert validated.counts["evidence"] == 5
        annotations = sql_rows(con, tmp_path, "source", "1", "annotations")
        assert sorted(
            (row["evidence_ordinal"], row["ordinal"], row["scope"])
            for row in annotations
            if row["owner_kind"] == "evidence" and row["owner_key"] == other["relation_key"]
        ) == [(1, 0, "object"), (2, 0, "subject")]


def test_unknown_table_and_nul_literals_are_rejected_without_executing_sql(tmp_path):
    with pytest.raises(ValueError, match="Unknown projection table"):
        projection_query(tmp_path, "source", "1", "payloads")
    with pytest.raises(ValueError, match="without NUL"):
        projection_query(tmp_path, "source\x00", "1", "entities")
    with pytest.raises(ValueError, match="without NUL"):
        projection_query(tmp_path / "bad\x00path", "source", "1", "entities")
