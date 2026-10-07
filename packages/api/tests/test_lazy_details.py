import pyarrow as pa
import pyarrow.parquet as pq
from fastapi.testclient import TestClient
from omnipath_api.engine import ParquetServingEngine
from omnipath_api.server import create_app
from omnipath_api.store.inventory import ReleaseStore
from omnipath_core.schema import ENTITY_SCHEMA, RELATION_SCHEMA, PAYLOAD_SCHEMA


def resource(root, version, label):
    folder = root / "resources/test" / version
    folder.mkdir(parents=True)
    pq.write_table(
        pa.Table.from_pylist(
            [
                dict(
                    entity_key=k,
                    entity_type="chemical_entity",
                    namespace="test",
                    identifier=k,
                    label=label if k == "a" else "Other",
                    identifiers=[],
                    annotations=[],
                )
                for k in ["a", "b"]
            ],
            schema=ENTITY_SCHEMA,
        ),
        folder / "entities.parquet",
    )
    pq.write_table(
        pa.Table.from_pylist(
            [
                dict(
                    relation_key=f"r{i}",
                    subject_entity_key="a",
                    object_entity_key="b",
                    predicate="has_part",
                    category="membership",
                    sources=["test"],
                    evidence_count=1,
                    annotations=[dict(term="note", value=f"{label}-{i}", source="test")],
                )
                for i in range(3)
            ],
            schema=RELATION_SCHEMA,
        ),
        folder / "relations.parquet",
    )
    pq.write_table(
        pa.Table.from_pylist([], schema=PAYLOAD_SCHEMA), folder / "evidence_payloads.parquet"
    )


def test_lazy_core_does_not_query_relations_and_reuses_cache(tmp_path, monkeypatch):
    resource(tmp_path, "1", "First")
    engine = ParquetServingEngine(tmp_path)
    client = TestClient(create_app(engine=engine))
    monkeypatch.setattr(
        engine,
        "_resolve_relation_paths",
        lambda *a: (_ for _ in ()).throw(AssertionError("relation scan")),
    )
    first = client.get("/entities/a?includeRelationships=false")
    assert first.status_code == 200 and first.json()["entity"]["label"] == "First"
    monkeypatch.setattr(
        engine,
        "_fetch_entities_by_keys",
        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("uncached entity")),
    )
    assert client.get("/entities/a?includeRelationships=false").json() == first.json()


def test_relationship_pagination_cache_and_release_isolation(tmp_path, monkeypatch):
    resource(tmp_path, "1", "First")
    ReleaseStore(tmp_path).publish(dict(schema_version=1, version="1", resources={"test": "1"}))
    engine = ParquetServingEngine(tmp_path)
    client = TestClient(create_app(engine=engine))
    first = client.get("/entities/a/relationships?limit=2").json()
    assert first["relationshipsTotal"] == 3 and first["nextCursor"] == "2"
    second = client.get("/entities/a/relationships?limit=2&offset=2").json()
    assert second["nextCursor"] is None
    assert [
        r["relation"]["relationPk"] for r in first["relationships"] + second["relationships"]
    ] == ["r0", "r1", "r2"]
    with monkeypatch.context() as m:
        m.setattr(
            engine,
            "_fetch_dicts",
            lambda *a, **kw: (_ for _ in ()).throw(AssertionError("uncached relationships")),
        )
        assert client.get("/entities/a/relationships?limit=2").json() == first
    resource(tmp_path, "2", "Second")
    assert (
        client.get("/entities/a?includeRelationships=false").json()["entity"]["label"] == "Second"
    )
    latest = client.get("/entities/a/relationships?limit=2").json()
    assert latest["relationships"][0]["annotations"][0]["value"] == "Second-0"
    pinned = client.get(
        "/entities/a/relationships?limit=2", headers={"X-OmniPath-Release": "1"}
    ).json()
    assert pinned == first
    assert client.get("/entities/missing/relationships").status_code == 404
    assert client.get("/entities/a/relationships?limit=0").status_code == 422


