"""Inventory scanning and immutable OmniPath release manifests."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from types import MappingProxyType
import json
import os
import re
import tempfile
import warnings
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq

FILES = ("entities.parquet", "relations.parquet", "evidence_payloads.parquet")


def safe_name(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", value):
        raise ValueError(f"Invalid version or resource name: {value!r}")
    return value


def validate_manifest(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise ValueError("Release manifest requires schema_version: 1")
    version = safe_name(data.get("version"))
    if not re.fullmatch(r"[0-9]+(?:\.[0-9]+)*", version):
        raise ValueError("OmniPath version must be numeric (for example 2026.09)")
    resources = data.get("resources")
    if not isinstance(resources, dict) or not resources:
        raise ValueError("Release must pin at least one resource version")
    for source, resource_version in resources.items():
        safe_name(source)
        safe_name(resource_version)
    taxonomy = data.get("references", {}).get("taxonomy")
    if taxonomy is not None:
        if not isinstance(taxonomy, dict) or not re.fullmatch(
            r"[0-9a-f]{64}", str(taxonomy.get("version", ""))
        ):
            raise ValueError("Taxonomy reference must pin a SHA-256 version")
    return data


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, filename = tempfile.mkstemp(prefix=".publish-", dir=path.parent)
    tmp = Path(filename)
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(data, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.link(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


class ReleaseStore:
    def __init__(self, data_root: str | Path):
        self.root = Path(data_root)

    def get(self, version: str) -> dict[str, Any]:
        path = self.root / "releases" / f"{safe_name(version)}.json"
        if not path.is_file():
            raise FileNotFoundError(f"Unknown OmniPath release: {version}")
        manifest = validate_manifest(json.loads(path.read_text()))
        if manifest["version"] != version:
            raise ValueError(f"Release filename and version disagree: {version}")
        return manifest

    def list(self) -> list[dict[str, Any]]:
        versions = [self.get(path.stem) for path in (self.root / "releases").glob("*.json")]
        return sorted(
            versions, key=lambda item: tuple(map(int, item["version"].split("."))), reverse=True
        )

    def default(self) -> str:
        return "latest"

    def validate_files(self, manifest: dict[str, Any]) -> None:
        taxonomy = manifest.get("references", {}).get("taxonomy")
        if taxonomy:
            path = self.root / "references/taxonomy" / taxonomy["version"] / "taxonomy.parquet"
            if hashlib.sha256(path.read_bytes()).hexdigest() != taxonomy["version"]:
                raise ValueError("Taxonomy reference checksum mismatch")
            pq.read_metadata(path)
        for source, version in manifest["resources"].items():
            directory = self.root / "resources" / source / version
            if (directory / ".entities.chunks.parquet").exists():
                raise ValueError(f"Resource build is incomplete: {source}/{version}")
            for name in FILES:
                path = directory / name
                if not path.is_file():
                    raise ValueError(f"Missing release artifact: {source}/{version}/{name}")
                pq.read_metadata(path)

    def publish(self, manifest: dict[str, Any]) -> dict[str, Any]:
        manifest = dict(validate_manifest(manifest))
        self.validate_files(manifest)
        # Legacy stores can still publish without a reference. Once provisioned,
        # every new release resolves its complete taxon set from the cached dump.
        if (
            not manifest.get("references", {}).get("taxonomy")
            and (self.root / "references/taxonomy/taxdump.tar.gz").is_file()
        ):
            from omnipath_core.taxonomy import prepare_reference

            manifest["references"] = {
                **manifest.get("references", {}),
                "taxonomy": prepare_reference(self.root, manifest),
            }
        if not manifest.get("references", {}).get("taxonomy"):
            warnings.warn(
                "Release has no taxonomy reference; provision references/taxonomy/taxdump.tar.gz for name resolution",
                stacklevel=2,
            )
        manifest.setdefault("created_at", datetime.now(timezone.utc).isoformat())
        _write_json(self.root / "releases" / f"{manifest['version']}.json", manifest)
        return manifest

    def references(self, source: str, version: str) -> list[str]:
        return [item["version"] for item in self.list() if item["resources"].get(source) == version]


@dataclass(frozen=True)
class InventorySnapshot:
    """A complete inventory published with one pointer replacement."""

    resources: dict
    latest: dict
    fingerprint: tuple

    def __post_init__(self):
        object.__setattr__(
            self,
            "resources",
            MappingProxyType(
                {key: MappingProxyType(dict(value)) for key, value in self.resources.items()}
            ),
        )
        object.__setattr__(
            self,
            "latest",
            MappingProxyType(
                {key: MappingProxyType(dict(value)) for key, value in self.latest.items()}
            ),
        )
