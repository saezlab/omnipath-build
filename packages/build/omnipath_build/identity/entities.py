"""Entities and members, partitioned by md5(entity_id)[:2].

An entity is the set of records sharing `records.entity_id`. Two kinds of entities have no
record: the gene `entrez:N` named by a gene-product link without an NCBI Gene record, and the
`inchikey:K` of every key claimed by a quarantined record (so a stated InChIKey still finds its
structure, as today).
"""

from __future__ import annotations

from .common import (
    PARTS,
    CHEMICAL,
    files,
    finish_part_loop,
    hub_rank,
    part_dirs,
    part_done,
    quote,
)


def stage_extras(ctx, directory):
    work = directory.parent
    c = ctx.connect("extras")
    records = quote(ctx.out / "records.parquet")
    qkeys = quote(work / "assign" / "quarantined_keys.parquet")
    c.execute(f"CREATE TABLE qk AS SELECT DISTINCT 'inchikey:' || ik entity_id FROM read_parquet({qkeys})")
    c.execute(
        f"""CREATE TABLE ik_extra AS SELECT entity_id,'chemical' kind0,entity_id anchor,NULL::VARCHAR taxon FROM qk
        WHERE entity_id NOT IN (SELECT anchor FROM read_parquet({records})
            WHERE anchor_kind='inchikey' AND anchor_count=1 AND anchor IN (SELECT entity_id FROM qk))"""
    )
    c.execute(
        f"""CREATE TABLE gene_extra AS SELECT 'entrez:' || p.entrez_id entity_id,'gene' kind0,NULL::VARCHAR anchor,
          min(p.taxon) FILTER (WHERE p.taxon NOT IN ('','0')) taxon
        FROM read_parquet({quote(ctx.out / 'gene_products.parquet')}) p
        ANTI JOIN (SELECT record_id FROM read_parquet({records}) WHERE hub='entrez') e
          ON e.record_id='entrez:' || p.entrez_id GROUP BY p.entrez_id"""
    )
    n = ctx.copy(
        c, "SELECT * FROM ik_extra UNION ALL SELECT * FROM gene_extra", directory / "extras.parquet"
    )
    return dict(
        inchikey_extras=c.execute("SELECT count(*) FROM ik_extra").fetchone()[0],
        gene_extras=c.execute("SELECT count(*) FROM gene_extra").fetchone()[0],
        rows=n,
    )


def stage_entin(ctx, directory):
    """Shuffle records (plus the record-less extras) to the entity partition."""
    work = directory.parent
    c = ctx.connect("entin")
    records = quote(ctx.out / "records.parquet")
    extras = quote(work / "extras" / "extras.parquet")
    query = f"""SELECT substr(md5(entity_id),1,2) part,entity_id,record_id,hub,local_id,taxon,reviewed,
          decision,anchor,anchor_count,NULL::VARCHAR kind0 FROM read_parquet({records})
        UNION ALL SELECT substr(md5(entity_id),1,2),entity_id,NULL,NULL,NULL,taxon,false,'extra',anchor,
          CASE WHEN anchor IS NULL THEN 0 ELSE 1 END,kind0 FROM read_parquet({extras})"""
    ctx.copy_partitioned(c, query, directory / "entin", "part", "entin-")
    return {}


def stage_entities(ctx, directory):
    work = directory.parent
    entin = work / "entin" / "entin"
    c = ctx.connect("entities")
    chem_hubs = ", ".join(quote(h) for h in CHEMICAL)
    kinds = {}
    quarantined = 0
    for p in PARTS:
        if part_done(directory, p):
            continue
        sources = part_dirs(entin, [p])
        if not sources:
            continue
        c.execute(f"CREATE OR REPLACE TEMP TABLE m AS SELECT * FROM {files(*sources)}")
        ctx.copy(
            c,
            "SELECT entity_id,record_id FROM m WHERE record_id IS NOT NULL ORDER BY entity_id,record_id",
            ctx.out / "members" / f"part={p}" / "data.parquet",
            ", ROW_GROUP_SIZE 65536",
        )
        # kind: by entity id prefix, else by the hub of the (single) record behind a native id.
        ctx.copy(
            c,
            f"""SELECT entity_id,kind,CASE WHEN kind='gene' THEN NULL ELSE anchor END anchor,taxon,quarantined,
              preferred_record FROM (
              SELECT entity_id,
                coalesce(min(kind0),CASE WHEN starts_with(entity_id,'inchikey:') OR starts_with(entity_id,'goslin:') THEN 'chemical'
                  WHEN starts_with(entity_id,'uniprot:') THEN 'protein' WHEN starts_with(entity_id,'entrez:') THEN 'gene'
                  WHEN min(hub)='ramp_gene' THEN 'gene' WHEN min(hub) IN ({chem_hubs}) THEN 'chemical' END) kind,
                min(anchor) FILTER (WHERE anchor_count=1) anchor,
                min(taxon) FILTER (WHERE taxon IS NOT NULL AND taxon<>'0') taxon,
                coalesce(bool_or(decision='quarantined'),false) quarantined,
                arg_min(record_id,struct_pack(o:=record_id<>entity_id,r:={hub_rank()},l:=local_id)) FILTER (WHERE record_id IS NOT NULL) preferred_record
              FROM m GROUP BY entity_id) ORDER BY entity_id""",
            ctx.out / "entities" / f"part={p}" / "data.parquet",
            ", ROW_GROUP_SIZE 65536",
        )
        finish_part_loop(directory, p)
    ent = files(str(ctx.out / "entities" / "*" / "data.parquet"))
    kinds = dict(c.execute(f"SELECT kind,count(*) FROM {ent} GROUP BY 1 ORDER BY 1").fetchall())
    quarantined = c.execute(f"SELECT count(*) FROM {ent} WHERE quarantined").fetchone()[0]
    members = c.execute(f"SELECT count(*) FROM {files(str(ctx.out / 'members' / '*' / 'data.parquet'))}").fetchone()[0]
    return dict(entities=sum(kinds.values()), by_kind=kinds, quarantined=quarantined, members=members)
