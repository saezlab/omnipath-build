"""Offline helpers preserve exact evidence identity and product record closure."""

import hashlib
import json
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from omnipath_client import Client

_IDENTIFIER = pa.struct([("ns", pa.string()), ("id", pa.string())])
_FORM = pa.struct(
    [
        ("protein_entity_key", pa.string()),
        ("transcript_entity_key", pa.string()),
        ("isoform_identifier", _IDENTIFIER),
    ]
)
EVIDENCE = pa.schema(
    [
        ("relation_id", pa.int32()),
        ("ordinal", pa.int32()),
        ("source", pa.string()),
        ("dataset", pa.string()),
        ("row_id", pa.string()),
        ("upstream_id", pa.string()),
        ("annotations", pa.list_(pa.struct([("term", pa.string()), ("value", pa.string())]))),
        ("subject_molecular_form", _FORM),
        ("object_molecular_form", _FORM),
    ]
)


def resource_snapshot(tmp_path, resources):
    """A snapshot from nested fixture rows: each relation's ``evidence`` becomes its
    ``relation_evidence`` rows."""
    records = []
    for resource, (entities, relations) in resources.items():
        folder = tmp_path / "resources" / resource / "1"
        folder.mkdir(parents=True)
        entity = [dict(row, entity_id=i) for i, row in enumerate(entities)]
        relation, evidence = [], []
        for i, row in enumerate(relations):
            items = row.get("evidence") or []
            relation.append({k: v for k, v in row.items() if k != "evidence"} | {"relation_id": i})
            evidence += [
                dict(item, relation_id=i, ordinal=n) for n, item in enumerate(items) if item
            ]
        files = []
        for name, table in [
            ("entity", pa.Table.from_pylist(entity)),
            ("relation", pa.Table.from_pylist(relation)),
            ("relation_evidence", pa.Table.from_pylist(evidence, schema=EVIDENCE)),
        ]:
            path = folder / f"{name}.parquet"
            pq.write_table(table, path)
            files.append(
                dict(
                    name=path.name,
                    size_bytes=path.stat().st_size,
                    sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                )
            )
        records.append(dict(resource_id=resource, version="1", files=files))
    (tmp_path / "snapshot.json").write_text(
        json.dumps(
            dict(
                snapshot_version=1,
                api_url="https://api.example",
                release="1",
                resources=records,
            )
        )
    )
    return Client.from_snapshot(tmp_path)


def snapshot(tmp_path):
    entities = [
        dict(
            entity_key=k,
            label=k,
            identifier=k,
            entity_type=t,
            identifiers=[],
            reference_entity_key="entrez:1",
            gene_reference_keys=["entrez:1"],
        )
        for k, t in [("gene", "gene"), ("anchor", "protein"), ("product", "protein")]
    ]
    evidence = [
        dict(source="test", row_id="1", subject_molecular_form=None, object_molecular_form=None),
        dict(
            source="test",
            row_id="2",
            subject_molecular_form=dict(
                protein_entity_key="product", isoform_identifier=dict(ns="uniprot", id="P1-2")
            ),
            object_molecular_form=None,
        ),
    ]
    relations = [
        dict(
            relation_key="r",
            subject_entity_key="anchor",
            object_entity_key="gene",
            subject_reference_entity_key="entrez:1",
            object_reference_entity_key="entrez:1",
            evidence=evidence,
            evidence_count=2,
        )
    ]
    return resource_snapshot(tmp_path, {"test": (entities, relations)})


def test_exact_product_helpers_and_closure(tmp_path):
    with snapshot(tmp_path) as client:
        assert len(client.related_reference("entrez:1", resources="test").fetchall()) == 1
        selected = client.related_product(
            "product", resources="test", isoform_identifier="uniprot:P1-2"
        )
        rows = selected.to_arrow_table().to_pylist()
        assert len(rows) == 1 and len(rows[0]["evidence"]) == 1
        assert rows[0]["subject_entity_key"] == "anchor"
        assert (
            client.related_product("product", resources="test", endpoint="target").fetchall() == []
        )
        assert (
            client.related_product(
                "product", resources="test", isoform_identifier="uniprot:P1-3"
            ).fetchall()
            == []
        )
        assert client.referenced_products(selected, resources="test").project(
            "entity_key"
        ).fetchall() == [("product",)]


