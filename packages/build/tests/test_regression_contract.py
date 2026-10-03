import json
from pathlib import Path

import pyarrow.parquet as pq

from library_fixture import build_fixture_library
from omnipath_resolver import EntityResolver
from omnipath_build.silver import RawEntityObservation, SilverExtractor
from omnipath_build.writer import ParquetWriter
from test_partition_contract import records, entity


def test_resolution_contract_across_batches(tmp_path):
    from dataclasses import asdict

    library = build_fixture_library(tmp_path / "library")
    fixture = json.loads((Path(__file__).parent / "fixtures/resolution_contract.json").read_text())
    rows = [RawEntityObservation(**r) for r in fixture["observations"]]
    resolver = EntityResolver(library, defer_aliases=True)
    try:
        for items in (rows, rows[::-1], rows[::2]):
            for offset in range(0, len(items), 17):
                batch = {o.entity_key: o for o in items[offset : offset + 17]}
                actual = resolver.resolve_entities(batch)
                assert {k: asdict(v) for k, v in actual.items()} == {
                    k: fixture["matches"][k] for k in batch
                }
        assert resolver.resolution_stats()["input_entities"] == len(rows)
    finally:
        resolver.close()


def test_flat_pipeline_matches_nested_contract_across_partitions(tmp_path):
    library = build_fixture_library(tmp_path / "library")
    rows = records() + [
        (
            "matched",
            {"subject": entity("P04637"), "predicate": "affects", "object": entity("P02340")},
        )
    ]
    rows += [
        (
            "ontology",
            {
                "type": "ontology_class",
                "identifiers": [{"type": "go", "value": "GO:1"}],
                "annotations": [{"term": "subclass_of", "value": "GO:2"}],
            },
        )
    ]
    expected = json.loads((Path(__file__).parent / "fixtures/resource_contract.json").read_text())
    for name, source, size in [("one", rows, 1), ("all", rows[::-1], len(rows))]:
        writer = ParquetWriter(tmp_path / name, library_dir=library)
        resolver = EntityResolver(library, defer_aliases=True)
        try:
            for start in range(0, len(source), size):
                extractor = SilverExtractor("fixture", "interactions")
                for locator, record in source[start : start + size]:
                    extractor.process_record(
                        record, raw_payload=record, row_id=locator, row_number=0
                    )
                writer.append_observations(extractor, resolver)
            resolver.close()
            paths = writer.close()[:3]
            actual = [
                sorted(pq.read_table(p).to_pylist(), key=lambda r: json.dumps(r, sort_keys=True))
                for p in paths
            ]
            assert actual == expected
        finally:
            resolver.close()
