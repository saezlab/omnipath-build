"""Shared lossless partition/key/value wire format for compiled references."""

from __future__ import annotations
import hashlib
import struct

FORMAT = "omnipath-full-two-index-v1"
PARTITIONS = 256
CHUNK_BYTES = 65536
MAX_RUNTIME_VALUE = 64 * 1024**2


def partition(value):
    return hashlib.md5(value.encode(), usedforsecurity=False).hexdigest()[:2]


def packed_key(key):
    return b"\0" + key if len(key) <= 500 else b"\1" + hashlib.sha256(key).digest()


def storage_pairs(key, value):
    # A long-key envelope validates the original key, rather than trusting a hash.
    if len(key) > 500:
        value = b"L" + struct.pack(">I", len(key)) + key + value
    else:
        value = b"V" + value
    k = packed_key(key)
    if len(value) <= CHUNK_BYTES:
        return [(k, b"J" + value)]
    digest = hashlib.sha256(key).digest()
    chunks = [value[i : i + CHUNK_BYTES] for i in range(0, len(value), CHUNK_BYTES)]
    return [(k, b"C" + struct.pack(">QI", len(value), len(chunks)))] + [
        (b"\2" + digest + struct.pack(">I", i), chunk) for i, chunk in enumerate(chunks)
    ]


def read_value(txn, key, *, max_bytes=MAX_RUNTIME_VALUE):
    raw = txn.get(packed_key(key))
    if raw is None:
        return None
    if raw[:1] == b"C":
        size, count = struct.unpack(">QI", raw[1:])
        if size > max_bytes:
            raise ValueError(
                "Stored value exceeds the runtime byte budget; no candidates truncated"
            )
        digest = hashlib.sha256(key).digest()
        chunks = [txn.get(b"\2" + digest + struct.pack(">I", i)) for i in range(count)]
        if any(x is None for x in chunks):
            raise ValueError("Incomplete chunked reference value")
        value = b"".join(chunks)
        if len(value) != size:
            raise ValueError("Corrupt chunked reference value")
    elif raw[:1] == b"J":
        value = raw[1:]
    else:
        raise ValueError("Unsupported stored value")
    if value[:1] == b"L":
        size = struct.unpack(">I", value[1:5])[0]
        if value[5 : 5 + size] != key:
            raise ValueError("Long-key collision or corrupt index")
        return value[5 + size :]
    if value[:1] != b"V":
        raise ValueError("Corrupt value envelope")
    return value[1:]
