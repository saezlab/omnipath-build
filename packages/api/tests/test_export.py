"""Exports of what the explorer shows, as Parquet: entities of a search, relations of a filter."""

import io

import pyarrow.parquet as pq
from fastapi.testclient import TestClient

from omnipath_api.engine import ParquetServingEngine
from omnipath_api.server import create_app
from omnipath_core.fixtures import write_resource


def entity(key, label, kind="protein", reference=None):
    return dict(
        entity_key=key,
        reference_entity_key=reference,
        label=label,
        entity_type=kind,
        namespace="uniprot",
        identifier=key.upper(),
        taxon="9606",
        identifiers=[dict(ns="genesymbol", id=label)],
    )


def client(tmp_path):
    relations = [
        dict(relation_key=f"r{i}", subject_entity_key="p1", object_entity_key="p2",
             subject_label="TP53", object_label="MDM2", predicate="interacts_with",
             category="interaction", sources=["a"], evidence_count=1)
        for i in range(3)
    ]  # fmt: skip
    write_resource(tmp_path / "resources/a/1", [entity("p1", "TP53", reference="entrez:7157"), entity("p2", "MDM2")], relations)
    write_resource(tmp_path / "resources/b/1", [entity("p1", "TP53")])
    return TestClient(create_app(engine=ParquetServingEngine(tmp_path)))


def rows(response):
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/vnd.apache.parquet"
    return pq.read_table(io.BytesIO(response.content)).to_pylist()


def test_entity_export_matches_the_search_one_row_per_entity(tmp_path):
    api = client(tmp_path)
    response = api.post("/entities/export", json={})
    assert 'filename="omnipath_entities.parquet"' in response.headers["content-disposition"]
    # one row per entity, listing the resources that have it
    assert [(r["label"], r["sources"], r["relation_count"]) for r in rows(response)] == [
        ("TP53", ["a", "b"], 3),
        ("MDM2", ["a"], 3),
    ]
    found = rows(api.post("/entities/export", json={"query": "MDM2"}))
    assert [r["label"] for r in found] == ["MDM2"]
    scoped = rows(api.post("/entities/export", json={"filters": {"sources": ["b"]}}))
    assert [(r["label"], r["sources"]) for r in scoped] == [("TP53", ["b"])]


def test_exports_are_parquet_and_relation_exports_are_bounded(tmp_path):
    api = client(tmp_path)
    assert len(rows(api.post("/export", json={"limit": 2}))) == 2
    assert api.post("/export", json={"limit": 100_001}).status_code == 422
    assert api.post("/export", json={"format": "csv"}).status_code == 422


def test_relation_export_resolves_a_search_like_the_relations_page(tmp_path):
    api = client(tmp_path)
    assert len(rows(api.post("/export", json={"query": "MDM2"}))) == 3
    assert rows(api.post("/export", json={"query": "no such entity"})) == []


def test_group_keys_select_their_members(tmp_path):
    api = client(tmp_path)
    engine = api.app.state.engine
    assert engine.resolve_entity_keys(["entity:p2"]) == ["p2"]
    assert engine.resolve_entity_keys(["gene:entrez:7157"]) == ["p1"]
    # a group key exports the relations of the group's members, like its entity keys
    by_key = rows(api.post("/export", json={"filters": {"entity_ids": ["entity:p2"]}}))
    assert len(by_key) == 3
