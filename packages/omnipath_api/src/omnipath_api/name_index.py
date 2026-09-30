"""Per-source preferred-label projections for legacy immutable entity Parquets.

New builds write the preferred name into label. Legacy sources get separate disposable
projections, one row per original row. Requests never build these projections.
"""

import argparse
from functools import lru_cache
from pathlib import Path
import tempfile
import json
import time

import pyarrow.parquet as pq
from omnipath_core.display_names import preferred_name_sql, name_rank_sql
from omnipath_api.serving_index import index_path, signature
from omnipath_api.store.connection import format_read_parquet, get_connection


@lru_cache(maxsize=512)
def _has_preferred_label(path, size, mtime):
    return (pq.read_metadata(path).metadata or {}).get(
        b"omnipath_label_policy"
    ) == b"preferred-name-v1"


def has_preferred_label(path):
    stat = Path(path).stat()
    return _has_preferred_label(str(path), stat.st_size, stat.st_mtime_ns)


def name_read(root, paths):
    """Compose selected sources at query time; nothing merged is persisted."""
    reads = []
    for path in paths:
        projection = index_path(root, "entity_labels", [path])
        if projection.is_file():
            reads.append(
                f"SELECT entity_key, label AS preferred_name FROM {format_read_parquet([str(projection)])}"
            )
        elif has_preferred_label(path):
            reads.append(
                f"SELECT entity_key, label AS preferred_name FROM {format_read_parquet([path])}"
            )
        else:
            reads.append(
                f"SELECT entity_key, {preferred_name_sql()} AS preferred_name FROM {format_read_parquet([path])}"
            )
    return "(" + " UNION ALL ".join(reads) + ")"


def lookup_names(engine, paths, keys):
    if not keys:
        return {}
    rows = engine._fetch_dicts(
        f"""SELECT entity_key,
        min({name_rank_sql("preferred_name")}) AS preferred
        FROM {name_read(engine.data_root, paths)}
        WHERE entity_key IN ({",".join("?" for _ in keys)})
          AND preferred_name IS NOT NULL AND preferred_name <> '' GROUP BY entity_key""",
        keys,
    )
    return {row["entity_key"]: row["preferred"]["name"] for row in rows}


def build_indexes(engine, threads=2):
    db = get_connection(memory_limit="2GB")
    db.execute("SET threads=?", [threads])
    db.execute("SET memory_limit='2GB'")
    temporary_root = Path(engine.data_root) / ".serving"
    temporary_root.mkdir(parents=True, exist_ok=True)
    work = tempfile.TemporaryDirectory(dir=temporary_root, prefix="name-build-")
    db.execute("SET temp_directory=?", [str(Path(work.name) / "spill")])
    try:
        for info in engine._selected_resource_infos(None):
            path = str(info["entities_path"])
            target = index_path(engine.data_root, "entity_labels", [path])
            if has_preferred_label(path) or target.is_file():
                continue
            started = time.monotonic()
            before = signature([path])
            target.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(dir=target.parent, prefix="build-") as temporary:
                staging = Path(temporary) / "names.parquet"
                legacy = index_path(engine.data_root, "entity_names", [path])
                query = (
                    f"SELECT entity_key, preferred_name AS label FROM {format_read_parquet([str(legacy)])}"
                    if legacy.is_file()
                    else f"SELECT entity_key, {preferred_name_sql()} AS label FROM {format_read_parquet([path])}"
                )
                db.execute(
                    f"COPY ({query}) TO ? (FORMAT PARQUET, COMPRESSION ZSTD)", [str(staging)]
                )
                if signature([path]) != before:
                    raise RuntimeError("Source changed during name projection build")
                staging.replace(target)
            print(
                json.dumps(
                    dict(
                        source=path,
                        bytes=target.stat().st_size,
                        seconds=round(time.monotonic() - started, 3),
                    )
                ),
                flush=True,
            )
    finally:
        db.close()
        work.cleanup()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", default="data")
    parser.add_argument("--threads", type=int, default=2)
    parser.add_argument("--release", default=None)
    args = parser.parse_args()
    from omnipath_api.engine import ParquetServingEngine

    engine = ParquetServingEngine(args.data_root)
    with engine.release_scope(args.release or engine.releases.default()):
        build_indexes(engine, args.threads)
