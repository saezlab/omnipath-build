"""Identity snapshot build on tiny fixture hubs: one case per rule, exact rows."""

from __future__ import annotations

import json

import duckdb
import pyarrow.parquet as pq
import pytest

from identity_fixtures import key, write_hubs
from omnipath_build.identity import build_identity

KA, KB, KC, KD, KE = (key(x) for x in "ABCDE")
KF, KG, KH = (key(x) for x in "FGH")


@pytest.fixture(scope="module")
def snapshot(tmp_path_factory):
    root = tmp_path_factory.mktemp("identity")
    hubs = write_hubs(root / "hubs")
    manifest = build_identity(
        hubs, root / "out", memory="1GB", threads=2, goslin_cache=root / "goslin", min_free_gib=0.01
    )
    return root, manifest


@pytest.fixture(scope="module")
def q(snapshot):
    root, _ = snapshot
    out = root / "out"
    c = duckdb.connect()

    def query(sql):
        sql = (
            sql.replace("$records", f"'{out}/records.parquet'")
            .replace("$entities", f"read_parquet('{out}/entities/*/*.parquet')")
            .replace("$members", f"read_parquet('{out}/members/*/*.parquet')")
            .replace("$rows", f"read_parquet('{out}/record_rows/*/*.parquet')")
            .replace("$products", f"'{out}/gene_products.parquet'")
            .replace("$labels", f"read_parquet('{out}/gene_labels/*/*.parquet')")
            .replace("$chem", f"read_parquet('{out}/access/target=1/*/*.parquet')")
            .replace("$prot", f"read_parquet('{out}/access/target=2/*/*.parquet')")
        )
        return c.execute(sql).fetchall()

    return query


def decisions(q):
    return {r: (e, d) for r, e, d in q("SELECT record_id,entity_id,decision FROM $records")}


# ------------------------------------------------------------------ rules 1 and 2
def test_rule1_anchors_and_quarantine(q):
    d = decisions(q)
    ik = lambda k: "inchikey:" + k
    # InChIKey records of different hubs share the entity; the "empty" InChIKey is no anchor.
    assert d["chebi:CHEBI:1"] == (ik(KA), "anchored")
    assert d["pubchem:100"] == (ik(KA), "anchored")
    assert d["pubchem:101"] == ("pubchem:101", "structureless")
    assert d["hmdb:HMDB0000004"] == (ik(KD), "anchored")  # hub id HMDB4 is normalized
    # Lipid name anchors: only records without an InChIKey, shared across hubs.
    lipid = "goslin:sn_position:PC 16:0/18:1"
    assert d["swisslipids:SLM:1"] == (lipid, "lipid_name")
    assert d["lipidmaps:LM1"] == (lipid, "lipid_name")
    assert d["chebi:CHEBI:5"] == (ik(KE), "anchored")  # InChIKey wins over its lipid name
    # Protein and gene anchors.
    assert d["uniprot:P04637"] == ("uniprot:P04637", "anchored")
    assert d["uniprot:P04637-2"] == ("uniprot:P04637-2", "anchored")
    assert d["entrez:7157"] == ("entrez:7157", "gene_identity")
    # Rule 2: two InChIKeys or two lipid names at the most specific level quarantine the record.
    assert d["hmdb:HMDB0000002"] == ("hmdb:HMDB0000002", "quarantined")
    assert d["lipidmaps:LMQ"] == ("lipidmaps:LMQ", "quarantined")
    assert q("SELECT anchor_kind,anchor_count,anchor FROM $records WHERE record_id='hmdb:HMDB0000002'") == [
        ("inchikey", 2, None)
    ]
    assert q("SELECT anchor_kind,anchor_count FROM $records WHERE record_id='lipidmaps:LMQ'") == [("goslin", 2)]


def test_records_columns_reviewed_taxon(q):
    assert q(
        "SELECT hub,local_id,anchor,anchor_kind,anchor_count,taxon,reviewed FROM $records "
        "WHERE record_id IN ('uniprot:P04637','uniprot:A0A000','chebi:CHEBI:1') ORDER BY record_id"
    ) == [
        ("chebi", "CHEBI:1", "inchikey:" + KA, "inchikey", 1, "0", False),
        ("uniprot", "A0A000", "uniprot:A0A000", "uniprot", 1, "9606", False),
        ("uniprot", "P04637", "uniprot:P04637", "uniprot", 1, "9606", True),
    ]


