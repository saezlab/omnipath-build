"""LMDB point-lookup store of one hub index (spec 3a): ``<index dir>/kv/``.

The Parquet index answers a batch of point lookups by scanning most of its 256 partitions; the
runtime reads this store instead. The Parquet files stay the build artifact (and are not
touched); the layout is described in ``omnipath_resolver.identity_kv``:

    id.<s>/   db ``id``:  utf8(ns) + 0x00 + utf8(identifier) -> [[local_id, tag], ...]
    rec.<s>/  db ``rec``: utf8(local_id) -> [taxon, anchor, anchor_count, reviewed, [[type, value], ...]]
    manifest.json

Rows are streamed from DuckDB in key order (``ORDER BY``; VARCHAR order is bytewise, the order of
LMDB's default comparison) and appended with ``putmulti(append=True)``. Large hubs are split into
16 shards by the first hex digit of the partition hash, which keeps every sort 16 times smaller
and lets shards build in parallel processes. This module does not touch ``hubindex.py``, whose
code hash names the existing indexes.
"""

from __future__ import annotations

import json
import multiprocessing
import shutil
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import duckdb
import lmdb

from omnipath_resolver.identity_kv import (
    HUB_KV_FORMAT,
    KV_DIR,
    MANIFEST,
    MAX_KEY,
    pack,
    sha256_file,
)

from .common import log

# A hub whose by_id (by_record) holds more rows than this gets 16 id (rec) shards.
SHARD_ROWS = 30_000_000
PARTS = [f"{n:02x}" for n in range(256)]
MAP_SIZE = 64 * 1024**3  # virtual; the file grows with the data, and doubles on MapFullError
BATCH_ITEMS = 200_000
BATCH_BYTES = 64 * 1024**2
ARROW_ROWS = 100_000


def quote(value) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def find_index(hub_index_root, hub) -> Path:
    """The newest ``<root>/<hub>/<sha12>/`` directory holding a finished index."""
    candidates = sorted(
        (Path(hub_index_root) / hub).glob("*/manifest.json"), key=lambda p: p.stat().st_mtime
    )
    if not candidates:
        raise FileNotFoundError(f"No finished hub index for {hub} under {hub_index_root}")
    return candidates[-1].parent


def _source_files(directory: Path, relative: str, parts) -> list[str]:
    out = []
    for part in parts:
        if (directory / relative / f"part={part}").is_dir():
            out.append(str(directory / relative / f"part={part}" / "*.parquet"))
    return out


def _parquet(patterns) -> str:
    return "read_parquet([" + ",".join(quote(p) for p in patterns) + "],hive_partitioning=false)"


def _parts(shard: int, shards: int) -> list[str]:
    return PARTS if shards == 1 else [p for p in PARTS if int(p[0], 16) == shard]


def _row_count(directory: Path, name: str, default: int) -> int:
    try:
        counts = json.loads((directory / "manifest.json").read_text()).get("counts", {})
    except (OSError, ValueError):
        return default
    if name == "by_id":
        return int((counts.get("by_id") or {}).get("rows", default))
    return int((counts.get("final") or {}).get("by_record", default))


# ------------------------------------------------------------------------- writing


class Writer:
    """Append-only writer of one LMDB database; keys must arrive in strictly increasing order."""

    def __init__(self, path: Path, name: str, min_free: int):
        path.mkdir(parents=True, exist_ok=True)
        self.path, self.min_free = path, min_free
        self.env = lmdb.open(
            str(path),
            map_size=MAP_SIZE,
            max_dbs=2,
            writemap=False,
            sync=False,
            metasync=False,
            max_readers=1,
        )
        self.db = self.env.open_db(name.encode())
        self.max_key = min(MAX_KEY, self.env.max_key_size())
        self.items, self.bytes, self.last = [], 0, b""
        self.keys = self.value_bytes = self.skipped = 0

    def add(self, key: bytes, value) -> None:
        if len(key) > self.max_key:
            self.skipped += 1
            return
        if key <= self.last:
            raise AssertionError(
                f"Rows are not in LMDB key order at {key[:60]!r} after {self.last[:60]!r}"
            )
        self.last = key
        raw = pack(value)
        self.items.append((key, raw))
        self.bytes += len(key) + len(raw)
        self.keys += 1
        self.value_bytes += len(raw)
        if len(self.items) >= BATCH_ITEMS or self.bytes >= BATCH_BYTES:
            self.flush()

    def flush(self) -> None:
        if not self.items:
            return
        if shutil.disk_usage(self.path).free < self.min_free:
            raise RuntimeError("Hub kv build stopped at the free-disk reserve")
        while True:
            try:
                with self.env.begin(db=self.db, write=True) as txn:
                    consumed, added = txn.cursor(self.db).putmulti(
                        self.items, dupdata=False, append=True
                    )
                if added != len(self.items):
                    raise AssertionError("putmulti stored fewer keys than it was given")
                break
            except lmdb.MapFullError:
                self.env.set_mapsize(self.env.info()["map_size"] * 2)
        self.items, self.bytes = [], 0

    def close(self) -> dict:
        self.flush()
        self.env.sync(True)
        with self.env.begin() as txn:
            entries = txn.stat(self.db)["entries"]
        self.env.close()
        if entries != self.keys:
            raise AssertionError(f"{self.path} holds {entries} keys, {self.keys} were written")
        return dict(
            keys=self.keys,
            value_bytes=self.value_bytes,
            skipped_long_keys=self.skipped,
            file_bytes=(self.path / "data.mdb").stat().st_size,
        )


