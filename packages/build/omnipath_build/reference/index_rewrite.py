"""Transactional, restartable correction of private unpublished LMDB partitions."""

import hashlib
import json
import os
import struct

import lmdb

from .compact_index import atomic_json
from .full_index import packed_key


def delete_value(txn, key):
    physical = packed_key(key)
    raw = txn.get(physical)
    if raw is None:
        return False
    if raw[:1] == b"C":
        _, count = struct.unpack(">QI", raw[1:])
        digest = hashlib.sha256(key).digest()
        for n in range(count):
            if not txn.delete(b"\2" + digest + struct.pack(">I", n)):
                raise ValueError("Missing value chunk during correction")
    txn.delete(physical)
    return True


def fsync_dir(path):
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def rewrite(index, work, kind, part, transform, *, compiler_layout=False):
    target = index / kind / part
    folder = (
        ("entity-checkpoints" if kind == "entities" else "identifiers-checkpoints")
        if compiler_layout
        else "checkpoints/" + kind
    )
    cp = index / folder / (part + ".json")
    before = work / "original-checkpoints" / kind / (part + ".json")
    plan = work / "plans" / kind / (part + ".json")
    ready = work / "checkpoints" / kind / (part + ".json")
    if ready.exists():
        report = json.loads(ready.read_text())
        if (target / "data.mdb").stat().st_size != report["bytes"]:
            raise ValueError("Corrected partition changed")
        return report
    if not before.exists():
        atomic_json(before, json.loads(cp.read_text()))
    original = json.loads(before.read_text())
    if not plan.exists() and (target / "data.mdb").stat().st_size != original["bytes"]:
        raise ValueError("Unexpected original partition size")
    # One LMDB transaction covers the entire partition. The plan is durable
    # before commit, so retries can distinguish a committed change from a lost
    # checkpoint without copying hundreds of GiB of immutable build output.
    with lmdb.open(str(target), map_size=8 * 1024**3) as env:
        with env.begin(write=True) as txn:
            details = transform(txn, resuming=plan.exists())
            if plan.exists():
                planned = json.loads(plan.read_text())
                if planned["after_sha256"] != details["after_sha256"]:
                    raise ValueError("Policy result changed while resuming a partition")
                details = planned
            else:
                atomic_json(plan, details)
        env.sync(True)
        stats = env.stat()
    report = dict(
        kind=kind,
        part=part,
        **details,
        bytes=(target / "data.mdb").stat().st_size,
        records=original["records"] - details.get("removed_keys", 0),
        lmdb=stats,
    )
    atomic_json(
        cp,
        dict(
            original,
            bytes=report["bytes"],
            records=report["records"],
            lmdb=stats,
            policy_correction=report,
        ),
    )
    atomic_json(ready, report)
    print(json.dumps(report), flush=True)
    return report
