#!/usr/bin/env python3
"""Replay source-attributed stored raw payloads through existing input mappers.

Each distinct evidence bundle stays separate; results can be compared per bundle
and per original entity key without mixing identifiers from different rows.
"""

import argparse
import concurrent.futures
import hashlib
import json
import time
from pathlib import Path
from functools import lru_cache
import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from omnipath_resolver.observations import (
    NS as NS,
    CODES as CODES,
    key as key,
    flush_lipid_cache,
    observation_bundle,
)

QSCHEMA = pa.schema(
    [
        (n, pa.string())
        for n in (
            "input_id",
            "source",
            "entity_key",
            "entity_type",
            "namespace",
            "identifier",
        )
    ]
    + [("target", pa.uint64())]
)
OSCHEMA = pa.schema([(n, pa.string()) for n in ("source", "entity_key", "entity_type", "library")])
DSCHEMA = pa.schema(
    [
        (n, pa.string())
        for n in (
            "source",
            "entity_key",
            "row_id",
            "input_id",
            "smiles",
            "inchikey",
            "inchi",
            "status",
            "message",
            "rdkit_version",
            "inchi_version",
            "policy",
        )
    ]
)
VSCHEMA = pa.schema(
    [(n, pa.string()) for n in ("input_id", "ns", "identifier", "scope", "anchor")]
    + [
        ("target", pa.uint64()),
        ("route", pa.uint64()),
        ("ordinal", pa.uint64()),
        ("primary", pa.bool_()),
        ("lookup_key", pa.binary()),
    ]
)


def log(event, **kw):
    print(
        json.dumps({"time": time.strftime("%FT%TZ", time.gmtime()), "event": event, **kw}),
        flush=True,
    )


@lru_cache(maxsize=64)
def source_modules(source):
    """Use build discovery's resource aliases and dataset module locations."""
    from omnipath_build.discovery import discover_datasets

    _, datasets, _ = discover_datasets(source)
    return {d.dataset_name: d.qualified_module for d in datasets}


def preparation_fingerprint(source):
    import importlib
    from omnipath_resolver.canonical import match, policy, identifiers, structures
    from omnipath_resolver import observations as resolution_observations, contracts
    from omnipath_resolver import goslin, goslin_cache
    from omnipath_build import silver
    from omnipath_build.extract import observations

    paths = {Path(__file__).resolve()}
    for module in [
        match,
        policy,
        identifiers,
        structures,
        silver,
        observations,
        resolution_observations,
        contracts,
        goslin,
        goslin_cache,
    ]:
        paths.add(Path(module.__file__).resolve())
    for name in source_modules(source).values():
        module = importlib.import_module(name)
        path = Path(module.__file__).resolve()
        paths.add(path)
        paths.update(Path(p).resolve() for p in getattr(module, "preparation_inputs", lambda: [])())
        parser = path.parent / "parsers" / path.name
        if parser.is_file():
            paths.add(parser)
    hashes = sorted(hashlib.sha256(p.read_bytes()).hexdigest() for p in paths)
    from omnipath_resolver.goslin_cache import cache_fingerprint
    from omnipath_resolver.goslin import normalize

    return hashlib.sha256(
        json.dumps([hashes, structures.fingerprint(), cache_fingerprint(normalize)]).encode()
    ).hexdigest()


def extract(task):
    source, index, records, directory = task
    from omnipath_build.two_phase import load_mapper
    from omnipath_build.silver import SilverExtractor
    from omnipath_resolver.canonical.match import votes_for
    from omnipath_resolver.canonical.policy import get_policy

    queries = {}
    votes = {}
    observations = {}
    derivations = []
    for row_id, payload in records:
        ds = row_id.rsplit(":", 1)[0]
        raw = json.loads(payload)
        mapped = load_mapper(source_modules(source)[ds], ds)(raw)
        if mapped is None:
            continue
        ex = SilverExtractor(source, ds)
        ex.process_record(mapped, raw, row_id, 0)
        for eid, obs in ex.entities.items():
            policy = get_policy(obs.entity_type)
            observations[eid] = dict(
                source=source,
                entity_key=eid,
                entity_type=obs.entity_type,
                library=policy.library,
            )
            if policy.library is None:
                continue
            normalized, observed = votes_for(obs, policy)
            query, rows = observation_bundle(eid, obs, normalized, observed, policy.library, source)
            signature = [{k: v for k, v in row.items() if k != "lookup_key"} for row in rows]
            sig = hashlib.sha256(
                json.dumps([query, signature], sort_keys=True).encode()
            ).hexdigest()
            query["input_id"] = sig
            queries[sig] = query
            for row in rows:
                row["input_id"] = sig
            votes[sig] = rows
            derivations.extend(
                dict(source=source, entity_key=eid, row_id=row_id, input_id=sig, **d)
                for d in obs.structure_derivations
            )
    flush_lipid_cache()
    out = Path(directory)
    pq.write_table(
        pa.Table.from_pylist(derivations, schema=DSCHEMA),
        out / f"d-{index}.parquet",
        compression="zstd",
    )
    pq.write_table(
        pa.Table.from_pylist(list(queries.values()), schema=QSCHEMA),
        out / f"q-{index}.parquet",
        compression="zstd",
    )
    pq.write_table(
        pa.Table.from_pylist([v for vs in votes.values() for v in vs], schema=VSCHEMA),
        out / f"v-{index}.parquet",
        compression="zstd",
    )
    pq.write_table(
        pa.Table.from_pylist(list(observations.values()), schema=OSCHEMA),
        out / f"o-{index}.parquet",
        compression="zstd",
    )
    return len(records), len(queries)