def _connect(spill: Path, memory: str, threads: int):
    spill.mkdir(parents=True, exist_ok=True)
    c = duckdb.connect()
    c.execute(f"SET memory_limit={quote(memory)}")
    c.execute(f"SET threads={int(threads)}")
    c.execute("SET preserve_insertion_order=false")
    c.execute(f"SET temp_directory={quote(spill)}")
    c.execute("SET max_temp_directory_size='200GiB'")
    return c


def _batches(c, sql: str):
    result = c.execute(sql)
    reader = (
        result.to_arrow_reader(ARROW_ROWS)
        if hasattr(result, "to_arrow_reader")
        else result.fetch_record_batch(ARROW_ROWS)
    )
    for batch in reader:
        yield [column.to_pylist() for column in batch.columns]


def build_id_shard(directory: Path, target: Path, shard: int, shards: int, memory, threads, min_free):
    """Group the shard's by_id rows by (ns, identifier) in key order and write them."""
    files = _source_files(directory, "by_id", _parts(shard, shards))
    writer = Writer(target, "id", min_free)
    rows = 0
    if files:
        c = _connect(target.parent / "spill" / target.name, memory, threads)
        sql = (
            f"SELECT ns,identifier,local_id,tag FROM {_parquet(files)} "
            "ORDER BY ns,identifier,local_id,tag"
        )
        key, group = None, []
        for ns, identifier, local_id, tag in (r for b in _batches(c, sql) for r in zip(*b)):
            rows += 1
            if (ns, identifier) != key:
                if key is not None:
                    writer.add(key[0].encode() + b"\0" + key[1].encode(), group)
                key, group = (ns, identifier), []
            group.append([local_id, tag])
        if key is not None:
            writer.add(key[0].encode() + b"\0" + key[1].encode(), group)
        c.close()
    return dict(writer.close(), rows=rows)


def build_rec_shard(directory: Path, target: Path, shard: int, shards: int, memory, threads, min_free):
    """One value per record: its records.parquet row plus all of its by_record rows."""
    files = _source_files(directory, "by_record", _parts(shard, shards))
    writer = Writer(target, "rec", min_free)
    c = _connect(target.parent / "spill" / target.name, memory, threads)
    where = "" if shards == 1 else f"WHERE substr(md5(record_id),1,1)={quote(f'{shard:x}')}"
    c.execute(
        f"""CREATE TEMP TABLE r AS SELECT local_id,taxon,anchor,anchor_count,reviewed
        FROM read_parquet({quote(directory / 'records.parquet')}) {where}"""
    )
    source = (
        _parquet(files)
        if files
        else "(SELECT NULL::VARCHAR AS local_id,NULL::VARCHAR AS source_type,NULL::VARCHAR AS value WHERE false)"
    )
    sql = f"""SELECT r.local_id,r.taxon,r.anchor,r.anchor_count,r.reviewed,b.source_type,b.value
      FROM r LEFT JOIN {source} b ON b.local_id=r.local_id ORDER BY r.local_id,b.source_type,b.value"""
    rows, key, head, group = 0, None, None, []

    def emit():
        taxon, anchor, count, reviewed = head
        writer.add(key.encode(), [taxon, anchor, int(count or 0), bool(reviewed), group])

    for local_id, taxon, anchor, count, reviewed, source_type, value in (
        r for b in _batches(c, sql) for r in zip(*b)
    ):
        rows += 1
        if local_id != key:
            if key is not None:
                emit()
            key, head, group = local_id, (taxon, anchor, count, reviewed), []
        if source_type is not None:
            group.append([source_type, value])
    if key is not None:
        emit()
    c.close()
    return dict(writer.close(), rows=rows)


