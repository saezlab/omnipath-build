"""Resolved identities, occurrence attribution and quantities survive projection."""

from collections import Counter, defaultdict
from copy import deepcopy
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from omnipath_core.schema import ENTITY_SCHEMA, PAYLOAD_SCHEMA, RELATION_SCHEMA
from omnipath_postgres import projection


ENTITY_A = "published::protein/A:'α"
ENTITY_B = "published::protein/B"
ENTITY_C = "published::chemical/standalone"
RELATION = "published::relation/key:'β"
QUANTITY = {
    "has_numeric_value": 12.5,
    "has_unit": "UO:0000061",
    "has_unit_prefix": "nano",
    "has_binary_relation": "less_than",
    "source_field": "IC50 (nM)",
    "comparator": "<=",
}
RAW_PAYLOAD = '{ "upstream": "SRC-42", "value": "≤ 12.5 nM", "nested": [null, true] }\n'


def annotation(term="has_attribute", *, value=None, quantity=None, scope="evidence"):
    return {
        "term": term,
        "value": value,
        "quantity": quantity,
        "source": "reported-source",
        "dataset": "reported-dataset",
        "scope": scope,
    }


def entity(key, identifier, *, namespace="uniprot", taxon="9606", entity_type="protein"):
    return {
        "entity_key": key,
        "entity_type": entity_type,
        "namespace": namespace,
        "identifier": identifier,
        "taxon": taxon,
        "label": identifier,
        "has_hierarchy": False,
        "parent_count": 0,
        "child_count": 0,
        "identifiers": [],
        "annotations": [],
    }


def fixture_rows():
    a = entity(ENTITY_A, "P04637")
    identifier = {"ns": "uniprot", "id": "P04637", "is_canonical": True, "source": "source"}
    a["identifiers"] = [deepcopy(identifier), deepcopy(identifier)]
    quantity = annotation(quantity=deepcopy(QUANTITY))
    quantity.pop("scope")  # ENTITY_SCHEMA intentionally has no scope field.
    scalar = annotation("description", value="A source description with α")
    scalar.pop("scope")
    a["annotations"] = [quantity, deepcopy(scalar), deepcopy(scalar)]
    b = entity(ENTITY_B, "P0DP23")
    b["identifiers"] = None
    b["annotations"] = None
    c = entity(ENTITY_C, "CHEBI:15377", namespace="chebi", taxon=None, entity_type="small_molecule")
    evidence = {
        "source": "reported-source",
        "dataset": "reported-dataset",
        "row_id": "00001",
        "upstream_id": "SRC-42",
        "annotations": [
            annotation(quantity=deepcopy(QUANTITY)),
            annotation("publications", value="PMID:123456"),
        ],
    }
    relation = {
        "relation_key": RELATION,
        "statement_kind": "relation",
        "subject_entity_key": ENTITY_A,
        "subject_label": "Subject α",
        "subject_type": "protein",
        "predicate": "affects",
        "object_entity_key": ENTITY_B,
        "object_label": "Object β",
        "object_type": "protein",
        "taxon": "9606",
        "is_directed": True,
        "sign": -1,
        "category": "interaction",
        "interaction_class": "chemical_modulation",
        "sources": ["reported-source", "other-source"],
        "evidence_count": 2,
        "evidence": [deepcopy(evidence), deepcopy(evidence)],
        "annotations": [
            annotation(quantity=deepcopy(QUANTITY), scope="relation"),
            annotation("object_direction_qualifier", value="decreased", scope="relation"),
        ],
    }
    payloads = [
        {
            "relation_key": RELATION,
            "entity_key": None,
            "source": "reported-source",
            "row_id": "00001",
            "payload_json": RAW_PAYLOAD,
        },
        {
            "relation_key": None,
            "entity_key": ENTITY_C,
            "source": "entity-source",
            "row_id": "entity-only",
            "payload_json": '{"standalone": true}',
        },
    ]
    return [a, b, c], [relation], payloads


def write_fixture(directory, *, entities=None, relations=None, payloads=None):
    defaults = fixture_rows()
    rows = (
        entities if entities is not None else defaults[0],
        relations if relations is not None else defaults[1],
        payloads if payloads is not None else defaults[2],
    )
    directory.mkdir(parents=True, exist_ok=True)
    for name, schema, items in zip(
        ("entities.parquet", "relations.parquet", "evidence_payloads.parquet"),
        (ENTITY_SCHEMA, RELATION_SCHEMA, PAYLOAD_SCHEMA),
        rows,
        strict=True,
    ):
        pq.write_table(pa.Table.from_pylist(items, schema=schema), directory / name)
    return rows


