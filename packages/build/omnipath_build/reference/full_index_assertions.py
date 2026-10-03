"""Bulk reference assertions, partitioned first by entity, then by identifier."""

from __future__ import annotations

import json
import shutil
import time

from .full_index import log, quote


def stage_assertions(compiler):
    root = compiler.output
    ready = root / "assertions-checkpoint.json"
    if ready.exists():
        return json.loads(ready.read_text())
    start = time.perf_counter()
    target = root / "raw-assertions"
    if target.exists():
        shutil.rmtree(target)
    with compiler.connection("raw-assertions") as c:
        uniprot = compiler.relation(c, "gene_protein-uniprot-forward")
        native_protein = compiler.relation(c, "gene_protein-identity-forward")
        # Project isoforms exactly as the previous compiler: only when the
        # authoritative parent exists and has a single primary anchor.
        c.execute(f"""CREATE TEMP TABLE isoforms AS
            SELECT DISTINCT entity_id,regexp_replace(entity_id,'-[0-9]+$','') parent
            FROM (SELECT entity_id FROM {uniprot} UNION ALL SELECT entity_id FROM {native_protein})
            WHERE regexp_full_match(entity_id,'uniprot:[A-Z0-9]+-[0-9]+')""")
        records = compiler.relation(c, "records-uniprot")
        c.execute(f"""CREATE TEMP TABLE parents AS SELECT r.anchor
            FROM {records} r SEMI JOIN (SELECT DISTINCT parent FROM isoforms) p ON r.anchor=p.parent
            WHERE r.anchor_count=1""")
        c.execute(
            "CREATE TEMP TABLE projections AS SELECT i.entity_id isoform,i.parent FROM isoforms i JOIN parents p ON i.parent=p.anchor"
        )
        c.execute(
            f"COPY projections TO {quote(root / 'isoform-projections.parquet')} (FORMAT PARQUET,COMPRESSION ZSTD)"
        )
        parts = []

        def add(
            table,
            target_kind,
            route,
            namespace="namespace",
            identifier="identifier",
            entity="entity_id",
            tag="'regular'",
            where="true",
        ):
            source = compiler.relation(c, table)
            parts.append(
                f"SELECT {target_kind}::UTINYINT AS target,{route}::UTINYINT AS route,{namespace}::VARCHAR AS namespace,{identifier}::VARCHAR AS identifier,{entity}::VARCHAR AS entity_id,{tag}::VARCHAR AS tag FROM {source} WHERE ({where}) AND identifier IS NOT NULL"
            )

        chemical_ns = "('inchikey','inchi','chebi','chembl','pubchem','hmdb','kegg','cas','lipidmaps','swisslipids','drugbank','bigg','metanetx','goslin','refmet','ramp')"
        protein_ns = "('uniprot','uniprot-sec','uniprot_entry','ensg','enst','ensp','hgnc','refseq','refseq_protein','genbank','genesymbol','genesymbol-syn')"
        add("chemical-identity-forward", 1, 1, tag="'native'", where=f"namespace IN {chemical_ns}")
        add(
            "gene_protein-identity-forward",
            2,
            1,
            tag="'regular'",
            where=f"namespace IN {protein_ns}",
        )
        for hub in [
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
            "kegg",
        ]:
            name = "claims-" + hub
            if name not in compiler.source["tables"]:
                continue
            where = "namespace IN ('cas','drugbank','kegg')"
            if hub in {"chebi", "bigg"}:
                where += f" OR namespace='{hub}'"
            add(
                name,
                1,
                1,
                tag="CASE WHEN namespace IN ('kegg','bigg') THEN 'chemical_fallback' ELSE 'regular' END",
                where=where,
            )
        # Exact symbol assertions are preferred to synonyms within a taxon.
        exact = "('uniprot_entry','ensg','ensp','enst','hgnc','refseq','refseq_protein','genbank','genesymbol','genesymbol-syn')"
        add("gene_protein-uniprot-forward", 2, 1, where=f"namespace IN {exact}")
        add(
            "gene_protein-uniprot-forward",
            2,
            1,
            namespace="'genesymbol-syn'",
            where="namespace='genesymbol'",
        )
        for ns in ["genesymbol", "genesymbol-syn"]:
            add(
                "gene_protein-uniprot-forward",
                2,
                1,
                namespace=quote(ns),
                tag="'symbol_synonym'",
                where="namespace='genesymbol-syn'",
            )
        for ns in ["uniprot", "uniprot-sec"]:
            add(
                "gene_protein-uniprot-forward",
                2,
                1,
                namespace=quote(ns),
                where="namespace='uniprot-sec'",
            )
        # Only the unversioned accession form receives the expanded assertion.
        add(
            "gene_protein-uniprot-forward",
            2,
            1,
            identifier="regexp_replace(identifier,'\\.[0-9]+$','')",
            where="(namespace='refseq_protein' AND regexp_full_match(regexp_replace(identifier,'\\.[0-9]+$',''),'(AP|NP|XP|YP|WP|ZP)_[0-9]+')) OR (namespace='genbank' AND regexp_full_match(regexp_replace(identifier,'\\.[0-9]+$',''),'[A-Z]{3}[0-9]{5,}'))",
        )
        add("gene-resolution-forward", 2, 2, namespace="'entrez'")
        if "source-gene-resolution" in compiler.source["tables"]:
            add("source-gene-resolution", 2, 2)
        add(
            "gene-products-forward",
            2,
            3,
            namespace="'_product_entrez'",
            identifier="entrez_id",
            entity="protein_entity_id",
            tag="'product'",
            where="entrez_id IS NOT NULL",
        )
        add(
            "gene_protein-uniprot-forward",
            2,
            3,
            namespace="'_product_ensg'",
            tag="'product'",
            where="namespace='ensg'",
        )
        # Identifier is a SELECT alias in the product table, which does not have
        # a physical `identifier` column. Its explicit source check is sufficient.
        parts[-2] = parts[-2].replace(" AND identifier IS NOT NULL", "")
        union = " UNION ALL ".join(parts)
        query = f"""SELECT a.target,a.route,a.namespace,a.identifier,
            CASE WHEN a.tag='product' THEN a.entity_id ELSE COALESCE(p.parent,a.entity_id) END entity_id,
            a.tag,substr(md5(CASE WHEN a.tag='product' THEN a.entity_id ELSE COALESCE(p.parent,a.entity_id) END),1,2) part
            FROM ({union}) a LEFT JOIN projections p ON a.entity_id=p.isoform"""
        log("assertion_partitioning_started")
        c.execute("SET partitioned_write_max_open_files=256")
        rows = c.execute(
            f"COPY ({query}) TO {quote(target)} (FORMAT PARQUET,COMPRESSION ZSTD,PARTITION_BY(part),ROW_GROUP_SIZE 131072)"
        ).fetchone()[0]
    report = dict(rows=rows, seconds=time.perf_counter() - start)
    ready.write_text(json.dumps(report, indent=2))
    shutil.rmtree(root / "scratch/raw-assertions")
    log("assertion_partitioning_complete", **report)
    return report


