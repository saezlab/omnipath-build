"""Validate an explicit OmniPath release before loading its resolved Parquets.

Only shared schemas and PyArrow are used here. No resource discovery, entity
resolution, source parsing or database connection occurs during validation.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
from types import MappingProxyType
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from omnipath_core.schema import ENTITY_SCHEMA, PAYLOAD_SCHEMA, RELATION_SCHEMA
from omnipath_core.versioning import validate_source, validate_version


FILE_SCHEMAS = MappingProxyType(
    {
        "entities.parquet": ENTITY_SCHEMA,
        "relations.parquet": RELATION_SCHEMA,
        "evidence_payloads.parquet": PAYLOAD_SCHEMA,
    }
)
FILES = tuple(FILE_SCHEMAS)
RELEASE_SCHEMA_VERSION = 1
BUILD_SCHEMA_VERSION = 1
SERVING_SCHEMA_VERSION = 3


class ReleaseValidationError(ValueError):
    """An explicit release or one of its pinned artifacts is invalid."""


@dataclass(frozen=True)
class ParquetArtifact:
    """An artifact attested by streamed content hash and Parquet metadata."""

    name: str
    path: Path
    sha256: str
    size_bytes: int
    rows: int


@dataclass(frozen=True)
class ResourceSelection:
    """One exact resource version, with immutable metadata and verified paths.

    ``schema_version`` describes its build manifest; ``serving_schema_version``
    describes the three Parquet tables. ``manifest_json`` preserves the source
    manifest text, while ``manifest_sha256`` hashes its original UTF-8 bytes.
    """

    source: str
    version: str
    schema_version: int
    serving_schema_version: int
    directory: Path
    manifest_path: Path
    manifest: Mapping[str, Any]
    manifest_json: str
    manifest_sha256: str
    files: Mapping[str, ParquetArtifact]

    @property
    def entities_path(self) -> Path:
        return self.files["entities.parquet"].path

    @property
    def relations_path(self) -> Path:
        return self.files["relations.parquet"].path

    @property
    def payloads_path(self) -> Path:
        return self.files["evidence_payloads.parquet"].path


@dataclass(frozen=True)
class PinnedRelease:
    """Validated release snapshot, suitable for reproducible loader bookkeeping.

    Resources are sorted by source. Nested manifest objects and artifact maps
    are immutable. ``sha256`` is the digest of canonical release JSON, so JSON
    whitespace and key ordering do not change release identity.
    """

    version: str
    schema_version: int
    resources: tuple[ResourceSelection, ...]
    data_root: Path
    manifest_path: Path
    manifest: Mapping[str, Any]
    canonical_json: str
    sha256: str
    references: Mapping[str, ParquetArtifact]
    manifest_json: str
    manifest_sha256: str


def _immutable(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({key: _immutable(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_immutable(item) for item in value)
    return value


def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ReleaseValidationError(f"Duplicate JSON field: {key}")
        result[key] = value
    return result


def _constant(value: str) -> None:
    raise ReleaseValidationError(f"Non-finite JSON number is not permitted: {value}")


def _read_json(path: Path) -> tuple[dict[str, Any], str, str]:
    try:
        content = path.read_bytes()
        text = content.decode("utf-8")
        value = json.loads(text, object_pairs_hook=_object, parse_constant=_constant)
    except (OSError, UnicodeError, ValueError) as exc:
        raise ReleaseValidationError(f"Cannot read valid JSON manifest {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ReleaseValidationError(f"Manifest must be a JSON object: {path}")
    return value, text, hashlib.sha256(content).hexdigest()


def _fields(value: dict, required: set[str], optional: set[str], context: str) -> None:
    if missing := required - value.keys():
        raise ReleaseValidationError(f"Missing {context} fields: {', '.join(sorted(missing))}")
    if unknown := value.keys() - required - optional:
        raise ReleaseValidationError(f"Unknown {context} fields: {', '.join(sorted(unknown))}")


def _integer(value: Any, context: str) -> int:
    if type(value) is not int or value < 0:
        raise ReleaseValidationError(f"{context} must be a nonnegative integer")
    return value


def _schema_version(value: Any, expected: int, context: str) -> int:
    if type(value) is not int or value != expected:
        raise ReleaseValidationError(f"Unsupported {context}: {value!r}; expected {expected}")
    return value


def _digest(value: Any, context: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ReleaseValidationError(f"{context} must be a lowercase SHA-256 digest")
    return value


def _artifact_path(root: Path, path: Path) -> Path:
    """Require literal pinned paths, rejecting escaping or mutable symlinks."""
    try:
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ReleaseValidationError(f"Missing or invalid release artifact: {path}") from exc
    if not resolved.is_relative_to(root):
        raise ReleaseValidationError(f"Artifact escapes data root: {path}")
    if resolved != path:
        raise ReleaseValidationError(f"Release artifact path must not contain symlinks: {path}")
    if not resolved.is_file():
        raise ReleaseValidationError(f"Release artifact is not a regular file: {path}")
    return resolved


def _verify_parquet(
    path: Path,
    expected_digest: str,
    *,
    schema: pa.Schema | None = None,
    size_bytes: int | None = None,
    rows: int | None = None,
) -> ParquetArtifact:
    try:
        with path.open("rb") as handle:
            before = os.fstat(handle.fileno())
            parquet = pq.ParquetFile(handle)
            actual_rows = parquet.metadata.num_rows
            if schema is not None and not parquet.schema_arrow.equals(schema):
                raise ReleaseValidationError(
                    f"Parquet schema does not match serving contract: {path}"
                )
            if rows is not None and actual_rows != rows:
                raise ReleaseValidationError(
                    f"Parquet row count differs from build manifest: {path}"
                )
            if size_bytes is not None and before.st_size != size_bytes:
                raise ReleaseValidationError(f"Artifact size differs from build manifest: {path}")
            checksum = hashlib.sha256()
            handle.seek(0)
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                checksum.update(chunk)
            after = os.fstat(handle.fileno())
            before_identity = (
                before.st_ino,
                before.st_size,
                before.st_mtime_ns,
                before.st_ctime_ns,
            )
            after_identity = (after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
            if before_identity != after_identity:
                raise ReleaseValidationError(f"Artifact changed during validation: {path}")
            if checksum.hexdigest() != expected_digest:
                raise ReleaseValidationError(f"Artifact checksum differs from manifest: {path}")
    except (OSError, pa.ArrowException) as exc:
        raise ReleaseValidationError(f"Cannot read release Parquet artifact {path}: {exc}") from exc
    return ParquetArtifact(path.name, path, expected_digest, before.st_size, actual_rows)


def _resource(root: Path, source: str, version: str) -> ResourceSelection:
    directory = root / "resources" / source / version
    path = _artifact_path(root, directory / "build_manifest.json")
    manifest, text, checksum = _read_json(path)
    if manifest.get("resource") != source or manifest.get("version") != version:
        raise ReleaseValidationError(
            f"Build manifest does not match pinned resource {source}/{version}"
        )
    build_schema = _schema_version(
        manifest.get("schema_version"), BUILD_SCHEMA_VERSION, "build schema version"
    )
    serving_schema = _schema_version(
        manifest.get("serving_schema_version"), SERVING_SCHEMA_VERSION, "serving schema version"
    )
    file_entries = manifest.get("files")
    if not isinstance(file_entries, dict) or file_entries.keys() != FILE_SCHEMAS.keys():
        raise ReleaseValidationError(
            f"Build manifest must describe exactly the three serving tables: {path}"
        )
    artifacts = {}
    for name, schema in FILE_SCHEMAS.items():
        metadata = file_entries[name]
        if not isinstance(metadata, dict):
            raise ReleaseValidationError(f"Invalid artifact metadata for {source}/{version}/{name}")
        _fields(metadata, {"sha256", "size_bytes", "rows"}, set(), "artifact metadata")
        artifacts[name] = _verify_parquet(
            _artifact_path(root, directory / name),
            _digest(metadata["sha256"], f"Checksum of {name}"),
            schema=schema,
            size_bytes=_integer(metadata["size_bytes"], f"Size of {name}"),
            rows=_integer(metadata["rows"], f"Row count of {name}"),
        )
    return ResourceSelection(
        source,
        version,
        build_schema,
        serving_schema,
        directory,
        path,
        _immutable(manifest),
        text,
        checksum,
        MappingProxyType(artifacts),
    )


def _references(root: Path, value: Any) -> Mapping[str, ParquetArtifact]:
    if not isinstance(value, dict):
        raise ReleaseValidationError("Release references must be an object")
    _fields(value, set(), {"taxonomy"}, "release references")
    if "taxonomy" not in value:
        return MappingProxyType({})
    taxonomy = value["taxonomy"]
    if not isinstance(taxonomy, dict):
        raise ReleaseValidationError("Taxonomy reference must be an object")
    _fields(
        taxonomy,
        {"version"},
        {"source_url", "source_sha256", "taxon_count", "missing_taxon_ids"},
        "taxonomy reference",
    )
    digest = _digest(taxonomy["version"], "Taxonomy version")
    if "source_sha256" in taxonomy:
        _digest(taxonomy["source_sha256"], "Taxonomy source checksum")
    count = (
        _integer(taxonomy["taxon_count"], "Taxonomy row count")
        if "taxon_count" in taxonomy
        else None
    )
    if "source_url" in taxonomy and not isinstance(taxonomy["source_url"], str):
        raise ReleaseValidationError("Taxonomy source_url must be a string")
    if "missing_taxon_ids" in taxonomy and (
        not isinstance(taxonomy["missing_taxon_ids"], list)
        or any(
            not isinstance(taxon, str) or not taxon.isdigit()
            for taxon in taxonomy["missing_taxon_ids"]
        )
    ):
        raise ReleaseValidationError("Taxonomy missing_taxon_ids must contain numeric strings")
    artifact = _verify_parquet(
        _artifact_path(root, root / "references" / "taxonomy" / digest / "taxonomy.parquet"),
        digest,
        rows=count,
    )
    return MappingProxyType({"taxonomy": artifact})


def load_release(data_root: str | Path, manifest_path: str | Path) -> PinnedRelease:
    """Validate and return the exact resource artifacts pinned by a release file.

    The manifest may be supplied from outside ``data_root``. Every referenced
    artifact must be a regular, literal path beneath the resolved data root.
    Mutable selections (including ``latest`` and ``working``) are rejected;
    there is no fallback to discovered resource versions or missing manifests.
    Validation reads only JSON, Parquet metadata and bounded hash chunks.
    """
    root = Path(data_root).resolve()
    path = Path(manifest_path).absolute()
    if path.is_symlink():
        raise ReleaseValidationError("Release manifest must not be a mutable symlink")
    manifest, original_json, original_digest = _read_json(path)
    _fields(
        manifest,
        {"schema_version", "version", "resources"},
        {"created_at", "references"},
        "release",
    )
    schema_version = _schema_version(
        manifest["schema_version"], RELEASE_SCHEMA_VERSION, "release schema version"
    )
    try:
        version = validate_version(manifest["version"])
        selections = manifest["resources"]
        if not isinstance(selections, dict) or not selections:
            raise ReleaseValidationError("Release must pin at least one resource version")
        for source, resource_version in selections.items():
            validate_source(source)
            validate_version(resource_version)
    except ValueError as exc:
        raise ReleaseValidationError(str(exc)) from exc
    if "created_at" in manifest and not isinstance(manifest["created_at"], str):
        raise ReleaseValidationError("Release created_at must be a string")
    canonical_json = json.dumps(
        manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )
    resources = tuple(_resource(root, source, selections[source]) for source in sorted(selections))
    references = _references(root, manifest.get("references", {}))
    return PinnedRelease(
        version,
        schema_version,
        resources,
        root,
        path.resolve(),
        _immutable(manifest),
        canonical_json,
        hashlib.sha256(canonical_json.encode("utf-8")).hexdigest(),
        references,
        original_json,
        original_digest,
    )


def _verify_manifest(path: Path, expected: str) -> None:
    if path.is_symlink():
        raise ReleaseValidationError(f"Pinned manifest became a mutable symlink: {path}")
    try:
        checksum = hashlib.sha256()
        with path.open("rb") as handle:
            before = os.fstat(handle.fileno())
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                checksum.update(chunk)
            after = os.fstat(handle.fileno())
    except OSError as exc:
        raise ReleaseValidationError(f"Cannot verify pinned manifest {path}: {exc}") from exc
    if (before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
    ) or checksum.hexdigest() != expected:
        raise ReleaseValidationError(f"Pinned manifest changed since release validation: {path}")


def verify_release(release: PinnedRelease) -> None:
    """Recheck the original selections before committing a database load.

    Changed manifests cannot select new versions: only the captured manifest
    hashes and already-selected artifact paths are checked. A loader can use
    this after ingestion and roll back if inputs changed while loading.
    """
    _verify_manifest(release.manifest_path, release.manifest_sha256)
    for resource in release.resources:
        _verify_manifest(
            _artifact_path(release.data_root, resource.manifest_path),
            resource.manifest_sha256,
        )
        for name, artifact in resource.files.items():
            _verify_parquet(
                _artifact_path(release.data_root, artifact.path),
                artifact.sha256,
                schema=FILE_SCHEMAS[name],
                size_bytes=artifact.size_bytes,
                rows=artifact.rows,
            )
    for artifact in release.references.values():
        _verify_parquet(
            _artifact_path(release.data_root, artifact.path),
            artifact.sha256,
            size_bytes=artifact.size_bytes,
            rows=artifact.rows,
        )
