"""Identity decisions (spec 3b): only the records whose entity is not trivially their own anchor.

Input: the hub indexes (records.parquet, xrefs.parquet, and the `goslin`/`ramp_gene` rows of
by_id). The entity of a record is its exception if present, else its anchor (anchor_count = 1),
else `entrez:<local_id>` for an entrez record, else its record_id. Hub rows are never rewritten.

exceptions.parquet decisions
  entity-defining: lipid_name, attached, grouped, structureless, ambiguous_native, quarantined
  route-2 only (ramp_gene source genes, brief 2.3; ignore them when deciding a record's entity):
      explicit_source_gene   the gene a ramp_gene source id names through a UniProt entry or an
                             NCBI Gene id (one row per gene)
      source_gene_only       a ramp_gene record without such a route: its own entity
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
from pathlib import Path

import pyarrow as pa

from .common import (
    CHEMICAL,
    GOSLIN_HUBS,
    HUBS,
    PROTEIN,
    STRUCTURE_LEVELS,
    Context,
    code_sha256,
    files,
    hub_rank,
    log,
    quote,
    sha256_file,
    sql_list,
)

CODE_FILES = ("decisions.py", "common.py")
FORMAT = "omnipath-identity-v2"
RAMP_TYPES = ("uniprot", "entrez", "ensg", "ensp", "enst", "hgnc", "refseq_protein", "genbank")


def find_indexes(root, hubs=HUBS) -> dict[str, Path]:
    """The newest complete index of every hub present under `root` (`<root>/<hub>/<sha12>/`)."""
    found = {}
    for hub in hubs:
        complete = sorted(
            (p for p in (Path(root) / hub).glob("*/manifest.json")), key=lambda p: p.stat().st_mtime
        )
        if complete:
            found[hub] = complete[-1].parent
    if not found:
        raise RuntimeError(f"No hub indexes under {root}")
    return found


def fingerprint(indexes, rules) -> str:
    digest = hashlib.sha256()
    for hub in sorted(indexes):
        digest.update(f"{hub}={sha256_file(indexes[hub] / 'manifest.json')}\n".encode())
    digest.update(f"rules={rules}".encode())
    return digest.hexdigest()


def records_of(indexes, hub):
    return f"read_parquet({quote(indexes[hub] / 'records.parquet')})"


def by_id_of(indexes, hub):
    return files(str(indexes[hub] / "by_id" / "*" / "*.parquet"))


def xrefs_of(indexes, hub):
    return f"read_parquet({quote(indexes[hub] / 'xrefs.parquet')})"


# ------------------------------------------------------------------------- stages
def decide(ctx, c, indexes):
    """Rules 1-4: quarantine, lipid-name anchors, one-step attach, grouping."""
    chem = [h for h in CHEMICAL if h in indexes]
    prot = [h for h in PROTEIN if h in indexes]
    # Cross-references between records of one domain (uniprot<->entrez are gene links, not identity).
    edges = []
    for hub in chem + prot:
        domain = sql_list(CHEMICAL if hub in CHEMICAL else PROTEIN)
        gene_link = (
            "AND ns<>'entrez'" if hub == "uniprot" else "AND ns<>'uniprot'" if hub == "entrez" else ""
        )
        edges.append(
            f"""SELECT {quote(hub + ':')} || local_id a,ns || ':' || identifier b
            FROM {xrefs_of(indexes, hub)} WHERE ns IN {domain} {gene_link}"""
        )
    c.execute(f"CREATE TABLE xref AS SELECT DISTINCT * FROM ({' UNION ALL '.join(edges)})")
    c.execute("CREATE TABLE nb AS SELECT a rec,b other FROM xref UNION SELECT b,a FROM xref")
    c.execute("CREATE TABLE ends AS SELECT DISTINCT rec record_id FROM nb")
    # Goslin names of chemical records without an InChIKey (rule 1).
    gl = [
        f"""SELECT {quote(h + ':')} || local_id record_id,identifier goslin FROM {by_id_of(indexes, h)}
        WHERE ns='goslin' AND anchor_count=0"""
        for h in GOSLIN_HUBS
        if h in indexes
    ]
    c.execute(
        "CREATE TABLE gl AS "
        + (
            f"SELECT record_id,count(DISTINCT goslin)::INTEGER n_gl,min(goslin) gl_min FROM ({' UNION ALL '.join(gl)}) GROUP BY 1"
            if gl
            else "SELECT NULL::VARCHAR record_id,0 n_gl,NULL::VARCHAR gl_min WHERE false"
        )
    )
    # Records that are not anchored by their own InChIKey/accession, plus every cross-reference endpoint.
    parts = []
    for hub in chem + prot:
        if hub == "entrez":
            parts.append(
                f"""SELECT 'entrez' hub,local_id,record_id,taxon,1 anchor_count,'entrez:' || local_id anchor
                FROM {records_of(indexes, hub)} WHERE record_id IN (SELECT record_id FROM ends)"""
            )
        else:
            parts.append(
                f"""SELECT {quote(hub)} hub,local_id,record_id,taxon,anchor_count,anchor
                FROM {records_of(indexes, hub)}
                WHERE anchor_count<>1 OR record_id IN (SELECT record_id FROM ends)"""
            )
    c.execute(f"CREATE TABLE rs AS {' UNION ALL '.join(parts)}")
    c.execute(
        """CREATE TABLE state AS SELECT r.hub,r.local_id,r.record_id,r.taxon,r.anchor_count count0,
          CASE WHEN r.anchor_count>0 THEN r.anchor_count ELSE coalesce(g.n_gl,0) END anchor_count,
          CASE WHEN r.hub='entrez' THEN 'entrez' WHEN r.hub='uniprot' THEN CASE WHEN r.anchor_count=1 THEN 'uniprot' END
               WHEN r.anchor_count>0 THEN 'inchikey' WHEN coalesce(g.n_gl,0)>0 THEN 'goslin' END anchor_kind,
          CASE WHEN r.anchor_count=1 THEN r.anchor WHEN r.anchor_count=0 AND coalesce(g.n_gl,0)=1 THEN 'goslin:' || g.gl_min END anchor
        FROM rs r LEFT JOIN gl g USING(record_id)"""
    )
    # Rule 3: an anchorless record sees the anchors of the records its own edges reach.
    c.execute(
        """CREATE TABLE st AS SELECT * FROM state WHERE record_id IN (SELECT record_id FROM ends)"""
    )
    c.execute(
        """CREATE TABLE seen AS
        SELECT n.rec,count(*) FILTER (WHERE o.anchor_count>1)::INTEGER n_quarantined,
          count(DISTINCT o.anchor) FILTER (WHERE o.anchor_count=1)::INTEGER n_anchors,
          min(o.anchor) FILTER (WHERE o.anchor_count=1) anchor
        FROM nb n JOIN st s ON s.record_id=n.rec AND s.anchor_count=0
          JOIN st o ON o.record_id=n.other AND o.anchor_count>0 GROUP BY n.rec"""
    )
    # Rule 4: unattached anchorless records without an edge to an anchored record are connected
    # through their edges; a group is one entity only with at most one record per hub.
    c.execute(
        "CREATE TABLE cand AS SELECT s.record_id,s.hub,s.local_id FROM st s ANTI JOIN seen ON seen.rec=s.record_id WHERE s.anchor_count=0"
    )
    pairs = c.execute(
        "SELECT x.a,x.b FROM xref x SEMI JOIN cand ca ON ca.record_id=x.a SEMI JOIN cand cb ON cb.record_id=x.b"
    ).to_arrow_table()
    index, parent = {}, []

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def node(record_id):
        if record_id not in index:
            index[record_id] = len(parent)
            parent.append(len(parent))
        return index[record_id]

    for a, b in zip(pairs.column(0).to_pylist(), pairs.column(1).to_pylist()):
        ra, rb = find(node(a)), find(node(b))
        if ra != rb:
            parent[ra] = rb
    c.register(
        "components",
        pa.table(
            {
                "record_id": pa.array(list(index), pa.string()),
                "comp": pa.array([find(i) for i in index.values()], pa.int64()),
            }
        ),
    )
    c.execute(
        f"""CREATE TABLE grouped AS SELECT k.record_id,gc.* FROM components k JOIN (
          SELECT k.comp,count(*) n,count(DISTINCT g.hub) hubs,
            arg_min(g.record_id,struct_pack(r:={hub_rank('g.hub')},l:=g.local_id)) preferred
          FROM components k JOIN cand g USING(record_id) GROUP BY k.comp) gc USING(comp)"""
    )
    c.execute(
        """CREATE TABLE exceptions AS
        SELECT s.record_id,
          CASE WHEN s.anchor_count>1 THEN s.record_id
               WHEN s.anchor_kind='goslin' THEN s.anchor
               WHEN sn.rec IS NOT NULL AND sn.n_quarantined=0 AND sn.n_anchors=1 THEN sn.anchor
               WHEN g.record_id IS NOT NULL AND g.n=g.hubs THEN g.preferred
               ELSE s.record_id END entity_id,
          CASE WHEN s.anchor_count>1 THEN 'quarantined'
               WHEN s.anchor_kind='goslin' THEN 'lipid_name'
               WHEN sn.rec IS NOT NULL THEN CASE WHEN sn.n_quarantined=0 AND sn.n_anchors=1 THEN 'attached' ELSE 'ambiguous_native' END
               WHEN g.record_id IS NOT NULL THEN CASE WHEN g.n=g.hubs THEN 'grouped' ELSE 'ambiguous_native' END
               ELSE 'structureless' END decision,
          s.hub,s.local_id,s.taxon,s.count0
        FROM state s LEFT JOIN seen sn ON sn.rec=s.record_id LEFT JOIN grouped g ON g.record_id=s.record_id
        WHERE s.count0<>1 AND s.hub<>'entrez'"""
    )
    return dict(
        decisions=dict(c.execute("SELECT decision,count(*) FROM exceptions GROUP BY 1 ORDER BY 1").fetchall()),
        groups_accepted=c.execute("SELECT count(DISTINCT comp) FROM grouped WHERE n=hubs").fetchone()[0],
        groups_rejected=c.execute("SELECT count(DISTINCT comp) FROM grouped WHERE n<>hubs").fetchone()[0],
        lipid_name_records_with_inchikey_edge=c.execute(
            """SELECT count(DISTINCT n.rec) FROM nb n JOIN st s ON s.record_id=n.rec AND s.anchor_kind='goslin'
            JOIN st o ON o.record_id=n.other AND o.anchor_kind='inchikey'"""
        ).fetchone()[0],
    )


def stage_gene_products(ctx, directory, indexes):
    """Rule 6, same derivation as `build_reference.gene_products_query` (brief 3.6)."""
    c = ctx.connect("gene_products")
    out = ctx.out
    empty = "SELECT NULL::VARCHAR protein_entity_id,NULL::VARCHAR entrez_id,NULL::VARCHAR taxon WHERE false"
    if "uniprot" not in indexes or "entrez" not in indexes:
        query = empty
    else:
        up = records_of(indexes, "uniprot")
        en = records_of(indexes, "entrez")
        c.execute(
            f"""CREATE TABLE ge AS SELECT local_id,ns,identifier FROM {xrefs_of(indexes, 'uniprot')} WHERE ns='entrez'"""
        )
        c.execute(
            f"""CREATE TABLE gf AS SELECT local_id,ns,identifier FROM {xrefs_of(indexes, 'entrez')} WHERE ns='uniprot'"""
        )
        query = f"""SELECT DISTINCT protein_entity_id,entrez_id,taxon FROM (
            SELECT s.anchor protein_entity_id,e.identifier entrez_id,s.taxon
            FROM ge e JOIN {up} s ON s.local_id=e.local_id AND s.anchor_count=1
            LEFT JOIN {en} t ON t.local_id=e.identifier
            WHERE t.taxon IS NULL OR s.taxon IS NULL OR s.taxon=t.taxon
            UNION
            SELECT t.anchor,s.local_id,t.taxon FROM gf e JOIN {en} s ON s.local_id=e.local_id
            JOIN {up} t ON t.local_id=e.identifier AND t.anchor_count=1
            WHERE s.taxon IS NULL OR t.taxon IS NULL OR s.taxon=t.taxon)"""
    n = ctx.copy(
        c, query + " ORDER BY protein_entity_id,entrez_id", out / "gene_products_by_protein.parquet", ", ROW_GROUP_SIZE 65536"
    )
    ctx.copy(
        c,
        f"SELECT * FROM read_parquet({quote(out / 'gene_products_by_protein.parquet')}) ORDER BY entrez_id,protein_entity_id",
        out / "gene_products_by_gene.parquet",
        ", ROW_GROUP_SIZE 65536",
    )
    return dict(gene_products=n)


def ramp_genes(ctx, c, indexes):
    """`ramp_gene` source ids resolve to genes through explicit UniProt/NCBI Gene source ids."""
    if "ramp_gene" not in indexes:
        c.execute("CREATE TABLE ramp_rows(record_id VARCHAR,entity_id VARCHAR,decision VARCHAR)")
        return dict(mappings=0)
    gp = quote(ctx.out / "gene_products_by_protein.parquet")
    c.execute(
        f"""CREATE TABLE src AS SELECT local_id,ns,identifier source_value,taxon FROM {by_id_of(indexes, 'ramp_gene')}
        WHERE ns IN {sql_list(RAMP_TYPES)} AND tag='claim'"""
    )
    if "uniprot" in indexes:
        # by_id is partitioned by identifier: only the partitions the source values hash to are read.
        parts = [
            r[0]
            for r in c.execute("SELECT DISTINCT substr(md5(source_value),1,2) FROM src").fetchall()
        ]
        sources = [str(indexes["uniprot"] / "by_id" / f"part={p}" / "*.parquet") for p in parts]
        c.execute(
            f"""CREATE TABLE u AS SELECT ns,identifier,local_id FROM {files(*sources)}
            WHERE ns IN {sql_list(RAMP_TYPES)} AND tag IN ('native','claim','secondary')
            AND identifier IN (SELECT source_value FROM src)"""
            if sources
            else "CREATE TABLE u(ns VARCHAR,identifier VARCHAR,local_id VARCHAR)"
        )
    else:
        c.execute("CREATE TABLE u(ns VARCHAR,identifier VARCHAR,local_id VARCHAR)")
    genes = (
        f"SELECT local_id FROM {records_of(indexes, 'entrez')} WHERE local_id IN (SELECT source_value FROM src WHERE ns='entrez')"
        if "entrez" in indexes
        else "SELECT NULL::VARCHAR local_id WHERE false"
    )
    c.execute(
        f"""CREATE TABLE ramp_cand AS
        SELECT DISTINCT k.local_id,'uniprot:' || u.local_id entity_id,k.taxon FROM src k JOIN u ON u.identifier=k.source_value
          AND u.ns=k.ns WHERE k.ns<>'entrez'
        UNION SELECT DISTINCT k.local_id,'entrez:' || k.source_value,k.taxon FROM src k
          WHERE k.ns='entrez' AND (k.source_value IN ({genes})
            OR k.source_value IN (SELECT entrez_id FROM read_parquet({gp})))"""
    )
    c.execute(
        f"""CREATE TABLE mapped AS SELECT DISTINCT 'ramp_gene:' || c.local_id record_id,
          CASE WHEN starts_with(c.entity_id,'entrez:') THEN c.entity_id ELSE 'entrez:' || p.entrez_id END entity_id
        FROM ramp_cand c LEFT JOIN read_parquet({gp}) p ON p.protein_entity_id=c.entity_id
        WHERE starts_with(c.entity_id,'entrez:')
          OR (p.entrez_id IS NOT NULL AND (c.taxon IS NULL OR c.taxon IN ('','0') OR p.taxon IS NULL OR c.taxon=p.taxon))"""
    )
    c.execute(
        """CREATE TABLE ramp_rows AS SELECT record_id,entity_id,'explicit_source_gene' decision FROM mapped
        UNION ALL SELECT e.record_id,e.entity_id,'source_gene_only' FROM exceptions e
          ANTI JOIN mapped m ON m.record_id=e.record_id
          WHERE e.hub='ramp_gene' AND NOT starts_with(e.entity_id,'uniprot:')"""
    )
    return dict(mappings=c.execute("SELECT count(*) FROM mapped").fetchone()[0])


def stage_structures(ctx, directory, indexes):
    """Rule 5: full-structure Goslin names that all their InChIKey records agree on."""
    c = ctx.connect("structures")
    gl = [
        f"""SELECT identifier goslin,anchor,anchor_count FROM {by_id_of(indexes, h)}
        WHERE ns='goslin' AND anchor_count>=1"""
        for h in GOSLIN_HUBS
        if h in indexes
    ]
    levels = sql_list(STRUCTURE_LEVELS)
    query = (
        f"""SELECT goslin,substr(min(anchor),10) inchikey,min(anchor) entity_id FROM ({' UNION ALL '.join(gl)})
        WHERE split_part(goslin,':',1) IN {levels} GROUP BY goslin
        HAVING count(DISTINCT anchor) FILTER (WHERE anchor_count=1)=1 AND NOT bool_or(anchor_count>1) ORDER BY goslin"""
        if gl
        else "SELECT NULL::VARCHAR goslin,NULL::VARCHAR inchikey,NULL::VARCHAR entity_id WHERE false"
    )
    return dict(structures=ctx.copy(c, query, ctx.out / "lipid_structures.parquet"))


def quarantined_key_entities(ctx, c, indexes):
    """InChIKeys claimed by quarantined records that no record is anchored on (extra entities)."""
    keys = []
    for hub in (h for h in CHEMICAL if h in indexes):
        c.execute(
            f"""CREATE OR REPLACE TEMP TABLE q AS SELECT local_id,substr(md5({quote(hub + ':')} || local_id),1,2) part
            FROM exceptions WHERE decision='quarantined' AND hub={quote(hub)} AND count0>1"""
        )
        parts = [r[0] for r in c.execute("SELECT DISTINCT part FROM q").fetchall()]
        sources = [
            str(indexes[hub] / "by_record" / f"part={p}" / "*.parquet")
            for p in parts
            if (indexes[hub] / "by_record" / f"part={p}").is_dir()
        ]
        if sources:
            c.execute(
                f"""CREATE TEMP TABLE k_{hub} AS SELECT DISTINCT value FROM {files(*sources)}
                WHERE source_type='inchikey' AND local_id IN (SELECT local_id FROM q)"""
            )
            keys.append(f"SELECT value FROM k_{hub}")
    if not keys:
        c.execute("CREATE TABLE qkeys(entity_id VARCHAR)")
        return
    c.execute(f"CREATE TABLE cand_keys AS SELECT DISTINCT value k FROM ({' UNION ALL '.join(keys)})")
    parts = [r[0] for r in c.execute("SELECT DISTINCT substr(md5(k),1,2) FROM cand_keys").fetchall()]
    anchored = []
    for hub in (h for h in CHEMICAL if h in indexes):
        sources = [
            str(indexes[hub] / "by_id" / f"part={p}" / "*.parquet")
            for p in parts
            if (indexes[hub] / "by_id" / f"part={p}").is_dir()
        ]
        if sources:
            anchored.append(
                f"""SELECT identifier FROM {files(*sources)} WHERE ns='inchikey' AND anchor_count=1
                AND identifier IN (SELECT k FROM cand_keys)"""
            )
    existing = " UNION ALL ".join(anchored) or "SELECT NULL::VARCHAR identifier WHERE false"
    c.execute(
        f"""CREATE TABLE qkeys AS SELECT 'inchikey:' || k entity_id FROM cand_keys
        WHERE k NOT IN ({existing})"""
    )


def write_outputs(ctx, c, indexes):
    out = ctx.out
    quarantined_key_entities(ctx, c, indexes)
    c.execute(
        "CREATE TABLE exceptions_out AS SELECT record_id,entity_id,decision,decision='quarantined' quarantined FROM exceptions "
        "UNION ALL SELECT record_id,entity_id,decision,false FROM ramp_rows"
    )
    n = ctx.copy(
        c,
        "SELECT * FROM exceptions_out ORDER BY record_id,decision,entity_id",
        out / "exceptions.parquet",
        ", ROW_GROUP_SIZE 65536",
    )
    ctx.copy(
        c,
        "SELECT entity_id,record_id FROM exceptions ORDER BY entity_id,record_id",
        out / "exception_members.parquet",
        ", ROW_GROUP_SIZE 65536",
    )
    # Entities that cannot be derived from a record's anchor: not those an attached record joins.
    extra = ctx.copy(
        c,
        f"""SELECT entity_id,kind,taxon,quarantined,preferred_record FROM (
          SELECT entity_id,
            CASE WHEN starts_with(entity_id,'goslin:') THEN 'chemical' WHEN min(hub)='ramp_gene' THEN 'gene'
                 WHEN min(hub)='uniprot' THEN 'protein' ELSE 'chemical' END kind,
            min(taxon) FILTER (WHERE taxon IS NOT NULL) taxon,bool_or(decision='quarantined') quarantined,
            arg_min(record_id,struct_pack(r:={hub_rank()},l:=local_id)) preferred_record
          FROM exceptions WHERE decision<>'attached' GROUP BY entity_id
          UNION ALL SELECT entity_id,'chemical',NULL,false,NULL FROM qkeys) ORDER BY entity_id""",
        out / "entities_extra.parquet",
    )
    return dict(exceptions=n, entities_extra=extra)


def build_identity(hub_index_root, output_dir, memory="7GB", threads=6, min_free_gib=50, keep_work=False):
    started = time.monotonic()
    indexes = find_indexes(hub_index_root)
    rules = code_sha256(*CODE_FILES)
    fp = fingerprint(indexes, rules)
    out = Path(output_dir) / fp
    if (out / "manifest.json").is_file():
        log("identity_exists", path=str(out))
        return json.loads((out / "manifest.json").read_text())
    building = Path(output_dir) / (fp + ".building")
    building.mkdir(parents=True, exist_ok=True)
    ctx = Context(building, memory, threads, None, min_free_gib)
    log("identity_start", fingerprint=fp, hubs=sorted(indexes))
    ctx.stage(
        "gene_products",
        [building / "gene_products_by_gene.parquet"],
        lambda c, d: stage_gene_products(c, d, indexes),
    )
    ctx.stage(
        "structures", [building / "lipid_structures.parquet"], lambda c, d: stage_structures(c, d, indexes)
    )

    def decisions(ctx, directory):
        c = ctx.connect("decisions")
        info = decide(ctx, c, indexes)
        info["ramp_gene"] = ramp_genes(ctx, c, indexes)
        info.update(write_outputs(ctx, c, indexes))
        return info

    ctx.stage(
        "decisions", [building / "exceptions.parquet", building / "entities_extra.parquet"], decisions
    )
    manifest = dict(
        format=FORMAT,
        fingerprint=fp,
        hub_indexes={h: str(p) for h, p in indexes.items()},
        hub_index_manifest_sha256={h: sha256_file(p / "manifest.json") for h, p in indexes.items()},
        rules_sha256=rules,
        counts=ctx.info,
        output_bytes={p.name: p.stat().st_size for p in sorted(building.glob("*.parquet"))},
        timings=dict(ctx.timings, total=round(time.monotonic() - started, 2)),
    )
    if not keep_work:
        shutil.rmtree(ctx.work, ignore_errors=True)
    (building / "manifest.json").write_text(json.dumps(manifest, indent=2))
    os.replace(building, out)
    log("identity_complete", path=str(out), seconds=manifest["timings"]["total"])
    return manifest


