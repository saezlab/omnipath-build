"""Unit tests for DuckDB ParquetServingEngine."""

import tempfile
import time
import unittest
from pathlib import Path

from omnipath_api.engine import ParquetServingEngine

from serving_fixtures import key, write_dataset
from omnipath_core.fixtures import write_resource


def _write_resource(root: Path, resource: str, version: str, *, n_relations: int = 1) -> Path:
    target = root / "resources" / resource / version
    entity = dict(entity_key=key("P1"), identifier="P1", label="Prot", namespace="uniprot")
    relations = [
        dict(
            relation_key=key(f"r{i}"),
            subject_entity_key=entity["entity_key"],
            object_entity_key=entity["entity_key"],
            predicate="associated_with",
        )
        for i in range(n_relations)
    ]
    write_resource(target, [entity], relations)
    (target / "resolution_stats.json").write_text("{}\n", encoding="utf-8")
    return target


class TestParquetServingEngine(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.tmp.cleanup)
        cls.engine = ParquetServingEngine(data_root=write_dataset(cls.tmp.name))
        cls.addClassCleanup(cls.engine.close)

    def test_01_list_resources(self):
        resources = self.engine.list_resources()
        self.assertIsInstance(resources, list)
        self.assertGreater(len(resources), 0, "Expected at least one resource indexed")

    def test_02_search_entities(self):
        result = self.engine.search_entities_api(query="TP53", limit=10)
        self.assertEqual(result["entities"][0]["label"], "TP53")
        self.assertIn("elapsed_ms", result)

    def test_03_search_relations(self):
        result = self.engine.search_relations(filters={}, limit=10)
        self.assertIn("rows", result)
        self.assertIn("total", result)
        self.assertIn("elapsed_ms", result)
        self.assertGreater(result["total"], 0, "Expected relations in dataset")

        first = result["rows"][0]
        self.assertIn("subject_label", first)
        self.assertIn("predicate", first)
        self.assertIn("object_label", first)
        # Verify lightweight projection by default
        self.assertNotIn("evidence", first)

        # Verify details when explicitly requested
        detailed = self.engine.search_relations(filters={}, limit=1, include_details=True)
        self.assertIn("evidence", detailed["rows"][0])

    def test_04_facet_counts(self):
        facets = self.engine.get_facets(filters={})
        self.assertIn("categories", facets)
        self.assertIn("predicates", facets)
        self.assertIn("signs", facets)
        self.assertIn("elapsed_ms", facets)

    def test_05_export_parquet(self):
        data, mime = self.engine.export_slice(filters={"genes": ["TP53"]}, format="parquet")
        self.assertEqual(mime, "application/vnd.apache.parquet")
        self.assertGreater(len(data), 0)

    def test_06_export_csv(self):
        data, mime = self.engine.export_slice(filters={"genes": ["TP53"]}, format="csv")
        self.assertTrue(mime.startswith("text/csv"))
        self.assertGreater(len(data), 0)

    def test_07_svelte_entity_search(self):
        sample = self.engine.search_entities_api(limit=1)
        self.assertTrue(sample["entities"])
        test_query = sample["entities"][0]["label"]
        result = self.engine.search_entities_api(query=test_query, limit=10)
        self.assertIn("entities", result)
        self.assertGreater(len(result["entities"]), 0)
        first = result["entities"][0]
        self.assertIn("entityPk", first)
        self.assertIn("canonicalIdentifier", first)

    def test_08_svelte_relation_search(self):
        result = self.engine.search_relations_api(filters={}, limit=10)
        self.assertIn("relations", result)
        self.assertIn("rows", result)
        self.assertNotIn("records", result)
        self.assertGreater(result["total"], 0)
        self.assertIn("relationPk", result["relations"][0])
        self.assertIn("subjectEntity", result["rows"][0])
        self.assertIn("entityPk", result["rows"][0]["subjectEntity"])

    def test_entity_search_is_scalar_first(self):
        sample = self.engine.search_entities_api(limit=1)
        self.assertTrue(sample["entities"])
        query = sample["entities"][0]["label"]
        result = self.engine.search_entities_api(query=query, limit=5)
        self.assertTrue(result["entities"])
        first = result["entities"][0]
        self.assertIsNone(first.get("entityAttributes"))
        self.assertLessEqual(len(first.get("identifiers") or []), 4)
        self.assertLess(result["elapsed_ms"], 1500)

    def test_entity_search_ranks_exact_label_first(self):
        result = self.engine.search_entities_api(query="ADA", limit=5)
        self.assertTrue(result["entities"])
        self.assertEqual(result["entities"][0].get("label"), "ADA")

    def test_entity_search_nested_identifier_fallback(self):
        result = self.engine.search_entities_api(query="P00813", limit=5)
        self.assertTrue(result["entities"])
        labels = {str(item.get("label") or "") for item in result["entities"]}
        identifiers = {str(item.get("canonicalIdentifier") or "") for item in result["entities"]}
        self.assertTrue("ADA" in labels or "100" in identifiers)

    def test_scoped_entity_facets_include_source_counts(self):
        facets = self.engine.get_scoped_entity_facets({"query": "ADA", "facetLimit": 5})
        sources = [item for item in facets if item["facetName"] == "source"]
        self.assertTrue(sources)
        self.assertTrue(any(item["scopedCount"] > 0 for item in sources))

    def test_entity_search_respects_source_filter(self):
        all_results = self.engine.search_entities_api(query="ADA", limit=5)
        self.assertTrue(all_results["entities"])
        for resource in ("signor", "uniprot", "chebi"):
            scoped = self.engine.search_entities_api(
                query="ADA", limit=5, filters={"sources": [resource]}
            )
            self.assertLessEqual(len(scoped["entities"]), len(all_results["entities"]))

    def test_entity_key_lookup_skips_identifier_scan(self):
        fake = "a" * 64
        self.assertEqual(self.engine.resolve_entity_keys([fake]), [fake])
        sample = self.engine.search_relations(limit=1)
        self.assertTrue(sample.get("rows"))
        key = sample["rows"][0]["subject_entity_key"]
        found = self.engine.get_entities_by_pks([key], slim=True)["entities"]
        self.assertTrue(
            any(
                item["entityPk"] == key or key in (item.get("sourceEntityPks") or [])
                for item in found
            )
        )

    def test_empty_relation_facets_are_cached(self):
        self.engine._facet_cache.clear()
        first = self.engine.get_scoped_relation_facets({})
        self.assertTrue(self.engine._facet_cache.entries)
        second = self.engine.get_scoped_relation_facets({})
        self.assertEqual(first, second)

    def test_concurrent_relation_queries(self):
        from concurrent.futures import ThreadPoolExecutor

        def run(_):
            return self.engine.search_relations(limit=5)

        with ThreadPoolExecutor(4) as pool:
            results = list(pool.map(run, range(8)))
        self.assertTrue(all(item.get("rows") for item in results))

    def test_09_scoped_relation_facets(self):
        sample_rel = self.engine.search_relations(limit=1)
        self.assertTrue(sample_rel.get("rows"))
        entity_id = sample_rel["rows"][0]["subject_label"]
        counts = self.engine.get_scoped_relation_facets({"entityIds": [entity_id]})
        self.assertTrue(any(item["facetName"] == "predicate" for item in counts))

    def test_10_relation_evidence(self):
        result = self.engine.search_relations(limit=1, include_details=True)
        self.assertTrue(result.get("rows"))
        pk = result["rows"][0]["relation_key"]
        evidence = self.engine.get_relation_evidence(pk)
        self.assertIn("evidence", evidence)
        self.assertIn("annotations", evidence)

    def test_11_ontology_hierarchy(self):
        rows = self.engine.search_entities_api(query="KW-0001", limit=100)["entities"]
        matching = [row for row in rows if row["canonicalIdentifier"] == "KW-0001"]
        self.assertTrue(matching)
        details = self.engine.get_entity_details(matching[0]["entityPk"])
        self.assertIsNotNone(details)
        ent = details["entity"]
        self.assertEqual(ent["entityPk"], matching[0]["entityPk"])
        hierarchy = ent.get("ontologyHierarchy")
        self.assertIsNotNone(hierarchy)
        self.assertEqual(hierarchy["termId"], "KW-0001")
        # The local resource release can change; this term must still have ancestry.
        self.assertGreater(hierarchy["parentCount"], 0)
        self.assertGreaterEqual(hierarchy["childCount"], 0)


