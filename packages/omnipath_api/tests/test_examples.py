import pyarrow as pa
import pyarrow.parquet as pq
from fastapi.testclient import TestClient

from omnipath_api.engine import ParquetServingEngine
from omnipath_api.server import create_app
from omnipath_api.store.inventory import ReleaseStore
from omnipath_core.schema import ENTITY_SCHEMA, RELATION_SCHEMA, PAYLOAD_SCHEMA


def write(root, version, label, key, kind="chemical_entity", taxon=None):
    folder = root / "resources/test" / version
    folder.mkdir(parents=True)
    pq.write_table(
        pa.Table.from_pylist(
            [
                dict(
                    entity_key=key,
                    label=label,
                    entity_type=kind,
                    namespace="inchikey" if kind == "chemical_entity" else "uniprot",
                    identifier=key,
                    taxon=taxon,
                    identifiers=[],
                    annotations=[],
                )
            ],
            schema=ENTITY_SCHEMA,
        ),
        folder / "entities.parquet",
    )
    pq.write_table(pa.Table.from_pylist([], schema=RELATION_SCHEMA), folder / "relations.parquet")
    pq.write_table(
        pa.Table.from_pylist([], schema=PAYLOAD_SCHEMA), folder / "evidence_payloads.parquet"
    )


def test_examples_are_cached_release_specific_and_do_not_replace_search(tmp_path, monkeypatch):
    write(tmp_path, "1", "Aspirin", "a" * 64)
    store = ReleaseStore(tmp_path)
    store.publish(dict(schema_version=1, version="1", resources={"test": "1"}))
    engine = ParquetServingEngine(tmp_path)
    first = engine.get_entity_examples()
    assert len(first["entities"]) == 1 and first["entities"][0]["label"] == "Aspirin"
    assert first["nextCursor"] is None
    assert len(list((tmp_path / ".presentation/examples-v1").glob("*.json"))) == 1
    # A fresh engine reads the prepared page without selecting or hydrating again.
    cached = ParquetServingEngine(tmp_path)
    monkeypatch.setattr(
        cached,
        "_fetch_entities_by_keys",
        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("cache miss")),
    )
    assert cached.get_entity_examples() == first
    write(tmp_path, "2", "Caffeine", "b" * 64)
    assert engine.get_entity_examples()["entities"][0]["label"] == "Caffeine"
    with engine.release_scope("1"):
        assert engine.get_entity_examples()["entities"][0]["label"] == "Aspirin"
    assert engine.search_entities_api("Aspirin")["entities"] == []
    client = TestClient(create_app(engine=engine))
    result = client.get("/entities/examples", headers={"X-OmniPath-Release": "1"})
    assert result.status_code == 200 and result.json()["kind"] == "examples"
    assert result.json()["entities"][0]["label"] == "Aspirin"


def test_unavailable_examples_are_skipped_and_proteins_are_human(tmp_path):
    write(tmp_path, "1", "TP53", "a" * 64, "protein", "10090")
    engine = ParquetServingEngine(tmp_path)
    assert engine.get_entity_examples()["entities"] == []
    write(tmp_path, "2", "TP53", "b" * 64, "protein", "9606")
    assert engine.get_entity_examples()["entities"][0]["taxonomyId"] == "9606"
