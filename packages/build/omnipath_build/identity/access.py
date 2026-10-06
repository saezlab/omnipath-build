"""The access table: every assertion relation of brief 2.3 with the entity metadata denormalized.

Row = (target, route, ns, identifier, entity_id, kind, anchor, taxon, quarantined, reviewed,
gene_ids, tag); `target` 1 = chemical, 2 = gene/protein; `route` 1 = identity, 2 = gene resolution.

tags (precedence is applied by the runtime inside one posting, never here):
  native         the entity's own identity row (a record's hub id, an InChIKey, a goslin anchor,
                 a primary UniProt accession, an NCBI gene id)
  regular        a cross-reference asserted by a record (claims, gene-level assertions, goslin
                 names that resolve to a structure)
  secondary      UniProt secondary accession, posted under both `uniprot` and `uniprot-sec`
  fallback       kegg / bigg claims; lose against a native row of the same posting
  symbol_synonym a genesymbol-syn claim posted as `genesymbol`; loses against an exact symbol of
                 the same taxon

Gene-level namespaces (hgnc, ensg, enst, refseq RNA, genesymbol, genesymbol-syn) post gene entities
`entrez:N` built like today's gene-role component (no 10-gene cap); `gene_ids` of a protein
entity are its linked NCBI gene ids and of a gene entity `[N]`.

Rows are produced by independent stages into `work/access_raw/target=T/part=XX/` (part =
md5(identifier)[:2]) and merged, de-duplicated (best tag wins) and sorted by the last stage.
"""

from __future__ import annotations

import json

from .common import (
    CHEMICAL,
    ISOFORM_RE,
    PARTS,
    STRUCTURE_LEVELS,
    TAG_RANK,
    files,
    finish_part_loop,
    part_dirs,
    part_done,
    quote,
    sql_list,
)

GROUP = 4  # record/entity parts processed together (fewer, larger raw files)
PRODUCT_RE = "(AP|NP|XP|YP|WP|ZP)_[0-9]+"
GENBANK_RE = "[A-Z]{3}[0-9]{5,}"
VERSION = r"\.[0-9]+$"
GENE_NS = "('ensg','enst','hgnc','genesymbol','genesymbol-syn','refseq')"
TARGET_NAMES = {1: "chemical", 2: "gene_protein"}
ACC_TYPES = (
    "target TINYINT,route TINYINT,ns VARCHAR,identifier VARCHAR,entity_id VARCHAR,kind VARCHAR,anchor VARCHAR,"
    "taxon VARCHAR,quarantined BOOLEAN,reviewed BOOLEAN,gene_ids VARCHAR[],tag VARCHAR"
)


def acc(target, route, ns, identifier, tag, meta="e"):
    """A typed access-row SELECT list; `ns`, `identifier`, `tag` are SQL expressions over `meta`."""
    return (
        f"SELECT {target}::TINYINT AS target,{route}::TINYINT AS route,{ns}::VARCHAR AS ns,"
        f"{identifier}::VARCHAR AS identifier,{meta}.entity_id::VARCHAR AS entity_id,"
        f"{meta}.kind::VARCHAR AS kind,{meta}.anchor::VARCHAR AS anchor,{meta}.taxon::VARCHAR AS taxon,"
        f"{meta}.quarantined::BOOLEAN AS quarantined,{meta}.reviewed::BOOLEAN AS reviewed,"
        f"{meta}.gene_ids::VARCHAR[] AS gene_ids,{tag}::VARCHAR AS tag"
    )


def emit(ctx, c, query, pattern):
    """Append access rows to the raw table, hash-partitioned by identifier."""
    wrapped = f"SELECT *,substr(md5(identifier),1,2) part FROM ({query})"
    return ctx.copy_partitioned(c, wrapped, ctx.work / "access_raw", "target,part", pattern)