def collect(directory, *, batch_size=1):
    tables = defaultdict(list)
    for record in projection.iter_resource_records(
        directory, "fixture-source", "4.2.1", batch_size=batch_size
    ):
        assert record.values["resource"] == "fixture-source"
        assert record.values["version"] == "4.2.1"
        tables[record.table].append(record.values)
    return tables


def test_every_projected_table_matches_the_loader_copy_contract(tmp_path):
    from omnipath_postgres.loader import COLUMNS

    write_fixture(tmp_path)
    seen = set()
    for record in projection.iter_resource_records(tmp_path, "source", "1", batch_size=2):
        assert set(record.values) == set(COLUMNS[record.table])
        seen.add(record.table)
    assert seen == set(COLUMNS)


def test_exact_identities_nested_source_and_raw_payload_text(tmp_path):
    # Quotes and a partition-like folder exercise parameterization and hive disablement.
    directory = tmp_path / "resource='fixture'"
    entities, relations, payloads = write_fixture(directory)
    tables = collect(directory)
    assert [row["entity_key"] for row in tables["entities"]] == [ENTITY_A, ENTITY_B, ENTITY_C]
    assert [row["record_json"] for row in tables["entities"]] == entities
    assert [row["record_json"] for row in tables["relations"]] == relations
    flat_relation = tables["relations"][0]
    for name, value in relations[0].items():
        if name not in {"annotations", "evidence"}:
            assert flat_relation[name] == value
    assert tables["payloads"][0]["payload_json"] == RAW_PAYLOAD
    assert tables["payloads"][0]["relation_key"] == RELATION
    assert tables["payloads"][1]["entity_key"] == ENTITY_C
    assert tables["payloads"][1]["relation_key"] is None
    assert tables["payloads"][1]["row_id"] == payloads[1]["row_id"]


def test_quantities_at_every_scope_and_duplicate_occurrences_are_retained(tmp_path):
    write_fixture(tmp_path)
    tables = collect(tmp_path)
    assert [row["ordinal"] for row in tables["identifiers"]] == [0, 1]
    assert [row["id"] for row in tables["identifiers"]] == ["P04637", "P04637"]
    evidence = tables["evidence"]
    assert [(row["ordinal"], row["row_id"], row["upstream_id"]) for row in evidence] == [
        (0, "00001", "SRC-42"),
        (1, "00001", "SRC-42"),
    ]
    measurements = [row for row in tables["annotations"] if row["quantity"] is not None]
    assert len(measurements) == 4
    assert all(row["quantity"] == QUANTITY for row in measurements)
    assert {
        (row["owner_kind"], row["owner_key"], row["evidence_ordinal"], row["scope"])
        for row in measurements
    } == {
        ("entity", ENTITY_A, None, None),
        ("relation", RELATION, None, "relation"),
        ("evidence", RELATION, 0, "evidence"),
        ("evidence", RELATION, 1, "evidence"),
    }
    assert all(
        row["source"] == "reported-source" and row["dataset"] == "reported-dataset"
        for row in measurements
    )
    descriptions = [row for row in tables["annotations"] if row["term"] == "description"]
    assert [row["ordinal"] for row in descriptions] == [1, 2]
    assert [row["value"] for row in descriptions] == ["A source description with α"] * 2


def test_null_lists_null_sources_and_null_payload_are_not_coerced(tmp_path):
    entities, relations, payloads = fixture_rows()
    relations[0]["sources"] = None
    payloads[1]["payload_json"] = None
    write_fixture(tmp_path, entities=entities, relations=relations, payloads=payloads)
    tables = collect(tmp_path)
    assert tables["entities"][1]["record_json"]["identifiers"] is None
    assert tables["entities"][1]["record_json"]["annotations"] is None
    assert tables["entities"][2]["record_json"]["identifiers"] == []
    assert tables["relations"][0]["sources"] is None
    assert tables["relations"][0]["record_json"]["sources"] is None
    assert tables["payloads"][1]["payload_json"] is None


