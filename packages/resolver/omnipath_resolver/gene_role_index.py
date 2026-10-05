"""Verified runtime reads for the independently supported gene component."""

from __future__ import annotations

from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path

from .index_storage import partition

FORMAT = "omnipath-gene-role-index-v1"
NAME = "gene-role-index"
GENE_NAMESPACES = frozenset({"ensg", "hgnc", "genesymbol", "genesymbol-syn", "enst", "refseq"})
ALIAS_NAMESPACES = frozenset({"ensg", "hgnc", "genesymbol", "genesymbol-syn"})
PARTS = tuple(f"{n:02x}" for n in range(256))


def sha256(path):
    with Path(path).open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def gene_roles_required(manifest):
    return manifest.get("gene_role_component_required", False) or os.getenv(
        "OMNIPATH_REQUIRE_GENE_ROLE_INDEX", ""
    ).lower() in {"1", "true", "yes"}


@lru_cache(maxsize=2048)
def _verify_file(path, inode, size, mtime, ctime, expected):
    if sha256(path) != expected:
        raise ValueError("Gene component checksum mismatch: " + path)


def component_identity(base, *, required=False):
    """Verify the complete component and its exact base; return cache provenance."""
    base = Path(base)
    return _component_identity(base, base / NAME, required=required)


def _component_identity(base, directory, *, required=False):
    if not directory.exists():
        if required:
            raise ValueError("Required gene component is missing")
        return None
    manifest_path = directory / "manifest.json"
    if not manifest_path.is_file():
        raise ValueError("Incomplete gene component")
    manifest = json.loads(manifest_path.read_text())
    if manifest.get("format") != FORMAT or manifest.get("complete") is not True:
        raise ValueError("Incomplete or unsupported gene component")
    if manifest.get("base_manifest_sha256") != sha256(base / "manifest.json"):
        raise ValueError("Gene component belongs to a different base manifest")
    if (
        manifest.get("reference_fingerprint")
        != json.loads((base / "manifest.json").read_text())["reference_fingerprint"]
    ):
        raise ValueError("Gene component reference fingerprint mismatch")
    expected = {f"{kind}/{p}/data.mdb" for kind in ("identifiers", "records") for p in PARTS}
    if set(manifest.get("files", {})) != expected:
        raise ValueError("Incomplete gene component partition manifest")
    for name, spec in manifest["files"].items():
        path = directory / name
        if path.is_symlink() or path.resolve().parent.parent.parent != directory.resolve():
            raise ValueError("Gene component file must stay inside its immutable generation")
        st = path.stat()
        if st.st_size != spec["bytes"]:
            raise ValueError("Gene component size mismatch: " + name)
        _verify_file(
            str(path), st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns, spec["sha256"]
        )
    return dict(
        format=FORMAT,
        manifest_sha256=sha256(manifest_path),
        base_manifest_sha256=manifest["base_manifest_sha256"],
        reference_fingerprint=manifest["reference_fingerprint"],
        sources=manifest["sources"],
        counts=manifest["counts"],
    )


class GeneRoleRuntime:
    def __init__(self, base, *, required=False):
        from omnipath_resolver.index import Shards

        self.identity = component_identity(base, required=required)
        self.identifiers = self.records = None
        if self.identity:
            self.identifiers = Shards(Path(base) / NAME / "identifiers")
            self.records = Shards(Path(base) / NAME / "records")

    def lookup(self, lookup_key, namespace, identifier):
        if (
            self.identifiers is None
            or lookup_key[1:3] != bytes((2, 1))
            or namespace not in GENE_NAMESPACES
        ):
            return None
        raw = self.identifiers.get(partition(identifier), lookup_key)
        return json.loads(raw) if raw is not None else None

    def record(self, entity_id, record):
        if self.records is None or not entity_id.startswith("entrez:"):
            return record
        raw = self.records.get(partition(entity_id), entity_id.encode())
        if raw is None:
            return record
        added = json.loads(raw)
        pairs = record["identifiers"]
        if isinstance(pairs, dict):
            pairs = [(ns, value) for ns, values in pairs.items() for value in values]
        merged = sorted({tuple(p) for p in pairs} | {tuple(p) for p in added["identifiers"]})
        result = dict(record, identifiers=[list(p) for p in merged])
        symbols = [v for ns, v in merged if ns == "genesymbol" and 1 <= len(v.strip()) <= 120]
        if symbols:
            result["label"] = min(
                (v.strip() for v in symbols), key=lambda v: (len(v) > 15, len(v), v)
            )
        return result

    def close(self):
        for shards in (self.identifiers, self.records):
            if shards:
                shards.close()