def entity_meta_sql(entities, members, gp):
    """Entity metadata: kind/anchor/taxon/quarantined plus reviewed and linked gene ids."""
    return f"""SELECT n.entity_id,n.kind,n.anchor,n.taxon,n.quarantined,coalesce(rv.reviewed,false) reviewed,
        CASE n.kind WHEN 'gene' THEN CASE WHEN starts_with(n.entity_id,'entrez:') THEN [substr(n.entity_id,8)]
            ELSE []::VARCHAR[] END
          WHEN 'protein' THEN coalesce(g.gene_ids,[]::VARCHAR[]) ELSE []::VARCHAR[] END gene_ids
        FROM {entities} n
        LEFT JOIN (SELECT entity_id,bool_or(reviewed) reviewed FROM {members} WHERE hub='uniprot' GROUP BY 1) rv
          ON rv.entity_id=n.entity_id
        LEFT JOIN (SELECT protein_entity_id entity_id,list(DISTINCT entrez_id ORDER BY entrez_id) gene_ids
          FROM {gp} GROUP BY 1) g ON g.entity_id=n.entity_id"""


def rna(column):
    return rf"regexp_full_match({column},'(NM|NR|XM|XR)_[0-9]+(\.[0-9]+)?')"


# --------------------------------------------------------- entity-aligned assertions
def stage_access_entity(ctx, directory):
    """Identity rows and UniProt product claims, one group of parts at a time.

    A uniprot record's id equals its entity id, so the record rows of part p and the entities of
    part p are aligned: no cross-part join is needed. Isoform rows go to `iso/` for projection.
    """
    work = directory.parent
    rows, entin = work / "rows" / "rows", work / "entin" / "entin"
    gp = work / "gene_products" / "gp"
    c = ctx.connect("access_entity")
    (directory / "iso").mkdir(exist_ok=True)
    chem = sql_list(CHEMICAL)
    emitted = {1: 0, 2: 0}
    for i in range(0, len(PARTS), GROUP):
        parts = PARTS[i : i + GROUP]
        name = parts[0]
        if part_done(directory, name):
            continue
        sources = part_dirs(entin, parts)
        if not sources:
            continue
        c.execute(
            "CREATE OR REPLACE TEMP TABLE mm AS SELECT entity_id,record_id,hub,local_id,reviewed "
            f"FROM {files(*sources)} WHERE record_id IS NOT NULL"
        )
        c.execute(f"CREATE OR REPLACE TEMP TABLE ent AS SELECT * FROM {files(*part_dirs(ctx.out / 'entities', parts))}")
        gps = part_dirs(gp, parts)
        c.execute(
            "CREATE OR REPLACE TEMP TABLE gpt AS "
            + (
                f"SELECT * FROM {files(*gps)}"
                if gps
                else "SELECT NULL::VARCHAR protein_entity_id,NULL::VARCHAR entrez_id,NULL::VARCHAR taxon WHERE false"
            )
        )
        c.execute("CREATE OR REPLACE TEMP TABLE e AS " + entity_meta_sql("ent", "mm", "gpt"))
        rr = part_dirs(rows, parts)
        c.execute(
            "CREATE OR REPLACE TEMP TABLE rr AS "
            + (
                f"""SELECT local_id,source_type,value FROM {files(*rr)} WHERE hub='uniprot'
                AND source_type IN ('uniprot_entry','ensp','refseq_protein','genbank','uniprot-sec')"""
                if rr
                else "SELECT NULL::VARCHAR local_id,NULL::VARCHAR source_type,NULL::VARCHAR AS value WHERE false"
            )
        )
        c.execute(
            rf"""CREATE OR REPLACE TEMP TABLE pc AS
            SELECT local_id,source_type ns,value identifier,'regular' tag FROM rr
              WHERE source_type IN ('uniprot_entry','ensp','refseq_protein','genbank')
            UNION ALL SELECT local_id,source_type,regexp_replace(value,'{VERSION}',''),'regular' FROM rr
              WHERE (source_type='refseq_protein' AND regexp_full_match(regexp_replace(value,'{VERSION}',''),'{PRODUCT_RE}'))
              OR (source_type='genbank' AND regexp_full_match(regexp_replace(value,'{VERSION}',''),'{GENBANK_RE}'))
            UNION ALL SELECT local_id,'uniprot',value,'secondary' FROM rr WHERE source_type='uniprot-sec'
            UNION ALL SELECT local_id,'uniprot-sec',value,'secondary' FROM rr WHERE source_type='uniprot-sec'"""
        )
        iso = f"regexp_full_match(x.local_id,'{ISOFORM_RE}')"
        chemical = " UNION ALL ".join(
            [
                acc(1, 1, "x.hub", "x.local_id", "'native'")
                + f" FROM mm x JOIN e ON e.entity_id=x.entity_id WHERE x.hub IN {chem}",
                acc(1, 1, "'inchikey'", "substr(e.entity_id,10)", "'native'")
                + " FROM e WHERE starts_with(e.entity_id,'inchikey:')",
                acc(1, 1, "'goslin'", "substr(e.entity_id,8)", "'native'")
                + " FROM e WHERE starts_with(e.entity_id,'goslin:')",
            ]
        )
        protein = " UNION ALL ".join(
            [
                acc(2, 1, "'uniprot'", "x.local_id", "'native'")
                + f" FROM mm x JOIN e ON e.entity_id=x.entity_id WHERE x.hub='uniprot' AND NOT {iso}",
                acc(2, 2, "'entrez'", "substr(e.entity_id,8)", "'native'")
                + " FROM e WHERE starts_with(e.entity_id,'entrez:')",
                acc(2, 1, "x.ns", "x.identifier", "x.tag")
                + f" FROM pc x JOIN e ON e.entity_id='uniprot:' || x.local_id WHERE NOT {iso}",
            ]
        )
        emitted[1] += emit(ctx, c, chemical, f"ent{name}-")
        emitted[2] += emit(ctx, c, protein, f"ent{name}-")
        isoforms = (
            acc(2, 1, "'uniprot'", "x.local_id", "'native'")
            + f" FROM mm x JOIN e ON e.entity_id=x.entity_id WHERE x.hub='uniprot' AND {iso} UNION ALL "
            + acc(2, 1, "x.ns", "x.identifier", "x.tag")
            + f" FROM pc x JOIN e ON e.entity_id='uniprot:' || x.local_id WHERE {iso}"
        )
        ctx.copy(c, isoforms, directory / "iso" / f"{name}.parquet")
        finish_part_loop(directory, name)
    return dict(rows_emitted={TARGET_NAMES[t]: n for t, n in emitted.items()})