def _run_shard(args):
    kind, directory, building, shard, shards, memory, threads, min_free = args
    directory, building = Path(directory), Path(building)
    name = f"{kind}.{shard}"
    final = building / name
    stats_file = final / "stats.json"
    if stats_file.is_file():  # finished by an earlier run
        return name, json.loads(stats_file.read_text())
    temp = building / (name + ".tmp")
    shutil.rmtree(temp, ignore_errors=True)
    started = time.monotonic()
    build = build_id_shard if kind == "id" else build_rec_shard
    stats = build(directory, temp, shard, shards, memory, threads, min_free)
    stats["seconds"] = round(time.monotonic() - started, 2)
    (temp / "stats.json").write_text(json.dumps(stats))
    shutil.rmtree(building / "spill" / temp.name, ignore_errors=True)
    temp.rename(final)
    log("hub_kv_shard", env=name, **stats)
    return name, stats


def build_hub_kv_dir(
    directory,
    *,
    id_shards: int | None = None,
    rec_shards: int | None = None,
    memory: str = "5GB",
    threads: int = 2,
    workers: int = 1,
    min_free_gib: float = 20,
) -> dict:
    """Write ``<directory>/kv/`` for one hub index directory (idempotent)."""
    directory = Path(directory)
    final = directory / KV_DIR
    if (final / MANIFEST).is_file():
        log("hub_kv_exists", path=str(final))
        return json.loads((final / MANIFEST).read_text())
    index_manifest = json.loads((directory / "manifest.json").read_text())
    hub = index_manifest["hub"]
    by_id_rows = _row_count(directory, "by_id", 0)
    by_record_rows = _row_count(directory, "by_record", 0)
    id_shards = id_shards or (16 if by_id_rows > SHARD_ROWS else 1)
    rec_shards = rec_shards or (16 if by_record_rows > SHARD_ROWS else 1)
    if id_shards not in (1, 16) or rec_shards not in (1, 16):
        raise ValueError("Shard counts must be 1 or 16")
    min_free = int(min_free_gib * 1024**3)
    building = directory / (KV_DIR + ".building")
    building.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(building).free < min_free:
        raise RuntimeError("Hub kv build stopped at the free-disk reserve")
    started = time.monotonic()
    log("hub_kv_start", hub=hub, id_shards=id_shards, rec_shards=rec_shards, workers=workers)
    per_worker = max(1, workers)
    memory_each = f"{max(256, int(_megabytes(memory) / per_worker))}MB"
    tasks = [
        (kind, str(directory), str(building), shard, count, memory_each, threads, min_free)
        for kind, count in (("rec", rec_shards), ("id", id_shards))
        for shard in range(count)
    ]
    if workers <= 1:
        results = [_run_shard(t) for t in tasks]
    else:
        context = multiprocessing.get_context("spawn")
        with ProcessPoolExecutor(max_workers=workers, mp_context=context) as pool:
            results = list(pool.map(_run_shard, tasks))
    envs = dict(results)
    shutil.rmtree(building / "spill", ignore_errors=True)
    totals = {
        kind: dict(
            keys=sum(s["keys"] for n, s in envs.items() if n.startswith(kind + ".")),
            rows=sum(s["rows"] for n, s in envs.items() if n.startswith(kind + ".")),
            file_bytes=sum(s["file_bytes"] for n, s in envs.items() if n.startswith(kind + ".")),
            skipped_long_keys=sum(
                s["skipped_long_keys"] for n, s in envs.items() if n.startswith(kind + ".")
            ),
        )
        for kind in ("id", "rec")
    }
    manifest = dict(
        format=HUB_KV_FORMAT,
        hub=hub,
        source_index=str(directory),
        source_sha256=sha256_file(directory / "manifest.json"),
        input=index_manifest.get("input"),
        id_shards=id_shards,
        rec_shards=rec_shards,
        totals=totals,
        envs=dict(sorted(envs.items())),
        seconds=round(time.monotonic() - started, 2),
    )
    (building / MANIFEST).write_text(json.dumps(manifest, indent=2))
    building.rename(final)
    log("hub_kv_done", hub=hub, path=str(final), seconds=manifest["seconds"], totals=totals)
    return manifest


def _megabytes(memory: str) -> float:
    text = str(memory).strip().upper()
    for suffix, factor in (("GIB", 1024), ("GB", 1000), ("MIB", 1), ("MB", 1), ("G", 1000), ("M", 1)):
        if text.endswith(suffix):
            return float(text[: -len(suffix)]) * factor
    raise ValueError(f"Unsupported memory size {memory}")


def build_hub_kv(hub, hub_index_root, **options) -> dict:
    """Write the kv store of the newest finished index of ``hub`` under ``hub_index_root``."""
    return build_hub_kv_dir(find_index(hub_index_root, hub), **options)
