"""Add authoritative RefSeq and EMBL-CDS claims from UniProt's bulk export."""

import argparse
import json
import time
import threading
from pathlib import Path

import duckdb


def build(selected, base, output, threads=6, memory="8GB"):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(output / "sequence-mappings.duckdb"))
    con.execute(f"SET memory_limit='{memory}'")
    con.execute(f"SET threads={threads}")
    con.execute("SET preserve_insertion_order=false")
    start = time.monotonic()

    def log(event, **fields):
        print(
            json.dumps(dict(event=event, seconds=round(time.monotonic() - start), **fields)),
            flush=True,
        )

    stop = threading.Event()

    def progress():
        while not stop.wait(15):
            log("sequence_prepare_progress")

    threading.Thread(target=progress, daemon=True).start()

    # The official idmapping README specifies 22 columns, with RefSeq at 4,
    # organism at 13 and EMBL-CDS (protein, not nucleotide) at 18.
    columns = "{" + ",".join(f"'c{i}':'VARCHAR'" for i in range(22)) + "}"
    log("sequence_export_scan_start")
    con.execute(
        f"""CREATE OR REPLACE TABLE sequences AS
        SELECT c0 accession,c1 entry_name,c12 taxon,c3 refseq,c17 cds
        FROM read_csv(?, delim='\t', header=false, quote='', escape='', columns={columns})
        WHERE nullif(trim(c3),'') IS NOT NULL OR nullif(trim(c17),'') IS NOT NULL""",
        [str(selected)],
    )
    log(
        "sequence_export_scan_done",
        records=con.execute("SELECT count(*) FROM sequences").fetchone()[0],
    )
    for ns, field in [("refseq_protein", "refseq"), ("genbank", "cds")]:
        dest = output / f"{ns}.parquet"
        con.execute(f"""COPY (
            SELECT '{ns}' source_type,trim(value) source_id,accession hub_id,
              coalesce(nullif(taxon,''),'0') taxonomy_id,'uniprot_selected' backend
            FROM sequences, unnest(string_split({field},';')) t(value)
            WHERE nullif(trim(value),'') IS NOT NULL)
            TO '{dest}' (FORMAT PARQUET,COMPRESSION ZSTD)""")
        log("sequence_claims_done", namespace=ns)
    # Preserve identity and entry-name/reviewed-status evidence for every target,
    # including primary accessions newly present in this authoritative release.
    claims = output / "sequence-identities.parquet"
    con.execute(f"""COPY (
        SELECT 'uniprot' source_type,accession source_id,accession hub_id,
          coalesce(nullif(taxon,''),'0') taxonomy_id,'uniprot_selected' backend FROM sequences
        UNION ALL SELECT 'uniprot_entry',entry_name,accession,
          coalesce(nullif(taxon,''),'0'),'uniprot_selected' FROM sequences WHERE entry_name IS NOT NULL
        ) TO '{claims}' (FORMAT PARQUET,COMPRESSION ZSTD)""")
    dest = output / "uniprot.parquet"
    paths = [
        str(base),
        str(claims),
        str(output / "refseq_protein.parquet"),
        str(output / "genbank.parquet"),
    ]
    con.execute(
        f"COPY (SELECT * FROM read_parquet(?)) TO '{dest}' (FORMAT PARQUET,COMPRESSION ZSTD)",
        [paths],
    )
    counts = con.execute(
        f"SELECT source_type,count(*) FROM read_parquet('{dest}') GROUP BY 1"
    ).fetchall()
    if dict(counts).get("refseq_protein", 0) <= 0:
        raise ValueError("Reference sequence mapping contains no RefSeq protein records")
    (output / "sequence-provenance.json").write_text(
        json.dumps(
            dict(
                selected_export=str(Path(selected).resolve()),
                base_hub=str(Path(base).resolve()),
                source_url="https://ftp.uniprot.org/pub/databases/uniprot/current_release/knowledgebase/idmapping/idmapping_selected.tab.gz",
                counts=dict(counts),
                seconds=time.monotonic() - start,
            ),
            indent=2,
        )
    )
    log("sequence_hub_complete", counts=dict(counts))
    stop.set()
    con.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--selected", required=True)
    p.add_argument("--base", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--threads", type=int, default=6)
    p.add_argument("--memory", default="8GB")
    a = p.parse_args()
    build(a.selected, a.base, a.output, a.threads, a.memory)