# ------------------------------------------------------------------ isoform parents
def stage_access_isoforms(ctx, directory):
    """Isoform rows are asserted for the parent entity when the parent has one primary anchor."""
    work = directory.parent
    iso = list((work / "access_entity" / "iso").glob("*.parquet"))
    if not iso:
        return dict(rows=0, isoform_parents=0)
    c = ctx.connect("access_isoforms")
    records = quote(ctx.out / "records.parquet")
    entities = files(str(ctx.out / "entities" / "*" / "data.parquet"))
    gp = quote(ctx.out / "gene_products.parquet")
    c.execute(f"CREATE TABLE iso AS SELECT * FROM {files(*map(str, iso))}")
    c.execute(
        f"""CREATE TABLE parents AS SELECT DISTINCT regexp_replace(entity_id,'-[0-9]+$','') parent FROM iso
        WHERE regexp_full_match(entity_id,'uniprot:{ISOFORM_RE}')"""
    )
    c.execute(
        f"""CREATE TABLE parent_records AS SELECT anchor entity_id,reviewed FROM read_parquet({records})
        WHERE hub='uniprot' AND anchor_count=1 AND anchor IN (SELECT parent FROM parents)"""
    )
    c.execute(
        f"""CREATE TABLE pm AS SELECT n.entity_id,n.kind,n.anchor,n.taxon,n.quarantined,r.reviewed,
          coalesce(g.gene_ids,[]::VARCHAR[]) gene_ids
        FROM {entities} n JOIN parent_records r USING(entity_id)
        LEFT JOIN (SELECT protein_entity_id entity_id,list(DISTINCT entrez_id ORDER BY entrez_id) gene_ids
          FROM read_parquet({gp}) WHERE protein_entity_id IN (SELECT entity_id FROM parent_records)
          GROUP BY 1) g USING(entity_id)"""
    )
    query = f"""SELECT i.target,i.route,i.ns,i.identifier,coalesce(p.entity_id,i.entity_id) entity_id,
        coalesce(p.kind,i.kind) kind,CASE WHEN p.entity_id IS NULL THEN i.anchor ELSE p.anchor END anchor,
        CASE WHEN p.entity_id IS NULL THEN i.taxon ELSE p.taxon END taxon,
        coalesce(p.quarantined,i.quarantined) quarantined,coalesce(p.reviewed,i.reviewed) reviewed,
        coalesce(p.gene_ids,i.gene_ids) gene_ids,i.tag
        FROM iso i LEFT JOIN pm p ON p.entity_id=regexp_replace(i.entity_id,'-[0-9]+$','')"""
    n = emit(ctx, c, query, "iso-")
    return dict(rows=n, isoform_parents=c.execute("SELECT count(*) FROM pm").fetchone()[0])


