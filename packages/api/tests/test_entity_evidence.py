import pyarrow as pa
import pyarrow.parquet as pq
from fastapi.testclient import TestClient

from omnipath_api.engine import ParquetServingEngine
from omnipath_api.server import create_app
from omnipath_api.serving_index import build_indexes, explode_evidence, index_path
from omnipath_core.schema import ENTITY_SCHEMA, RELATION_SCHEMA

KEY = "a" * 64


def resource(root, name, counts):
    folder = root / "resources" / name / "1"
    folder.mkdir(parents=True)
    entities = [
        dict(
            entity_key=key,
            entity_type="small_molecule",
            namespace="test",
            identifier=key[:4],
            label=key[:4],
            identifiers=[],
            annotations=[],
            evidence=None
            if n is None
            else [dict(source=name, dataset="d", row_id=f"{key[:1]}{i}") for i in range(n)],
        )
        for key, n in counts.items()
    ]
    pq.write_table(
        pa.Table.from_pylist(entities, schema=ENTITY_SCHEMA), folder / "entities.parquet"
    )
    pq.write_table(pa.Table.from_pylist([], schema=RELATION_SCHEMA), folder / "relations.parquet")
    return folder / "entities.parquet"


def test_evidence_is_counted_in_details_and_paged_from_its_endpoint(tmp_path):
    first = resource(tmp_path, "one", {KEY: 3, "b" * 64: None, "c" * 64: 0})
    resource(tmp_path, "two", {KEY: 2})
    engine = ParquetServingEngine(tmp_path)
    client = TestClient(create_app(engine=engine))

    def read():
        engine._detail_cache.clear()
        details = client.get(f"/entities/{KEY}").json()["entity"]
        pages = [
            client.get(f"/entities/{KEY}/evidence?limit=2&offset={o}").json() for o in (0, 2, 4)
        ]
        return details, pages

    expected = read()
    details, pages = expected
    assert details["molecularEvidence"] == [] and details["molecularEvidenceTotal"] == 5
    assert [p["nextCursor"] for p in pages] == ["2", "4", None]
    items = [(e["source"], e["row_id"]) for p in pages for e in p["evidence"]]
    assert items == [("one", "a0"), ("one", "a1"), ("one", "a2"), ("two", "a0"), ("two", "a1")]
    build_indexes(engine, threads=2, memory_limit="128MB", min_free_disk=0)
    assert read() == expected
    index_path(tmp_path, "entity_evidence", [str(first)]).unlink()
    assert read() == expected
    assert client.get(f"/entities/{'d' * 64}/evidence").status_code == 404
    assert client.get(f"/entities/{'c' * 64}/evidence").json()["evidenceTotal"] == 0


def test_exploded_evidence_keeps_each_items_position(tmp_path):
    source = resource(tmp_path, "one", {KEY: 3, "b" * 64: None, "c" * 64: 0, "e" * 64: 2})
    for batch_size in (1, 2, 8192):  # batches starting inside the file have shifted offsets
        explode_evidence(source, tmp_path / "out.parquet", row_group_size=2, batch_size=batch_size)
        rows = pq.read_table(tmp_path / "out.parquet").to_pylist()
        assert [(r["entity_key"][:1], r["evidence_index"], r["item"]["row_id"]) for r in rows] == [
            ("a", 0, "a0"), ("a", 1, "a1"), ("a", 2, "a2"), ("e", 0, "e0"), ("e", 1, "e1"),
        ]  # fmt: skip
