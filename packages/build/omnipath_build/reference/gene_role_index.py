"""Immutable, independently supported gene lookups beside a compact base library.

Gene aliases are direct gene claims or claims from an explicitly, uniquely linked
product. Unlinked products never become gene candidates. The base indexes remain
unchanged; missing support uses their existing conservative policy.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import time

import duckdb

from .compact_index import atomic_json
from .full_index import Writer, log, partition, quote, read_value
from omnipath_resolver.gene_role_index import (
    component_identity,
    _component_identity,
    _verify_file as _verify_file,
    gene_roles_required as gene_roles_required,
    sha256,
    GeneRoleRuntime as GeneRoleRuntime,
)
from .replay_resources import key

FORMAT = "omnipath-gene-role-index-v1"
NAME = "gene-role-index"
GENE_NAMESPACES = frozenset({"ensg", "hgnc", "genesymbol", "genesymbol-syn", "enst", "refseq"})
ALIAS_NAMESPACES = frozenset({"ensg", "hgnc", "genesymbol", "genesymbol-syn"})
PARTS = tuple(f"{n:02x}" for n in range(256))


def assertion_query(reference):
    """Only supported gene roles, with product and gene organism agreement."""
    r = Path(reference)
    genes = quote(r / "gene_protein-entities/entities.parquet")

    def scan(relative, columns):
        path = r / relative
        if path.is_file():
            return "read_parquet(" + quote(path) + ")"
        return "(SELECT " + ",".join("NULL::VARCHAR " + c for c in columns) + " WHERE false)"

    claims = scan(
        "claims-uniprot/identifier_claims.parquet", ("entity_id", "namespace", "identifier")
    )
    products = scan(
        "gene-products/gene_products.parquet", ("protein_entity_id", "entrez_id", "taxon")
    )
    entrez = scan(
        "claims-entrez/identifier_claims.parquet", ("record_id", "namespace", "identifier")
    )
    return f"""WITH genes AS (
        SELECT entity_id,taxon FROM read_parquet({genes}) WHERE kind='gene' AND NOT quarantined
    ), admitted_products AS (
        SELECT entity_id,taxon FROM read_parquet({genes}) WHERE kind='protein' AND NOT quarantined
    ), links AS (
        SELECT protein_entity_id,min(entrez_id) gene_id,
            min(taxon) FILTER(WHERE taxon NOT IN ('','0')) taxon
        FROM {products} GROUP BY protein_entity_id
        HAVING count(DISTINCT entrez_id)=1
        AND count(DISTINCT taxon) FILTER(WHERE taxon NOT IN ('','0'))<=1
    ), assertions AS (
        SELECT c.record_id entity_id,c.namespace,c.identifier,g.taxon
        FROM {entrez} c JOIN genes g ON g.entity_id=c.record_id
        WHERE c.namespace IN ('ensg','enst','hgnc','genesymbol','genesymbol-syn')
        OR (c.namespace='refseq' AND regexp_full_match(c.identifier,'(NM|NR|XM|XR)_[0-9]+(\\.[0-9]+)?'))
        UNION ALL
        SELECT g.entity_id,c.namespace,c.identifier,
            coalesce(nullif(nullif(g.taxon,'0'),''),l.taxon,nullif(nullif(p.taxon,'0'),'')) taxon
        FROM {claims} c JOIN links l ON l.protein_entity_id=c.entity_id
        JOIN admitted_products p ON p.entity_id=c.entity_id
        JOIN genes g ON g.entity_id='entrez:' || l.gene_id
        WHERE c.namespace IN ('ensg','hgnc','genesymbol','genesymbol-syn')
        AND (g.taxon IS NULL OR g.taxon IN ('','0') OR l.taxon IS NULL OR g.taxon=l.taxon)
        AND (p.taxon IS NULL OR p.taxon IN ('','0') OR l.taxon IS NULL OR p.taxon=l.taxon)
        AND (p.taxon IS NULL OR p.taxon IN ('','0') OR g.taxon IS NULL OR g.taxon IN ('','0') OR p.taxon=g.taxon)
    ) SELECT entity_id,namespace,
        CASE WHEN namespace IN ('ensg','enst','refseq') THEN regexp_replace(identifier,'\\.[0-9]+$','') ELSE identifier END identifier,
        nullif(nullif(taxon,'0'),'') taxon
    FROM assertions WHERE identifier IS NOT NULL AND trim(identifier)<>''"""


def _guard(output, reserve):
    if shutil.disk_usage(output).free < reserve * 1024**3:
        raise RuntimeError("Gene component stopped at free-disk reserve")


def _connection(output, memory, threads):
    c = duckdb.connect()
    c.execute("SET memory_limit=?", [memory])
    c.execute(f"SET threads={int(threads)}")
    c.execute("SET preserve_insertion_order=false")
    spill = output / "spill"
    spill.mkdir(exist_ok=True)
    c.execute("SET temp_directory=" + quote(spill))
    c.execute("SET max_temp_directory_size='32GiB'")
    c.execute("SET partitioned_write_max_open_files=8")
    return c


def _stage(output, name, query, memory, threads, reserve):
    started = time.perf_counter()
    log("gene_role_stage_started", stage=name)
    ready = output / (name + ".json")
    target = output / name
    if ready.exists():
        info = json.loads(ready.read_text())
        actual = {str(p.relative_to(output)): p.stat().st_size for p in target.rglob("*.parquet")}
        if actual != info["files"]:
            raise ValueError("Gene component assertion checkpoint changed")
        log(
            "gene_role_stage_complete",
            stage=name,
            resumed=True,
            rows=info["rows"],
            bytes=sum(actual.values()),
            seconds=time.perf_counter() - started,
        )
        return info
    _guard(output, reserve)
    if target.exists():
        shutil.rmtree(target)
    with _connection(output, memory, threads) as c:
        rows = c.execute(
            f"COPY ({query}) TO {quote(target)} "
            "(FORMAT PARQUET, COMPRESSION ZSTD, PARTITION_BY(part), ROW_GROUP_SIZE 65536)"
        ).fetchone()[0]
    info = dict(
        rows=rows,
        files={str(p.relative_to(output)): p.stat().st_size for p in target.rglob("*.parquet")},
    )
    atomic_json(ready, info)
    log(
        "gene_role_stage_complete",
        stage=name,
        resumed=False,
        rows=rows,
        bytes=sum(info["files"].values()),
        seconds=time.perf_counter() - started,
    )
    return info


def _gene(base, entity_id):
    raw = base.entities.get(partition(entity_id), entity_id.encode())
    if raw is None:
        raise ValueError("Supported gene absent from base: " + entity_id)
    obj = base.codecs["entities"].decode(raw) if base.codecs else json.loads(raw)
    m = obj["meta"]
    if m["kind"] != 3 or m["quarantined"]:
        raise ValueError("Unsupported gene metadata: " + entity_id)
    return [m["id"], entity_id, 3, m["anchor"], False, m["reviewed"], [entity_id[7:]]]


def _partition(output, part, kind, base, memory, threads, reserve):
    started = time.perf_counter()
    checkpoint = output / "checkpoints" / kind / (part + ".json")
    target = output / kind / part
    if checkpoint.exists():
        cp = json.loads(checkpoint.read_text())
        if (
            cp.get("exact_round_trip_records") != cp["records"]
            or sha256(target / "data.mdb") != cp["sha256"]
        ):
            raise ValueError("Gene component partition checkpoint changed")
        log(
            "gene_role_partition_complete",
            kind=kind,
            part=part,
            resumed=True,
            records=cp["records"],
            bytes=cp["bytes"],
            seconds=time.perf_counter() - started,
        )
        return cp
    _guard(output, reserve)
    staging = output / kind / ("." + part + ".tmp")
    for path in (staging, target):
        if path.exists():
            shutil.rmtree(path)
    writer = Writer(staging)
    from omnipath_resolver.index import Shards

    alias_postings = Shards(output / "identifiers") if kind == "records" else None
    source = output / ("assertions" if kind == "identifiers" else "aliases") / ("part=" + part)
    rejected, written, round_trips = 0, 0, 0

    def put_checked(batch):
        nonlocal round_trips
        writer.put(batch)
        # Read the committed storage envelope, including long keys/chunks.
        # Writer.close synchronizes these verified commits before publication.
        with writer.env.begin() as txn:
            for lookup, expected in batch:
                if read_value(txn, lookup) != expected:
                    raise ValueError("Gene component exact round-trip mismatch")
                round_trips += 1

    try:
        if source.exists():
            with _connection(output, memory, threads) as c:
                src = f"read_parquet({quote(source / '*.parquet')},hive_partitioning=false)"
                # A complete lookup key/record stays within one group. Sorted
                # rows stream to Python; no whole-partition LIST aggregates.
                if kind == "identifiers":
                    eligible = f"""(SELECT * EXCLUDE(preferred_synonym) FROM (
                        SELECT *,min(is_synonym) OVER(PARTITION BY namespace,identifier,taxon) preferred_synonym
                        FROM {src}) WHERE is_synonym=preferred_synonym)"""
                    query = f"""SELECT DISTINCT namespace,identifier,lookup_scope,entity_id FROM (
                        SELECT namespace,identifier,'' AS lookup_scope,entity_id FROM {eligible}
                        WHERE namespace NOT IN ('genesymbol','genesymbol-syn')
                        UNION ALL SELECT namespace,identifier,taxon AS lookup_scope,entity_id FROM {eligible} WHERE taxon IS NOT NULL)
                        ORDER BY namespace,identifier,lookup_scope,entity_id"""
                else:
                    query = f"SELECT DISTINCT entity_id,namespace,identifier,taxon FROM {src} ORDER BY entity_id,namespace,identifier,taxon"
                active, values = None, []
                batch = []

                def flush():
                    nonlocal rejected, written, batch
                    if active is None:
                        return
                    if kind == "identifiers":
                        ns, ident, scope = active
                        # Cap distinct supported genes, while retaining a durable
                        # empty posting for over-limit support (no base fallback).
                        if len(values) > 10:
                            rejected += 1
                        obj = dict(
                            gene=True,
                            products=False,
                            candidates=sorted(
                                (_gene(base, eid) for eid in values), key=lambda fact: fact[0]
                            )
                            if len(values) <= 10
                            else [],
                        )
                        lookup = key(2, 1, ns, scope, ident)
                        row = (lookup, json.dumps(obj, separators=(",", ":")).encode())
                    else:
                        row = (
                            active.encode(),
                            json.dumps({"identifiers": values}, separators=(",", ":")).encode(),
                        )
                    batch.append(row)
                    written += 1
                    if len(batch) >= 4096:
                        _guard(output, reserve)
                        put_checked(batch)
                        batch = []

                for arrow in c.execute(query).to_arrow_reader(batch_size=8192):
                    for row in zip(
                        *(arrow.column(i).to_pylist() for i in range(arrow.num_columns))
                    ):
                        group, value = (
                            (row[:3], row[3]) if kind == "identifiers" else (row[0], list(row[1:]))
                        )
                        if group != active:
                            flush()
                            active, values = group, []
                        # An oversized posting only needs eleven genes to prove
                        # rejection; never hold enormous candidate lists in RAM.
                        if kind == "identifiers":
                            if len(values) < 11:
                                values.append(value)
                        else:
                            # Only restore an alias when this gene survives the
                            # corresponding supported posting and ambiguity cap.
                            ns, ident, taxon = row[1:]
                            scope = (taxon or "") if ns.startswith("genesymbol") else ""
                            raw = alias_postings.get(partition(ident), key(2, 1, ns, scope, ident))
                            posting = json.loads(raw) if raw else {"candidates": []}
                            if (
                                any(c[1] == group for c in posting["candidates"])
                                and list(row[1:3]) not in values
                            ):
                                values.append(list(row[1:3]))
                flush()
                if batch:
                    put_checked(batch)
        cp = writer.close()
    except BaseException:
        writer.env.close()
        raise
    finally:
        if alias_postings is not None:
            alias_postings.close()
    staging.rename(target)
    cp.update(
        sha256=sha256(target / "data.mdb"),
        rejected_gene_keys=rejected,
        exact_records=written,
        exact_round_trip_records=round_trips,
    )
    if written != cp["records"] or written != round_trips:
        raise ValueError("Gene component record count changed")
    atomic_json(checkpoint, cp)
    log(
        "gene_role_partition_complete",
        kind=kind,
        part=part,
        resumed=False,
        records=cp["records"],
        bytes=cp["bytes"],
        seconds=time.perf_counter() - started,
    )
    return cp


def build_gene_role_index(
    reference, base, *, output=None, memory="3GB", threads=1, min_free_gib=40
):
    """Build/resume a component, then atomically publish it beside this exact base."""
    from omnipath_resolver.index import FullRuntime

    reference, base = Path(reference).resolve(), Path(base).resolve()
    final = Path(output).absolute() if output else base / NAME
    if final != base / NAME:
        raise ValueError("Gene component must be published beside its exact base library")
    source_paths = [
        reference / p
        for p in (
            "manifest.json",
            "gene_protein-entities/entities.parquet",
            "claims-uniprot/identifier_claims.parquet",
            "claims-entrez/identifier_claims.parquet",
            "gene-products/gene_products.parquet",
        )
        if (reference / p).is_file()
    ]
    manifest = json.loads((base / "manifest.json").read_text())
    assigned = json.loads(source_paths[0].read_text())
    if not manifest.get("complete") or assigned.get("status") != "complete":
        raise ValueError("Complete base and assigned reference required")
    if manifest["reference_fingerprint"] != assigned["fingerprint"]:
        raise ValueError("Base and assigned reference fingerprints disagree")
    sources = {
        str(p.relative_to(reference)): dict(bytes=p.stat().st_size, sha256=sha256(p))
        for p in source_paths
    }
    contract = dict(
        format=FORMAT,
        base_manifest_sha256=sha256(base / "manifest.json"),
        reference_fingerprint=assigned["fingerprint"],
        sources=sources,
        builder_sha256=sha256(Path(__file__)),
        partitions=256,
        candidate_limit=10,
    )
    if final.exists():
        published = json.loads((final / "manifest.json").read_text())
        if any(published.get(k) != value for k, value in contract.items()):
            raise ValueError("Published gene component differs from requested build contract")
        return component_identity(base, required=True)
    staging = base / ("." + NAME + ".building")
    staging.mkdir(exist_ok=True)
    previous = staging / "contract.json"
    if previous.exists() and json.loads(previous.read_text()) != contract:
        raise ValueError("Cannot resume a different gene component")
    atomic_json(previous, contract)
    if (staging / "manifest.json").is_file():
        # Publication interrupted after completed partition proof. Temporary
        # assertion files may already have been retired; no rescan is needed.
        _component_identity(base, staging, required=True)
        _publish(staging, final)
        return component_identity(base, required=True)
    query = assertion_query(reference)
    expanded = f"""SELECT a.entity_id,n.namespace,a.namespace original_namespace,a.identifier,a.taxon,
        a.namespace='genesymbol-syn' is_synonym
        FROM ({query}) a CROSS JOIN LATERAL (
            SELECT a.namespace namespace WHERE a.namespace NOT IN ('genesymbol','genesymbol-syn')
            UNION ALL SELECT unnest(['genesymbol','genesymbol-syn']) WHERE a.namespace IN ('genesymbol','genesymbol-syn')
        ) n"""
    _stage(
        staging,
        "assertions",
        f"SELECT *,substr(md5(identifier),1,2) part FROM ({expanded})",
        memory,
        threads,
        min_free_gib,
    )
    files = sorted((staging / "assertions").glob("part=*/*.parquet"))
    aliases = "original_namespace IN ('ensg','hgnc','genesymbol','genesymbol-syn')"
    if files:
        src = "read_parquet([" + ",".join(quote(p) for p in files) + "],hive_partitioning=false)"
        _stage(
            staging,
            "aliases",
            f"SELECT entity_id,original_namespace namespace,identifier,taxon,substr(md5(entity_id),1,2) part FROM {src} WHERE {aliases}",
            memory,
            threads,
            min_free_gib,
        )
    else:
        started = time.perf_counter()
        log("gene_role_stage_started", stage="aliases")
        (staging / "aliases").mkdir(exist_ok=True)
        log(
            "gene_role_stage_complete",
            stage="aliases",
            resumed=False,
            rows=0,
            bytes=0,
            seconds=time.perf_counter() - started,
        )
    reader = FullRuntime(base, _load_gene_roles=False)
    cps = {}
    try:
        for kind in ("identifiers", "records"):
            for part in PARTS:
                cps[kind, part] = _partition(
                    staging, part, kind, reader, memory, threads, min_free_gib
                )
    finally:
        reader.close()
    if contract["base_manifest_sha256"] != sha256(base / "manifest.json"):
        raise ValueError("Base changed during gene component build")
    for p in source_paths:
        if sources[str(p.relative_to(reference))]["sha256"] != sha256(p):
            raise ValueError("Source changed during gene component build")
    component = dict(
        contract,
        complete=True,
        files={
            f"{kind}/{part}/data.mdb": dict(bytes=cp["bytes"], sha256=cp["sha256"])
            for (kind, part), cp in cps.items()
        },
        counts={
            kind: sum(cp["records"] for (k, _), cp in cps.items() if k == kind)
            for kind in ("identifiers", "records")
        },
        rejected_gene_keys=sum(cp["rejected_gene_keys"] for cp in cps.values()),
    )
    atomic_json(staging / "manifest.json", component)
    _publish(staging, final)
    return component_identity(base, required=True)


def _publish(staging, final):
    for name in ("assertions", "aliases", "spill"):
        if (staging / name).exists():
            shutil.rmtree(staging / name)
    staging.rename(final)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--memory", default="3GB")
    parser.add_argument("--threads", type=int, default=1)
    parser.add_argument("--min-free-gib", type=float, default=40)
    args = parser.parse_args()
    print(
        json.dumps(
            build_gene_role_index(
                args.reference,
                args.base,
                memory=args.memory,
                threads=args.threads,
                min_free_gib=args.min_free_gib,
            ),
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
