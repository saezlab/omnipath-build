"""Build a relation dataset's published tables with the columnar executor.

    uv run python scripts/columnar/build_relations.py bindingdb interactions WORK TARGET \
        --library-dir DIR [--param bindingdb_release=202610] [--stop-after observations]

Stages: parse (SQL table parser) -> observations (distinct evaluation) -> resolve
(each distinct entity once) -> write (parallel shards) -> finalize (row path's SQL).
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import duckdb

from omnipath_build.columnar.raw import add_payloads, load_raw
from omnipath_build.columnar.relations import RelationExecutor
from omnipath_build.columnar.resolve import export_inputs, resolve_distinct, write_resource


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("source")
    parser.add_argument("dataset")
    parser.add_argument("work", type=Path)
    parser.add_argument("target", type=Path)
    parser.add_argument("--library-dir", type=Path)
    parser.add_argument("--workers", type=int, default=24)
    parser.add_argument("--threads", type=int, default=16)
    parser.add_argument("--param", action="append", default=[])
    parser.add_argument("--stop-after", choices=("observations", "resolve"))
    args = parser.parse_args()

    from omnipath_build.discovery import discover_datasets
    from omnipath_build.two_phase import load_mapper

    args.work.mkdir(parents=True, exist_ok=True)
    timings = {}
    clock = time.perf_counter()

    def lap(name):
        nonlocal clock
        now = time.perf_counter()
        timings[name] = round(now - clock, 1)
        clock = now
        print(f"{name:14s} {timings[name]:8.1f}s", flush=True)

    _, found, _ = discover_datasets(source=args.source, datasets=[args.dataset])
    ds = found[0]
    params = dict(p.split("=", 1) for p in args.param)
    inputs = args.work / "inputs"
    if not (inputs / "rows.parquet").exists():
        db = duckdb.connect(str(args.work / "columnar.duckdb"))
        db.execute(f"SET threads={args.threads}; SET preserve_insertion_order=false")
        if ds.raw_dataset.has_table:
            rows = ds.raw_dataset.table(db, "raw", source=args.source, dataset=args.dataset, **params)
            add_payloads(db, "raw")
            columns = [r[0] for r in db.execute("DESCRIBE raw").fetchall() if r[0] not in ("rid", "payload_json")]
            raw_info = dict(rows=rows, columns=columns, json_columns=[])
        else:
            raw_info = load_raw(db, ds.raw_dataset.raw(source=args.source, dataset=args.dataset, **params))
        lap("parse")
        mapper = load_mapper(ds.qualified_module, args.dataset)
        executor = RelationExecutor(db, args.source, args.dataset, mapper, raw_info, workers=args.workers)
        predicate = executor.run()
        export_inputs(db, inputs, args.dataset, predicate)
        (inputs / "meta.json").write_text(json.dumps(dict(predicate=predicate, rows=raw_info["rows"])))
        db.close()
        lap("observations")
    meta = json.loads((inputs / "meta.json").read_text())
    if args.stop_after == "observations":
        return
    done = inputs / "resolution" / "done"
    if done.exists():
        paths = sorted((inputs / "resolution").glob("resolution_keys-*.parquet"))
    else:
        paths, _, distinct = resolve_distinct(inputs, args.source, args.library_dir, workers=args.workers)
        done.write_text(str(distinct))
        print(f"resolved {distinct:,} distinct entity observations", flush=True)
        lap("resolve")
    if args.stop_after == "resolve":
        return
    target = args.target / "resources" / args.source / "columnar"
    outputs, parts = write_resource(
        inputs, target, args.source, args.dataset, meta["predicate"], args.library_dir, paths,
        shards=args.workers,
    )
    timings.update({k: round(v, 1) for k, v in parts.items()})
    lap("write+finalize")
    print(json.dumps(dict(rows=meta["rows"], tables=outputs["rows"], timings=timings), indent=2))
    (args.work / "timings.json").write_text(json.dumps(timings, indent=2))


if __name__ == "__main__":
    main()
