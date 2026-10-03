"""Lossless compact values and resumable conversion of a complete two-index build."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import time

import lmdb

from .full_index import FORMAT as SOURCE_FORMAT, MAX_RUNTIME_VALUE, Writer, read_value

from omnipath_resolver.index_codec import (
    FORMAT,
    KINDS,
    pack as pack,
    unpack as unpack,
    Codec,
    load_codecs,
)

# JSON may be much larger than the decoded MessagePack runtime representation.
# This allowance is used only while converting trusted compiler output.
MAX_SOURCE_JSON_VALUE = 256 * 1024**2


class CompactWriter:
    """Write compiler batches straight to the final representation, without a JSON database."""

    def __init__(self, path, kind, dictionary, *, audit_limit=None):
        self.codec = Codec(kind, dictionary)
        self.writer = Writer(path)
        self.verified = 0
        self.audit_limit = audit_limit
        self.oversized = []

    def put_objects(self, rows):
        batch, size = [], 0
        for key, value in rows:
            if self.audit_limit is not None and len(value["candidates"]) > self.audit_limit:
                self.oversized.append([key.hex(), len(value["candidates"])])
            encoded = self.codec.encode(value)
            if self.codec.decode(encoded) != value:
                raise ValueError("Direct compact record failed exact round-trip validation")
            batch.append((key, encoded))
            size += len(key) + len(encoded)
            self.verified += 1
            if len(batch) >= 8192 or size >= 16 * 1024**2:
                self.writer.put(batch)
                batch, size = [], 0
        if batch:
            self.writer.put(batch)

    def put_json(self, rows):
        # Existing DuckDB stages can fuse serialization with final writing before
        # they are migrated to a typed Arrow/native writer.
        self.put_objects((key, json.loads(raw)) for key, raw in rows)

    def put(self, rows):
        self.put_json(rows)

    @property
    def env(self):
        return self.writer.env

    def close(self):
        result = self.writer.close()
        if result["records"] != self.verified:
            raise ValueError("Direct compact writer lost records")
        result = dict(result, exact_round_trip_records=self.verified)
        if self.audit_limit is not None:
            result["candidate_limit_audit"] = dict(limit=self.audit_limit, keys=self.oversized)
        return result


def atomic_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name("." + path.name + ".tmp")
    with temp.open("w") as f:
        json.dump(obj, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    temp.replace(path)
    fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def logical_values(txn, *, max_bytes=MAX_RUNTIME_VALUE):
    """Visit each original key once, including collision-checked long keys."""
    with txn.cursor() as cursor:
        for key, value in cursor:
            if key[:1] == b"\2":
                continue
            if key[:1] == b"\0":
                raw_key = key[1:]
                # Most values are inline: avoid a redundant B-tree lookup.
                raw = (
                    value[2:]
                    if value[:2] == b"JV"
                    else read_value(txn, raw_key, max_bytes=max_bytes)
                )
            elif key[:1] == b"\1":
                if value[:1] == b"C":
                    size, count = struct.unpack(">QI", value[1:])
                    if size > max_bytes:
                        raise ValueError("Oversized long-key value")
                    chunks = [txn.get(b"\2" + key[1:] + struct.pack(">I", i)) for i in range(count)]
                    if any(c is None for c in chunks):
                        raise ValueError("Incomplete chunked long-key value")
                    envelope = b"".join(chunks)
                    if len(envelope) != size:
                        raise ValueError("Invalid chunked value length")
                elif value[:1] == b"J":
                    envelope = value[1:]
                else:
                    raise ValueError("Unsupported long-key value")
                if envelope[:1] != b"L":
                    raise ValueError("Missing long-key envelope")
                length = struct.unpack(">I", envelope[1:5])[0]
                raw_key = envelope[5 : 5 + length]
                if hashlib.sha256(raw_key).digest() != key[1:]:
                    raise ValueError("Long-key hash mismatch")
                raw = read_value(txn, raw_key, max_bytes=max_bytes)
            else:
                raise ValueError("Unknown physical key")
            yield raw_key, raw


def checkpoint_path(source, kind, part):
    return (
        source
        / ("entity-checkpoints" if kind == "entities" else "identifiers-checkpoints")
        / (part + ".json")
    )


def initialize(source, output, dictionaries):
    source, output, dictionaries = Path(source), Path(output), Path(dictionaries)
    if source.resolve() == output.resolve():
        raise ValueError("Conversion needs a separate output directory")
    original = json.loads((source / "build-contract.json").read_text())
    if original["format"] != SOURCE_FORMAT:
        raise ValueError("Unexpected source format")
    specs = {}
    output.mkdir(parents=True, exist_ok=True)
    for kind in KINDS:
        raw = (dictionaries / (kind + "-dictionary.zstd")).read_bytes()
        file = "dictionaries/" + kind + ".zstd"
        specs[kind] = dict(file=file, bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
    contract = dict(
        format=FORMAT,
        reference_fingerprint=original["reference_fingerprint"],
        source_format=SOURCE_FORMAT,
        dictionaries=specs,
    )
    path = output / "build-contract.json"
    if path.exists() and json.loads(path.read_text()) != contract:
        raise ValueError("Cannot resume a different reference or codec dictionary")
    for kind in KINDS:
        raw = (dictionaries / (kind + "-dictionary.zstd")).read_bytes()
        file = output / specs[kind]["file"]
        file.parent.mkdir(exist_ok=True)
        if file.exists() and file.read_bytes() != raw:
            raise ValueError("Dictionary changed")
        if not file.exists():
            with file.open("xb") as f:
                f.write(raw)
                f.flush()
                os.fsync(f.fileno())
    atomic_json(path, contract)
    return contract


def convert_partition(source, output, kind, part):
    source, output = Path(source), Path(output)
    if kind not in KINDS or part not in {f"{n:02x}" for n in range(256)}:
        raise ValueError("Unknown partition")
    started = time.monotonic()
    original_bytes = checkpoint_path(source, kind, part).read_bytes()
    original = json.loads(original_bytes)
    source_hash = hashlib.sha256(original_bytes).hexdigest()
    ready = output / "checkpoints" / kind / (part + ".json")
    target = output / kind / part
    if ready.exists():
        result = json.loads(ready.read_text())
        if (
            result["source_checkpoint_sha256"] != source_hash
            or (target / "data.mdb").stat().st_size != result["bytes"]
        ):
            raise ValueError("Compact checkpoint or source changed")
        return result
    path = source / kind / part
    if (path / "data.mdb").stat().st_size != original["bytes"]:
        raise ValueError("Source checkpoint does not match its file")
    contract = json.loads((output / "build-contract.json").read_text())
    codec = load_codecs(output, contract["dictionaries"])[kind]
    staging = output / kind / ("." + part + ".tmp")
    for unfinished in (staging, target):
        if unfinished.exists():
            shutil.rmtree(unfinished)  # No durable checkpoint: retain the source and rebuild.
    writer = Writer(staging)
    count, compressed, long_keys, largest = 0, 0, 0, 0
    batch, batch_bytes = [], 0
    try:
        with lmdb.open(str(path), readonly=True, lock=False, readahead=False) as env:
            with env.begin() as txn:
                for key, raw in logical_values(txn, max_bytes=MAX_SOURCE_JSON_VALUE):
                    obj = json.loads(raw)
                    encoded = codec.encode(obj)
                    if codec.decode(encoded) != obj:
                        raise ValueError("Compact record failed exact round-trip validation")
                    count += 1
                    compressed += encoded[:1] == b"Z"
                    long_keys += len(key) > 500
                    largest = max(largest, len(raw))
                    batch.append((key, encoded))
                    batch_bytes += len(key) + len(encoded)
                    if len(batch) >= 8192 or batch_bytes >= 16 * 1024**2:
                        writer.put(batch)
                        batch, batch_bytes = [], 0
        if batch:
            writer.put(batch)
        result = writer.close()
    except BaseException:
        writer.env.close()
        raise
    if count != original["records"] or result["records"] != count:
        raise ValueError("Logical record count changed during conversion")
    result.update(
        kind=kind,
        part=part,
        original_bytes=original["bytes"],
        source_checkpoint_sha256=source_hash,
        compressed_records=compressed,
        long_keys=long_keys,
        largest_json_record=largest,
        exact_round_trip_records=count,
        seconds=time.monotonic() - started,
    )
    staging.rename(target)
    atomic_json(ready, result)
    return result


def capture_source_manifest(source, output):
    """Persist the completed source manifest before any original partitions are retired."""
    path = output / "source-manifest.json"
    if path.exists():
        return json.loads(path.read_text())
    source_manifest = source / "manifest.json"
    if not source_manifest.exists():
        return None
    manifest = json.loads(source_manifest.read_text())
    contract = json.loads((output / "build-contract.json").read_text())
    if (
        manifest.get("complete") is not True
        or manifest["format"] != SOURCE_FORMAT
        or manifest["reference_fingerprint"] != contract["reference_fingerprint"]
    ):
        raise ValueError("Unexpected completed source manifest")
    atomic_json(path, manifest)
    return manifest


def retire_converted_sources(source, output):
    source, output = Path(source), Path(output)
    manifest = capture_source_manifest(source, output)
    if manifest is None:
        return 0
    # The experimental JSON generation is now retired, so it cannot be opened as complete.
    (source / "manifest.json").unlink(missing_ok=True)
    reclaimed = 0
    for kind in KINDS:
        for cp in sorted((output / "checkpoints" / kind).glob("*.json")):
            result = json.loads(cp.read_text())
            part = cp.stem
            file = f"{kind}/{part}/data.mdb"
            target = output / file
            old = source / kind / part
            if not old.exists():
                continue
            original_cp = checkpoint_path(source, kind, part).read_bytes()
            if (
                hashlib.sha256(original_cp).hexdigest() != result["source_checkpoint_sha256"]
                or target.stat().st_size != result["bytes"]
                or result["original_bytes"] != manifest["files"][file]
                or (old / "data.mdb").stat().st_size != result["original_bytes"]
            ):
                raise ValueError("Cannot retire an unverified source partition")
            shutil.rmtree(old)
            reclaimed += result["original_bytes"]
    return reclaimed


def publish(output):
    output = Path(output)
    source = json.loads((output / "source-manifest.json").read_text())
    contract = json.loads((output / "build-contract.json").read_text())
    load_codecs(output, contract["dictionaries"])
    files, counts, verified = {}, {}, 0
    for kind in KINDS:
        counts[kind] = 0
        for n in range(256):
            part = f"{n:02x}"
            cp = json.loads((output / "checkpoints" / kind / (part + ".json")).read_text())
            file = f"{kind}/{part}/data.mdb"
            if (
                (output / file).stat().st_size != cp["bytes"]
                or cp["original_bytes"] != source["files"][file]
                or cp["records"] != cp["exact_round_trip_records"]
            ):
                raise ValueError("Compact partition is incomplete or unverified")
            files[file] = cp["bytes"]
            counts[kind] += cp["records"]
            verified += cp["exact_round_trip_records"]
    if counts != source["counts"]:
        raise ValueError("Full reference record counts changed")
    manifest = dict(
        contract,
        complete=True,
        counts=counts,
        files=files,
        assertions=source["assertions"],
        namespace_codes=source["namespace_codes"],
        application_result_cache=False,
        exact_round_trip_records=verified,
    )
    atomic_json(output / "manifest.json", manifest)
    return manifest
