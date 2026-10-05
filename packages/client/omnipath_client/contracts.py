"""Validate transport records once while preserving additive server metadata."""

from __future__ import annotations

from copy import deepcopy
import re
from typing import Any, NotRequired, TypedDict, cast
from urllib.parse import urlsplit

from .errors import ClientError

TABLES = ("entities", "relations", "evidence_payloads")


class Artifact(TypedDict):
    name: str
    size_bytes: int
    url: NotRequired[str | None]
    sha256: NotRequired[str]
    rows: NotRequired[int | None]


class ResourceRecord(TypedDict):
    resource_id: str
    version: str
    files: list[Artifact]


class Snapshot(TypedDict):
    snapshot_version: int
    api_url: str
    release: str
    resources: list[ResourceRecord]


def validate_url(value: Any) -> str:
    if not isinstance(value, str):
        raise ClientError("Expected a public HTTP(S) URL without embedded credentials")
    try:
        parsed = urlsplit(value)
        valid = parsed.scheme in {"http", "https"} and parsed.hostname and parsed.username is None
        _ = parsed.port
    except ValueError:
        valid = False
    if not valid:
        raise ClientError("Expected a public HTTP(S) URL without embedded credentials")
    return value


def validate_resource(value: Any, *, snapshot: bool = False) -> ResourceRecord:
    if not isinstance(value, dict):
        raise ClientError("Expected a resource object")
    source, version = value.get("resource_id"), value.get("version")
    if not isinstance(source, str) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", source):
        raise ClientError("Invalid resource ID in catalog")
    if not isinstance(version, str) or not re.fullmatch(r"[0-9]+(?:\.[0-9]+)*", version):
        raise ClientError("Invalid resource version in catalog")
    artifacts = value.get("files")
    if not isinstance(artifacts, list):
        raise ClientError("Resource files must be a list")
    names: set[str] = set()
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            raise ClientError("Expected an artifact object")
        name = artifact.get("name")
        if (
            not isinstance(name, str)
            or name not in {f"{t}.parquet" for t in TABLES}
            or name in names
        ):
            raise ClientError("Unknown or duplicate artifact in catalog")
        size = artifact.get("size_bytes")
        if type(size) is not int or size < 8:
            raise ClientError("Invalid artifact size in catalog")
        if artifact.get("url") is not None:
            validate_url(artifact["url"])
        digest = artifact.get("sha256")
        if snapshot or digest is not None:
            if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
                raise ClientError("Invalid or missing artifact checksum")
        rows = artifact.get("rows")
        if rows is not None and (type(rows) is not int or rows < 0):
            raise ClientError("Invalid artifact row count")
        names.add(name)
    if not {"entities.parquet", "relations.parquet"} <= names:
        raise ClientError("Resource must contain entities.parquet and relations.parquet")
    return cast(ResourceRecord, deepcopy(value))


def validate_catalog(value: Any, *, snapshot: bool = False) -> dict[str, ResourceRecord]:
    if not isinstance(value, list):
        raise ClientError("Resources must be a list")
    catalog: dict[str, ResourceRecord] = {}
    for raw in value:
        record = validate_resource(raw, snapshot=snapshot)
        source = record["resource_id"]
        if source in catalog:
            raise ClientError(f"Duplicate resource in catalog: {source}")
        catalog[source] = record
    return catalog


def validate_snapshot(value: Any) -> Snapshot:
    if not isinstance(value, dict):
        raise ClientError("Expected a snapshot object")
    if type(value.get("snapshot_version")) is not int or value["snapshot_version"] != 1:
        raise ClientError("Unsupported snapshot version")
    api_url = validate_url(value.get("api_url"))
    release = value.get("release")
    if not isinstance(release, str) or not release.strip():
        raise ClientError("Snapshot release must be a non-empty string")
    catalog = validate_catalog(value.get("resources"), snapshot=True)
    if not catalog:
        raise ClientError("Snapshot must contain at least one resource")
    return Snapshot(
        snapshot_version=1, api_url=api_url, release=release, resources=list(catalog.values())
    )