def prepare(args):
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    for source in args.sources:
        dest = out / source
        dest.mkdir(exist_ok=True)
        parts = dest / "parts"
        parts.mkdir(exist_ok=True)
        if (dest / "prepared.json").exists():
            prior = json.loads((dest / "prepared.json").read_text())
            if (
                prior.get("preparation_fingerprint") != preparation_fingerprint(source)
                or prior["version"] != args.version
            ):
                raise RuntimeError(
                    "Prepared input code/version changed; choose a fresh output directory"
                )
            continue
        src = Path(args.data) / "resources" / source / args.version / "evidence_payloads.parquet"
        pending = set()
        seen = set()
        batch = []
        submitted = 0
        finished = 0
        start = time.monotonic()
        tick = start
        with concurrent.futures.ProcessPoolExecutor(max_workers=args.workers) as pool:

            def drain(block=False):
                nonlocal pending, finished, tick
                done, pending = concurrent.futures.wait(
                    pending,
                    timeout=1 if block else 0,
                    return_when=concurrent.futures.FIRST_COMPLETED,
                )
                for f in done:
                    finished += f.result()[0]
                if time.monotonic() - tick >= 10:
                    log(
                        "extract_progress",
                        source=source,
                        rows=finished,
                        unique_raw_rows=len(seen),
                        seconds=round(time.monotonic() - start),
                    )
                    tick = time.monotonic()

            for b in pq.ParquetFile(src, read_dictionary=["payload_json"]).iter_batches(
                batch_size=2048, columns=["row_id", "payload_json"]
            ):
                # Deduplicate on the narrow ID column before converting bulky
                # repeated payload strings to Python objects.
                selected = []
                for index, row_id in enumerate(b.column("row_id").to_pylist()):
                    if row_id not in seen:
                        seen.add(row_id)
                        selected.append(index)
                for r in b.take(pa.array(selected, type=pa.int64())).to_pylist():
                    batch.append((r["row_id"], r["payload_json"]))
                    if len(batch) == 2048:
                        while len(pending) >= args.workers * 2:
                            drain(True)
                        pending.add(pool.submit(extract, (source, submitted, batch, str(parts))))
                        batch = []
                        submitted += 1
                        drain()
                drain()
            if batch:
                pending.add(pool.submit(extract, (source, submitted, batch, str(parts))))
            while pending:
                drain(True)
        c = duckdb.connect()
        c.execute("SET memory_limit='4GB'")
        c.execute("SET threads=6")
        c.execute("SET preserve_insertion_order=false")
        c.execute(f"SET temp_directory='{dest}/spill'")
        for prefix, name, schema in [
            ("q", "queries", QSCHEMA),
            ("v", "votes", VSCHEMA),
            ("o", "observations", OSCHEMA),
            ("d", "structure-derivations", DSCHEMA),
        ]:
            if not any(parts.glob(f"{prefix}-*.parquet")):
                pq.write_table(
                    pa.Table.from_pylist([], schema=schema),
                    parts / f"{prefix}-empty.parquet",
                )
            c.execute(
                f"COPY (SELECT DISTINCT * FROM read_parquet('{parts}/{prefix}-*.parquet')) TO '{dest}/{name}.parquet' (FORMAT PARQUET,COMPRESSION ZSTD)"
            )
        summary = {
            "source": source,
            "preparation_fingerprint": preparation_fingerprint(source),
            "version": args.version,
            "payload_rows": len(seen),
            "entity_keys": c.execute(
                f"SELECT count(DISTINCT entity_key) FROM read_parquet('{dest}/queries.parquet')"
            ).fetchone()[0],
            "evidence_bundles": pq.ParquetFile(dest / "queries.parquet").metadata.num_rows,
            "seconds": time.monotonic() - start,
        }
        (dest / "prepared.json").write_text(json.dumps(summary, indent=2))
        log("extract_done", **summary)
        c.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--data", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--version", default="2026.9.8.1")
    p.add_argument("--sources", nargs="+", default=["brenda", "bindingdb"])
    p.add_argument("--workers", type=int, default=6)
    prepare(p.parse_args())
