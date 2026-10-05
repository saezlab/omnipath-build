"""End-to-end invariants across local reducers and final Parquet aggregation."""

import json
from pathlib import Path

import pyarrow.parquet as pq

from omnipath_resolver import EntityResolver
from omnipath_build.silver import SilverExtractor
from omnipath_build.writer import ParquetWriter
from omnipath_build.discovery import DiscoveredDataset


def entity(identifier, *, label="", taxon=None, aliases=()):
    return {
        "type": "protein",
        "identifiers": [
            {"type": "uniprot", "value": identifier},
            *({"type": "genesymbol", "value": value} for value in aliases),
        ],
        "annotations": [
            *([{"term": "name", "value": label}] if label else []),
            *([{"term": "in_taxon", "value": f"NCBITaxon:{taxon}"}] if taxon else []),
        ],
    }


def records():
    rows = []
    for i, (label, taxon) in enumerate([("Zulu", "9606"), ("Alpha", "10090"), ("Beta", "9606")]):
        rows.append(
            (
                str(i),
                {
                    "subject": entity("P1", label=label, taxon=taxon, aliases=(label,)),
                    "predicate": "affects",
                    "object": entity("P2"),
                    "annotations": [
                        {"term": "object_direction_qualifier", "value": "increased"},
                        {"term": "description", "value": "support"},
                    ],
                },
            )
        )
    rows.append(
        (
            "opposite",
            {
                **rows[0][1],
                "annotations": [{"term": "object_direction_qualifier", "value": "decreased"}],
            },
        )
    )
    for i, (a, b) in enumerate([("P1", "P2"), ("P2", "P1")]):
        rows.append(
            (
                f"symmetric:{i}",
                {
                    "subject": entity(a),
                    "predicate": "interacts_with",
                    "object": entity(b),
                    "annotations": [],
                },
            )
        )
    for i, parent in enumerate(["P3", "P4"]):
        rows.append(
            (
                f"part:{i}",
                {
                    "subject": entity("P1"),
                    "predicate": "part_of",
                    "object": entity(parent),
                    "annotations": [],
                },
            )
        )
    return rows


def run_rows(path: Path, rows, batch: int, library=None):
    writer = ParquetWriter(path, library_dir=library)
    resolver = EntityResolver(library_dir=library or path / "no-library", defer_aliases=True)
    try:
        for offset in range(0, len(rows), batch):
            extractor = SilverExtractor("fixture", "interactions")
            for locator, record in rows[offset : offset + batch]:
                extractor.process_record(record, raw_payload=record, row_id=locator, row_number=0)
            writer.append_observations(extractor, resolver)
        paths = writer.close()[:3]
        return [
            sorted(pq.read_table(p).to_pylist(), key=lambda row: json.dumps(row, sort_keys=True))
            for p in paths
        ]
    finally:
        resolver.close()


def test_complete_outputs_are_partition_and_order_invariant(tmp_path):
    source = records()
    expected = run_rows(tmp_path / "all", source, len(source))
    for name, rows, size in [
        ("single", source, 1),
        ("reversed", source[::-1], 2),
        ("interleaved", source[::2] + source[1::2], 3),
    ]:
        assert run_rows(tmp_path / name, rows, size) == expected
    entities, relations, payloads = expected
    assert sum(r["evidence_count"] for r in relations) == len(source)
    assert len(payloads) == len(source)
    assert len([r for r in relations if r["predicate"] == "interacts_with"]) == 1
    assert {r["sign"] for r in relations if r["predicate"] == "affects"} == {-1, 1}
    assert next(e for e in entities if e["identifier"] == "P1")["parent_count"] == 0


def test_unresolved_symbols_keep_taxon_context(tmp_path):
    source = []
    for taxon in ["9606", "10090"]:
        subject = entity("unused", taxon=taxon)
        subject["identifiers"] = [{"type": "genesymbol", "value": "SHARED"}]
        source.append((taxon, {"subject": subject, "predicate": "affects", "object": entity("P2")}))
    entities, relations, _ = run_rows(tmp_path, source, 1)
    assert len([e for e in entities if e["identifier"] == "SHARED"]) == 2
    assert len(relations) == 2


