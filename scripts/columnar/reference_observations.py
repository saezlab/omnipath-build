"""Dump the row path's observations (parse, map, SilverExtractor) as Parquet.

The reference for the columnar path: what the current pipeline hands to
resolution, without resolving. Rows are mapped in parallel worker processes;
each chunk writes its own files, so an entity seen in several chunks appears
once per chunk.

    uv run python scripts/columnar/reference_observations.py bindingdb interactions OUT \
        --workers 32 [--max-records N] [--param bindingdb_release=202605]
"""

from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import time
from itertools import islice
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

CHUNK = 5000


def _json(value):
    return None if value is None else json.dumps(value, sort_keys=True, default=str)


def _map_chunk(args):
    source, dataset, module, index, start, rows, out = args
    from omnipath_build.silver import SilverExtractor
    from omnipath_build.two_phase import load_mapper
    from omnipath_core.keys import canonical_json

    mapper = load_mapper(module, dataset)
    extractor = SilverExtractor(source, dataset)
    for offset, raw in enumerate(rows):
        number = start + offset
        record = mapper(raw)
        if record is not None:
            extractor.process_record(
                record, raw, f"{dataset}:{number}", number, payload_json=canonical_json(raw)
            )
    tables = {name: [] for name in TABLES}
    for key, e in extractor.entities.items():
        tables["entities"].append(
            dict(key=key, entity_type=e.entity_type, namespace=e.namespace,
                 identifier=e.identifier, taxon=e.taxon, identity_scope=e.identity_scope,
                 label=e.label, molecular_form=_json(e.molecular_form))
        )  # fmt: skip
        for ordinal, i in enumerate(e.identifiers):
            tables["entity_identifiers"].append(
                dict(key=key, ordinal=ordinal, ns=i["ns"], id=i["id"],
                     is_canonical=i["is_canonical"], source=i["source"])
            )  # fmt: skip
        for a in e.annotations:
            tables["entity_annotations"].append(
                dict(key=key, term=a["term"], value=a["value"], quantity=_json(a["quantity"]))
            )
        for v in e.evidence:
            tables["entity_evidence"].append(
                dict(key=key, row_id=v["row_id"], upstream_id=v["upstream_id"],
                     annotations=_json(v["annotations"]), molecular_form=_json(v["molecular_form"]))
            )  # fmt: skip
    for n, r in enumerate(extractor.relations):
        rid = f"{index}:{n}"
        tables["relations"].append(
            dict(rid=rid, relation_key=r.relation_key, statement_kind=r.statement_kind,
                 subject=r.subject_entity_key, predicate=r.predicate, object=r.object_entity_key,
                 row_id=r.row_id, upstream_id=r.upstream_id)
        )  # fmt: skip
        for ordinal, a in enumerate(r.annotations):
            tables["relation_annotations"].append(
                dict(rid=rid, ordinal=ordinal, term=a["term"], value=a["value"],
                     quantity=_json(a["quantity"]), scope=a.get("scope"))
            )  # fmt: skip
    for p in extractor.payloads:
        tables["payloads"].append(
            dict(relation_key=p.get("relation_key"), entity_key=p.get("entity_key"),
                 row_id=p["row_id"])
        )  # fmt: skip
    for name, rows_ in tables.items():
        if rows_:
            pq.write_table(pa.Table.from_pylist(rows_), Path(out) / name / f"{index:05d}.parquet")
    return len(rows)


TABLES = (
    "entities",
    "entity_identifiers",
    "entity_annotations",
    "entity_evidence",
    "relations",
    "relation_annotations",
    "payloads",
)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("source")
    parser.add_argument("dataset")
    parser.add_argument("out", type=Path)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--max-records", type=int)
    parser.add_argument("--param", action="append", default=[], help="raw() keyword, key=value")
    args = parser.parse_args()

    from omnipath_build.discovery import discover_datasets

    _, found, _ = discover_datasets(source=args.source, datasets=[args.dataset])
    ds = found[0]
    params = dict(p.split("=", 1) for p in args.param)
    for name in TABLES:
        (args.out / name).mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    rows = ds.raw_dataset.raw(
        source=args.source, dataset=args.dataset, max_records=args.max_records, **params
    )

    def chunks():
        index = start = 0
        iterator = iter(rows)
        while batch := list(islice(iterator, CHUNK)):
            yield (args.source, args.dataset, ds.qualified_module, index, start, batch, str(args.out))
            index, start = index + 1, start + len(batch)

    done = 0
    with mp.get_context("spawn").Pool(args.workers) as pool:
        for count in pool.imap_unordered(_map_chunk, chunks()):
            done += count
            if done % (CHUNK * 20) < CHUNK:
                print(f"{done:,} rows {time.perf_counter() - started:.0f}s", flush=True)
    elapsed = time.perf_counter() - started
    print(f"done {done:,} rows in {elapsed:.0f}s on {args.workers} workers")
    (args.out / "summary.json").write_text(
        json.dumps(dict(rows=done, seconds=elapsed, workers=args.workers), indent=2)
    )


if __name__ == "__main__":
    main()