# ---------------------------------------------------------------- gene-level rows
def stage_access_genes(ctx, directory):
    """Gene-level namespaces post gene entities (brief 2.7, minus the 10-gene cap)."""
    work = directory.parent
    c = ctx.connect("access_genes")
    sources = [
        str(work / "rows" / "rows" / "*" / f"{hub}-*.parquet") for hub in ctx.present("uniprot", "entrez")
    ]
    entities = files(str(ctx.out / "entities" / "*" / "data.parquet"))
    gp = quote(ctx.out / "gene_products.parquet")
    c.execute(f"CREATE TABLE genes AS SELECT entity_id,taxon FROM {entities} WHERE kind='gene' AND NOT quarantined")
    # Proteins that link to exactly one gene (and at most one real taxon).
    c.execute(
        f"""CREATE TABLE lp AS SELECT l.protein_entity_id,l.gene_id,l.taxon link_taxon,p.taxon protein_taxon FROM (
          SELECT protein_entity_id,min(entrez_id) gene_id,min(taxon) FILTER (WHERE taxon NOT IN ('','0')) taxon
          FROM read_parquet({gp}) GROUP BY protein_entity_id
          HAVING count(DISTINCT entrez_id)=1 AND count(DISTINCT taxon) FILTER (WHERE taxon NOT IN ('','0'))<=1) l
          JOIN (SELECT entity_id,taxon FROM {entities} WHERE kind='protein' AND NOT quarantined) p
            ON p.entity_id=l.protein_entity_id"""
    )
    c.execute(
        "CREATE TABLE gene_rows AS "
        + (
            f"""SELECT hub,local_id,source_type,value FROM {files(*sources)} WHERE source_type IN {GENE_NS}
            AND hub IN ('uniprot','entrez') AND (source_type<>'refseq' OR {rna('value')})"""
            if sources
            else "SELECT NULL::VARCHAR hub,NULL::VARCHAR local_id,NULL::VARCHAR source_type,NULL::VARCHAR AS value WHERE false"
        )
    )
    c.execute(
        """CREATE TABLE assertions AS
        SELECT g.entity_id,r.source_type ns,r.value identifier,nullif(nullif(g.taxon,'0'),'') taxon
        FROM gene_rows r JOIN genes g ON g.entity_id='entrez:' || r.local_id WHERE r.hub='entrez'
        UNION ALL
        SELECT g.entity_id,r.source_type,r.value,
          nullif(nullif(coalesce(nullif(nullif(g.taxon,'0'),''),l.link_taxon,nullif(nullif(l.protein_taxon,'0'),'')),'0'),'')
        FROM gene_rows r JOIN lp l ON l.protein_entity_id='uniprot:' || r.local_id
          JOIN genes g ON g.entity_id='entrez:' || l.gene_id
        WHERE r.hub='uniprot'
          AND (g.taxon IS NULL OR g.taxon IN ('','0') OR l.link_taxon IS NULL OR g.taxon=l.link_taxon)
          AND (l.protein_taxon IS NULL OR l.protein_taxon IN ('','0') OR l.link_taxon IS NULL OR l.protein_taxon=l.link_taxon)
          AND (l.protein_taxon IS NULL OR l.protein_taxon IN ('','0') OR g.taxon IS NULL OR g.taxon IN ('','0')
               OR l.protein_taxon=g.taxon)"""
    )
    # Symbols answer both symbol namespaces; a genesymbol-syn claim is the weaker synonym.
    c.execute(
        r"""CREATE TABLE gene_level AS
        WITH a AS (SELECT entity_id,ns,
            CASE WHEN ns IN ('ensg','enst','refseq') THEN regexp_replace(identifier,'\.[0-9]+$','') ELSE identifier END identifier,
            taxon,CASE WHEN ns='genesymbol-syn' THEN 'symbol_synonym' ELSE 'regular' END tag
          FROM assertions WHERE identifier IS NOT NULL AND trim(identifier)<>'')
        SELECT DISTINCT * FROM (
          SELECT entity_id,ns,identifier,taxon,tag FROM a WHERE ns NOT IN ('genesymbol','genesymbol-syn')
          UNION ALL SELECT entity_id,'genesymbol',identifier,taxon,tag FROM a WHERE ns IN ('genesymbol','genesymbol-syn')
          UNION ALL SELECT entity_id,'genesymbol-syn',identifier,taxon,tag FROM a WHERE ns IN ('genesymbol','genesymbol-syn'))"""
    )
    query = """SELECT 2::TINYINT AS target,1::TINYINT AS route,ns,identifier,entity_id,'gene' AS kind,
        NULL::VARCHAR AS anchor,taxon,false AS quarantined,false AS reviewed,
        [substr(entity_id,8)] AS gene_ids,tag FROM gene_level"""
    n = emit(ctx, c, query, "genes-")
    # Gene label: the shortest exact symbol of the gene, 15 characters or fewer first (rule 13).
    ctx.copy_partitioned(
        c,
        """SELECT substr(md5(entity_id),1,2) part,entity_id,
          arg_min(identifier,struct_pack(long:=length(identifier)>15,n:=length(identifier),v:=identifier)) AS label
        FROM gene_level WHERE ns='genesymbol' AND tag='regular' AND length(trim(identifier)) BETWEEN 1 AND 120
        GROUP BY entity_id""",
        ctx.out / "gene_labels",
        "part",
        "labels-",
    )
    return dict(rows=n)