def molecular_snapshot(tmp_path):
    def form(protein=None, transcript=None, isoform=None):
        return dict(
            protein_entity_key=protein,
            transcript_entity_key=transcript,
            isoform_identifier=dict(ns="uniprot", id=isoform) if isoform else None,
        )

    def occurrence(row_id, subject=None, object=None):
        return dict(
            source="current",
            row_id=row_id,
            subject_molecular_form=subject,
            object_molecular_form=object,
        )

    source = occurrence("source", form("product", isoform="P1-2"), form(transcript="transcript"))
    target = occurrence("target", None, form("product", isoform="P1-2"))
    paired = occurrence("paired", form("product", isoform="P1-2"), form("product", isoform="P1-2"))
    wrong_isoform = occurrence(
        "wrong_isoform", form("product", isoform="P1-3"), form("other", isoform="P1-2")
    )
    null_key = occurrence("null_key", form(isoform="P1-2"))
    entities = [
        dict(entity_key=key, label=key, identifier=key, entity_type=kind, identifiers=[])
        for key, kind in [
            ("product", "protein"),
            ("other", "protein"),
            ("transcript", "transcript"),
        ]
    ]
    relations = [
        dict(
            relation_key=key,
            subject_entity_key="anchor",
            object_entity_key="gene",
            evidence=evidence,
            evidence_count=len(evidence) if evidence is not None else 0,
        )
        for key, evidence in [
            (
                "mixed",
                [
                    None,
                    occurrence("unknown"),
                    source,
                    source,
                    target,
                    paired,
                    wrong_isoform,
                    null_key,
                ],
            ),
            ("split", [source, target]),
            ("empty", []),
            ("null", None),
        ]
    ]
    legacy = [
        dict(
            relation_key="legacy",
            subject_entity_key="anchor",
            object_entity_key="gene",
            evidence_count=0,
        )
    ]
    contextless = [dict(**legacy[0], evidence=[dict(source="contextless", row_id="1")])]
    return resource_snapshot(
        tmp_path,
        {
            "current": (entities, relations),
            "legacy": (entities, legacy),
            "contextless": (entities, contextless),
            "unselected": (entities, relations),
        },
    )


@pytest.mark.parametrize(
    ("endpoint", "expected"),
    [
        ("source", {"mixed": ["source", "source", "paired"], "split": ["source"]}),
        ("target", {"mixed": ["target", "paired"], "split": ["target"]}),
        ("any", {"mixed": ["source", "source", "target", "paired"], "split": ["source", "target"]}),
        ("both", {"mixed": ["paired"]}),
    ],
)
def test_mixed_schemas_preserve_occurrence_endpoint_and_isoform_identity(
    tmp_path, endpoint, expected
):
    with molecular_snapshot(tmp_path) as client:
        selected = client.related_product(
            "product",
            resources=["current", "legacy", "contextless"],
            isoform_identifier="uniprot:P1-2",
            endpoint=endpoint,
        )
        rows = selected.to_arrow_table().to_pylist()
        assert {
            row["relation_key"]: [ev["row_id"] for ev in row["evidence"]] for row in rows
        } == expected
        assert all(row["evidence_count"] == len(row["evidence"]) for row in rows)
        assert all(row["_resource"] == "current" for row in rows)
        assert all(
            (row["subject_entity_key"], row["object_entity_key"]) == ("anchor", "gene")
            for row in rows
        )
        assert all("relation_id" not in ev for row in rows for ev in row["evidence"])
        closure = (
            client.referenced_products(
                selected, resources=["current", "legacy", "contextless", "unselected"]
            )
            .project("entity_key, _resource")
            .fetchall()
        )
        expected_keys = {"product", "transcript"} if endpoint in {"source", "any"} else {"product"}
        assert set(closure) == {(key, "current") for key in expected_keys}
        assert len(closure) == len(expected_keys)


def test_transcript_and_absent_context_fields(tmp_path):
    with molecular_snapshot(tmp_path) as client:
        selected = client.related_product(
            "transcript", resources="current", product_type="transcript", endpoint="target"
        )
        assert selected.project("relation_key, evidence_count").order(
            "relation_key"
        ).fetchall() == [("mixed", 2), ("split", 1)]
        assert client.related_product("product", resources="contextless").fetchall() == []
        assert (
            client.referenced_products(
                client.relations("contextless"), resources="current"
            ).fetchall()
            == []
        )


def test_product_closure_plan_expands_selected_evidence_once(tmp_path):
    with molecular_snapshot(tmp_path) as client:
        selected = client.related_product("product", resources="current")
        closure = client.referenced_products(selected, resources="current")
        assert "to_json" not in selected.sql_query().lower()
        plan = json.loads(
            client._db().execute(f"EXPLAIN (FORMAT JSON) {closure.sql_query()}").fetchone()[1]
        )

        def nodes(items):
            for item in items:
                yield item
                yield from nodes(item["children"])

        assert not any("DELIM" in node["name"] for node in nodes(plan))