def enrich_assertion_partition(compiler, part):
    """Read each entity's small metadata exactly once for its raw assertions."""
    root = compiler.output
    ready = root / "enriched-checkpoints" / f"{part}.json"
    if ready.exists():
        return json.loads(ready.read_text())
    if not (root / "entity-checkpoints" / f"{part}.json").exists():
        raise ValueError("Entity partition is not complete: " + part)
    if not (root / "assertions-checkpoint.json").exists():
        raise ValueError("Raw assertion stage is incomplete")
    for domain in ["chemical", "gene_protein"]:
        if not (root / "metadata" / f"{domain}-{part}.parquet").exists():
            raise ValueError("Entity metadata is not compiled for " + part)
    target = root / "enriched-assertions" / part
    target.parent.mkdir(exist_ok=True)
    if target.exists():
        shutil.rmtree(target)
    start = time.perf_counter()
    raw = root / "raw-assertions" / f"part={part}" / "*.parquet"
    metadata = [root / "metadata" / f"{d}-{part}.parquet" for d in ["chemical", "gene_protein"]]
    with compiler.connection("enrich-" + part) as c:
        if not raw.parent.exists():
            c.execute(
                "CREATE TEMP TABLE empty_raw(target UTINYINT,route UTINYINT,namespace VARCHAR,identifier VARCHAR,entity_id VARCHAR,tag VARCHAR)"
            )
            raw_source = "empty_raw"
        else:
            raw_source = f"read_parquet({quote(raw)})"
        meta = f"read_parquet([{','.join(quote(p) for p in metadata)}])"
        q = f"""SELECT a.target,a.route,a.namespace,a.identifier,a.entity_id,a.tag,
            m.id::UBIGINT AS id,m.kind,m.anchor,m.taxon,m.quarantined,m.reviewed,m.gene_ids,m.ensg_ids,
            substr(md5(a.identifier),1,2) shard
            FROM {raw_source} a LEFT JOIN {meta} m USING(entity_id)"""
        c.execute("SET partitioned_write_max_open_files=256")
        rows = c.execute(
            f"COPY ({q}) TO {quote(target)} (FORMAT PARQUET,COMPRESSION ZSTD,PARTITION_BY(shard),ROW_GROUP_SIZE 131072)"
        ).fetchone()[0]
    report = dict(part=part, rows=rows, seconds=time.perf_counter() - start)
    ready.parent.mkdir(exist_ok=True)
    ready.write_text(json.dumps(report, indent=2))
    shutil.rmtree(root / "scratch" / ("enrich-" + part))
    log("assertions_enriched", **report)
    return report