# -------------------------------------------------------------- rules 3 and 4
def test_rule3_attach_one_step(q):
    d = decisions(q)
    assert d["chembl:CHEMBL1"] == ("inchikey:" + KA, "attached")  # own edge to an anchored record
    assert d["metanetx:MNXM3"] == ("inchikey:" + KD, "attached")  # the anchored record names it
    # A record that only reaches an anchorless (attached) record is not chained.
    assert d["metanetx:MNXM4"] == ("metanetx:MNXM4", "structureless")
    # Two different anchors, or a quarantined neighbour, block attachment.
    assert d["bigg:b1"] == ("bigg:b1", "ambiguous_native")
    assert d["bigg:b2"] == ("bigg:b2", "ambiguous_native")
    # Proteins: a source gene attaches to the UniProt entry or the gene it names.
    assert d["ramp_gene:RAMP_G_1"] == ("uniprot:P04637", "attached")
    assert d["ramp_gene:RAMP_G_2"] == ("entrez:7157", "attached")
    assert d["ramp_gene:RAMP_G_5"] == ("ramp_gene:RAMP_G_5", "ambiguous_native")  # uniprot AND gene


def test_rule4_group(q):
    d = decisions(q)
    # One record per hub: the entity is the record of the most preferred hub (metanetx > bigg).
    assert d["metanetx:MNXM10"] == ("metanetx:MNXM10", "grouped")
    assert d["bigg:g10"] == ("metanetx:MNXM10", "grouped")
    # Two metanetx records in one group: nobody is merged.
    for r in ("metanetx:MNXM20", "metanetx:MNXM21", "ramp:RAMP_C_1"):
        assert d[r] == (r, "ambiguous_native")
    assert d["bigg:b3"] == ("bigg:b3", "structureless")
    assert q("SELECT preferred_record FROM $entities WHERE entity_id='metanetx:MNXM10'") == [
        ("metanetx:MNXM10",)
    ]


def test_decision_totals_and_manifest(snapshot, q):
    assert dict(q("SELECT decision,count(*) FROM $records GROUP BY 1")) == {
        "anchored": 27,
        "attached": 5,
        "grouped": 2,
        "lipid_name": 3,
        "structureless": 4,
        "ambiguous_native": 6,
        "quarantined": 2,
        "gene_identity": 16,
    }
    _, manifest = snapshot
    assert manifest["format"] == "omnipath-identity-v1"
    assert set(manifest["hubs"]["chebi"]) == {"sha256", "bytes"}
    assert manifest["counts"]["assign"]["groups_accepted"] == 1
    assert manifest["counts"]["assign"]["groups_rejected"] == 1
    assert {"rows", "parts", "entities", "access", "total"} <= set(manifest["timings"])
    assert manifest["counts"]["entities"]["quarantined"] == 2


# ----------------------------------------------------------- entities and members
def test_entities_and_members(q):
    assert q(
        "SELECT entity_id,kind,anchor,taxon,quarantined,preferred_record FROM $entities WHERE entity_id IN "
        "('inchikey:" + KA + "','inchikey:" + KB + "','hmdb:HMDB0000002','goslin:sn_position:PC 16:0/18:1',"
        "'entrez:7157','entrez:5555','uniprot:P04637','ramp_gene:RAMP_G_3') ORDER BY entity_id"
    ) == [
        ("entrez:5555", "gene", None, "9606", False, None),  # named by a gene-product link only
        ("entrez:7157", "gene", None, "9606", False, "entrez:7157"),
        ("goslin:sn_position:PC 16:0/18:1", "chemical", "goslin:sn_position:PC 16:0/18:1", None, False, "lipidmaps:LM1"),
        ("hmdb:HMDB0000002", "chemical", None, None, True, "hmdb:HMDB0000002"),
        ("inchikey:" + KA, "chemical", "inchikey:" + KA, None, False, "chebi:CHEBI:1"),
        # A quarantined record's keys still get entities, without members.
        ("inchikey:" + KB, "chemical", "inchikey:" + KB, None, False, None),
        ("ramp_gene:RAMP_G_3", "gene", None, None, False, "ramp_gene:RAMP_G_3"),
        ("uniprot:P04637", "protein", "uniprot:P04637", "9606", False, "uniprot:P04637"),
    ]
    assert q("SELECT record_id FROM $members WHERE entity_id='inchikey:" + KA + "' ORDER BY 1") == [
        ("chebi:CHEBI:1",),
        ("chembl:CHEMBL1",),
        ("pubchem:100",),
    ]
    assert q("SELECT count(*) FROM $members") == [(65,)]


