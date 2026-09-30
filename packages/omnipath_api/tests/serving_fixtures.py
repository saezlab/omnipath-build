"""Small deterministic serving dataset; never consult a developer's data directory."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
from omnipath_core.schema import ENTITY_SCHEMA, RELATION_SCHEMA


def key(value):
    return hashlib.sha256(value.encode()).hexdigest()


def write_dataset(root):
    entities = [
        dict(
            entity_key=key(ident),
            identifier=ident,
            label=label,
            namespace=ns,
            entity_type=kind,
            taxon="9606",
            has_hierarchy=kind == "ontology_class",
            parent_count=int(kind == "ontology_class"),
            child_count=0,
            identifiers=[dict(ns="uniprot", id="P00813", source="signor")]
            if label == "ADA"
            else [],
            annotations=[
                dict(
                    term="description", value="Example annotation", source="signor", dataset="test"
                )
            ],
        )
        for ident, label, ns, kind in [
            ("P04637", "TP53", "uniprot", "protein"),
            ("P38398", "BRCA1", "uniprot", "protein"),
            ("100", "ADA", "entrez", "protein"),
            ("KW-0001", "KW-0001", "keyword", "ontology_class"),
        ]
    ]
    relations = [
        dict(
            relation_key=key("relation"),
            subject_entity_key=key("P04637"),
            subject_label="TP53",
            object_entity_key=key("P38398"),
            object_label="BRCA1",
            subject_type="protein",
            object_type="protein",
            predicate="affects",
            category="interaction",
            interaction_class="protein_protein",
            taxon="9606",
            is_directed=True,
            sign=1,
            sources=["signor"],
            evidence_count=1,
            evidence=[],
            annotations=[],
        )
    ]
    for source in ["signor", "uniprot", "chebi"]:
        target = Path(root) / "resources" / source / "1"
        target.mkdir(parents=True, exist_ok=True)
        pq.write_table(
            pa.Table.from_pylist(entities if source != "chebi" else [], schema=ENTITY_SCHEMA),
            target / "entities.parquet",
        )
        pq.write_table(
            pa.Table.from_pylist(relations if source == "signor" else [], schema=RELATION_SCHEMA),
            target / "relations.parquet",
        )
        pq.write_table(
            pa.table(
                {
                    "relation_key": [key("relation")],
                    "source": ["signor"],
                    "row_id": ["1"],
                    "payload_json": ["{}"],
                }
            ),
            target / "evidence_payloads.parquet",
        )
    return Path(root)
