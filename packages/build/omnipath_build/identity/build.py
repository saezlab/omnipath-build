"""Build one identity snapshot (spec section 3) from a directory of hub Parquet files."""

from __future__ import annotations

import concurrent.futures
import hashlib
import json
import os
import shutil
import time
from pathlib import Path

from . import access, entities, records
from .common import FORMAT, Context, log, rules_sha256


def hub_fingerprints(ctx: Context) -> dict:
    """sha256 and size of every hub file (cached by size and mtime across resumed runs)."""
    cache_file = ctx.work / "hubs.json"
    cache = json.loads(cache_file.read_text()) if cache_file.exists() else {}

    def one(item):
        name, path = item
        stat = path.stat()
        key = [str(path), stat.st_size, stat.st_mtime_ns]
        if cache.get(name, {}).get("key") == key:
            return name, cache[name]
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
                digest.update(chunk)
        return name, dict(key=key, sha256=digest.hexdigest(), bytes=stat.st_size)

    with concurrent.futures.ThreadPoolExecutor(max_workers=min(ctx.threads, 4)) as pool:
        result = dict(pool.map(one, sorted(ctx.hubs.items())))
    cache_file.write_text(json.dumps(result, indent=2))
    return {n: dict(sha256=v["sha256"], bytes=v["bytes"]) for n, v in result.items()}


def build_identity(
    hubs_dir,
    output_dir,
    memory="7GB",
    threads=6,
    goslin_cache=None,
    min_free_gib=50,
    drop_work=False,
):
    ctx = Context(hubs_dir, output_dir, memory, threads, goslin_cache, min_free_gib)
    started = time.monotonic()
    ctx.timings["fingerprint"] = 0.0
    t = time.monotonic()
    hubs = hub_fingerprints(ctx)
    rules = rules_sha256()
    fingerprint = hashlib.sha256(
        json.dumps(dict(format=FORMAT, hubs=hubs), sort_keys=True).encode()
    ).hexdigest()
    ctx.timings["fingerprint"] = round(time.monotonic() - t, 2)
    marker = ctx.work / "fingerprint.json"
    if marker.exists() and json.loads(marker.read_text())["fingerprint"] != fingerprint:
        raise RuntimeError("Hubs changed since this output began: use a fresh directory")
    marker.write_text(json.dumps(dict(fingerprint=fingerprint)))
    manifest_path = ctx.out / "manifest.json"
    if manifest_path.exists():
        done = json.loads(manifest_path.read_text())
        if done["fingerprint"] == fingerprint and done["rules_sha256"] == rules:
            log("identity_complete", output=str(ctx.out), reused=True)
            return done
    log("identity_start", hubs=sorted(ctx.hubs), memory=memory, threads=threads, fingerprint=fingerprint)

    w = ctx.work
    ctx.stage("rows", [w / "rows" / "rows"], records.stage_rows)
    ctx.stage("goslin", [w / "goslin" / "claims.parquet"], records.stage_goslin)
    ctx.stage("parts", [w / "parts" / "records0"], records.stage_parts)
    ctx.stage("assign", [w / "assign" / "assign.parquet"], records.stage_assign)
    ctx.stage("records", [ctx.out / "records.parquet"], records.stage_records)
    ctx.stage(
        "gene_products",
        [ctx.out / "gene_products.parquet", w / "gene_products" / "gp"],
        records.stage_gene_products,
    )
    ctx.stage("gene_aliases", [], records.stage_gene_aliases)
    ctx.stage("extras", [w / "extras" / "extras.parquet"], entities.stage_extras)
    ctx.stage("entin", [w / "entin" / "entin"], entities.stage_entin)
    ctx.stage("entities", [ctx.out / "entities", ctx.out / "members"], entities.stage_entities)
    ctx.stage("access_entity", [w / "access_entity" / "iso"], access.stage_access_entity)
    ctx.stage("access_isoforms", [], access.stage_access_isoforms)
    ctx.stage("access_genes", [ctx.out / "gene_labels"], access.stage_access_genes)
    ctx.stage("access_chemicals", [], access.stage_access_chemicals)
    ctx.stage("access_ramp", [], access.stage_access_ramp)
    ctx.stage("access", [ctx.out / "access"], access.stage_access)

    sizes = {}
    for name in ("records.parquet", "gene_products.parquet", "entities", "members", "record_rows", "access", "gene_labels"):
        path = ctx.out / name
        if path.is_file():
            sizes[name] = path.stat().st_size
        elif path.is_dir():
            sizes[name] = sum(f.stat().st_size for f in path.rglob("*.parquet"))
    manifest = dict(
        format=FORMAT,
        fingerprint=fingerprint,
        hubs=hubs,
        rules_sha256=rules,
        parameters=dict(memory=memory, threads=threads, goslin_cache=str(ctx.goslin_cache)),
        counts={k: v for k, v in ctx.info.items()},
        output_bytes=sizes,
        timings=dict(ctx.timings, total=round(time.monotonic() - started, 2)),
    )
    tmp = ctx.out / "manifest.json.tmp"
    tmp.write_text(json.dumps(manifest, indent=2))
    os.replace(tmp, manifest_path)
    if drop_work:
        shutil.rmtree(ctx.work, ignore_errors=True)
    log("identity_complete", output=str(ctx.out), seconds=manifest["timings"]["total"])
    return manifest