# -------------------------------------------------------------- gene products (6)
def test_gene_products(q):
    rows = q("SELECT * FROM $products ORDER BY 1,2")
    assert ("uniprot:P04637", "7157", "9606") in rows
    assert ("uniprot:P99999", "7158", "9606") in rows and ("uniprot:P99999", "7159", "9606") in rows
    assert ("uniprot:Q00003", "5555", "9606") in rows  # target record absent: kept
    assert not [r for r in rows if r[0] == "uniprot:Q00002"]  # known, different organisms: dropped
    assert len(rows) == 16


# ----------------------------------------------------------------- access: chemical
def chem(q, ns=None):
    where = f"WHERE ns='{ns}'" if ns else ""
    return q(f"SELECT ns,identifier,entity_id,tag FROM $chem {where} ORDER BY ns,identifier,entity_id")


def test_access_chemical_identity_native_fallback_regular(q):
    ka, kd = "inchikey:" + KA, "inchikey:" + KD
    assert chem(q, "chebi") == [
        ("chebi", "CHEBI:1", ka, "native"),
        ("chebi", "CHEBI:3", kd, "native"),
        ("chebi", "CHEBI:5", "inchikey:" + KE, "native"),
        ("chebi", "CHEBI:6", "inchikey:" + KF, "native"),
        ("chebi", "CHEBI:7", "inchikey:" + KH, "native"),
        ("chebi", "CHEBI:99", ka, "regular"),  # secondary ChEBI id
    ]
    assert chem(q, "pubchem") == [("pubchem", "100", ka, "native"), ("pubchem", "101", "pubchem:101", "native")]
    assert chem(q, "chembl") == [("chembl", "CHEMBL1", ka, "native")]  # attached record: own id row
    assert chem(q, "cas") == [("cas", "50-00-0", ka, "regular")]
    assert chem(q, "drugbank") == [("drugbank", "DB00001", kd, "regular")]
    assert chem(q, "kegg") == [("kegg", "C00001", ka, "fallback"), ("kegg", "C00009", "metanetx:MNXM10", "fallback")]
    # The same bigg id is native for b1 and a weaker fallback claim of b3.
    assert chem(q, "bigg") == [
        ("bigg", "b1", "bigg:b1", "native"),
        ("bigg", "b1", "bigg:b3", "fallback"),
        ("bigg", "b2", "bigg:b2", "native"),
        ("bigg", "b3", "bigg:b3", "native"),
        ("bigg", "g10", "metanetx:MNXM10", "native"),
    ]
    assert chem(q, "metanetx") == [
        ("metanetx", "MNXM10", "metanetx:MNXM10", "native"),
        ("metanetx", "MNXM20", "metanetx:MNXM20", "native"),
        ("metanetx", "MNXM21", "metanetx:MNXM21", "native"),
        ("metanetx", "MNXM3", kd, "native"),
        ("metanetx", "MNXM4", "metanetx:MNXM4", "native"),
    ]
    # Every InChIKey entity, including the quarantined record's keys.
    assert [r[1] for r in chem(q, "inchikey")] == [KA, KB, KC, KD, KE, KF, KG, KH]
    assert chem(q, "hmdb") == [
        ("hmdb", "HMDB0000002", "hmdb:HMDB0000002", "native"),
        ("hmdb", "HMDB0000004", kd, "native"),
    ]


def test_access_chemical_columns(q):
    assert q(
        "SELECT route,kind,anchor,taxon,quarantined,reviewed,gene_ids FROM $chem WHERE ns='hmdb' AND identifier='HMDB0000002'"
    ) == [(1, "chemical", None, None, True, False, [])]
    assert q(
        "SELECT kind,anchor,taxon FROM $chem WHERE ns='chebi' AND identifier='CHEBI:1'"
    ) == [("chemical", "inchikey:" + KA, None)]


# ---------------------------------------------------------------------- rule 5
def test_rule5_goslin_names(q):
    full = "full_structure:FA 18:0;5Me"
    assert chem(q, "goslin") == [
        # Every record with this name and an InChIKey agrees: the name reaches the structure; the
        # InChIKey-less record keeps its own lipid-name entity (two candidates -> abstain at runtime).
        ("goslin", full, "goslin:" + full, "native"),
        ("goslin", full, "inchikey:" + KE, "regular"),
        ("goslin", "sn_position:PC 16:0/18:1", "goslin:sn_position:PC 16:0/18:1", "native"),
    ]
    # FA 20:0;5Me: two InChIKeys disagree; PC 34:1 is species level: neither is a lookup key.
    assert not q("SELECT * FROM $chem WHERE ns='goslin' AND identifier LIKE '%20:0%'")
    assert not q("SELECT * FROM $chem WHERE ns='goslin' AND identifier LIKE 'species%'")


