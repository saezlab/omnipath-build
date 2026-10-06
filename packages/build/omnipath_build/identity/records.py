"""Records, anchors and the entity assignment (rules 1-4, 6).

Stages (all DuckDB; the large hubs are processed one md5(record_id)[:2] part at a time):

  rows           hub rows normalized like today, hash-partitioned by record
  goslin         Goslin names of the lipid hubs (cached parser, reuses reference.goslin_identifiers)
  parts          per part: record aggregates, record_rows (output), identity-xref edges
  assign         rules 3 and 4 over the small set of anchorless records with identity edges
  records        records.parquet with entity_id and decision
  gene_products  rule 6, derived exactly as today (brief 3.6)
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pyarrow as pa

from .common import (
    ATTRIBUTE_TYPES,
    CHEMICAL,
    EMPTY_KEYS,
    INCHIKEY_RE,
    hub_rank,
    PARTS,
    PROTEIN,
    files,
    finish_part_loop,
    log,
    norm,
    part_done,
    part_dirs,
    quote,
    scan,
    sql_list,
)

GOSLIN_HUBS = ("swisslipids", "lipidmaps", "hmdb", "chebi", "refmet")


# --------------------------------------------------------------------------- rows
def stage_rows(ctx, directory):
    c = ctx.connect("rows")
    out = directory / "rows"
    counts = {}
    for hub, path in ctx.hubs.items():
        marker = directory / f"hub-{hub}.done"
        if marker.exists():
            continue
        for stale in out.glob(f"part=*/{hub}-*.parquet"):
            stale.unlink()
        ident = norm(quote(hub), "hub_id")
        value = norm("source_type", "source_id")
        query = f"""SELECT {quote(hub)} hub,{ident} local_id,source_type,{value} AS value,
            taxonomy_id taxon,substr(md5({quote(hub + ":")} || {ident}),1,2) part
            FROM {scan(path)}
            WHERE hub_id IS NOT NULL AND trim(hub_id)<>'' AND source_id IS NOT NULL
            AND trim(source_id)<>'' AND source_type NOT IN {sql_list(ATTRIBUTE_TYPES)}"""
        ctx.copy_partitioned(c, query, out, "part", hub + "-")
        marker.write_text("")
        log("rows_hub_done", hub=hub)
    for hub in ctx.hubs:
        counts[hub] = c.execute(
            f"SELECT count(*) FROM read_parquet({quote(out / '*' / (hub + '-*.parquet'))})"
        ).fetchone()[0]
    return dict(rows=sum(counts.values()), by_hub=counts)


# ------------------------------------------------------------------------- goslin
def stage_goslin(ctx, directory):
    from omnipath_build.reference.goslin_identifiers import build

    hubs = {h: str(ctx.hubs[h]) for h in GOSLIN_HUBS if h in ctx.hubs}
    if hubs:
        build(hubs, directory, ctx.threads, ctx.memory, str(ctx.goslin_cache))
    else:
        c = ctx.connect("goslin")
        ctx.copy(
            c,
            "SELECT NULL::VARCHAR hub,NULL::VARCHAR hub_id,NULL::VARCHAR record_id,"
            "NULL::VARCHAR name_type,NULL::VARCHAR source_name,NULL::VARCHAR name,"
            "NULL::VARCHAR goslin,NULL::VARCHAR level WHERE false",
            directory / "claims.parquet",
        )
    shutil.rmtree(directory / "spill", ignore_errors=True)
    c = ctx.connect("goslin")
    claims = c.execute(
        f"SELECT count(*),count(DISTINCT record_id),count(DISTINCT goslin) "
        f"FROM read_parquet({quote(directory / 'claims.parquet')})"
    ).fetchone()
    levels = c.execute(
        f"SELECT level,count(DISTINCT goslin) FROM read_parquet({quote(directory / 'claims.parquet')}) GROUP BY 1"
    ).fetchall()
    return dict(claims=claims[0], records=claims[1], names=claims[2], levels=dict(levels))


# -------------------------------------------------------------------------- parts
def valid_inchikey(column="value"):
    return (
        f"regexp_matches({column},'{INCHIKEY_RE}') AND {column} NOT IN {sql_list(EMPTY_KEYS)}"
    )


def stage_parts(ctx, directory):
    rows = directory.parent / "rows" / "rows"
    c = ctx.connect("parts")
    chem, prot = sql_list(CHEMICAL), sql_list(PROTEIN)
    c.execute(
        f"""CREATE TEMP TABLE gcl AS SELECT DISTINCT record_id,goslin,substr(md5(record_id),1,2) part
        FROM read_parquet({quote(directory.parent / 'goslin' / 'claims.parquet')})"""
    )
    for p in PARTS:
        if part_done(directory, p):
            continue
        sources = part_dirs(rows, [p])
        if not sources:
            continue
        c.execute(
            f"CREATE OR REPLACE TEMP TABLE r AS SELECT hub,local_id,source_type,value,taxon FROM {files(*sources)}"
        )
        # One row per record. The InChIKey list is kept only for multi-anchor (quarantined) records.
        ctx.copy(
            c,
            f"""SELECT hub,local_id,hub || ':' || local_id record_id,
              coalesce(min(taxon) FILTER (WHERE taxon NOT IN ('','0')),'0') taxon,
              coalesce(bool_or(hub='uniprot' AND source_type='uniprot_entry'
                  AND split_part(value,'_',1)<>local_id),false) reviewed,
              count(DISTINCT ik)::INTEGER n_ik,min(ik) ik_min,
              CASE WHEN count(DISTINCT ik)>1 THEN list(DISTINCT ik) FILTER (WHERE ik IS NOT NULL) END ik_list,
              coalesce(bool_or(hub='uniprot' AND source_type='uniprot' AND value=local_id),false) has_uniprot
            FROM (SELECT *,CASE WHEN source_type='inchikey' AND hub IN {chem} AND {valid_inchikey()}
                  THEN value END ik FROM r) GROUP BY hub,local_id""",
            directory / "records0" / f"{p}.parquet",
        )
        # Every row of the record, plus its most specific Goslin name as a `goslin` row (labels).
        ctx.copy(
            c,
            f"""SELECT record_id,source_type,value FROM (
              SELECT DISTINCT hub || ':' || local_id record_id,source_type,value FROM r
              UNION SELECT g.record_id,'goslin',g.goslin FROM gcl g
                SEMI JOIN (SELECT DISTINCT hub || ':' || local_id record_id FROM r) USING(record_id)
                WHERE g.part={quote(p)}) ORDER BY record_id,source_type,value""",
            directory.parent.parent / "record_rows" / f"part={p}" / "data.parquet",
            ", ROW_GROUP_SIZE 65536",
        )
        # Identity cross-references between records of one domain; uniprot<->entrez are gene links.
        ctx.copy(
            c,
            f"""SELECT DISTINCT hub || ':' || local_id source_record,source_type || ':' || value target_record,
              CASE WHEN (hub='uniprot' AND source_type='entrez') OR (hub='entrez' AND source_type='uniprot')
                   THEN 'gene_product' ELSE 'identity_xref' END semantics
            FROM r WHERE ((hub IN {chem} AND source_type IN {chem}) OR (hub IN {prot} AND source_type IN {prot}))
            AND NOT (source_type=hub AND value=local_id)""",
            directory / "edges" / f"{p}.parquet",
        )
        finish_part_loop(directory, p)
    n = c.execute(f"SELECT count(*) FROM {files(str(directory / 'records0' / '*.parquet'))}").fetchone()[0]
    e = c.execute(f"SELECT count(*) FROM {files(str(directory / 'edges' / '*.parquet'))}").fetchone()[0]
    return dict(records=n, edges=e)


def create_state(c, work: Path):
    """Views `r0` (record aggregates), `gl` (Goslin per record) and `state` (anchors per record)."""
    c.execute(f"CREATE OR REPLACE VIEW r0 AS SELECT * FROM {files(str(work / 'parts' / 'records0' / '*.parquet'))}")
    c.execute(
        f"""CREATE OR REPLACE VIEW gl AS SELECT record_id,count(DISTINCT goslin)::INTEGER n_gl,min(goslin) gl_min
        FROM read_parquet({quote(work / 'goslin' / 'claims.parquet')}) GROUP BY record_id"""
    )
    # Rule 1: InChIKey (chemical), own primary accession (uniprot), entrez record (gene), else the
    # most specific Goslin name (the Goslin claims keep only each record's most specific level).
    c.execute(
        """CREATE OR REPLACE VIEW state AS
        SELECT r.record_id,r.hub,r.local_id,r.taxon,r.reviewed,
          CASE WHEN r.hub='entrez' THEN 'entrez' WHEN r.hub='uniprot' THEN CASE WHEN r.has_uniprot THEN 'uniprot' END
               WHEN r.n_ik>0 THEN 'inchikey' WHEN coalesce(g.n_gl,0)>0 THEN 'goslin' END anchor_kind,
          CASE WHEN r.hub='entrez' THEN 1 WHEN r.hub='uniprot' THEN r.has_uniprot::INTEGER
               WHEN r.n_ik>0 THEN r.n_ik ELSE coalesce(g.n_gl,0) END anchor_count,
          CASE WHEN r.hub='entrez' THEN 'entrez:' || r.local_id
               WHEN r.hub='uniprot' THEN CASE WHEN r.has_uniprot THEN 'uniprot:' || r.local_id END
               WHEN r.n_ik=1 THEN 'inchikey:' || r.ik_min
               WHEN r.n_ik=0 AND coalesce(g.n_gl,0)=1 THEN 'goslin:' || g.gl_min END anchor
        FROM r0 r LEFT JOIN gl g USING(record_id)"""
    )


# ------------------------------------------------------------------------- assign
def stage_assign(ctx, directory):
    """Rule 3 (attach, one step) and rule 4 (group) for anchorless records with identity edges."""
    work = directory.parent
    c = ctx.connect("assign")
    create_state(c, work)
    c.execute(
        f"""CREATE TABLE xref AS SELECT DISTINCT source_record a,target_record b
        FROM {files(str(work / 'parts' / 'edges' / '*.parquet'))} WHERE semantics='identity_xref'"""
    )
    c.execute("CREATE TABLE nb AS SELECT a rec,b other FROM xref UNION SELECT b,a FROM xref")
    c.execute(
        "CREATE TABLE st AS SELECT s.* FROM state s SEMI JOIN (SELECT DISTINCT rec record_id FROM nb) USING(record_id)"
    )
    # Rule 3: an anchorless record sees the anchors of the records its own edges reach.
    c.execute(
        """CREATE TABLE seen AS
        SELECT n.rec,count(*) FILTER (WHERE o.anchor_count>1)::INTEGER n_quarantined,
          count(DISTINCT o.anchor) FILTER (WHERE o.anchor_count=1)::INTEGER n_anchors,
          min(o.anchor) FILTER (WHERE o.anchor_count=1) anchor
        FROM nb n JOIN st s ON s.record_id=n.rec AND s.anchor_count=0
          JOIN st o ON o.record_id=n.other AND o.anchor_count>0 GROUP BY n.rec"""
    )
    # Rule 4: anchorless, unattached records with no edge to any anchored record; group edges
    # connect candidates only.
    c.execute(
        "CREATE TABLE cand AS SELECT s.record_id,s.hub,s.local_id FROM st s ANTI JOIN seen ON seen.rec=s.record_id WHERE s.anchor_count=0"
    )
    edges = c.execute(
        "SELECT x.a,x.b FROM xref x SEMI JOIN cand ca ON ca.record_id=x.a SEMI JOIN cand cb ON cb.record_id=x.b"
    ).fetch_arrow_table()
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

    for a, b in zip(edges.column(0).to_pylist(), edges.column(1).to_pylist()):
        ra, rb = find(node(a)), find(node(b))
        if ra != rb:
            parent[ra] = rb
    components = pa.table(
        {
            "record_id": pa.array(list(index), pa.string()),
            "comp": pa.array([find(i) for i in index.values()], pa.int64()),
        }
    )
    c.register("components", components)
    c.execute(
        f"""CREATE TABLE grouped AS SELECT k.record_id,gc.* FROM components k JOIN (
          SELECT k.comp,count(*) n,count(DISTINCT g.hub) hubs,
            arg_min(g.record_id,struct_pack(r:={hub_rank('g.hub')},l:=g.local_id)) preferred
          FROM components k JOIN cand g USING(record_id) GROUP BY k.comp) gc USING(comp)"""
    )
    c.execute(
        """CREATE TABLE assign AS
        SELECT rec record_id,anchor entity_id,'attached' decision FROM seen WHERE n_quarantined=0 AND n_anchors=1
        UNION ALL SELECT rec,rec,'ambiguous_native' FROM seen WHERE NOT (n_quarantined=0 AND n_anchors=1)
        UNION ALL SELECT record_id,CASE WHEN n=hubs THEN preferred ELSE record_id END,
          CASE WHEN n=hubs THEN 'grouped' ELSE 'ambiguous_native' END FROM grouped"""
    )
    ctx.copy(c, "SELECT * FROM assign ORDER BY record_id", directory / "assign.parquet")
    # Quarantined chemical records contribute their InChIKeys as extra entities (see entities).
    ctx.copy(
        c,
        "SELECT record_id,unnest(ik_list) ik FROM r0 WHERE n_ik>1",
        directory / "quarantined_keys.parquet",
    )
    counts = dict(
        c.execute("SELECT decision,count(*) FROM assign GROUP BY 1").fetchall()
    )
    groups = c.execute(
        "SELECT count(DISTINCT comp) FILTER (WHERE n=hubs),count(DISTINCT comp) FILTER (WHERE n<>hubs) FROM grouped"
    ).fetchone()
    # Diagnostic only: lipid-name records that also have an identity edge to an InChIKey record.
    lipid_with_inchikey_edge = c.execute(
        """SELECT count(DISTINCT n.rec) FROM nb n JOIN st s ON s.record_id=n.rec AND s.anchor_kind='goslin'
        JOIN st o ON o.record_id=n.other AND o.anchor_kind='inchikey'"""
    ).fetchone()[0]
    return dict(
        assigned=counts,
        groups_accepted=groups[0],
        groups_rejected=groups[1],
        lipid_name_records_with_inchikey_edge=lipid_with_inchikey_edge,
    )


# ------------------------------------------------------------------------ records
def stage_records(ctx, directory):
    work = directory.parent
    c = ctx.connect("records")
    create_state(c, work)
    assign = quote(work / "assign" / "assign.parquet")
    query = f"""SELECT s.record_id,s.hub,s.local_id,
        CASE WHEN s.anchor_count=1 THEN s.anchor END anchor,s.anchor_kind,s.anchor_count,s.taxon,s.reviewed,
        coalesce(a.entity_id,CASE WHEN s.hub='entrez' THEN 'entrez:' || s.local_id
            WHEN s.anchor_count=1 THEN s.anchor ELSE s.record_id END) entity_id,
        coalesce(a.decision,CASE WHEN s.hub='entrez' THEN 'gene_identity' WHEN s.anchor_count>1 THEN 'quarantined'
            WHEN s.anchor_count=1 THEN CASE s.anchor_kind WHEN 'goslin' THEN 'lipid_name' ELSE 'anchored' END
            ELSE 'structureless' END) decision
        FROM state s LEFT JOIN read_parquet({assign}) a USING(record_id)"""
    n = ctx.copy(c, query, ctx.out / "records.parquet", ", ROW_GROUP_SIZE 262144")
    by_decision = dict(
        c.execute(
            f"SELECT decision,count(*) FROM read_parquet({quote(ctx.out / 'records.parquet')}) GROUP BY 1 ORDER BY 1"
        ).fetchall()
    )
    return dict(records=n, by_decision=by_decision)


# ----------------------------------------------------------------- gene_products
def stage_gene_products(ctx, directory):
    """Rule 6, same derivation as `build_reference.gene_products_query` (brief 3.6)."""
    if "uniprot" not in ctx.hubs:
        c = ctx.connect("gene_products")
        ctx.copy(
            c,
            "SELECT NULL::VARCHAR protein_entity_id,NULL::VARCHAR entrez_id,NULL::VARCHAR taxon WHERE false",
            ctx.out / "gene_products.parquet",
        )
        (directory / "gp").mkdir(exist_ok=True)
        return dict(gene_products=0)
    work = directory.parent
    c = ctx.connect("gene_products")
    records = quote(ctx.out / "records.parquet")
    c.execute(
        f"""CREATE TABLE ge AS SELECT source_record,target_record
        FROM {files(str(work / 'parts' / 'edges' / '*.parquet'))} WHERE semantics='gene_product'"""
    )
    c.execute(
        f"""CREATE TABLE rec AS SELECT record_id,hub,local_id,anchor,anchor_count,taxon FROM read_parquet({records})
        WHERE hub IN ('uniprot','entrez')"""
    )
    query = """SELECT DISTINCT protein_entity_id,entrez_id,taxon FROM (
        SELECT s.anchor protein_entity_id,substr(e.target_record,8) entrez_id,s.taxon
        FROM ge e JOIN rec s ON s.record_id=e.source_record AND s.hub='uniprot' AND s.anchor_count=1
        LEFT JOIN rec t ON t.record_id=e.target_record
        WHERE (t.taxon IS NULL OR t.taxon IN ('','0') OR s.taxon IN ('','0') OR s.taxon=t.taxon)
        UNION
        SELECT t.anchor,s.local_id,t.taxon
        FROM ge e JOIN rec s ON s.record_id=e.source_record AND s.hub='entrez'
        JOIN rec t ON t.record_id=e.target_record AND t.hub='uniprot' AND t.anchor_count=1
        WHERE (s.taxon IN ('','0') OR t.taxon IN ('','0') OR s.taxon=t.taxon))"""
    n = ctx.copy(
        c,
        query + " ORDER BY protein_entity_id,entrez_id",
        ctx.out / "gene_products.parquet",
        ", ROW_GROUP_SIZE 65536",
    )
    # The same rows hash-partitioned by protein (aligned with record and entity parts).
    ctx.copy_partitioned(
        c,
        f"SELECT *,substr(md5(protein_entity_id),1,2) part FROM read_parquet({quote(ctx.out / 'gene_products.parquet')})",
        directory / "gp",
        "part",
        "gp-",
    )
    return dict(gene_products=n)


def stage_gene_aliases(ctx, directory):
    """Gene aliases as extra record_rows of the NCBI Gene record `entrez:N` (brief 3.3).

    A gene receives the genesymbol, genesymbol-syn, hgnc and ensg claims of every protein that
    links to exactly one gene (`gene_protein-gene-forward`). They are written beside the record's
    own rows as `record_rows/part=XX/aliases.parquet`.
    """
    work = directory.parent
    c = ctx.connect("gene_aliases")
    if "uniprot" not in ctx.hubs or "entrez" not in ctx.hubs:
        return dict(alias_rows=0)
    c.execute(
        f"""CREATE TABLE links AS SELECT protein_entity_id,min(entrez_id) gene_id
        FROM read_parquet({quote(ctx.out / 'gene_products.parquet')}) GROUP BY 1 HAVING count(DISTINCT entrez_id)=1"""
    )
    c.execute(
        f"""CREATE TABLE genes AS SELECT record_id FROM read_parquet({quote(ctx.out / 'records.parquet')})
        WHERE hub='entrez'"""
    )
    c.execute(
        f"""CREATE TABLE aliases AS SELECT DISTINCT 'entrez:' || l.gene_id record_id,r.source_type,r.value,
          substr(md5('entrez:' || l.gene_id),1,2) part
        FROM {files(str(work / 'rows' / 'rows' / '*' / 'uniprot-*.parquet'))} r
        JOIN links l ON l.protein_entity_id='uniprot:' || r.local_id
        SEMI JOIN genes g ON g.record_id='entrez:' || l.gene_id
        WHERE r.hub='uniprot' AND r.source_type IN ('genesymbol','genesymbol-syn','hgnc','ensg')"""
    )
    n = 0
    for (p,) in c.execute("SELECT DISTINCT part FROM aliases ORDER BY 1").fetchall():
        n += ctx.copy(
            c,
            f"SELECT record_id,source_type,value FROM aliases WHERE part={quote(p)} ORDER BY record_id,source_type,value",
            ctx.out / "record_rows" / f"part={p}" / "aliases.parquet",
            ", ROW_GROUP_SIZE 65536",
        )
    return dict(alias_rows=n)
