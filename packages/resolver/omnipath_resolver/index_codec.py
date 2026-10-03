"""Lossless runtime compact codecs and validated dictionaries."""

from __future__ import annotations
import hashlib
import msgpack
import zstandard as zstd
from .index_storage import MAX_RUNTIME_VALUE

FORMAT = "omnipath-full-two-index-msgpack-zstd-v2"
KINDS = ("entities", "identifiers")


def pack(kind, obj):
    if kind == "entities":
        r, m = obj["record"], obj["meta"]
        if (
            set(obj) != {"record", "meta"}
            or set(r) != {"entity_id", "kind", "anchor", "taxon", "label", "identifiers"}
            or set(m) != {"id", "entity_id", "kind", "anchor", "quarantined", "reviewed"}
        ):
            raise ValueError("Unexpected entity schema")
        if any(r[k] != m[k] for k in ("entity_id", "kind", "anchor")):
            raise ValueError("Entity metadata disagrees with record")
        value = [
            r["entity_id"],
            r["kind"],
            r["anchor"],
            r["taxon"],
            r["label"],
            r["identifiers"],
            m["id"],
            m["quarantined"],
            m["reviewed"],
        ]
    elif kind == "identifiers":
        if set(obj) != {"gene", "products", "candidates"}:
            raise ValueError("Unexpected identifier schema")
        cs = []
        for num, eid, k, anchor, q, r in obj["candidates"]:
            if type(q) is not bool or type(r) is not bool:
                raise ValueError("Candidate flags must be booleans")
            same = anchor == eid
            cs.append(
                [num, eid, k, None if same else anchor, int(q) | (int(r) << 1) | (int(same) << 2)]
            )
        if type(obj["gene"]) is not bool or type(obj["products"]) is not bool:
            raise ValueError("Evidence flags must be booleans")
        value = [int(obj["gene"]) | (int(obj["products"]) << 1), cs]
    else:
        raise ValueError("Unknown compact store")
    return msgpack.packb(value, use_bin_type=True)


def unpack(kind, raw):
    v = msgpack.unpackb(raw, raw=False)
    if kind == "entities":
        eid, k, a, t, label, ids, num, q, r = v
        return dict(
            record=dict(entity_id=eid, kind=k, anchor=a, taxon=t, label=label, identifiers=ids),
            meta=dict(id=num, entity_id=eid, kind=k, anchor=a, quarantined=q, reviewed=r),
        )
    if kind != "identifiers":
        raise ValueError("Unknown compact store")
    flags, cs = v
    if flags & ~3 or any(c[4] & ~7 for c in cs):
        raise ValueError("Unknown compact flags")
    return dict(
        gene=bool(flags & 1),
        products=bool(flags & 2),
        candidates=[
            [num, eid, k, eid if f & 4 else a, bool(f & 1), bool(f & 2)] for num, eid, k, a, f in cs
        ],
    )


class Codec:
    def __init__(self, kind, dictionary):
        if kind not in KINDS:
            raise ValueError("Unknown compact store")
        self.kind = kind
        data = zstd.ZstdCompressionDict(dictionary)
        self.compressor = zstd.ZstdCompressor(level=3, dict_data=data)
        self.decompressor = zstd.ZstdDecompressor(dict_data=data)

    def encode(self, obj):
        binary = pack(self.kind, obj)
        if len(binary) > MAX_RUNTIME_VALUE:
            raise ValueError("Compact record exceeds runtime byte budget")
        compressed = self.compressor.compress(binary)
        return b"Z" + compressed if len(compressed) < len(binary) else b"B" + binary

    def decode(self, raw):
        if raw[:1] == b"Z":
            size = zstd.frame_content_size(raw[1:])
            if size < 0 or size > MAX_RUNTIME_VALUE:
                raise ValueError("Compressed record exceeds runtime byte budget or omits its size")
            binary = self.decompressor.decompress(raw[1:], max_output_size=MAX_RUNTIME_VALUE)
        elif raw[:1] == b"B":
            binary = raw[1:]
        else:
            raise ValueError("Unsupported compact record codec")
        if len(binary) > MAX_RUNTIME_VALUE:
            raise ValueError("Compact record exceeds runtime byte budget")
        return unpack(self.kind, binary)


def load_codecs(path, specs):
    if set(specs) != set(KINDS):
        raise ValueError("Missing compact dictionary")
    codecs = {}
    for kind in KINDS:
        spec = specs[kind]
        if spec["file"] != "dictionaries/" + kind + ".zstd":
            raise ValueError("Unexpected dictionary path")
        raw = (path / spec["file"]).read_bytes()
        if len(raw) != spec["bytes"] or hashlib.sha256(raw).hexdigest() != spec["sha256"]:
            raise ValueError("Compact dictionary checksum mismatch")
        codecs[kind] = Codec(kind, raw)
    return codecs