# --------------------------------------------------------------- access: proteins
def prot(q, ns, route=1):
    return q(
        f"SELECT identifier,entity_id,tag FROM $prot WHERE ns='{ns}' AND route={route} ORDER BY identifier,entity_id"
    )


def test_access_protein_native_secondary_and_projection(q):
    p, c = "uniprot:P04637", "uniprot:P99999"
    rows = prot(q, "uniprot")
    # The isoform asserts its parent entity; primary rows are native, secondaries are tagged.
    assert ("P04637", p, "native") in rows and ("P04637-2", p, "native") in rows
    assert [r for r in rows if r[0] == "Q16535"] == [("Q16535", p, "secondary"), ("Q16535", c, "secondary")]
    assert ("Q15086", p, "secondary") in rows
    assert prot(q, "uniprot-sec") == [("Q15086", p, "secondary"), ("Q16535", p, "secondary"), ("Q16535", c, "secondary")]
    assert prot(q, "uniprot_entry")[-1] == ("P53_HUMAN", p, "regular")


def test_access_protein_products_and_versions(q):
    p = "uniprot:P04637"
    assert prot(q, "ensp") == [("ENSP00000269305", p, "regular")]  # version stripped by normalization
    assert prot(q, "refseq_protein") == [("NP_000537", p, "regular"), ("NP_000537.3", p, "regular")]
    assert prot(q, "genbank") == [("AAA12345", p, "regular"), ("AAA12345.1", p, "regular")]
    assert q(
        "SELECT kind,anchor,taxon,quarantined,reviewed,gene_ids FROM $prot WHERE ns='uniprot' AND identifier IN ('P04637','P99999') ORDER BY identifier"
    ) == [
        ("protein", "uniprot:P04637", "9606", False, True, ["7157"]),
        ("protein", "uniprot:P99999", "9606", False, True, ["7158", "7159"]),
    ]


def test_access_gene_level_rows(q):
    g = "entrez:7157"
    assert prot(q, "ensg") == [("ENSG0000007158", "entrez:7158", "regular"), ("ENSG00000141510", g, "regular")]
    assert prot(q, "enst") == [("ENST00000269305", g, "regular")]
    assert prot(q, "refseq") == [("NM_000546", g, "regular")]  # RNA accession, version stripped
    # hgnc of a protein linked to two genes (P99999) is not asserted; unique links are, even to a
    # gene known only from the link.
    assert prot(q, "hgnc") == [("HGNC:11998", g, "regular"), ("HGNC:5555", "entrez:5555", "regular")]
    # Exact symbol beats synonym later: both tags are present with their taxon.
    assert q(
        "SELECT ns,identifier,entity_id,taxon,tag FROM $prot WHERE identifier IN ('TP53','P53') ORDER BY ns,identifier"
    ) == [
        ("genesymbol", "P53", g, "9606", "symbol_synonym"),
        ("genesymbol", "TP53", g, "9606", "regular"),
        ("genesymbol-syn", "P53", g, "9606", "symbol_synonym"),
        ("genesymbol-syn", "TP53", g, "9606", "regular"),
    ]
    assert q("SELECT kind,anchor,quarantined,reviewed,gene_ids FROM $prot WHERE ns='ensg' AND identifier='ENSG00000141510'") == [
        ("gene", None, False, False, ["7157"])
    ]
    # No candidate cutoff: 12 genes behind one symbol are all kept.
    many = q("SELECT entity_id FROM $prot WHERE ns='genesymbol' AND identifier='MANYSYM' ORDER BY 1")
    assert len(many) == 12 and many[0] == ("entrez:100",)
    # The mouse protein's symbol is not asserted for a gene (known, different organisms).
    assert not q("SELECT * FROM $prot WHERE identifier='MOUSEY'")


def test_access_gene_resolution_route2(q):
    assert q("SELECT identifier,entity_id,tag,taxon FROM $prot WHERE ns='entrez' AND route=2 AND identifier IN ('5555','7157')") == [
        ("5555", "entrez:5555", "native", "9606"),
        ("7157", "entrez:7157", "native", "9606"),
    ]
    assert q("SELECT identifier,entity_id FROM $prot WHERE ns='ramp_gene' AND route=2 ORDER BY 1,2") == [
        ("RAMP_G_1", "entrez:7157"),  # via its UniProt source id
        ("RAMP_G_2", "entrez:7157"),  # via its NCBI Gene source id
        ("RAMP_G_3", "ramp_gene:RAMP_G_3"),  # no route: its own entity
        ("RAMP_G_4", "entrez:7158"),  # P99999 links two genes: both candidates
        ("RAMP_G_4", "entrez:7159"),
        ("RAMP_G_5", "entrez:7157"),
    ]


