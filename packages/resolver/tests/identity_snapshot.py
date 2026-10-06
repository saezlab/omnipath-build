"""A small synthetic ``omnipath-identity-v1`` snapshot written straight to the spec layout.

Only the Parquet layout of docs/identity-layer-spec.md section 3 is exercised: the builder
(``omnipath_build.identity``) is not involved. Each fixture entity is one case for a rule.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

WATER = "XLYOFNOQVPJJNP-UHFFFAOYSA-N"
ASPIRIN = "BSYNRYMUTXBXSQ-UHFFFAOYSA-N"
ETHANOL = "LFQSCWFLJHTTHZ-UHFFFAOYSA-N"
LIPID = "AAAAAAAAAAAAAA-LLLLLLLLLL-N"
NAMELESS = "BBBBBBBBBBBBBB-CCCCCCCCCC-N"
ONLY_PC = "CCCCCCCCCCCCCC-DDDDDDDDDD-N"
OTHERS = "DDDDDDDDDDDDDD-EEEEEEEEEE-N"
SYSTEMATIC = "EEEEEEEEEEEEEE-FFFFFFFFFF-N"
FINGERPRINT = "synthetic-0001"
LONG_NAME = "x" * 81


def part(value: str) -> str:
    return hashlib.md5(value.encode(), usedforsecurity=False).hexdigest()[:2]


def spec():
    """(entities, access, record_rows, gene_products) as plain python."""
    entities = {}  # eid -> (kind, anchor, taxon, quarantined, [record ids])
    rows = {}  # record id -> [(source_type, value)]
    access = []  # (target, route, ns, identifier, eid, tag)

    def chem(eid, records, anchor=True, quarantined=False):
        kind_anchor = eid if anchor and eid.startswith("inchikey:") else None
        entities[eid] = ("chemical", kind_anchor, None, quarantined, list(records))

    def add(target, route, ns, identifier, eid, tag="regular"):
        access.append((target, route, ns, identifier, eid, tag))

    # ---- chemicals -------------------------------------------------------------------
    rows["chebi:CHEBI:15377"] = [
        ("chebi", "CHEBI:15377"),
        ("name", "water"),
        ("name", "Water molecule"),
        ("synonym", "H2O"),
        ("inchikey", WATER),
        ("smiles", "O"),
    ]
    rows["hmdb:HMDB0002111"] = [("hmdb", "HMDB0002111"), ("name", "Water (hmdb)")]
    rows["pubchem:962"] = [("pubchem", "962"), ("name", "PUBCHEM WATER")]
    chem(f"inchikey:{WATER}", ["chebi:CHEBI:15377", "hmdb:HMDB0002111", "pubchem:962"])

    rows["chebi:CHEBI:15365"] = [("chebi", "CHEBI:15365"), ("name", ASPIRIN)]  # name is a key
    rows["hmdb:HMDB0001879"] = [("hmdb", "HMDB0001879"), ("name", LONG_NAME)]  # too long
    rows["chembl:CHEMBL25"] = [("chembl", "CHEMBL25"), ("name", "ASPIRIN"), ("name", "aspirin2")]
    rows["pubchem:2244"] = [("pubchem", "2244"), ("name", "aspirin-pc")]
    chem(
        f"inchikey:{ASPIRIN}",
        ["chebi:CHEBI:15365", "hmdb:HMDB0001879", "chembl:CHEMBL25", "pubchem:2244"],
    )

    rows["chebi:CHEBI:16236"] = [("chebi", "CHEBI:16236"), ("name", "ethanol")]
    chem(f"inchikey:{ETHANOL}", ["chebi:CHEBI:16236"])

    rows["chebi:CHEBI:50000"] = [
        ("chebi", "CHEBI:50000"),
        ("name", "phosphatidylcholine"),
        ("goslin", "species:PC 34:1"),
        ("goslin", "full_structure:PC 16:0/18:1"),
    ]
    chem(f"inchikey:{LIPID}", ["chebi:CHEBI:50000"])

    rows["swisslipids:SLM:1"] = [("swisslipids", "SLM:1"), ("name", "a long lipid name")]
    entities["goslin:species:PE 36:2"] = ("chemical", None, None, False, ["swisslipids:SLM:1"])

    chem(f"inchikey:{NAMELESS}", [])
    rows["pubchem:5"] = [("pubchem", "5"), ("name", "pc-name")]
    rows["lipidmaps:LM1"] = [("lipidmaps", "LM1"), ("name", "lm")]
    chem(f"inchikey:{ONLY_PC}", ["pubchem:5", "lipidmaps:LM1"])
    rows["swisslipids:SLM:2"] = [
        ("swisslipids", "SLM:2"),
        ("name", "zz"),
        ("name", "b"),
        ("name", "a"),
    ]
    chem(f"inchikey:{OTHERS}", ["swisslipids:SLM:2"])
    rows["lipidmaps:LM2"] = [("lipidmaps", "LM2"), ("systematic_name", "sys-name")]
    chem(f"inchikey:{SYSTEMATIC}", ["lipidmaps:LM2"])

    rows["chebi:CHEBI:99999"] = [("chebi", "CHEBI:99999"), ("name", "multi")]
    chem("chebi:CHEBI:99999", ["chebi:CHEBI:99999"], quarantined=True)

    for n in range(12):  # twelve structure-less records behind one cas number
        eid = f"pubchem:{1000 + n}"
        rows[eid] = [("pubchem", str(1000 + n)), ("name", f"cas-twin-{n}")]
        chem(eid, [eid])
        add(1, 1, "cas", "0-0-0", eid)
        add(1, 1, "pubchem", str(1000 + n), eid, "native")

    water, aspirin, ethanol = f"inchikey:{WATER}", f"inchikey:{ASPIRIN}", f"inchikey:{ETHANOL}"
    for ns, ident, eid in [
        ("inchikey", WATER, water),
        ("chebi", "CHEBI:15377", water),
        ("hmdb", "HMDB0002111", water),
        ("pubchem", "962", water),
        ("inchikey", ASPIRIN, aspirin),
        ("chebi", "CHEBI:15365", aspirin),
        ("chembl", "CHEMBL25", aspirin),
        ("pubchem", "2244", aspirin),
        ("inchikey", ETHANOL, ethanol),
        ("chebi", "CHEBI:16236", ethanol),
        ("inchikey", LIPID, f"inchikey:{LIPID}"),
        ("goslin", "species:PE 36:2", "goslin:species:PE 36:2"),
        ("chebi", "CHEBI:99999", "chebi:CHEBI:99999"),
    ]:
        add(1, 1, ns, ident, eid, "native")
    add(1, 1, "bigg", "h2o", water, "native")  # native beats fallback ...
    add(1, 1, "bigg", "h2o", aspirin, "fallback")
    add(1, 1, "kegg", "C00001", water, "fallback")  # ... but a lone fallback row survives

    # ---- genes and proteins --------------------------------------------------------------
    def gene(n, taxon, symbol_rows):
        rec = f"entrez:{n}"
        rows[rec] = [("entrez", str(n)), *symbol_rows]
        entities[rec] = ("gene", None, taxon, False, [rec])
        add(2, 2, "entrez", str(n), rec, "native")

    def protein(acc, taxon, extra, genes=()):
        rec = f"uniprot:{acc}"
        rows[rec] = [("uniprot", acc), *extra]
        entities[rec] = ("protein", rec, taxon, False, [rec])
        add(2, 1, "uniprot", acc, rec, "native")
        return [(rec, g) for g in genes]

    gene(7157, "9606", [("genesymbol", "TP53"), ("genesymbol-syn", "P53"), ("genesymbol", "TP53L")])
    gene(801, "9606", [("genesymbol", "CALM1")])
    gene(805, "9606", [("genesymbol", "CALM2")])
    gene(808, "9606", [("genesymbol", "CALM3")])
    gene(22059, "10090", [("genesymbol", "Trp53")])
    gene(555, None, [])  # no taxon: visible only unscoped; label is the id
    gene(901, "9606", [("genesymbol", "ABC1")])
    gene(902, "9606", [("genesymbol-syn", "XYZ")])

    links = []
    links += protein(
        "P04637",
        "9606",
        [
            ("uniprot_entry", "P53_HUMAN"),
            ("genesymbol", "TP53"),
            ("genesymbol-syn", "P53"),
            ("entrez", "7157"),
            ("ensg", "ENSG00000141510"),
            ("hgnc", "HGNC:11998"),
            ("uniprot-sec", "Q15086"),
        ],
        ["7157"],
    )
    links += protein(
        "A0A0U1RQF1",
        "9606",
        [("genesymbol-syn", "TPX"), ("uniprot_entry", "A0A0U1RQF1_HUMAN")],
        ["7157"],
    )
    links += protein("Q99999", "9606", [])
    links += protein("P0DP23", "9606", [("genesymbol", "CALM1")], ["801", "805", "808"])
    links += protein("P02340", "10090", [("genesymbol", "Trp53")], ["22059"])

    p53, calm = "uniprot:P04637", "uniprot:P0DP23"
    for ns, ident, eid, tag in [
        ("uniprot", "Q15086", p53, "secondary"),
        ("uniprot-sec", "Q15086", p53, "secondary"),
        ("uniprot", "Q99999", "uniprot:Q99999", "native"),  # primary ...
        ("uniprot", "Q99999", p53, "secondary"),  # ... beats a secondary claim
        ("uniprot-sec", "Q99999", p53, "secondary"),
        ("uniprot", "Q88888", p53, "secondary"),  # secondary of two entries: ambiguous
        ("uniprot", "Q88888", calm, "secondary"),
        ("uniprot_entry", "P53_HUMAN", p53, "regular"),
        ("uniprot_entry", "A0A0U1RQF1_HUMAN", "uniprot:A0A0U1RQF1", "regular"),
    ]:
        add(2, 1, ns, ident, eid, tag)
    for ns, ident, eid, taxon in [
        ("ensg", "ENSG00000141510", "entrez:7157", "9606"),
        ("hgnc", "HGNC:11998", "entrez:7157", "9606"),
        ("refseq", "NM_000546", "entrez:7157", "9606"),
        ("ensg", "ENSG00000999999", "entrez:555", None),
    ]:
        add(2, 1, ns, ident, eid, "regular")
    # symbols: taxon filtering comes from the entity taxon, exact beats synonym per taxon
    for ns in ("genesymbol", "genesymbol-syn"):
        add(2, 1, ns, "TP53", "entrez:7157", "regular")
        add(2, 1, ns, "TP53", "entrez:22059", "symbol_synonym")  # mouse: only a synonym
        add(2, 1, ns, "ABC1", "entrez:901", "regular")
        add(2, 1, ns, "ABC1", "entrez:902", "symbol_synonym")
        add(2, 1, ns, "XYZ", "entrez:902", "symbol_synonym")
        add(2, 1, ns, "Trp53", "entrez:22059", "regular")
    add(2, 1, "genesymbol", "CALM1", "entrez:801", "regular")

    return entities, rows, access, links


def _write(path: Path, table: pa.Table) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, path, row_group_size=64 * 1024)


def build_snapshot(root: Path, *, fingerprint: str = FINGERPRINT) -> Path:
    """Write the snapshot under ``root`` and return its directory."""
    root = Path(root)
    entities, rows, access, links = spec()
    eid_info = entities

    # access rows with their denormalized entity metadata
    tables = {}
    gene_ids = {}
    for rec, entrez in links:
        gene_ids.setdefault(rec, []).append(entrez)
    for target, route, ns, ident, eid, tag in access:
        kind, anchor, taxon, quarantined, _ = eid_info[eid]
        row = dict(
            route=route,
            ns=ns,
            identifier=ident,
            entity_id=eid,
            kind=kind,
            anchor=anchor,
            taxon=taxon,
            quarantined=quarantined,
            reviewed=eid in ("uniprot:P04637",),
            gene_ids=[eid.split(":", 1)[1]] if kind == "gene" else gene_ids.get(eid, []),
            tag=tag,
        )
        tables.setdefault((target, part(ident)), []).append(row)
    schema = pa.schema(
        [
            ("route", pa.int32()),
            ("ns", pa.string()),
            ("identifier", pa.string()),
            ("entity_id", pa.string()),
            ("kind", pa.string()),
            ("anchor", pa.string()),
            ("taxon", pa.string()),
            ("quarantined", pa.bool_()),
            ("reviewed", pa.bool_()),
            ("gene_ids", pa.list_(pa.string())),
            ("tag", pa.string()),
        ]
    )
    for (target, p), items in tables.items():
        items.sort(key=lambda r: (r["ns"], r["identifier"], r["entity_id"]))
        _write(
            root / f"access/target={target}/part={p}/data.parquet",
            pa.Table.from_pylist(items, schema=schema),
        )

    ent_parts, mem_parts, row_parts = {}, {}, {}
    for eid, (kind, anchor, taxon, quarantined, members) in entities.items():
        ent_parts.setdefault(part(eid), []).append(
            dict(
                entity_id=eid,
                kind=kind,
                anchor=anchor,
                taxon=taxon,
                quarantined=quarantined,
                preferred_record=members[0] if members else None,
            )
        )
        for record in members:
            mem_parts.setdefault(part(eid), []).append(dict(entity_id=eid, record_id=record))
    for record, values in rows.items():
        for source_type, value in values:
            row_parts.setdefault(part(record), []).append(
                dict(record_id=record, source_type=source_type, value=value)
            )
    text = pa.string()
    layouts = {
        "entities": pa.schema(
            [
                ("entity_id", text),
                ("kind", text),
                ("anchor", text),
                ("taxon", text),
                ("quarantined", pa.bool_()),
                ("preferred_record", text),
            ]
        ),
        "members": pa.schema([("entity_id", text), ("record_id", text)]),
        "record_rows": pa.schema([("record_id", text), ("source_type", text), ("value", text)]),
    }
    for name, groups, key in (
        ("entities", ent_parts, "entity_id"),
        ("members", mem_parts, "entity_id"),
        ("record_rows", row_parts, "record_id"),
    ):
        for p, items in groups.items():
            items.sort(key=lambda r: r[key])
            _write(
                root / f"{name}/part={p}/data.parquet",
                pa.Table.from_pylist(items, schema=layouts[name]),
            )
    _write(
        root / "gene_products.parquet",
        pa.Table.from_pylist(
            [
                dict(
                    protein_entity_id=rec,
                    entrez_id=entrez,
                    taxon=entities[rec][2],
                )
                for rec, entrez in links
            ]
        ),
    )
    _write(
        root / "records.parquet",
        pa.Table.from_pylist(
            [dict(record_id=r, hub=r.split(":", 1)[0], local_id=r.split(":", 1)[1]) for r in rows]
        ),
    )
    (root / "manifest.json").write_text(
        json.dumps(
            dict(
                format="omnipath-identity-v1",
                fingerprint=fingerprint,
                hubs={},
                rules_sha256="0" * 64,
                counts=dict(entities=len(entities), records=len(rows)),
                timings={},
            )
        )
    )
    return root
