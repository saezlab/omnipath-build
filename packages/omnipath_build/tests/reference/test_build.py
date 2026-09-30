"""Small real-Parquet contract and graph-policy tests for the offline builder."""

import json
import subprocess
import sys
from pathlib import Path
import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from omnipath_build import reference

ROOT = Path(reference.__file__).resolve().parent
BIN = Path(__file__).resolve().parents[3] / "omnipath_resolver/rust/reference/target/release"
HUBS = (
    "chebi",
    "pubchem",
    "chembl",
    "hmdb",
    "lipidmaps",
    "swisslipids",
    "bigg",
    "metanetx",
    "refmet",
    "ramp",
    "ramp_gene",
    "uniprot",
    "entrez",
)
K1 = "AAAAAAAAAAAAAA-BBBBBBBBSA-N"
K2 = "AAAAAAAAAAAAAA-CCCCCCCCSA-N"
K3 = "DDDDDDDDDDDDDD-EEEEEEEESA-N"


def fixture(hubs):
    hubs.mkdir()
    data = {h: [] for h in HUBS}

    def add(hub, ident, ns, value, taxon="0"):
        data[hub].append(
            dict(
                hub_id=ident,
                source_type=ns,
                source_id=value,
                taxonomy_id=taxon,
                backend=hub,
            )
        )

    for hub, ident, key in [
        ("pubchem", "1", K1),
        ("chebi", "CHEBI:1", K1),
        ("pubchem", "2", K2),
        ("chembl", "CHEMBL3", K3),
    ]:
        add(hub, ident, "inchikey", key)
    add("chebi", "CHEBI:2", "pubchem", "2")  # uniquely attached keyless
    add("chebi", "CHEBI:3", "hmdb", "H1")
    add("hmdb", "H1", "pubchem", "1")
    add("hmdb", "H1", "chembl", "CHEMBL3")  # indirect competing anchors
    add("chebi", "CHEBI:4", "inchikey", K2)
    add("chebi", "CHEBI:4", "pubchem", "1")  # direct same-connectivity disagreement
    add("chebi", "CHEBI:5", "hmdb", "H2")
    add("hmdb", "H2", "name", "keyless")  # zero-anchor component
    add("hmdb", "MULTI", "inchikey", K1)
    add("hmdb", "MULTI", "inchikey", K3)
    add("chebi", "CHEBI:6", "hmdb", "MULTI")
    add("chebi", "CHEBI:7", "inchikey", "MOSFIJXAXDLOML-UHFFFAOYSA-N")
    add("chebi", "CHEBI:8", "pubchem", "missing")
    # Same descriptor can attach a keyless record, but cannot bridge full keys.
    add("chebi", "CHEBI:90", "name", "PC(16:0/18:1)")
    add("chebi", "CHEBI:90", "inchikey", K1)
    add("hmdb", "GOSLIN_ATTACH", "name", "PC 16:0/18:1")
    add("hmdb", "GOSLIN_ATTACH", "synonym", "PC 34:1")
    add("chebi", "CHEBI:91", "name", "PC 18:0/18:1")
    add("chebi", "CHEBI:91", "inchikey", K2)
    add("lipidmaps", "GOSLIN_CONFLICT", "name", "PC 18:0/18:1")
    add("lipidmaps", "GOSLIN_CONFLICT", "inchikey", K3)
    add("hmdb", "GOSLIN_CONFLICT", "name", "PC(18:0/18:1)")
    add("hmdb", "GOSLIN_ZERO", "name", "PE 36:2")
    add(
        "swisslipids",
        "GOSLIN_ZERO",
        "synonym",
        "arbitrary text | PE(36:2)",
    )
    add("lipidmaps", "GOSLIN_ZERO", "synonym", "PE(36:2); unrelated label")
    add("swisslipids", "GOSLIN_ABBREV", "lipid_shorthand", "PE(36:2)")
    add(
        "lipidmaps",
        "GOSLIN_SYSTEMATIC",
        "systematic_name",
        "5-methyl-octadecanoic acid",
    )
    add("hmdb", "GOSLIN_COARSE", "name", "PC 16:0_18:1")
    add("ramp", "RAMP_C_MATCH", "pubchem", "1")
    add("ramp", "RAMP_C_CONFLICT", "pubchem", "1")
    add("ramp", "RAMP_C_CONFLICT", "pubchem", "2")
    add("ramp_gene", "RAMP_G_1", "entrez", "4")
    add("ramp_gene", "RAMP_G_2", "uniprot", "P00002", "9606")
    add("ramp_gene", "RAMP_G_2", "uniprot", "P00003", "9606")
    add("ramp_gene", "RAMP_G_wrong", "uniprot", "P00002", "10090")
    add("refmet", "MATCH", "inchikey", K1)
    add("refmet", "COARSE", "name", "PC 16:0_18:1")
    add("refmet", "STANDALONE", "name", "TG 51:7")
    for p, g in [("P00001", "1"), ("P00002", "2"), ("P00003", "2")]:
        add("uniprot", p, "uniprot", p, "9606")
        add("uniprot", p, "entrez", g)
        add("entrez", g, "entrez", g, "9606")
    for protein, gene, entry in [
        ("P00004", "4", "FOUR_HUMAN"),
        ("P00005", "4", "FIVE_HUMAN"),
        ("P00006", "4", "P00006_HUMAN"),
        ("P00007", "5", "P00007_HUMAN"),
        ("P00008", "5", "P00008_HUMAN"),
        ("P00009", "6", "NINE_HUMAN"),  # no native Entrez row
    ]:
        add("uniprot", protein, "uniprot", protein, "9606")
        add("uniprot", protein, "entrez", gene, "9606")
        add("uniprot", protein, "uniprot_entry", entry, "9606")
    for protein, gene in [("P00010", "7"), ("P00011", "7")]:
        add("uniprot", protein, "uniprot", protein, "9606")
        add("uniprot", protein, "uniprot_entry", protein + "REVIEWED_HUMAN", "9606")
        add("entrez", gene, "uniprot", protein, "9606")
    add("entrez", "3", "entrez", "3", "9606")
    add("uniprot", "P00002", "uniprot_entry", "REVIEWED_HUMAN", "9606")
    add("uniprot", "P00003", "uniprot_entry", "P00003_HUMAN", "9606")
    add("uniprot", "__________", "uniprot_entry", "__________")
    schema = pa.schema(
        [(n, pa.string()) for n in ("source_type", "source_id", "hub_id", "taxonomy_id", "backend")]
    )
    for h in HUBS:
        pq.write_table(pa.Table.from_pylist(data[h], schema=schema), hubs / (h + ".parquet"))


