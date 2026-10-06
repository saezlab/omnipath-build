"""LMDB point-lookup store of the identity decisions (spec 3b): ``<identity dir>/kv/``.

One environment with seven small dbs written from the decision Parquet files (see
``omnipath_resolver.identity_kv`` for the layout): ``exc``, ``exc_members``, ``extra``,
``gp_protein``, ``gp_gene``, ``lipid`` and ``cand`` (from ``record_candidates.parquet``; empty
for snapshots built before that file existed). It is a separate step so existing decisions need not be
rebuilt; ``decisions.py`` (whose code hash names the decisions) is not touched.
"""

from __future__ import annotations

import json
import shutil
import time
from collections import defaultdict
from pathlib import Path

import lmdb
import pyarrow.parquet as pq

from omnipath_resolver.identity_kv import (
    DECISION_DBS,
    DECISIONS_KV_FORMAT,
    KV_DIR,
    MANIFEST,
    MAX_KEY,
    pack,
)

from .common import log

MAP_SIZE = 64 * 1024**3


def _rows(path: Path) -> list[dict]:
    if not path.is_file():
        raise FileNotFoundError(f"Identity decisions are incomplete: {path} is missing")
    return pq.read_table(path).to_pylist()


def _unique(pairs, name: str) -> dict[str, object]:
    out: dict[str, object] = {}
    for key, value in pairs:
        if key in out:
            raise ValueError(f"Duplicate key {key!r} in {name}")
        out[key] = value
    return out


def _grouped(pairs) -> dict[str, list]:
    out: dict[str, list] = defaultdict(list)
    for key, value in pairs:
        out[key].append(value)
    return out


def tables(identity_dir: Path) -> dict[str, dict[str, object]]:
    """db name -> {string key: value}, from the decision Parquet files."""
    d = Path(identity_dir)
    exc = _rows(d / "exceptions.parquet")
    members = _rows(d / "exception_members.parquet")
    extra = _rows(d / "entities_extra.parquet")
    by_protein = _rows(d / "gene_products_by_protein.parquet")
    by_gene = _rows(d / "gene_products_by_gene.parquet")
    lipid = _rows(d / "lipid_structures.parquet")
    cand_file = d / "record_candidates.parquet"  # absent from snapshots built before the rule
    candidates = _rows(cand_file) if cand_file.is_file() else []
    return dict(
        exc=_unique(
            ((r["record_id"], [r["entity_id"], r["decision"], bool(r["quarantined"])]) for r in exc),
            "exceptions",
        ),
        exc_members={
            k: sorted(v)
            for k, v in _grouped((r["entity_id"], r["record_id"]) for r in members).items()
        },
        extra=_unique(
            (
                (
                    r["entity_id"],
                    [r["kind"], r["taxon"], bool(r["quarantined"]), r.get("preferred_record")],
                )
                for r in extra
            ),
            "entities_extra",
        ),
        gp_protein={
            k: sorted(v, key=lambda x: (x[0], x[1] or ""))
            for k, v in _grouped(
                (r["protein_entity_id"], [r["entrez_id"], r["taxon"]]) for r in by_protein
            ).items()
        },
        gp_gene={
            k: sorted(v, key=lambda x: (x[0], x[1] or ""))
            for k, v in _grouped(
                (r["entrez_id"], [r["protein_entity_id"], r["taxon"]]) for r in by_gene
            ).items()
        },
        lipid=_unique(((r["goslin"], r["inchikey"]) for r in lipid), "lipid_structures"),
        cand={
            k: sorted(set(v))
            for k, v in _grouped((r["record_id"], r["entity_id"]) for r in candidates).items()
        },
    )


def build_identity_kv(identity_dir, *, force: bool = False, min_free_gib: float = 1) -> dict:
    """Write ``<identity_dir>/kv/`` from the decision Parquet files (idempotent)."""
    identity_dir = Path(identity_dir)
    final = identity_dir / KV_DIR
    if (final / MANIFEST).is_file() and not force:
        log("identity_kv_exists", path=str(final))
        return json.loads((final / MANIFEST).read_text())
    identity = json.loads((identity_dir / "manifest.json").read_text())
    if shutil.disk_usage(identity_dir).free < min_free_gib * 1024**3:
        raise RuntimeError("Identity kv build stopped at the free-disk reserve")
    started = time.monotonic()
    building = identity_dir / (KV_DIR + ".building")
    shutil.rmtree(building, ignore_errors=True)
    building.mkdir(parents=True)
    data = tables(identity_dir)
    env = lmdb.open(
        str(building), map_size=MAP_SIZE, max_dbs=len(DECISION_DBS) + 1, sync=False, metasync=False
    )
    counts, skipped = {}, {}
    for name in DECISION_DBS:
        db = env.open_db(name.encode())
        items = sorted(
            (key.encode(), pack(value)) for key, value in data[name].items()
        )
        long_keys = sum(1 for key, _ in items if len(key) > MAX_KEY)
        items = [(k, v) for k, v in items if len(k) <= MAX_KEY]
        while True:
            try:
                with env.begin(db=db, write=True) as txn:
                    _, added = txn.cursor(db).putmulti(items, dupdata=False, append=True)
                break
            except lmdb.MapFullError:
                env.set_mapsize(env.info()["map_size"] * 2)
        if added != len(items):
            raise AssertionError(f"{name}: stored {added} of {len(items)} keys")
        counts[name], skipped[name] = added, long_keys
    env.sync(True)
    env.close()
    manifest = dict(
        format=DECISIONS_KV_FORMAT,
        fingerprint=identity.get("fingerprint"),
        counts=counts,
        skipped_long_keys=skipped,
        file_bytes=(building / "data.mdb").stat().st_size,
        seconds=round(time.monotonic() - started, 2),
    )
    (building / MANIFEST).write_text(json.dumps(manifest, indent=2))
    if final.exists():
        shutil.rmtree(final)
    building.rename(final)
    log("identity_kv_done", path=str(final), **{k: v for k, v in manifest.items() if k != "format"})
    return manifest