class TestInventoryHotReload(unittest.TestCase):
    def test_picks_up_new_version_without_reload_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_resource(root, "uniprot", "v1", n_relations=1)
            engine = ParquetServingEngine(data_root=root)
            self.assertEqual(engine._latest_by_resource["uniprot"]["version"], "v1")
            (path,) = engine._table_paths("relation", ["uniprot"]).values()
            self.assertTrue(path.endswith("/v1/relation.parquet"))

            time.sleep(0.05)
            _write_resource(root, "uniprot", "v2", n_relations=3)
            infos = engine._selected_resource_infos(["uniprot"])
            self.assertEqual(infos[0]["version"], "v2")
            self.assertTrue(str(infos[0]["tables"]["relation"]).endswith("/v2/relation.parquet"))
            listed = {item["key"]: item["relations_count"] for item in engine.list_resources()}
            self.assertEqual(listed["uniprot/v2"], 3)

    def test_skips_version_with_a_missing_table(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_resource(root, "uniprot", "v1", n_relations=1)
            engine = ParquetServingEngine(data_root=root)
            time.sleep(0.05)
            pending = _write_resource(root, "uniprot", "v2", n_relations=9)
            serving = pending / "entity_term.parquet"
            serving.rename(pending / "entity_term.partial")
            infos = engine._selected_resource_infos(["uniprot"])
            self.assertEqual(infos[0]["version"], "v1")
            (pending / "entity_term.partial").rename(serving)
            infos = engine._selected_resource_infos(["uniprot"])
            self.assertEqual(infos[0]["version"], "v2")

    def test_picks_up_in_place_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_resource(root, "uniprot", "v1", n_relations=1)
            engine = ParquetServingEngine(data_root=root)
            self.assertEqual(engine.list_resources()[0]["relations_count"], 1)
            time.sleep(0.05)
            _write_resource(root, "uniprot", "v1", n_relations=4)
            self.assertEqual(engine.list_resources()[0]["relations_count"], 4)


if __name__ == "__main__":
    unittest.main()


def test_catalog_exposes_sample_limit_and_partial_build(tmp_path):
    import json

    folder = _write_resource(tmp_path, "sample", "1")
    (folder / "build_manifest.json").write_text(json.dumps({"max_records": 10, "datasets": None}))
    engine = ParquetServingEngine(data_root=tmp_path)
    item = engine.list_resource_catalog()[0]
    assert item["sample_build"] is True
    assert item["max_records"] == 10
    assert engine.get_stats_build_manifest()["partialBuild"] is True
