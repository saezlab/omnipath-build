"""Serving contract and evidence preservation through the production writer."""

import json
import pyarrow.parquet as pq

from omnipath_build.resolver import EntityResolver
from omnipath_build.silver import SilverExtractor
from omnipath_build.writer import ParquetWriter
from omnipath_core.schema import ENTITY_SCHEMA, RELATION_SCHEMA, PAYLOAD_SCHEMA
from test_partition_contract import entity, run_rows


def test_evidence_occurrences_survive_batches(tmp_path):
    forward = {
        "subject": entity("P1"),
        "predicate": "affects",
        "object": entity("P2"),
        "annotations": [
            {"term": "object_direction_qualifier", "value": "increased"},
            {"term": "description", "value": "first"},
        ],
    }
    later = {
        **forward,
        "annotations": [forward["annotations"][0], {"term": "description", "value": "later"}],
    }
    opposite = {
        **forward,
        "annotations": [{"term": "object_direction_qualifier", "value": "decreased"}],
    }
    source = [("1", forward), ("2", later), ("1", forward), ("3", opposite)]
    expected = run_rows(tmp_path / "all", source, len(source))
    for name, rows in [("split", source), ("reverse", source[::-1])]:
        assert run_rows(tmp_path / name, rows, 1) == expected
    _, relations, payloads = expected
    assert len(relations) == 2 and len(payloads) == 4
    merged = next(r for r in relations if r["sign"] == 1)
    assert merged["evidence_count"] == 3
    assert [e["row_id"] for e in merged["evidence"]].count("1") == 2
    assert {a["value"] for a in merged["annotations"] if a["term"] == "description"} == {
        "first",
        "later",
    }
    assert any(any(a["value"] == "later" for a in e["annotations"]) for e in merged["evidence"])


def test_empty_writer_keeps_all_schemas(tmp_path):
    writer = ParquetWriter(tmp_path)
    result = writer.close()
    assert result[3:] == (0, 0, 0)
    for path, schema in zip(result[:3], (ENTITY_SCHEMA, RELATION_SCHEMA, PAYLOAD_SCHEMA)):
        assert pq.read_table(path).schema == schema
    assert not (tmp_path / ".bulk").exists()


def test_aliases_and_label_merge_across_batches(tmp_path):
    source = [("first", entity("P1")), ("later", entity("P1", label="CDKN2A", aliases=("CDKN2A",)))]
    entities, _, _ = run_rows(tmp_path, source, 1)
    (row,) = entities
    assert row["label"] == "CDKN2A"
    assert ("genesymbol", "CDKN2A") in {(i["ns"], i["id"]) for i in row["identifiers"]}


def test_nonempty_output_schemas_and_payload_links(tmp_path):
    source = [("1", {"subject": entity("P1"), "predicate": "affects", "object": entity("P1")})]
    entities, relations, payloads = run_rows(tmp_path, source, 1)
    assert (len(entities), len(relations), len(payloads)) == (1, 1, 1)
    assert payloads[0]["relation_key"] == relations[0]["relation_key"]
    assert relations[0]["subject_entity_key"] == entities[0]["entity_key"]
    for name, schema in [
        ("entities", ENTITY_SCHEMA),
        ("relations", RELATION_SCHEMA),
        ("evidence_payloads", PAYLOAD_SCHEMA),
    ]:
        assert pq.read_schema(tmp_path / f"{name}.parquet") == schema


def test_payload_arrow_batches_preserve_shared_source_rows(tmp_path):
    writer = ParquetWriter(tmp_path)
    resolver = EntityResolver()
    extractor = SilverExtractor("fixture", "test")
    raw = {"source_text": "é" * 200}
    for i in range(1030):
        extractor.process_record(
            {"subject": entity("P1"), "predicate": "affects", "object": entity("P2")},
            raw,
            str(i),
            i,
        )
    try:
        writer.append_observations(extractor, resolver)
        writer.close()
    finally:
        resolver.close()
    file = pq.ParquetFile(writer.payload_path)
    assert file.metadata.num_row_groups == 1
    rows = file.read().to_pylist()
    assert len(rows) == 1030
    assert {r["row_id"] for r in rows} == {str(i) for i in range(1030)}
    assert all(json.loads(r["payload_json"]) == raw for r in rows)
    assert writer.metrics["payload_rows"] == 1030


def test_large_shared_payloads_are_bounded_before_arrow_conversion(tmp_path, monkeypatch):
    writer = ParquetWriter(tmp_path)
    resolver = EntityResolver()
    extractor = SilverExtractor("fixture", "test")
    import random

    raw = {"text": random.Random(42).randbytes(200000).hex() + "é"}
    for i in range(40):
        extractor.process_record(
            {"subject": entity("P1"), "predicate": "affects", "object": entity("P2")},
            raw,
            str(i),
            i,
        )
    sizes = []
    original = writer._input

    def capture(name, rows, schema):
        if name == "input_payloads":
            assert all("payload_json" not in r for r in rows)
            sizes.append(len(rows))
        return original(name, rows, schema)

    monkeypatch.setattr(writer, "_input", capture)
    try:
        writer.append_observations(extractor, resolver)
        writer.close()
    finally:
        resolver.close()
    assert sizes == [40]
    assert writer.payload_path.stat().st_size < 500000
    assert (
        "RLE_DICTIONARY"
        in pq.ParquetFile(writer.payload_path).metadata.row_group(0).column(4).encodings
    )
    rows = pq.read_table(writer.payload_path).to_pylist()
    assert len(rows) == 40
    assert {r["row_id"] for r in rows} == {str(i) for i in range(40)}
    assert all(json.loads(r["payload_json"]) == raw for r in rows)


