"""Build a dataset's raw table with its SQL parser and diff it with the row parser's.

    uv run python scripts/columnar/compare_raw.py bindingdb interactions ROW_DB OUT_DB [--param k=v]

ROW_DB is a run_relations.py database whose ``raw`` table came from the row parser.
"""

import argparse
import time

import duckdb

from omnipath_build.columnar.raw import add_payloads
from omnipath_build.discovery import discover_datasets


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("source")
    parser.add_argument("dataset")
    parser.add_argument("row_db")
    parser.add_argument("out_db")
    parser.add_argument("--threads", type=int, default=16)
    parser.add_argument("--param", action="append", default=[])
    args = parser.parse_args()
    _, found, _ = discover_datasets(source=args.source, datasets=[args.dataset])
    ds = found[0].raw_dataset
    params = dict(p.split("=", 1) for p in args.param)
    db = duckdb.connect(args.out_db)
    db.execute(f"SET threads={args.threads}")
    started = time.perf_counter()
    n = ds.table(db, "raw", source=args.source, dataset=args.dataset, **params)
    parsed = time.perf_counter() - started
    add_payloads(db, "raw")
    total = time.perf_counter() - started
    print(f"sql parse: {n:,} rows in {parsed:.1f}s, with payloads {total:.1f}s")
    db.execute(f"ATTACH '{args.row_db}' AS rowdb (READ_ONLY)")
    new = {r[0] for r in db.execute("DESCRIBE raw").fetchall()}
    old = {r[0] for r in db.execute("DESCRIBE rowdb.raw").fetchall()}
    print("columns only in row parser:", sorted(old - new)[:10], "only in sql:", sorted(new - old)[:10])
    bad = 0
    for column in sorted(new & old):
        c = '"' + column.replace('"', '""') + '"'
        diff = db.execute(
            f"SELECT count(*) FROM raw FULL JOIN rowdb.raw o USING (rid) WHERE raw.{c} IS DISTINCT FROM o.{c}"
        ).fetchone()[0]
        if diff:
            bad += 1
            sample = db.execute(
                f"SELECT rid, raw.{c}, o.{c} FROM raw FULL JOIN rowdb.raw o USING (rid) WHERE raw.{c} IS DISTINCT FROM o.{c} LIMIT 3"
            ).fetchall()
            print(f"  {column}: {diff:,} rows differ, e.g. {[tuple(str(v)[:60] for v in s) for s in sample]}")
    print(f"{len(new & old)} common columns, {bad} differ")


if __name__ == "__main__":
    main()