def test_ambiguous_symbols_resolve_independently_of_batch(tmp_path):
    from library_fixture import build_fixture_library

    library = build_fixture_library(tmp_path / "library")
    source = []
    for taxon in ["9606", "10090"]:
        subject = entity("unused", taxon=taxon)
        subject["identifiers"] = [{"type": "genesymbol", "value": "TP53"}]
        source.append((taxon, {"subject": subject, "predicate": "affects", "object": entity("P2")}))
    expected = run_rows(tmp_path / "all", source, 2, library)
    assert run_rows(tmp_path / "split", source[::-1], 1, library) == expected
    assert {e["identifier"] for e in expected[0]} == {"7157", "22059", "P2"}
    assert all(e["entity_type"] == "protein" for e in expected[0])
    assert {(e["identifier"], e["taxon"]) for e in expected[0] if e["namespace"] == "entrez"} == {
        ("7157", "9606"),
        ("22059", "10090"),
    }
    assert all(r["evidence"][0]["subject_molecular_form"] is None for r in expected[1])


def test_nested_membership_keeps_occurrences_and_payload_links(tmp_path):
    complex_record = {
        "type": "macromolecular_complex",
        "identifiers": [{"type": "signor", "value": "C1"}],
        "membership": [
            {"member": entity("P1")},
            {"member": entity("P1")},
            {"member": entity("P2")},
        ],
    }
    _, relations, payloads = run_rows(tmp_path, [("complex:0", complex_record)], 1)
    assert len(relations) == 2
    assert sorted(r["evidence_count"] for r in relations) == [1, 2]
    assert len(payloads) == 2
    assert all(json.loads(p["payload_json"]) == complex_record for p in payloads)


def test_symmetric_annotations_follow_canonical_endpoint_orientation(tmp_path):
    source = [
        (
            "forward",
            {
                "subject": entity("P1", label="left"),
                "predicate": "interacts_with",
                "object": entity("P2", label="right"),
            },
        ),
        (
            "backward",
            {
                "subject": entity("P2", label="right"),
                "predicate": "interacts_with",
                "object": entity("P1", label="left"),
            },
        ),
    ]
    _, relations, _ = run_rows(tmp_path, source, 1)
    assert len(relations) == 1
    evidence = relations[0]["evidence"]
    assert evidence[0]["annotations"] != []
    annotations = [
        {(a["scope"], a["term"], a["value"]) for a in e["annotations"]} for e in evidence
    ]
    assert annotations[0] == annotations[1]


def test_pipeline_snapshots_parsed_rows_and_qualifies_locators(
    tmp_path, monkeypatch, mapper_module
):
    from omnipath_build import pipeline
    from pypath.inputs_v2.base import Dataset

    def parse(_opener, **kwargs):
        yield {"original": "before mapping"}

    def mapper(row):
        row["original"] = "mutated"
        return {"subject": entity("P1"), "predicate": "affects", "object": entity("P2")}

    ds = Dataset(None, mapper, parse)
    discovered = [
        DiscoveredDataset(
            "fixture", name, mapper_module.__name__, ds, raw_dataset=ds, mapper=mapper
        )
        for name in ["first", "second"]
    ]
    monkeypatch.setattr(pipeline, "discover_datasets", lambda **kwargs: ("fixture", discovered, {}))
    result = pipeline.build_resource(
        "fixture", batch_workers=1, version="1.0.0", output_dir=tmp_path, progress=False
    )
    payloads = pq.read_table(result["payloads_path"]).to_pylist()
    assert {p["row_id"] for p in payloads} == {"first:0", "second:0"}
    assert all(json.loads(p["payload_json"]) == {"original": "before mapping"} for p in payloads)
    relations = pq.read_table(result["relations_path"]).to_pylist()
    assert relations[0]["evidence_count"] == 2
    assert {e["dataset"] for e in relations[0]["evidence"]} == {"first", "second"}


def test_repeated_endpoints_flush_by_records_and_bytes(tmp_path, monkeypatch, mapper_module):
    from omnipath_build import pipeline
    from pypath.inputs_v2.base import Dataset

    def parse(_opener, **kwargs):
        for i in range(31):
            yield {"occurrence": i, "text": "x" * 200}

    def mapper(row):
        return {"subject": entity("P1"), "predicate": "affects", "object": entity("P2")}

    ds = Dataset(None, mapper, parse)
    found = DiscoveredDataset(
        "fixture", "repeated", mapper_module.__name__, ds, raw_dataset=ds, mapper=mapper
    )
    monkeypatch.setattr(pipeline, "discover_datasets", lambda **kwargs: ("fixture", [found], {}))
    for name, kwargs in [
        ("records", {"max_batch_records": 5}),
        ("relations", {"max_batch_relations": 5}),
        ("bytes", {"max_batch_bytes": 5000}),
    ]:
        result = pipeline.build_resource(
            "fixture",
            batch_workers=1,
            version="1.0.0",
            output_dir=tmp_path / name,
            progress=False,
            **kwargs,
        )
        assert result["batch_metrics"]["flushes"] >= 7
        assert result["batch_metrics"]["peak_records"] <= 5
        rels = pq.read_table(result["relations_path"]).to_pylist()
        assert len(rels) == 1 and rels[0]["evidence_count"] == 31
        assert len(pq.read_table(result["payloads_path"])) == 31


