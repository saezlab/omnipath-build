import json
import pyarrow as pa
import pyarrow.parquet as pq

from omnipath_build.reference.finalize_source_reference import finalize


def test_complete_source_multimap_and_anchor_taxonomy(tmp_path):
    ref = tmp_path / "ref"
    out = tmp_path / "derived"

    def write(name, rows, schema=None):
        p = ref / name
        p.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(pa.Table.from_pylist(rows, schema=schema), p)

    a = "inchikey:AAAAAAAAAAAAAA-BBBBBBBBBB-C"
    b = "inchikey:AAAAAAAAAAAAAA-DDDDDDDDDD-E"
    member = [
        dict(hub="ramp", local_id="R1", record_id="r1"),
        dict(hub="ramp", local_id="R2", record_id="r2"),
    ]
    write("members-ramp/members.parquet", member)
    write("members-kegg/members.parquet", [dict(hub="kegg", local_id="C1", record_id="k1")])
    write(
        "chemical-graph/edges.parquet",
        [
            dict(source_record="r1", target_record="p1"),
            dict(source_record="r1", target_record="p2"),
            dict(source_record="k1", target_record="r1"),
        ],
    )
    write(
        "chemical-decisions/vertices.parquet",
        [dict(record_id="r1", component=1), dict(record_id="k1", component=1)],
    )
    write(
        "chemical-decisions/boundaries.parquet",
        [dict(component=1, boundary_record="p1"), dict(component=1, boundary_record="p2")],
    )
    for hub, rows in [
        (
            "ramp",
            [
                dict(record_id="r1", anchor=None, anchor_count=0),
                dict(record_id="r2", anchor=None, anchor_count=0),
            ],
        ),
        ("kegg", [dict(record_id="k1", anchor=None, anchor_count=0)]),
        (
            "pubchem",
            [
                dict(record_id="p1", anchor=a, anchor_count=1),
                dict(record_id="p2", anchor=b, anchor_count=1),
            ],
        ),
    ]:
        write(f"records-{hub}/records.parquet", rows)
        write(
            f"records-{hub}/multiple_anchor_claims.parquet",
            [],
            pa.schema([("record_id", pa.string()), ("anchor", pa.string())]),
        )
    write(
        "chemical-entities/identity_identifiers.parquet",
        [
            dict(namespace=ns, identifier=i, entity_id=e, record_id=r)
            for ns, i, e, r in [
                ("ramp", "R1", "ramp:R1", "r1"),
                ("ramp", "R2", "ramp:R2", "r2"),
                ("kegg", "C1", "kegg:C1", "k1"),
                ("pubchem", "1", a, "p1"),
            ]
        ],
    )
    write(
        "chemical-entities/entities.parquet",
        [
            dict(entity_id=x, kind="chemical", primary_anchor=x, taxon=None, quarantined=False)
            for x in [a, b]
        ],
    )
    write(
        "gene_protein-entities/entities.parquet",
        [
            dict(
                entity_id="uniprot:P1",
                kind="protein",
                primary_anchor="uniprot:P1",
                taxon="4932",
                quarantined=False,
            )
        ],
    )
    write(
        "gene_protein-entities/identity_identifiers.parquet",
        [dict(namespace="uniprot", identifier="P1", entity_id="uniprot:P1", record_id="u1")],
    )
    for h in ["ramp_gene", "kegg_gene"]:
        write(f"members-{h}/members.parquet", [dict(entity_id="uniprot:P1")])
    write(
        "records-uniprot/records.parquet",
        [dict(anchor="uniprot:P1", anchor_count=1, taxon="559292")],
    )
    (ref / "manifest.json").write_text(
        json.dumps(
            dict(
                status="complete",
                fingerprint="fixture",
                sources=["ramp", "kegg", "pubchem", "uniprot"],
                outputs={"chemical": {}, "gene_protein": {}},
                stages=[],
            )
        )
    )
    finalize(ref, out, memory="512MB", threads=2)
    rows = pq.read_table(out / "chemical-entities/identity_identifiers.parquet").to_pylist()
    assert {
        r["entity_id"] for r in rows if r["namespace"] == "ramp" and r["identifier"] == "R1"
    } == {a, b}
    assert {r["entity_id"] for r in rows if r["namespace"] == "kegg"} == {"kegg:C1"}
    assert {r["entity_id"] for r in rows if r["identifier"] == "R2"} == {"ramp:R2"}
    assert (
        pq.read_table(out / "gene_protein-entities/entities.parquet").to_pylist()[0]["taxon"]
        == "559292"
    )
    qc = pq.read_table(out / "source-chemical-mappings/qc.parquet").to_pylist()
    assert all(r["candidate_anchors"] == 2 and r["connectivity_blocks"] == 1 for r in qc)