@pytest.mark.parametrize(
    "entity_key,relation_key", [(None, None), (ENTITY_A, RELATION), ("", None)]
)
def test_payload_must_identify_exactly_one_nonempty_owner(tmp_path, entity_key, relation_key):
    _, _, payloads = fixture_rows()
    payloads[0].update(entity_key=entity_key, relation_key=relation_key)
    write_fixture(tmp_path, payloads=payloads)
    with pytest.raises(ValueError, match="exactly one|nonempty"):
        collect(tmp_path)


@pytest.mark.parametrize("text", ['{"value": NaN}', '{"value": Infinity}', "{broken"])
def test_invalid_or_nonfinite_payload_json_is_rejected_without_repair(tmp_path, text):
    _, _, payloads = fixture_rows()
    payloads[0]["payload_json"] = text
    write_fixture(tmp_path, payloads=payloads)
    with pytest.raises(ValueError, match="Invalid payload_json"):
        collect(tmp_path)


def test_nonfinite_nested_measurement_is_rejected(tmp_path):
    entities, _, _ = fixture_rows()
    entities[0]["annotations"][0]["quantity"]["has_numeric_value"] = float("nan")
    write_fixture(tmp_path, entities=entities)
    with pytest.raises(ValueError, match="Invalid JSON value in entity"):
        collect(tmp_path)


@pytest.mark.parametrize("batch_size", [0, -1, True, 1.5, "1"])
def test_invalid_batch_size_rejected_before_reading(tmp_path, batch_size):
    with pytest.raises(ValueError, match="positive integer"):
        list(projection.iter_rows(tmp_path / "missing.parquet", batch_size=batch_size))
    with pytest.raises(ValueError, match="positive integer"):
        list(projection.iter_resource_records(tmp_path, "source", "1", batch_size=batch_size))


def test_duckdb_reader_fetches_bounded_batches_and_closes_when_stopped(tmp_path, monkeypatch):
    class Connection:
        description = [("entity_key",)]
        closed = False

        def __init__(self):
            self.sizes = []
            self.position = 0

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            self.closed = True

        def execute(self, query, params):
            assert "read_parquet(?, hive_partitioning=false)" in query
            assert params == [str(tmp_path / "file'with'quotes.parquet")]

        def fetchmany(self, size):
            self.sizes.append(size)
            rows = [
                (f"exact-{index}",) for index in range(self.position, min(self.position + size, 5))
            ]
            self.position += len(rows)
            return rows

        def fetchall(self):
            raise AssertionError("Reading the complete input is forbidden")

    connection = Connection()
    monkeypatch.setattr(projection.duckdb, "connect", lambda **_kwargs: connection)
    rows = projection.iter_rows(tmp_path / "file'with'quotes.parquet", batch_size=2)
    assert connection.sizes == []
    assert next(rows) == {"entity_key": "exact-0"}
    assert next(rows) == {"entity_key": "exact-1"}
    assert connection.sizes == [2]
    assert next(rows) == {"entity_key": "exact-2"}
    assert connection.sizes == [2, 2]
    rows.close()
    assert connection.closed


@pytest.mark.integration
def test_existing_bounded_signor_smoke_preserves_all_twenty_occurrences():
    directory = Path(__file__).resolve().parents[3] / "data/migration-smoke/resources/signor/0.1.0"
    if not directory.exists():
        pytest.skip("Run the offline 20-record migration smoke build first")
    tables = collect(directory, batch_size=3)
    assert len(tables["entities"]) == 2
    assert len(tables["relations"]) == 2
    assert len(tables["evidence"]) == len(tables["payloads"]) == 20
    assert sorted(row["evidence_count"] for row in tables["relations"]) == [6, 14]
    assert {row["identifier"] for row in tables["entities"]} == {"P04637", "P0DP23"}
    assert {row["predicate"] for row in tables["relations"]} == {"affects"}
    assert {row["sign"] for row in tables["relations"]} == {-1, 1}
    evidence = Counter(
        (row["source"], row["row_id"], row["relation_key"]) for row in tables["evidence"]
    )
    payloads = Counter(
        (row["source"], row["row_id"], row["relation_key"]) for row in tables["payloads"]
    )
    assert evidence == payloads
    source_rows = pq.read_table(directory / "evidence_payloads.parquet").to_pylist()
    assert [row["payload_json"] for row in tables["payloads"]] == [
        row["payload_json"] for row in source_rows
    ]