# ---------------------------------------------------- chemical claims and lipid names
def stage_access_chemicals(ctx, directory):
    work = directory.parent
    c = ctx.connect("access_chemicals")
    sources = [
        str(work / "rows" / "rows" / "*" / f"{hub}-*.parquet") for hub in ctx.present(*CHEMICAL)
    ]
    entities = files(str(ctx.out / "entities" / "*" / "data.parquet"))
    records = quote(ctx.out / "records.parquet")
    total = 0
    if sources:
        c.execute(
            f"""CREATE TABLE claims AS SELECT hub,local_id,source_type,value FROM {files(*sources)}
            WHERE source_type IN ('cas','drugbank','kegg') OR (source_type='chebi' AND hub='chebi')
              OR (source_type='bigg' AND hub='bigg')"""
        )
        c.execute(
            f"""CREATE TABLE ce AS SELECT c.source_type ns,c.value identifier,r.entity_id,
              CASE WHEN c.source_type IN ('kegg','bigg') THEN 'fallback' ELSE 'regular' END tag
            FROM claims c JOIN read_parquet({records}) r ON r.record_id=c.hub || ':' || c.local_id"""
        )
        c.execute(
            f"""CREATE TABLE em AS SELECT n.entity_id,n.kind,n.anchor,n.taxon,n.quarantined,false reviewed,
              []::VARCHAR[] gene_ids FROM {entities} n SEMI JOIN ce USING(entity_id)"""
        )
        total += emit(
            ctx,
            c,
            acc(1, 1, "x.ns", "x.identifier", "x.tag") + " FROM ce x JOIN em e ON e.entity_id=x.entity_id",
            "claims-",
        )
    # Rule 5: a full-structure Goslin name resolves to an InChIKey only if every record carrying
    # the name and an InChIKey agrees on exactly one key.
    c.execute(
        f"""CREATE TABLE agree AS SELECT g.goslin,min(k.ik) ik FROM (SELECT DISTINCT record_id,goslin
            FROM read_parquet({quote(work / 'goslin' / 'claims.parquet')}) WHERE level IN {sql_list(STRUCTURE_LEVELS)}) g
          JOIN (SELECT record_id,unnest(CASE WHEN n_ik=1 THEN [ik_min] ELSE ik_list END) ik
                FROM {files(str(work / 'parts' / 'records0' / '*.parquet'))} WHERE n_ik>0) k USING(record_id)
        GROUP BY g.goslin HAVING count(DISTINCT k.ik)=1"""
    )
    c.execute(
        f"""CREATE TABLE gm AS SELECT a.goslin,n.entity_id,n.kind,n.anchor,n.taxon,n.quarantined,false reviewed,
          []::VARCHAR[] gene_ids FROM agree a JOIN {entities} n ON n.entity_id='inchikey:' || a.ik"""
    )
    total += emit(ctx, c, acc(1, 1, "'goslin'", "e.goslin", "'regular'") + " FROM gm e", "goslin-")
    both = c.execute(
        f"""SELECT count(*) FROM agree a SEMI JOIN (SELECT anchor FROM read_parquet({records})
        WHERE anchor_kind='goslin' AND anchor_count=1) r ON r.anchor='goslin:' || a.goslin"""
    ).fetchone()[0]
    return dict(
        rows=total,
        goslin_structure_names=c.execute("SELECT count(*) FROM gm").fetchone()[0],
        goslin_names_with_structure_and_lipid_entity=both,
    )


