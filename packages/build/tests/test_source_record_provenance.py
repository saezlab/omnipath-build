"""Reaction evidence retains exact source provenance without changing keys."""

import hashlib
import json

import pyarrow.parquet as pq
import pytest

from omnipath_resolver import EntityResolver
from omnipath_build.silver import SilverExtractor
from omnipath_build.writer import ParquetWriter
from omnipath_core.biolink import qualifiers
from omnipath_core.keys import relation_key
from omnipath_core.source_attributes import (
    SOURCE_RECORD_REFERENCE,
    SOURCE_RECORD_SHA256_PREFIX,
    SOURCE_RECORD_TYPE,
)


def entity(identifier, kind="chemical_entity", namespace="chebi"):
    return {"type": kind, "identifiers": [{"type": namespace, "value": identifier}]}


def reaction():
    return {
        **entity("R1", "molecular_activity", "rhea"),
        "membership": [
            {"member": entity("CHEBI:1"), "predicate": "has_input"},
            {"member": entity("CHEBI:2"), "predicate": "has_output"},
            {"member": entity("P1", "protein", "uniprot"), "predicate": "enabled_by"},
        ],
    }


@pytest.mark.parametrize("raw,shape", [({"value": 1}, "object"), ([1], "array"), (None, "scalar")])
def test_exact_payload_hash_and_shape_are_nonidentity_attributes(raw, shape):
    extractor = SilverExtractor("rhea", "reactions")
    payload = json.dumps(raw, indent=2)
    extractor.process_record(reaction(), raw, "reactions:7", 7, payload_json=payload)
    expected = SOURCE_RECORD_SHA256_PREFIX + hashlib.sha256(payload.encode()).hexdigest()
    assert len(extractor.relations) == 3
    for row in extractor.relations:
        attrs = {a["term"]: a["value"] for a in row.annotations}
        assert attrs[SOURCE_RECORD_REFERENCE] == expected
        assert attrs[SOURCE_RECORD_TYPE] == shape
        assert all(a["scope"] == "relation" for a in row.annotations)
        assert qualifiers(row.annotations) == ()
        assert row.relation_key == relation_key(
            row.subject_entity_key, row.predicate, row.object_entity_key
        )
    assert {p["payload_json"] for p in extractor.payloads} == {payload}


def test_evidence_hashes_survive_parquet_merge_as_separate_source_occurrences(tmp_path):
    writer = ParquetWriter(tmp_path)
    resolver = EntityResolver(tmp_path / "no-reference", defer_aliases=True)
    payloads = ['{"value":1}', '{ "value": 1 }']
    try:
        for number, payload in enumerate(payloads):
            extractor = SilverExtractor("rhea", "reactions")
            extractor.process_record(
                reaction(), {"value": 1}, f"reactions:{number}", number, payload_json=payload
            )
            writer.append_observations(extractor, resolver)
        _, relations_path, raw_path, *_ = writer.close()
    finally:
        resolver.close()
    expected = {
        SOURCE_RECORD_SHA256_PREFIX + hashlib.sha256(p.encode()).hexdigest() for p in payloads
    }
    compiled = pq.read_table(relations_path).to_pylist()
    assert len(compiled) == 3
    for row in compiled:
        assert row["evidence_count"] == 2
        assert {
            a["value"]
            for ev in row["evidence"]
            for a in ev["annotations"]
            if a["term"] == SOURCE_RECORD_REFERENCE
        } == expected
        assert {ev["row_id"] for ev in row["evidence"]} == {"reactions:0", "reactions:1"}
    assert {p["payload_json"] for p in pq.read_table(raw_path).to_pylist()} == set(payloads)


def test_nonreaction_edges_do_not_gain_reaction_provenance():
    extractor = SilverExtractor("signor", "interactions")
    row = {
        "subject": entity("P1", "protein", "uniprot"),
        "predicate": "interacts_with",
        "object": entity("P2", "protein", "uniprot"),
    }
    extractor.process_record(row, {"raw": "record"}, "interactions:0", 0)
    assert extractor.relations[0].annotations == []


def test_direct_reaction_relation_retains_source_provenance():
    extractor = SilverExtractor("reactome", "reactions")
    record = {
        "subject": entity("R1", "molecular_activity", "reactome"),
        "predicate": "has_input",
        "object": entity("CHEBI:1"),
    }
    raw = {"conversion_direction": "REVERSIBLE"}
    extractor.process_record(record, raw, "reactions:3", 3)
    assert len(extractor.relations) == 1
    row = extractor.relations[0]
    stored = extractor.payloads[0]["payload_json"]
    assert {a["term"]: a["value"] for a in row.annotations} == {
        SOURCE_RECORD_REFERENCE: SOURCE_RECORD_SHA256_PREFIX
        + hashlib.sha256(stored.encode()).hexdigest(),
        SOURCE_RECORD_TYPE: "object",
    }
    assert qualifiers(row.annotations) == ()