def test_group_relationships_include_both_endpoints_once_and_paginate(tmp_path):
    resource(tmp_path, "1", "First")
    client = TestClient(create_app(engine=ParquetServingEngine(tmp_path)))
    body = {"member_keys": ["a", "b", "a"], "limit": 2}
    response = client.post("/entities/group-relationships", json=body)
    assert response.status_code == 200
    first = response.json()
    assert first["relationshipsTotal"] == 3
    assert first["nextCursor"] == "2"
    second = client.post("/entities/group-relationships", json={**body, "offset": 2}).json()
    assert second["nextCursor"] is None
    assert [
        r["relation"]["relationPk"] for r in first["relationships"] + second["relationships"]
    ] == ["r0", "r1", "r2"]
    assert client.post("/entities/group-relationships", json={"member_keys": []}).status_code == 422
    scoped = client.post("/entities/group-relationships", json={**body, "resources": ["missing"]})
    assert scoped.status_code == 400


def test_normal_entity_details_default_to_bounded_pages(tmp_path):
    resource(tmp_path, "1", "First")
    path = tmp_path / "resources/test/1/entities.parquet"
    rows = pq.read_table(path).to_pylist()
    rows[0]["annotations"] = [
        dict(term="description", value=f"Note {i}", source="test") for i in range(45)
    ]
    pq.write_table(pa.Table.from_pylist(rows, schema=ENTITY_SCHEMA), path)
    client = TestClient(create_app(engine=ParquetServingEngine(tmp_path)))
    first = client.get("/entities/a?includeRelationships=false").json()["entity"]
    assert len(first["entityAttributes"]) == 20
    assert first["entityAttributesTotal"] == 45
    assert first["detailNextCursor"] == "20"
    second = client.get("/entities/a?includeRelationships=false&detail_offset=20").json()["entity"]
    assert len(second["entityAttributes"]) == 20
    third = client.get("/entities/a?includeRelationships=false&detail_offset=40").json()["entity"]
    assert len(third["entityAttributes"]) == 5
    assert third["detailNextCursor"] is None


def test_relationships_and_counts_do_not_depend_on_projections(tmp_path):
    from omnipath_api.serving_index import build_indexes, index_path

    resource(tmp_path, "1", "First")
    other = tmp_path / "resources/other/1"
    other.mkdir(parents=True)
    pq.write_table(
        pq.read_table(tmp_path / "resources/test/1/entities.parquet"), other / "entities.parquet"
    )
    pq.write_table(
        pa.Table.from_pylist(
            [
                # a self-loop has both endpoints in the entity and counts once
                dict(relation_key=f"s{i}", subject_entity_key="a", object_entity_key="a",
                     predicate="has_member", category="association", sources=["other"],
                     evidence_count=1, annotations=[])
                for i in range(2)
            ],
            schema=RELATION_SCHEMA,
        ),
        other / "relations.parquet",
    )  # fmt: skip
    pq.write_table(
        pa.Table.from_pylist([], schema=PAYLOAD_SCHEMA), other / "evidence_payloads.parquet"
    )
    engine = ParquetServingEngine(tmp_path)

    def read():
        engine._detail_cache.clear()
        engine._relationship_cache.clear()
        pages = [engine.get_entity_relationships("a", limit=2, offset=o) for o in (0, 2, 4)]
        return engine.get_entity_details("a"), pages

    expected = read()
    details, pages = expected
    assert details["summary"]["interactionCount"] == 5
    assert {a["relationPk"] for a in details["annotations"]} == {"s0", "s1"}
    assert pages[0]["relationshipsTotal"] == 5
    assert [r["relation"]["relationPk"] for p in pages for r in p["relationships"]] == [
        "s0", "s1", "r0", "r1", "r2",
    ]  # fmt: skip
    build_indexes(engine, threads=2, memory_limit="128MB", min_free_disk=0)
    assert read() == expected
    # One indexed and one unindexed resource read the same.
    index_path(tmp_path, "adjacency", [str(other / "relations.parquet")]).unlink()
    assert read() == expected