# ------------------------------------------------------------- ramp_gene (route 2)
def stage_access_ramp(ctx, directory):
    """`ramp_gene` source ids resolve to genes through explicit UniProt/NCBI Gene source ids."""
    work = directory.parent
    if "ramp_gene" not in ctx.hubs:
        return dict(rows=0)
    c = ctx.connect("access_ramp")
    ramp = work / "rows" / "rows" / "*" / "ramp_gene-*.parquet"
    entities = files(str(ctx.out / "entities" / "*" / "data.parquet"))
    records = quote(ctx.out / "records.parquet")
    gp = quote(ctx.out / "gene_products.parquet")
    types = "('uniprot','entrez','ensg','ensp','enst','hgnc','refseq_protein','genbank')"
    c.execute(
        f"""CREATE TABLE src AS SELECT local_id identifier,source_type ns,value source_value,taxon
        FROM read_parquet({quote(ramp)}) WHERE source_type IN {types}"""
    )
    prot = [str(work / "rows" / "rows" / "*" / "uniprot-*.parquet")] if "uniprot" in ctx.hubs else []
    c.execute(
        "CREATE TABLE u AS "
        + (
            f"""SELECT local_id,source_type,value FROM {files(*prot)}
            WHERE source_type IN ('uniprot','uniprot-sec','ensg','ensp','enst','hgnc','refseq_protein','genbank')
            AND value IN (SELECT source_value FROM src)"""
            if prot
            else "SELECT NULL::VARCHAR local_id,NULL::VARCHAR source_type,NULL::VARCHAR AS value WHERE false"
        )
    )
    c.execute(
        f"""CREATE TABLE cand AS
        SELECT DISTINCT k.identifier,'uniprot:' || u.local_id entity_id,k.taxon FROM src k JOIN u ON u.value=k.source_value
          AND (u.source_type=k.ns OR (k.ns='uniprot' AND u.source_type='uniprot-sec')) WHERE k.ns<>'entrez'
        UNION SELECT DISTINCT k.identifier,n.entity_id,k.taxon FROM src k
          JOIN (SELECT entity_id FROM {entities} WHERE kind='gene' AND starts_with(entity_id,'entrez:')) n
          ON n.entity_id='entrez:' || k.source_value WHERE k.ns='entrez'"""
    )
    c.execute(
        f"""CREATE TABLE mapped AS SELECT DISTINCT c.identifier,
          CASE WHEN starts_with(c.entity_id,'entrez:') THEN c.entity_id ELSE 'entrez:' || p.entrez_id END entity_id
        FROM cand c LEFT JOIN read_parquet({gp}) p ON p.protein_entity_id=c.entity_id
        WHERE starts_with(c.entity_id,'entrez:') OR (p.entrez_id IS NOT NULL AND (c.taxon IN ('','0') OR c.taxon=p.taxon))"""
    )
    # Source genes without a UniProt/NCBI Gene route keep their own (possibly attached) entity.
    c.execute(
        f"""CREATE TABLE own AS SELECT r.local_id identifier,r.entity_id FROM read_parquet({records}) r
        ANTI JOIN mapped m ON m.identifier=r.local_id
        WHERE r.hub='ramp_gene' AND NOT starts_with(r.entity_id,'uniprot:')"""
    )
    c.execute(
        f"""CREATE TABLE em AS SELECT n.entity_id,n.kind,n.anchor,n.taxon,n.quarantined,false reviewed,
          CASE WHEN starts_with(n.entity_id,'entrez:') THEN [substr(n.entity_id,8)] ELSE []::VARCHAR[] END gene_ids
        FROM {entities} n SEMI JOIN (SELECT entity_id FROM mapped UNION SELECT entity_id FROM own) USING(entity_id)"""
    )
    query = (
        acc(2, 2, "'ramp_gene'", "x.identifier", "'regular'")
        + " FROM (SELECT * FROM mapped UNION ALL SELECT * FROM own) x JOIN em e ON e.entity_id=x.entity_id"
    )
    return dict(rows=emit(ctx, c, query, "ramp-"))


