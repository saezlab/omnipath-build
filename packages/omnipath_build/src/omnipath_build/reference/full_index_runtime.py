"""Two direct disk reads around the existing Rust entity decision policy."""

from __future__ import annotations

from collections import defaultdict
import json
from pathlib import Path
import time

import lmdb

from .full_index import FORMAT, partition, read_value
from .replay_resources import CODES


class Shards:
    def __init__(self, path):
        self.path = path
        self.opened = {}

    def get(self, shard, key):
        if shard not in self.opened:
            env = lmdb.open(str(self.path / shard), readonly=True, lock=False, readahead=False)
            self.opened[shard] = (env, env.begin())
        return read_value(self.opened[shard][1], key)

    def close(self):
        for env, txn in self.opened.values():
            txn.abort()
            env.close()
        self.opened.clear()


class FullRuntime:
    def __init__(self, path, *, reference_fingerprint=None):
        path = Path(path)
        manifest = json.loads((path / "manifest.json").read_text())
        from .compact_index import FORMAT as COMPACT_FORMAT, load_codecs

        if (
            manifest.get("format") not in (FORMAT, COMPACT_FORMAT)
            or manifest.get("complete") is not True
        ):
            raise ValueError("A published complete two-index reference is required")
        if manifest.get("namespace_codes") != CODES:
            raise ValueError("Namespace encoding differs from compiled index")
        if reference_fingerprint and manifest["reference_fingerprint"] != reference_fingerprint:
            raise ValueError("Reference fingerprint mismatch")
        expected = {
            f"{category}/{p:02x}/data.mdb"
            for category in ("identifiers", "entities")
            for p in range(256)
        }
        if set(manifest["files"]) != expected:
            raise ValueError("Incomplete index partition manifest")
        for file, size in manifest["files"].items():
            if (path / file).stat().st_size != size:
                raise ValueError("Missing or changed index partition: " + file)
        self.codecs = (
            load_codecs(path, manifest["dictionaries"])
            if manifest["format"] == COMPACT_FORMAT
            else None
        )
        self.identifiers = Shards(path / "identifiers")
        self.entities = Shards(path / "entities")
        self.codes = {code: ns for ns, code in CODES.items()}

    def lookup(self, key):
        if (
            len(key) < 7
            or key[0] != 1
            or key[1] not in (1, 2)
            or key[2] not in (1, 2)
            or key[5] not in (0, 1)
        ):
            raise ValueError("Unsupported normalized lookup key")
        ns = self.codes.get(int.from_bytes(key[3:5], "big"))
        if ns is None:
            raise ValueError("Unsupported identifier namespace")
        offset = 10 if key[5] else 6
        identifier = key[offset:].decode()
        if not identifier:
            raise ValueError("Empty identifier")
        raw = self.identifiers.get(partition(identifier), key)
        if raw is not None:
            return self.codecs["identifiers"].decode(raw) if self.codecs else json.loads(raw)
        return dict(
            candidates=[],
            gene=key[1] == 2 and ns in {"hgnc", "ensg", "genesymbol", "genesymbol-syn"},
            products=key[1] == 2 and ns in {"hgnc", "ensg"},
        )

    def record(self, entity_id):
        raw = self.entities.get(partition(entity_id), entity_id.encode())
        if raw is None:
            raise ValueError("Candidate references absent entity: " + entity_id)
        value = self.codecs["entities"].decode(raw) if self.codecs else json.loads(raw)
        return value["record"]

    def close(self):
        self.identifiers.close()
        self.entities.close()

    def resolve(self, queries, votes, *, decision_batch_size=4096):
        from omnipath_resolver._omnipath_resolver import resolve_precomputed_batch

        start = time.perf_counter()
        keys = sorted({v["lookup_key"] for v in votes})
        selected = {key: self.lookup(key) for key in keys}
        postings, metadata = [], {}
        for key, value in selected.items():
            postings.append((key, [c[0] for c in value["candidates"]]))
            metadata.update({c[0]: tuple(c) for c in value["candidates"]})
        grouped = defaultdict(list)
        primary_symbol = {
            v["input_id"]
            for v in votes
            if v["ns"] == "genesymbol" and selected[v["lookup_key"]]["candidates"]
        }
        for v in votes:
            if v["ns"] == "genesymbol-syn" and v["input_id"] in primary_symbol:
                continue
            value = selected[v["lookup_key"]]
            grouped[v["input_id"]].append(
                (
                    v["lookup_key"],
                    v["anchor"],
                    v["route"],
                    v["ordinal"],
                    value["gene"],
                    value["products"],
                )
            )
        batches = [(q["input_id"], q["target"], grouped[q["input_id"]]) for q in queries]
        fetched = time.perf_counter()
        result = []
        for offset in range(0, len(batches), decision_batch_size):
            result.extend(
                resolve_precomputed_batch(
                    batches[offset : offset + decision_batch_size],
                    postings,
                    list(metadata.values()),
                )
            )
        decided = time.perf_counter()
        accepted = {eid for _, _, ids, _ in result for eid in ids}
        records = {eid: self.record(eid) for eid in accepted}
        finished = time.perf_counter()
        return dict(
            results=[
                dict(input_id=i, outcome=o, entities=sorted(ids), candidate_count=n)
                for i, o, ids, n in result
            ],
            records=records,
        ), dict(
            lookup_seconds=fetched - start,
            decision_seconds=decided - fetched,
            entity_fetch_seconds=finished - decided,
            total_seconds=finished - start,
            unique_keys=len(keys),
            candidate_records=len(metadata),
            entity_records=len(records),
        )
