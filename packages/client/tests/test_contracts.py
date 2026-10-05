"""Malformed metadata fails before querying or publishing a snapshot."""

from copy import deepcopy
import json

import httpx
import pytest

from omnipath_client import Client, ClientError
from omnipath_client.contracts import validate_catalog, validate_snapshot


def resource():
    return {
        "resource_id": "alpha",
        "version": "1.0",
        "description": "Additive metadata",
        "files": [
            {"name": f"{table}.parquet", "size_bytes": 100, "sha256": "a" * 64}
            for table in ("entities", "relations")
        ],
    }


@pytest.mark.parametrize(
    "field,value",
    [
        ("resource_id", 123),
        ("version", 1),
        ("files", None),
        ("files", {}),
        ("files", []),
        ("files", [None]),
    ],
)
def test_invalid_catalog_rejected_by_public_client(field, value, tmp_path):
    record = resource()
    record[field] = value
    transport = httpx.MockTransport(lambda _: httpx.Response(200, json={"resources": [record]}))
    with httpx.Client(transport=transport) as http, Client(http_client=http) as client:
        with pytest.raises(ClientError):
            client.download(tmp_path / "invalid")
    assert not (tmp_path / "invalid").exists()


@pytest.mark.parametrize(
    "field,value",
    [
        ("size_bytes", True),
        ("size_bytes", -1),
        ("size_bytes", "100"),
        ("rows", False),
        ("rows", -1),
        ("sha256", "invalid"),
        ("url", 123),
        ("url", "file:///tmp/test"),
        ("name", "../entities.parquet"),
    ],
)
def test_invalid_artifact_metadata_rejected(field, value):
    record = resource()
    record["files"][0][field] = value
    with pytest.raises(ClientError):
        validate_catalog([record])


def test_required_artifacts_and_duplicates():
    record = resource()
    record["files"].pop()
    with pytest.raises(ClientError, match="must contain"):
        validate_catalog([record])
    record = resource()
    record["files"].append(deepcopy(record["files"][0]))
    with pytest.raises(ClientError, match="duplicate artifact"):
        validate_catalog([record])
    with pytest.raises(ClientError, match="Duplicate resource"):
        validate_catalog([resource(), resource()])


def test_snapshot_checksums_are_required_before_open(tmp_path):
    record = resource()
    del record["files"][0]["sha256"]
    manifest = {
        "snapshot_version": 1,
        "api_url": "https://api.example/api",
        "release": "1.0",
        "resources": [record],
    }
    (tmp_path / "snapshot.json").write_text(json.dumps(manifest))
    with pytest.raises(ClientError, match="checksum"):
        Client.from_snapshot(tmp_path)
    manifest["snapshot_version"] = True
    with pytest.raises(ClientError, match="snapshot version"):
        validate_snapshot(manifest)


def test_additive_metadata_is_preserved_without_aliasing():
    record = resource()
    catalog = validate_catalog([record])
    assert catalog["alpha"]["description"] == "Additive metadata"
    record["files"].clear()
    assert len(catalog["alpha"]["files"]) == 2


@pytest.mark.parametrize("payload", [None, [], {"resources": None}, {"resources": "alpha"}])
def test_invalid_catalog_envelopes(payload):
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))
    ) as http:
        with Client(http_client=http) as client, pytest.raises(ClientError):
            client.resources()
