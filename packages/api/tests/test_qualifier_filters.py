"""Qualifier filters must operate on whole statements, not projected signs."""

import io
import shutil

import pyarrow.parquet as pq
import pytest

from omnipath_api.engine import ParquetServingEngine
from omnipath_core.fixtures import rewrite_resource


@pytest.fixture
def engine(tmp_path):
    directory = tmp_path / "resources/test/1"
    directory.mkdir(parents=True)
    rewrite_resource(directory / "entity.parquet", entities=[])
    rows = []
    for key, aspect, direction, scope in [
        ("a", "activity", "increased", "relation"),
        ("b", "activity", "decreased", "relation"),
        ("c", "expression", "increased", "relation"),
        ("d", None, None, "relation"),
        ("e", "activity", "increased", "subject"),
    ]:
        annotations = [
            dict(term=term, value=value, scope=scope, source="test", dataset="test")
            for term, value in [
                ("object_aspect_qualifier", aspect),
                ("object_direction_qualifier", direction),
            ]
            if value
        ]
        if key in {"a", "e"}:
            annotations.append(
                dict(
                    term="causal_mechanism_qualifier",
                    value="binding",
                    scope=scope,
                    source="test",
                    dataset="test",
                )
            )
        # Duplicated evidence annotations must never inflate facet counts.
        rows.append(
            dict(
                relation_key=key,
                subject_entity_key="s",
                object_entity_key="o",
                subject_type="protein",
                object_type="protein",
                predicate="affects",
                category="interaction",
                sources=["test"],
                annotations=annotations * 2,
                sign=0,
                is_directed=True,
                evidence_count=1,
            )
        )
    rewrite_resource(directory / "relation.parquet", relations=rows)
    return ParquetServingEngine(tmp_path)


def test_combined_filters_and_multi_select(engine):
    filters = {"object_aspect_qualifier": ["activity"], "object_direction_qualifier": ["increased"]}
    assert [r["relation_key"] for r in engine.search_relations(filters=filters)["rows"]] == ["a"]
    filters["object_direction_qualifier"].append("decreased")
    assert engine.search_relations(filters=filters)["total"] == 2
    assert engine.search_relations()["total"] == 5
    with pytest.raises(ValueError, match="Invalid DirectionQualifierEnum"):
        engine.search_relations(filters={"object_direction_qualifier": ["activation"]})


def test_facets_ignore_own_selection_but_keep_other_filters(engine):
    facets = engine.get_scoped_relation_facets(
        {
            "object_aspect_qualifier": ["activity"],
            "object_direction_qualifier": ["increased"],
        }
    )
    counts = {(f["facetName"], f["facetValue"]): f["scopedCount"] for f in facets}
    assert counts["object_aspect_qualifier", "expression"] == 1
    assert counts["object_aspect_qualifier", "activity"] == 1
    assert counts["object_direction_qualifier", "decreased"] == 1
    assert counts["object_direction_qualifier", "increased"] == 1
    assert engine.get_scoped_relation_facets({"relation_categories": ["ontology"]}) == []


def test_export_uses_same_qualifier_filter(engine):
    data, _ = engine.export_slice(
        filters={"object_direction_qualifier": ["decreased"]})
    assert pq.read_table(io.BytesIO(data))["relation_key"].to_pylist() == ["b"]


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"sources": ["test"]},
        {"object_aspect_qualifier": ["activity"]},
        {"object_direction_qualifier": ["increased", "decreased"]},
        {"causal_mechanism_qualifier": ["binding"]},
        {"object_aspect_qualifier": ["activity"], "object_direction_qualifier": ["increased"]},
        {"object_aspect_qualifier": ["activity"], "causal_mechanism_qualifier": ["binding"]},
        {
            "object_aspect_qualifier": ["activity"],
            "object_direction_qualifier": ["increased"],
            "causal_mechanism_qualifier": ["binding"],
        },
    ],
)
def test_batched_qualifier_counts_match_independent_scans(engine, payload):
    from omnipath_api.queries.constants import RELATION_QUALIFIER_FILTERS

    # Repeat the same keys in another resource: DISTINCT must span files too.
    source = engine.data_root / "resources/test/1"
    shutil.copytree(source, engine.data_root / "resources/duplicate/1")
    engine._refresh_inventory_if_stale()
    expected = {}
    for term in RELATION_QUALIFIER_FILTERS:
        selection, params = engine._relation_selection({**payload, term: []})
        rows = engine._db.execute(
            f"""
            SELECT a.value, count(DISTINCT r.relation_key)
            FROM ({selection}) r JOIN {engine._table("relation_annotation")} a
              USING (resource, relation_id)
            WHERE a.scope = 'relation' AND a.term = ?
            GROUP BY a.value
        """,
            [*params, term],
        ).fetchall()
        expected.update({(term, value): count for value, count in rows})
    actual = {
        (f["facetName"], f["facetValue"]): f["scopedCount"]
        for f in engine.get_scoped_relation_facets(payload)
        if f["facetName"] in RELATION_QUALIFIER_FILTERS
    }
    assert actual == expected