def test_payload_batches_bound_unique_bytes_and_preserve_nulls():
    from omnipath_build.writer import _payload_batches, _payload_dictionary

    rows = [{"payload_json": value} for value in ("é" * 100, "é" * 100, None, "x" * 1000, "last")]
    batches = list(_payload_batches(rows, max_bytes=1500))
    assert [row for batch in batches for row in batch] == rows
    assert len(batches) == 3
    for batch in batches:
        encoded = _payload_dictionary(batch)
        assert encoded.to_pylist() == [row["payload_json"] for row in batch]
    assert len(_payload_dictionary(rows).dictionary) == 3


def test_oversized_payload_references_share_dictionary(tmp_path):
    import random
    import pyarrow as pa
    from omnipath_build.writer import _payload_batches, _payload_dictionary, PAYLOAD_STORAGE_SCHEMA

    # Tiny limit exercises the same condition as a >16 MiB production JSON.
    value = random.Random(3).randbytes(5000).hex()
    rows = [
        dict(relation_key=str(i), entity_key=None, source="x", row_id=str(i), payload_json=value)
        for i in range(20)
    ]
    batches = list(_payload_batches(rows, max_bytes=8000))
    assert len(batches) == 1
    path = tmp_path / "oversized.parquet"
    with pq.ParquetWriter(path, PAYLOAD_STORAGE_SCHEMA, store_schema=False) as writer:
        for batch in batches:
            table = pa.Table.from_pylist(batch, schema=PAYLOAD_SCHEMA.remove(4))
            writer.write_table(table.append_column("payload_json", _payload_dictionary(batch)))
    assert pq.read_table(path).to_pylist() == rows
    assert path.stat().st_size < len(value) * 3
    assert len(list(_payload_batches(rows, max_bytes=8000, max_rows=7))) == 3
    extra = dict(rows[0], payload_json="different oversized" * 1000)
    assert len(list(_payload_batches(rows + [extra], max_bytes=8000))) == 2


def test_private_shards_match_single_writer_with_overlapping_event_ids(tmp_path):
    source = [
        (
            "1",
            {
                "subject": entity("P1"),
                "predicate": "affects",
                "object": entity("P2"),
                "annotations": [{"term": "description", "value": "first"}],
            },
        ),
        (
            "2",
            {
                "subject": entity("P1"),
                "predicate": "affects",
                "object": entity("P2"),
                "annotations": [{"term": "description", "value": "second"}],
            },
        ),
        (
            "1",
            {
                "subject": entity("P1"),
                "predicate": "affects",
                "object": entity("P2"),
                "annotations": [{"term": "description", "value": "first"}],
            },
        ),
    ]
    expected = run_rows(tmp_path / "single", source, 3)
    shards = []
    for i, (row_id, record) in enumerate(source):
        resolver = EntityResolver()
        writer = ParquetWriter(tmp_path / str(i))
        extractor = SilverExtractor("fixture", "interactions")
        extractor.process_record(record, record, row_id, i)
        writer.append_observations(extractor, resolver)
        resolver.close()
        shards.append(writer.seal_observation_shard())
    writer = ParquetWriter(tmp_path / "merged")
    for shard in shards[::-1]:
        writer.import_observation_shard(shard)
    result = writer.close()
    import json

    actual = [pq.read_table(p).to_pylist() for p in result[:3]]

    # Ignore table row order; preserve every nested field and duplicate row.
    def ordered(rows):
        return sorted(json.dumps(r, sort_keys=True) for r in rows)

    assert [ordered(t) for t in actual] == [ordered(t) for t in expected]


def test_writer_marks_preferred_label_policy(tmp_path):
    entities, _, _ = run_rows(tmp_path, [("one", entity("P1", label="CDKN2A"))], 1)
    assert entities[0]["label"] == "CDKN2A"
    assert "preferred_name" not in pq.read_schema(tmp_path / "entities.parquet").names
    assert (
        pq.read_metadata(tmp_path / "entities.parquet").metadata[b"omnipath_label_policy"]
        == b"preferred-name-v1"
    )


def test_preferred_chemical_label_keeps_other_names(tmp_path):
    chemical = {
        "type": "chemical_entity",
        "identifiers": [
            {"type": "inchikey", "value": "QNAYBMKLOCPYGJ-UQEXSWPGSA-N"},
            {"type": "name", "value": "alanine-d7"},
            {"type": "name", "value": "Alanine"},
        ],
    }
    rows, _, _ = run_rows(tmp_path, [("one", chemical)], 1)
    assert rows[0]["label"] == "Alanine"
    assert {"alanine-d7", "Alanine"} <= {i["id"] for i in rows[0]["identifiers"]}