def test_build_and_resume(tmp_path):
    hubs = tmp_path / "hubs"
    fixture(hubs)
    out = tmp_path / "reference"
    cmd = [
        sys.executable,
        str(ROOT / "build_reference.py"),
        "--hubs",
        str(hubs),
        "--output",
        str(out),
        "--components",
        str(BIN / "anchor-components"),
        "--memory",
        "256MB",
        "--threads",
        "1",
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    c = duckdb.connect()
    members = dict(
        c.execute(
            "select record_id,entity_id from read_parquet(?)",
            [str(out / "members-*" / "members.parquet")],
        ).fetchall()
    )
    assert members["pubchem:1"] == members["chebi:CHEBI:1"] == "inchikey:" + K1
    assert members["chebi:CHEBI:2"] == "inchikey:" + K2
    assert members["chebi:CHEBI:3"] == "chebi:CHEBI:3" and members["hmdb:H1"] == "hmdb:H1"
    assert members["chebi:CHEBI:5"] == members["hmdb:H2"] == "chebi:CHEBI:5"
    assert members["chebi:CHEBI:6"] == "chebi:CHEBI:6" and members["hmdb:MULTI"] == "hmdb:MULTI"
    assert members["chebi:CHEBI:7"] == "chebi:CHEBI:7"
    assert members["hmdb:GOSLIN_ATTACH"] == "inchikey:" + K1
    assert members["hmdb:GOSLIN_CONFLICT"] == "hmdb:GOSLIN_CONFLICT"
    assert members["chebi:CHEBI:91"] == "inchikey:" + K2
    assert members["lipidmaps:GOSLIN_CONFLICT"] == "inchikey:" + K3
    assert (
        members["hmdb:GOSLIN_ZERO"]
        == members["swisslipids:GOSLIN_ZERO"]
        == members["lipidmaps:GOSLIN_ZERO"]
    )
    assert members["hmdb:GOSLIN_COARSE"] != "inchikey:" + K1
    assert members["ramp:RAMP_C_MATCH"] == "inchikey:" + K1
    assert members["ramp:RAMP_C_CONFLICT"] == "ramp:RAMP_C_CONFLICT"
    source_products = c.execute(
        "SELECT namespace,identifier,entity_id FROM read_parquet(?) ORDER BY ALL",
        [str(out / "source-gene-resolution/protein_identifiers.parquet")],
    ).fetchall()
    assert source_products == [
        ("ramp_gene", "RAMP_G_1", "uniprot:P00004"),
        ("ramp_gene", "RAMP_G_1", "uniprot:P00005"),
        ("ramp_gene", "RAMP_G_2", "uniprot:P00002"),
    ]
    assert (
        c.execute(
            "SELECT count(*) FROM read_parquet(?) WHERE identifier='mmu:wrong'",
            [str(out / "source-gene-resolution/identifiers.parquet")],
        ).fetchone()[0]
        == 0
    )
    assert members["refmet:MATCH"] == "inchikey:" + K1
    assert members["refmet:COARSE"] == members["hmdb:GOSLIN_COARSE"]
    assert members["refmet:STANDALONE"] != "inchikey:" + K1
    assert c.execute(
        "select goslin from read_parquet(?) where record_id='hmdb:GOSLIN_ATTACH'",
        [str(out / "goslin-identifiers/claims.parquet")],
    ).fetchall() == [("sn_position:PC 16:0/18:1",)]
    assert c.execute(
        "select distinct entity_id from read_parquet(?) where namespace='goslin' and identifier='sn_position:PC 16:0/18:1'",
        [str(out / "chemical-entities/identity_identifiers.parquet")],
    ).fetchall() == [("inchikey:" + K1,)]
    assert c.execute(
        "select full_anchors from read_parquet(?) where goslin='sn_position:PC 18:0/18:1'",
        [str(out / "goslin-qc/anchor_counts.parquet")],
    ).fetchone() == (2,)

    assert members["entrez:1"] == members["uniprot:P00001"] == "uniprot:P00001"
    assert members["entrez:2"] == "entrez:2" and members["entrez:3"] == "entrez:3"
    assert (
        c.execute(
            "select count(*) from read_parquet(?) where category='different_anchors' and same_connectivity",
            [str(out / "chemical-entities/cross_reference_exceptions.parquet")],
        ).fetchone()[0]
        == 1
    )
    assert c.execute(
        "select protein_entity_id from read_parquet(?) where entrez_id='2' and projection_eligible",
        [str(out / "gene-products/gene_products.parquet")],
    ).fetchall() == [("uniprot:P00002",)]
    assert c.execute(
        "select primary_anchor from read_parquet(?) where entity_id='uniprot:__________'",
        [str(out / "gene_protein-entities/entities.parquet")],
    ).fetchone() == (None,)
    mapping = c.execute(
        "select identifier,entity_id,admission from read_parquet(?) order by identifier,entity_id",
        [str(out / "gene-resolution/entrez_identifiers.parquet")],
    ).fetchall()
    assert mapping == [
        ("1", "uniprot:P00001", "unreviewed_gene_product"),
        ("2", "uniprot:P00002", "reviewed_gene_product"),
        ("3", "entrez:3", "entrez_only"),
        ("4", "uniprot:P00004", "reviewed_gene_product"),
        ("4", "uniprot:P00005", "reviewed_gene_product"),
        ("5", "uniprot:P00007", "unreviewed_gene_product"),
        ("5", "uniprot:P00008", "unreviewed_gene_product"),
        ("6", "uniprot:P00009", "reviewed_gene_product"),
        ("7", "uniprot:P00010", "reviewed_gene_product"),
        ("7", "uniprot:P00011", "reviewed_gene_product"),
    ]
    assert json.loads((out / "manifest.json").read_text())["full_scope"]
    stamps = {str(p): p.stat().st_mtime_ns for p in out.glob("*/_SUCCESS.json")}
    subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    assert stamps == {str(p): p.stat().st_mtime_ns for p in out.glob("*/_SUCCESS.json")}

    derived = tmp_path / "derived"
    subprocess.run(
        [
            sys.executable,
            str(ROOT / "build_reference.py"),
            "--derive-gene-resolution-from",
            str(out),
            "--output",
            str(derived),
            "--memory",
            "256MB",
            "--threads",
            "1",
        ],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    assert pq.read_table(derived / "gene-resolution/entrez_identifiers.parquet").equals(
        pq.read_table(out / "gene-resolution/entrez_identifiers.parquet")
    )
    assert (derived / "gene_protein-entities").is_symlink()
    assert json.loads((derived / "manifest.json").read_text())["parent_reference"] == str(out)
