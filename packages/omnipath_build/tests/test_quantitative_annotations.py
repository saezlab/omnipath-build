"""Quantities retain units, comparator and source context across chunk merges."""

import json

import pyarrow.parquet as pq
from biolink_model.datamodel.model import QuantityValue, slots
from omnipath_core.measurements import Measurement
from omnipath_core.silver_schema import Annotation
from omnipath_core.biolink import annotation_value
from test_partition_contract import run_rows, entity


def measured(number, unit="nM", field="IC50", comparator="<"):
    return Annotation(
        term="BAO:0000190",
        value=Measurement(
            QuantityValue(has_numeric_value=number, has_unit=unit), field, comparator
        ),
    )


def test_quantity_survives_batch_merges_with_context(tmp_path):
    rows = []
    for i, annotation in enumerate(
        [measured(10), measured(10, unit="uM"), measured(10, field="IC50 maximum"), measured(10)]
    ):
        record = {
            "subject": entity("P1"),
            "predicate": "interacts_with",
            "object": entity("P2"),
            "annotations": [annotation],
        }
        rows.append((str(i), record))
    all_rows = run_rows(tmp_path / "all", rows, len(rows))
    split_rows = run_rows(tmp_path / "split", rows, 1)
    assert all_rows == split_rows
    (relation,) = all_rows[1]
    assert relation["evidence_count"] == 4
    assert len(relation["annotations"]) == 3
    assert {a["quantity"]["has_unit"] for a in relation["annotations"]} == {"nM", "uM"}
    assert all(a["quantity"]["comparator"] == "<" for a in relation["annotations"])
    assert all(ev["annotations"][0]["quantity"] for ev in relation["evidence"])


def test_entity_quantity_survives_projection(tmp_path):
    subject = entity("P1")
    subject["annotations"].append(measured(3.2))
    entities, relations, _ = run_rows(tmp_path, [("1", subject)], 1)
    (annotation,) = next(e for e in entities if e["identifier"] == "P1")["annotations"]
    assert annotation["quantity"]["has_numeric_value"] == 3.2


def test_quantity_json_is_canonical_and_rejects_nonfinite():
    import pytest

    value = measured(10).value
    text = annotation_value("BAO:0000190", value)
    assert annotation_value("BAO:0000190", text) == text
    with pytest.raises(ValueError):
        annotation_value("BAO:0000190", measured(float("nan")).value)


def test_quantity_cannot_replace_qualifier_enum():
    import pytest

    for term in (slots.object_direction_qualifier, slots.description):
        with pytest.raises(ValueError):
            annotation_value(term, measured(1).value)


def test_bindingdb_ic50_reaches_final_typed_parquet(tmp_path):
    from pypath.inputs_v2.bindingdb import interactions_schema

    row = {
        "BindingDB MonomerID": "1",
        "UniProt (SwissProt) Primary ID of Target Chain 1": "P00533",
        "IC50 (nM)": "<=10",
        "Ki (nM)": "20",
    }
    record = interactions_schema(row)
    _, relations, _ = run_rows(tmp_path, [("1", record)], 1)
    (relation,) = relations
    attrs = {a["term"]: a for a in relation["annotations"]}
    assert attrs["BAO:0000190"]["quantity"]["has_numeric_value"] == 10
    assert attrs["BAO:0000190"]["quantity"]["has_unit"] == "nM"
    assert attrs["BAO:0000190"]["quantity"]["comparator"] == "<="
    assert attrs["BAO:0000192"]["quantity"]["has_numeric_value"] == 20
    import duckdb

    with duckdb.connect() as conn:
        count = conn.execute(
            "SELECT count(*) FROM read_parquet(?) r, UNNEST(r.annotations) a(item) WHERE item.term='BAO:0000190' AND item.quantity.has_numeric_value <= 10 AND item.quantity.has_unit='nM'",
            [str(tmp_path / "relations.parquet")],
        ).fetchone()[0]
        assert count == 1


def test_api_retains_typed_quantities():
    import pytest

    pytest.importorskip("omnipath_api", reason="API package is not part of this workspace yet")
    from omnipath_api.annotations import split_compiled_annotations
    from omnipath_core.measurements import quantity_dict

    q = quantity_dict(measured(10).value)
    result = split_compiled_annotations(
        [{"term": "BAO:0000190", "value": "10", "quantity": q, "scope": "relation"}]
    )
    assert result["relation"][0]["quantity"] == q
    from omnipath_api.shape.evidence import shape_evidence_item
    from omnipath_api.shape.entity import shape_entity_summary

    source = {"annotations": [{"term": "BAO:0000190", "value": "10", "quantity": q}]}
    assert shape_evidence_item(source)["annotations"][0]["quantity"] == q
    assert (
        shape_entity_summary(
            {
                **source,
                "entity_key": "e",
                "namespace": "chebi",
                "identifier": "1",
                "entity_type": "chemical_entity",
            }
        )["annotations"][0]["quantity"]
        == q
    )


def test_standalone_entity_keeps_original_fields_in_payload(tmp_path):
    from omnipath_build.silver import SilverExtractor
    from omnipath_build.resolver import EntityResolver
    from omnipath_build.writer import ParquetWriter

    source = {"accession": "P1", "unmodeled_source_field": {"flag": True}}
    extractor = SilverExtractor("fixture", "entities")
    extractor.process_record(entity("P1", aliases=("Alias",)), source, "entities:1", 1)
    resolver = EntityResolver(library_dir=tmp_path / "none")
    try:
        writer = ParquetWriter(tmp_path)
        writer.append_observations(extractor, resolver)
        writer.close()
    finally:
        resolver.close()
    (payload,) = pq.read_table(tmp_path / "evidence_payloads.parquet").to_pylist()
    assert payload["entity_key"] not in extractor.entities
    assert payload["relation_key"] is None
    assert (
        payload["entity_key"]
        == pq.read_table(tmp_path / "entities.parquet").to_pylist()[0]["entity_key"]
    )
    assert json.loads(payload["payload_json"]) == source
