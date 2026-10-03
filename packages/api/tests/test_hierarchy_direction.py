"""Ontology projection uses declared statement context, not resource/type lists."""

import pyarrow as pa
import pyarrow.parquet as pq
from omnipath_api.engine import ParquetServingEngine


def test_composition_is_not_an_ontology_but_axioms_are(tmp_path):
    target = tmp_path / "resources/arbitrary_source/1"
    target.mkdir(parents=True)
    pq.write_table(
        pa.table(
            {
                "entity_key": ["food", "compound", "term", "class", "unrelated"],
                "identifier": ["FOOD00005", "FDB1", "TERM", "CLASS", "OTHER"],
                "label": ["Allium", "Compound", "Term", "Class", "Other"],
                "namespace": ["arbitrary"] * 5,
            }
        ),
        target / "entities.parquet",
    )
    pq.write_table(
        pa.table(
            {
                "subject_entity_key": ["food", "class", "term"],
                "subject_label": ["Allium", "Class", "Term"],
                "object_entity_key": ["compound", "term", "unrelated"],
                "object_label": ["Compound", "Term", "Other"],
                "predicate": ["has_part", "has_part", "related_to"],
                "statement_kind": ["relation", "ontology", "ontology"],
            }
        ),
        target / "relations.parquet",
    )
    engine = ParquetServingEngine(data_root=tmp_path)
    food = engine.get_ontology_tree(["FOOD00005"])["root"]
    assert food is None
    assert engine.get_ontology_children("FOOD00005")["children"] == []
    tree = engine.get_ontology_tree(["TERM"])["root"]
    assert tree["id"] == "CLASS"
    assert [item["id"] for item in tree["children"]] == ["TERM"]
    assert [item["id"] for item in engine.get_ontology_children("CLASS")["children"]] == ["TERM"]