# ------------------------------------------------------------------ final merge
def stage_access(ctx, directory):
    """Merge the raw rows per (target, identifier part): best tag wins, sorted for lookups."""
    raw = ctx.work / "access_raw"
    c = ctx.connect("access")
    rank = "CASE tag " + " ".join(f"WHEN {quote(t)} THEN {i}" for i, t in enumerate(TAG_RANK)) + " END"
    rows, by_tag = {}, {}
    (directory / "done").mkdir(exist_ok=True)
    for target in (1, 2):
        rows[TARGET_NAMES[target]] = 0
        for p in PARTS:
            source = raw / f"target={target}" / f"part={p}"
            if not source.is_dir():
                continue
            marker = directory / "done" / f"{target}-{p}"
            if not marker.exists():
                c.execute(
                    f"""CREATE OR REPLACE TEMP TABLE merged AS SELECT route,ns,identifier,entity_id,any_value(kind) kind,
                      any_value(anchor) anchor,taxon,any_value(quarantined) quarantined,any_value(reviewed) reviewed,
                      any_value(gene_ids) gene_ids,arg_min(tag,{rank}) tag
                    FROM {files(str(source / '*.parquet'))} GROUP BY route,ns,identifier,entity_id,taxon"""
                )
                ctx.copy(
                    c,
                    "SELECT * FROM merged ORDER BY ns,identifier,entity_id,taxon",
                    ctx.out / "access" / f"target={target}" / f"part={p}" / "data.parquet",
                    ", ROW_GROUP_SIZE 65536",
                )
                marker.write_text(json.dumps(dict(c.execute("SELECT tag,count(*) FROM merged GROUP BY 1").fetchall())))
            for tag, n in json.loads(marker.read_text()).items():
                key = f"{TARGET_NAMES[target]}/{tag}"
                by_tag[key] = by_tag.get(key, 0) + n
                rows[TARGET_NAMES[target]] += n
    return dict(rows=rows, by_tag=dict(sorted(by_tag.items())))
