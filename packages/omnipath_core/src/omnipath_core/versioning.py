"""Explicit resource versions, build manifests, and standardized filesystem layout paths."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re
from typing import Any


def validate_version(version: str | None) -> str:
    """Validate that version is explicit, non-empty, and numeric dotted (e.g. 1.0.0)."""
    if not isinstance(version, str) or not re.fullmatch(r"[0-9]+(?:\.[0-9]+)*", version):
        raise ValueError("An explicit numeric resource version is required (for example 1.0.0)")
    return version


def validate_source(source: str) -> str:
    """Validate resource/source slug."""
    if not isinstance(source, str) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", source):
        raise ValueError(f"Invalid resource name: {source!r}")
    return source


@dataclass
class ManifestFile:
    path: str
    sha256: str
    bytes: int
    num_rows: int | None = None


@dataclass
class BuildManifest:
    resource: str
    version: str
    created_at: str
    schema_version: str = "2.0"
    files: list[ManifestFile] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


def resource_dir(data_root: str | Path, source: str, version: str) -> Path:
    """Return resources/<source>/<version> directory."""
    return (
        Path(data_root).resolve()
        / "resources"
        / validate_source(source)
        / validate_version(version)
    )


def manifest_path(data_root: str | Path, source: str, version: str) -> Path:
    """Return resources/<source>/<version>/build_manifest.json path."""
    return resource_dir(data_root, source, version) / "build_manifest.json"


def entities_path(data_root: str | Path, source: str, version: str) -> Path:
    """Return resources/<source>/<version>/entities.parquet path."""
    return resource_dir(data_root, source, version) / "entities.parquet"


def relations_path(data_root: str | Path, source: str, version: str) -> Path:
    """Return resources/<source>/<version>/relations.parquet path."""
    return resource_dir(data_root, source, version) / "relations.parquet"


def payloads_path(data_root: str | Path, source: str, version: str) -> Path:
    """Return resources/<source>/<version>/evidence_payloads.parquet path."""
    return resource_dir(data_root, source, version) / "evidence_payloads.parquet"
