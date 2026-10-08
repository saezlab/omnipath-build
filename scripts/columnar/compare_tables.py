"""Diff two resource versions' published tables row for row.

    uv run python scripts/columnar/compare_tables.py REFERENCE_DIR CANDIDATE_DIR [--threads N]

Each table is compared on every column with EXCEPT ALL in both directions.
"""

import argparse
from pathlib import Path

import duckdb

from omnipath_core.versioning import RESOURCE_FILES, SERVING_FILES


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("reference", type=Path)
    parser.add_argument("candidate", type=Path)
    parser.add_argument("--threads", type=int, default=16)
    parser.add_argument("--memory", default="64GB")
    args = parser.parse_args()
    db = duckdb.connect()
    db.execute(f"SET threads={args.threads}; SET memory_limit='{args.memory}'; SET preserve_insertion_order=false")
    differ = 0
    for name in [*RESOURCE_FILES, *SERVING_FILES]:
        a, b = args.reference / name, args.candidate / name
        if not a.exists() or not b.exists():
            print(f"{name:28s} missing: reference={a.exists()} candidate={b.exists()}")
            differ += 1
            continue
        ra, rb = (f"read_parquet('{p}')" for p in (a, b))
        cols_a = [r[0] for r in db.execute(f"DESCRIBE SELECT * FROM {ra}").fetchall()]
        cols_b = [r[0] for r in db.execute(f"DESCRIBE SELECT * FROM {rb}").fetchall()]
        if cols_a != cols_b:
            print(f"{name:28s} columns differ: {sorted(set(cols_a) ^ set(cols_b))}")
            differ += 1
            continue
        na = db.execute(f"SELECT count(*) FROM {ra}").fetchone()[0]
        nb = db.execute(f"SELECT count(*) FROM {rb}").fetchone()[0]
        only_a = db.execute(f"SELECT count(*) FROM (SELECT * FROM {ra} EXCEPT ALL SELECT * FROM {rb})").fetchone()[0]
        only_b = db.execute(f"SELECT count(*) FROM (SELECT * FROM {rb} EXCEPT ALL SELECT * FROM {ra})").fetchone()[0]
        print(f"{name:28s} reference={na:>12,} candidate={nb:>12,} only_reference={only_a:>10,} only_candidate={only_b:>10,}", flush=True)
        if only_a or only_b:
            differ += 1
            for side, (x, y) in (("ref", (ra, rb)), ("new", (rb, ra))):
                for row in db.execute(f"SELECT * FROM (SELECT * FROM {x} EXCEPT ALL SELECT * FROM {y}) LIMIT 2").fetchall():
                    print(f"    {side}: {str(row)[:400]}")
    print("identical" if not differ else f"{differ} table(s) differ")


if __name__ == "__main__":
    main()