def test_gene_labels(q):
    assert q("SELECT entity_id,label FROM $labels WHERE entity_id IN ('entrez:7157','entrez:100') ORDER BY 1") == [
        ("entrez:100", "MANYSYM"),
        ("entrez:7157", "TP53"),
    ]


# ------------------------------------------------------------------ record rows
def test_record_rows_for_labels_and_aliases(q):
    rows = q("SELECT source_type,value FROM $rows WHERE record_id='chebi:CHEBI:1' ORDER BY 1,2")
    assert rows == [
        ("cas", "50-00-0"),
        ("chebi", "CHEBI:1"),
        ("chebi", "CHEBI:99"),
        ("inchikey", KA),
        ("kegg", "C00001"),
        ("name", "Alpha"),
        ("synonym", "alpha synonym"),
    ]  # structure attributes (smiles, formula) stay in the hub
    # Lipid records carry their most specific Goslin name for labels.
    assert q("SELECT source_type,value FROM $rows WHERE record_id='swisslipids:SLM:1' ORDER BY 1") == [
        ("goslin", "sn_position:PC 16:0/18:1"),
        ("lipid_shorthand", "PC 16:0/18:1"),
        ("name", "PC 34:1"),
        ("swisslipids", "SLM:1"),
    ]
    # Gene entities receive the symbols/ids of uniquely linked proteins (P04637 yes, P99999 no).
    assert q("SELECT DISTINCT source_type,value FROM $rows WHERE record_id='entrez:7157' ORDER BY 1,2") == [
        ("ensg", "ENSG00000141510"),
        ("enst", "ENST00000269305"),
        ("entrez", "7157"),
        ("genesymbol", "TP53"),
        ("genesymbol-syn", "P53"),
        ("hgnc", "HGNC:11998"),
        ("refseq", "NM_000546.6"),
    ]
    assert q("SELECT DISTINCT source_type,value FROM $rows WHERE record_id='entrez:7158' ORDER BY 1,2") == [
        ("ensg", "ENSG0000007158"),
        ("entrez", "7158"),
    ]
    assert q("SELECT source_type,value FROM $rows WHERE record_id='uniprot:P04637' AND source_type IN ('uniprot_entry','genesymbol') ORDER BY 1") == [
        ("genesymbol", "TP53"),
        ("uniprot_entry", "P53_HUMAN"),
    ]


# ------------------------------------------------- layout, ordering, resumability
def test_layout_sorting_and_partitions(snapshot):
    root, _ = snapshot
    out = root / "out"
    assert (out / "manifest.json").is_file() and (out / "records.parquet").is_file()
    assert (out / "gene_products.parquet").is_file()
    import hashlib

    for path in (out / "entities").glob("part=*/data.parquet"):
        table = pq.read_table(path)
        ids = table.column("entity_id").to_pylist()
        assert ids == sorted(ids)
        assert {hashlib.md5(i.encode()).hexdigest()[:2] for i in ids} == {path.parent.name[5:]}
        assert str(table.schema.field("quarantined").type) == "bool"
    for path in (out / "access" / "target=2").glob("part=*/data.parquet"):
        table = pq.read_table(path)
        keys = list(zip(table.column("ns").to_pylist(), table.column("identifier").to_pylist(), table.column("entity_id").to_pylist()))
        assert keys == sorted(keys)
        assert {hashlib.md5(i.encode()).hexdigest()[:2] for i in table.column("identifier").to_pylist()} == {path.parent.name[5:]}
        assert str(table.schema.field("gene_ids").type) == "list<element: string>"
        assert pq.ParquetFile(path).metadata.row_group(0).column(0).statistics is not None
    products = pq.read_table(out / "gene_products.parquet").column("protein_entity_id").to_pylist()
    assert products == sorted(products)


def test_resume_reuses_stages(snapshot):
    root, manifest = snapshot
    again = build_identity(root / "hubs", root / "out", memory="1GB", threads=2, goslin_cache=root / "goslin", min_free_gib=0.01)
    assert again["fingerprint"] == manifest["fingerprint"]
    (root / "out" / "manifest.json").unlink()
    again = build_identity(root / "hubs", root / "out", memory="1GB", threads=2, goslin_cache=root / "goslin", min_free_gib=0.01)
    assert again["counts"] == manifest["counts"]  # every stage came from its checkpoint
    assert json.loads((root / "out" / "work" / "records" / "_SUCCESS.json").read_text())["info"]["records"] == 65