def test_ontology_context_is_preserved_in_identity_and_projection(tmp_path):
    from omnipath_build.silver import SilverExtractor
    from omnipath_resolver import EntityResolver
    from omnipath_build.writer import ParquetWriter
    import pyarrow.parquet as pq

    source = entity("P1")
    source["membership"] = [{"member": entity("P2"), "predicate": "has_part"}]
    source["ontology_relations"] = [{"object": entity("P2"), "predicate": "has_part"}]
    extractor = SilverExtractor("synthetic", "test")
    extractor.process_record(source, raw_payload=source, row_id="test:0", row_number=0)
    assert len({r.relation_key for r in extractor.relations}) == 2
    assert {r.statement_kind for r in extractor.relations} == {"relation", "ontology"}
    resolver = EntityResolver(library_dir=tmp_path / "missing")
    writer = ParquetWriter(tmp_path / "out")
    writer.append_observations(extractor, resolver)
    resolver.close()
    paths = writer.close()
    assert {r["statement_kind"] for r in pq.read_table(paths[1]).to_pylist()} == {
        "relation",
        "ontology",
    }
    assert sum(e["parent_count"] for e in pq.read_table(paths[0]).to_pylist()) == 1


def test_reference_aliases_are_partition_invariant(tmp_path):
    from library_fixture import build_fixture_library

    library = build_fixture_library(tmp_path / "library")
    source = records()
    for i, ident in enumerate(["P04637", "P04637", "P02340"]):
        source.append(
            (
                f"matched:{i}",
                {
                    "subject": entity(ident, label=f"Observed {i}", aliases=(f"NewAlias{i}",)),
                    "predicate": "interacts_with",
                    "object": entity("P04637"),
                },
            )
        )
    expected = run_rows(tmp_path / "baseline", source, 2, library)
    for name, rows, batch in [
        ("one", source, 1),
        ("all", source, len(source)),
        ("reverse", source[::-1], 3),
    ]:
        assert run_rows(tmp_path / name, rows, batch, library) == expected


def test_gene_and_protein_identity_for_same_node(tmp_path):
    from library_fixture import build_fixture_library

    library = build_fixture_library(tmp_path / "library")
    gene = {
        **entity("unused"),
        "type": "gene",
        "identifiers": [{"type": "entrez", "value": "7157"}],
    }
    source = [
        ("gene", gene),
        ("protein", entity("P04637")),
        ("edge", {"subject": gene, "predicate": "affects", "object": entity("P04637")}),
    ]
    expected = run_rows(tmp_path / "baseline", source, 1, library)
    assert run_rows(tmp_path / "new", source[::-1], 2, library) == expected
    assert {(e["entity_type"], e["namespace"], e["identifier"]) for e in expected[0]} == {
        ("gene", "entrez", "7157"),
        ("protein", "entrez", "7157"),
        ("protein", "uniprot", "P04637"),
    }
    entities, relations, payloads = expected
    assert {e["reference_entity_key"] for e in entities} == {"entrez:7157"}
    assert all(e["gene_reference_keys"] == ["entrez:7157"] for e in entities)
    by_key = {e["entity_key"]: e for e in entities}
    relation = relations[0]
    assert relation["subject_type"] == "gene" and relation["object_type"] == "protein"
    assert relation["subject_entity_key"] != relation["object_entity_key"]
    assert relation["evidence"][0]["subject_molecular_form"] is None
    product = relation["evidence"][0]["object_molecular_form"]["protein_entity_key"]
    assert by_key[product]["identifier"] == "P04637"
    gene = next(e for e in entities if e["entity_type"] == "gene")
    assert gene["evidence"][0]["molecular_form"] is None
    protein = by_key[relation["object_entity_key"]]
    assert protein["evidence"][0]["molecular_form"]["protein_entity_key"] == product
    assert len(payloads) == 3
    assert all(
        p["relation_key"] == relation["relation_key"]
        if p["relation_key"]
        else p["entity_key"] in by_key
        for p in payloads
    )
