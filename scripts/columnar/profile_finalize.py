"""Write shards, then finalize with every slow DuckDB statement timed.

    uv run python scripts/columnar/profile_finalize.py INPUTS TARGET --library-dir DIR --source S --dataset D
"""

import argparse
import json
import multiprocessing as mp
import time
from pathlib import Path

from omnipath_build.columnar.resolve import _write_shard


class TimedDB:
    """A DuckDB connection that logs statements slower than one second."""

    def __init__(self, db, log):
        self._db, self.log = db, log

    total = 0.0
    count = 0

    def execute(self, sql, *args, **kwargs):
        started = time.perf_counter()
        result = self._db.execute(sql, *args, **kwargs)
        seconds = time.perf_counter() - started
        TimedDB.total += seconds
        TimedDB.count += 1
        if seconds >= 1:
            self.log.append((round(seconds, 1), " ".join(str(sql).split())[:160]))
            print(f"{seconds:8.1f}s  {self.log[-1][1]}", flush=True)
        return result

    def __getattr__(self, name):
        return getattr(self._db, name)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("inputs", type=Path)
    parser.add_argument("target", type=Path)
    parser.add_argument("--library-dir", required=True)
    parser.add_argument("--source", required=True)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--shards", type=int, default=24)
    parser.add_argument("--memory", default="16GB")
    parser.add_argument("--threads", type=int, default=16)
    args = parser.parse_args()
    from omnipath_build.writer import ParquetWriter

    meta = json.loads((args.inputs / "meta.json").read_text())
    (args.inputs / "shards").mkdir(exist_ok=True)
    started = time.perf_counter()
    tasks = [(i, args.shards, str(args.inputs), args.source, args.dataset, meta["predicate"],
              args.library_dir, 20000) for i in range(args.shards)]
    with mp.get_context("spawn").Pool(args.shards) as pool:
        sealed = pool.map(_write_shard, tasks, chunksize=1)
    print(f"shards written in {time.perf_counter() - started:.1f}s", flush=True)
    args.target.mkdir(parents=True, exist_ok=True)
    writer = ParquetWriter(args.target, library_dir=args.library_dir, memory_limit=args.memory)
    writer.set_threads(args.threads)
    log = []
    writer._db = TimedDB(writer._db, log)
    started = time.perf_counter()
    for shard in sealed:
        writer.import_observation_shard(shard)
    print(f"imported in {time.perf_counter() - started:.1f}s", flush=True)
    paths = sorted((args.inputs / "resolution").glob("resolution_keys-*.parquet"))
    writer.resolution_summary(paths)
    import omnipath_build.complexes as complexes
    import omnipath_build.writer as writer_module

    phases = {}

    def timed(name, fn):
        def run(*a, **k):
            t, sql = time.perf_counter(), TimedDB.total
            try:
                return fn(*a, **k)
            finally:
                phases[name] = (round(time.perf_counter() - t, 1), round(TimedDB.total - sql, 1))
        return run

    writer_module.resolve_complexes = complexes.resolve_complexes = timed("resolve_complexes", complexes.resolve_complexes)
    for name in ("_reference_identifiers", "_prepare_display", "_write_tables"):
        setattr(writer, name, timed(name, getattr(writer, name)))
    TimedDB.total, TimedDB.count = 0.0, 0
    started = time.perf_counter()
    writer.close()
    total = time.perf_counter() - started
    print(f"close {total:.1f}s; all SQL {TimedDB.total:.1f}s in {TimedDB.count} statements; over 1s: {sum(s for s, _ in log):.1f}s")
    print("phases (wall, sql):", phases)
    for seconds, sql in sorted(log, reverse=True)[:25]:
        print(f"{seconds:8.1f}s  {sql}")


if __name__ == "__main__":
    main()
