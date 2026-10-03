"""Publish an immutable derived reference with complete source-ID anchor mappings.

Keeps the parent graph/membership QC. Ambiguous source IDs expose every supported
anchor rather than an artificial unique native candidate. No resolver outcomes
are used to construct these mappings.
"""

import argparse
import hashlib
import json
import time
from pathlib import Path
import duckdb
import pyarrow.parquet as pq


def finalize(reference, output, memory="6GB", threads=6):
    ref = Path(reference).resolve()
    out = Path(output).resolve()
    parent = json.loads((ref / "manifest.json").read_text())
    if parent["status"] != "complete":
        raise ValueError("Parent reference is incomplete")
    out.mkdir(parents=True, exist_ok=False)
    for child in ref.iterdir():
        if child.is_dir() and child.name not in {"chemical-entities", "gene_protein-entities"}:
            (out / child.name).symlink_to(child, target_is_directory=True)
    for domain in ["chemical", "gene_protein"]:
        dest = out / (domain + "-entities")
        dest.mkdir()
        for f in (ref / (domain + "-entities")).iterdir():
            if f.name not in {"_SUCCESS.json", "job.json", "worker.log"} and f.name not in (
                {"identity_identifiers.parquet", "counts.json"}
                if domain == "chemical"
                else {"entities.parquet", "counts.json"}
            ):
                (dest / f.name).symlink_to(f)
    stage = out / "source-chemical-mappings"
    stage.mkdir()
    c = duckdb.connect(str(stage / "work.duckdb"))
    c.execute(f"SET memory_limit='{memory}'")
    c.execute(f"SET threads={threads}")
    c.execute("SET preserve_insertion_order=false")
    c.execute(f"SET temp_directory='{stage}/spill'")

    def sql(q):
        start = time.monotonic()
        print(json.dumps(dict(event="projection_sql_start", sql=q[:95])), flush=True)
        c.execute(q)
        print(
            json.dumps(
                dict(event="projection_sql_done", seconds=round(time.monotonic() - start, 2))
            ),
            flush=True,
        )

    def copy(q, path):
        sql(f"COPY ({q}) TO '{path}' (FORMAT PARQUET,COMPRESSION ZSTD)")

    sql(
        f"CREATE TABLE natives AS SELECT hub namespace,local_id identifier,record_id FROM read_parquet('{ref}/members-ramp/members.parquet')"
    )
    sql(f"""CREATE TABLE links AS SELECT record_id native_record,record_id via_record FROM natives
        UNION SELECT n.record_id,e.target_record FROM natives n JOIN read_parquet('{ref}/chemical-graph/edges.parquet') e ON n.record_id=e.source_record
        UNION SELECT n.record_id,e.source_record FROM natives n JOIN read_parquet('{ref}/chemical-graph/edges.parquet') e ON n.record_id=e.target_record""")
    sql(f"""CREATE TABLE support_records AS SELECT * FROM links UNION
        SELECT l.native_record,b.boundary_record FROM links l
        JOIN read_parquet('{ref}/chemical-decisions/vertices.parquet') v ON v.record_id=l.via_record
        JOIN read_parquet('{ref}/chemical-decisions/boundaries.parquet') b USING(component)""")
    chemical = tuple(
        h for h in parent["sources"] if h not in {"uniprot", "entrez", "ramp_gene", "kegg_gene"}
    )
    anchor_queries = []
    for hub in chemical:
        anchor_queries.append(
            f"SELECT record_id,anchor FROM read_parquet('{ref}/records-{hub}/records.parquet') SEMI JOIN (SELECT DISTINCT via_record FROM support_records) s ON record_id=s.via_record WHERE anchor_count=1"
        )
        anchor_queries.append(
            f"SELECT record_id,anchor FROM read_parquet('{ref}/records-{hub}/multiple_anchor_claims.parquet') SEMI JOIN (SELECT DISTINCT via_record FROM support_records) s ON record_id=s.via_record"
        )
    sql("CREATE TABLE anchors AS " + " UNION ALL ".join(anchor_queries))
    copy(
        "SELECT DISTINCT n.namespace,n.identifier,a.anchor entity_id,n.record_id,s.via_record FROM natives n JOIN support_records s ON n.record_id=s.native_record JOIN anchors a ON a.record_id=s.via_record",
        stage / "provenance.parquet",
    )
    sql(
        f"CREATE TABLE projected AS SELECT DISTINCT namespace,identifier,entity_id,record_id FROM read_parquet('{stage}/provenance.parquet')"
    )
    copy(
        "SELECT namespace,identifier,count(DISTINCT entity_id) candidate_anchors,count(DISTINCT substr(entity_id,10,14)) connectivity_blocks FROM projected GROUP BY ALL",
        stage / "qc.parquet",
    )
    copy(
        f"""SELECT * FROM read_parquet('{ref}/chemical-entities/identity_identifiers.parquet') WHERE namespace NOT IN ('ramp')
        UNION ALL SELECT * FROM projected
        UNION ALL SELECT i.* FROM read_parquet('{ref}/chemical-entities/identity_identifiers.parquet') i
        ANTI JOIN projected p USING(namespace,identifier) WHERE i.namespace IN ('ramp')""",
        out / "chemical-entities/identity_identifiers.parquet",
    )
    # Restrict metadata repair to proteins touched by the new source hubs.
    sql(
        f"CREATE TABLE touched AS SELECT DISTINCT entity_id FROM read_parquet('{ref}/members-ramp_gene/members.parquet') WHERE starts_with(entity_id,'uniprot:')"
    )
    sql(
        f"CREATE TABLE authoritative_taxa AS SELECT anchor entity_id,min(taxon) taxon FROM read_parquet('{ref}/records-uniprot/records.parquet') SEMI JOIN touched t ON anchor=t.entity_id WHERE anchor_count=1 AND taxon NOT IN ('','0') GROUP BY anchor"
    )
    copy(
        f"""SELECT e.entity_id,e.kind,e.primary_anchor,coalesce(r.taxon,e.taxon) taxon,e.quarantined
        FROM read_parquet('{ref}/gene_protein-entities/entities.parquet') e
        LEFT JOIN authoritative_taxa r USING(entity_id)""",
        out / "gene_protein-entities/entities.parquet",
    )
    sql("CREATE TABLE projected_entities AS SELECT DISTINCT entity_id FROM projected")
    sql(
        f"CREATE TABLE existing_projected AS SELECT e.entity_id FROM read_parquet('{ref}/chemical-entities/entities.parquet') e SEMI JOIN projected_entities p USING(entity_id)"
    )
    missing = c.execute(
        "SELECT count(*) FROM projected_entities ANTI JOIN existing_projected USING(entity_id)"
    ).fetchone()[0]
    if missing:
        raise ValueError(f"{missing} projected anchors missing from reference entities")
    summary = {
        "missing_projected_anchors": missing,
        "mappings": c.execute("SELECT count(*) FROM projected").fetchone()[0],
        "by_namespace": c.execute(
            f"SELECT namespace,count(*) identifiers,count(*) FILTER(WHERE candidate_anchors>1) ambiguous_identifiers FROM read_parquet('{stage}/qc.parquet') GROUP BY 1"
        ).fetchall(),
    }
    (stage / "summary.json").write_text(json.dumps(summary, indent=2))
    for domain in ["chemical", "gene_protein"]:
        d = out / (domain + "-entities")
        counts = {f.name: pq.ParquetFile(f).metadata.num_rows for f in d.glob("*.parquet")}
        (d / "counts.json").write_text(json.dumps(counts, indent=2))
        parent["outputs"][domain].update(
            entities=str(d / "entities.parquet"),
            identity_identifiers=str(d / "identity_identifiers.parquet"),
            counts=counts,
        )
    identity = {
        "parent_fingerprint": parent["fingerprint"],
        "code_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "policy": "source-id-complete-anchor-multimap-v1",
    }
    parent.update(
        fingerprint=hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest(),
        parent_reference=str(ref),
        derivation=identity,
        source_chemical_policy="RaMP IDs expose all supported full anchors; different anchors remain separate. Native-only fallback only where no anchored candidate exists.",
    )
    parent["stages"] += ["source-chemical-mappings", "authoritative-protein-taxonomy"]
    c.close()
    (stage / "work.duckdb").unlink()
    (out / "manifest.json").write_text(json.dumps(parent, indent=2))
    print(json.dumps(dict(event="source_reference_finalized", **summary)), flush=True)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--reference", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--memory", default="6GB")
    p.add_argument("--threads", type=int, default=6)
    a = p.parse_args()
    finalize(a.reference, a.output, a.memory, a.threads)
