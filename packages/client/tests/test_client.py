"""Network-independent integration tests using real Parquet and DuckDB."""

import json

import duckdb
import httpx
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from omnipath_client import Client, ClientError


@pytest.fixture
def service():
    requests, artifacts, catalog = [], {}, []
    for resource, version in [("alpha", "1.0"), ("beta", "2.0")]:
        entities = pa.Table.from_pylist(
            [
                {
                    "entity_key": "key-a",
                    "identifier": "P04637",
                    "label": "TP53",
                    "taxon": "9606",
                    "entity_type": "protein",
                    "identifiers": [
                        {"ns": "hgnc", "id": "11998", "is_canonical": False, "source": resource}
                    ],
                },
                {
                    "entity_key": "key-b",
                    "identifier": "CHEBI:27732",
                    "label": "caffeine",
                    "taxon": None,
                    "entity_type": "small_molecule",
                    "identifiers": [],
                },
                {
                    "entity_key": "key-c",
                    "identifier": "quote",
                    "label": "O'Brien",
                    "taxon": "9606",
                    "entity_type": "protein",
                    "identifiers": [],
                },
            ]
        )
        relations = pa.Table.from_pylist(
            [
                {
                    "relation_key": "r1",
                    "subject_entity_key": "key-a",
                    "object_entity_key": "key-b",
                    "subject_label": "TP53",
                    "object_label": "caffeine",
                    "predicate": "affects",
                    "taxon": "9606",
                },
                {
                    "relation_key": "r2",
                    "subject_entity_key": "key-c",
                    "object_entity_key": "key-a",
                    "subject_label": "O'Brien",
                    "object_label": "TP53",
                    "predicate": "interacts_with",
                    "taxon": "9606",
                },
            ]
        )
        # Union-by-name must tolerate additive schema changes across resources.
        if resource == "beta":
            entities = entities.append_column("new_field", pa.array(["x", "x", "x"]))
        evidence = pa.table(
            {"relation_key": ["r1"], "source": [resource], "row_id": ["0"], "payload_json": ["{}"]}
        )
        files = []
        for name, table in [
            ("entities", entities),
            ("relations", relations),
            ("evidence_payloads", evidence),
        ]:
            buffer = pa.BufferOutputStream()
            pq.write_table(table, buffer)
            payload = buffer.getvalue().to_pybytes()
            url = f"https://data.example/resources/{resource}/{version}/{name}.parquet"
            artifacts[url] = payload
            files.append(
                {
                    "name": f"{name}.parquet",
                    "url": url,
                    "size_bytes": len(payload),
                    "rows": len(table),
                }
            )
        catalog.append(
            {"resource_id": resource, "version": version, "files": files[:2], "all_files": files}
        )

    def respond(request):
        requests.append(request)
        if str(request.url) in artifacts:
            return httpx.Response(200, content=artifacts[str(request.url)])
        if request.url.path == "/api/resources":
            return httpx.Response(
                200,
                json={
                    "resources": [{k: v for k, v in r.items() if k != "all_files"} for r in catalog]
                },
            )
        if request.url.path == "/api/releases":
            return httpx.Response(
                200,
                json={
                    "releases": [
                        {"version": "2026.09", "resources": {"alpha": "1.0", "beta": "2.0"}}
                    ]
                },
            )
        if request.url.path.startswith("/api/resources/"):
            record = next(r for r in catalog if r["resource_id"] == request.url.path.split("/")[3])
            return httpx.Response(200, json={**record, "files": record["all_files"]})
        raise AssertionError(f"Unexpected request: {request.url}")

    with httpx.Client(transport=httpx.MockTransport(respond)) as http:
        with Client("https://api.example/api", release="2026.09", http_client=http) as client:
            yield client, requests, artifacts, catalog


def test_discovery_is_lazy_and_pinned(service):
    client, requests, _, catalog = service
    assert requests == []
    result = client.resources()
    result[0]["files"].clear()
    catalog[0]["version"] = "9.0"
    assert client.resources()[0]["version"] == "1.0"
    assert len(client.files("alpha")) == 2
    assert len(requests) == 1
    assert requests[0].url.params["release"] == "2026.09"
    assert client.releases()[0]["version"] == "2026.09"


def test_offline_queries_and_provenance(service, tmp_path, monkeypatch):
    client, requests, _, _ = service
    snapshot = client.download(tmp_path / "snapshot")
    assert not any("evidence_payloads" in str(r.url) for r in requests)

    def forbidden(*args, **kwargs):
        raise AssertionError("Offline query attempted HTTP")

    monkeypatch.setattr(httpx.Client, "get", forbidden)
    with Client.from_snapshot(snapshot) as offline:
        rows = offline.entities(
            filters={"taxon": ["9606", None]},
            columns=["identifier", "_resource", "_resource_version"],
        ).fetchall()
        assert len(rows) == 6
        assert {row[1:] for row in rows} == {("alpha", "1.0"), ("beta", "2.0")}
        assert offline.entities("alpha", filters={"label": "O'Brien"}).fetchone()[2] == "O'Brien"
        assert offline.entities("alpha", filters={"label": "' OR TRUE --"}).fetchall() == []
        assert offline.entities("alpha", filters={"taxon": []}).fetchall() == []
        assert offline.entities("alpha", filters={"taxon": None}).fetchone()[2] == "caffeine"
        assert offline.entities(["alpha", "alpha"]).count("*").fetchone() == (3,)
        assert offline.entities().filter("new_field IS NULL").count("*").fetchone() == (3,)
        assert not offline._httpfs_loaded
        with pytest.raises(ClientError, match="offline"):
            offline.releases()
        with pytest.raises(ClientError, match="lacks evidence"):
            offline.evidence("alpha")


def test_sql_joins_and_lookup(service, tmp_path):
    client, _, _, _ = service
    snapshot = client.download(tmp_path / "snapshot", include_evidence=True)
    with Client.from_snapshot(snapshot) as offline:
        assert offline.lookup("tp53").count("*").fetchone() == (2,)
        assert offline.lookup("11998", resources="alpha").fetchone()[2] == "TP53"
        assert offline.lookup("O'Brien", resources="alpha").count("*").fetchone() == (1,)
        assert offline.lookup("key-b", resources="alpha").fetchone()[2] == "caffeine"
        assert offline.related("TP53", resources="alpha").count("*").fetchone() == (2,)
        assert offline.related(subject="TP53", resources="alpha").count("*").fetchone() == (1,)
        assert offline.related(
            subject="TP53", object="caffeine", resources=["alpha", "beta"]
        ).count("*").fetchone() == (2,)
        assert (
            offline.related(subject="caffeine", object="TP53", resources="alpha").fetchall() == []
        )
        joined = offline.sql(
            """SELECT e.label, r._resource FROM entities e
            JOIN relations r ON e.entity_key = r.subject_entity_key AND e._resource = r._resource
            WHERE e.identifier = ?""",
            resources=["alpha", "beta"],
            parameters=["P04637"],
        )
        assert set(joined.fetchall()) == {("TP53", "alpha"), ("TP53", "beta")}
        assert offline.evidence("alpha").count("*").fetchone() == (1,)
        assert offline.sql(
            "SELECT count(*) FROM evidence_payloads", resources="alpha", include_evidence=True
        ).fetchone() == (1,)
        with pytest.raises(duckdb.CatalogException):
            offline.sql("SELECT * FROM evidence_payloads", resources="alpha")
        alpha = offline.sql("SELECT DISTINCT _resource FROM entities;", resources="alpha")
        beta = offline.sql(
            "WITH selected AS (SELECT * FROM entities) SELECT DISTINCT _resource FROM selected",
            resources="beta",
        )
        assert alpha.fetchall() == [("alpha",)]
        assert beta.fetchall() == [("beta",)]


def test_latest_cannot_mix_evidence_versions(service):
    client, requests, _, catalog = service
    client.resources()
    catalog[0]["version"] = "3.0"
    with pytest.raises(ClientError, match="changed since discovery"):
        client.files("alpha", include_evidence=True)
    assert client.resources()[0]["version"] == "1.0"
    assert requests[-1].url.params["include_evidence"] == "true"


def test_failed_download_is_not_published(service, tmp_path):
    client, _, artifacts, _ = service
    key = next(iter(artifacts))
    artifacts[key] = artifacts[key][:10]
    with pytest.raises(ClientError, match="Truncated"):
        client.download(tmp_path / "broken", resources="alpha")
    assert list(tmp_path.iterdir()) == []


def test_corruption_and_existing_destination(service, tmp_path):
    client, _, _, _ = service
    snapshot = client.download(tmp_path / "snapshot", resources="alpha")
    with pytest.raises(FileExistsError):
        client.download(snapshot)
    path = snapshot / "resources/alpha/1.0/entities.parquet"
    data = bytearray(path.read_bytes())
    data[10] ^= 1
    path.write_bytes(data)
    with Client.from_snapshot(snapshot) as offline:
        with pytest.raises(ClientError, match="checksum mismatch"):
            offline.entities("alpha")


def test_snapshot_path_escape_rejected(service, tmp_path):
    client, _, _, _ = service
    snapshot = client.download(tmp_path / "snapshot", resources="alpha")
    manifest = snapshot / "snapshot.json"
    data = json.loads(manifest.read_text())
    data["resources"][0]["resource_id"] = "../escape"
    manifest.write_text(json.dumps(data))
    with pytest.raises(ClientError, match="Invalid resource"):
        Client.from_snapshot(snapshot)


def test_validation_and_lifecycle(service, tmp_path):
    client, _, _, _ = service
    with pytest.raises(ValueError, match="Unknown resources"):
        client.files("missing")
    with pytest.raises(ValueError, match="at least one"):
        client.download(tmp_path / "empty", resources=[])
    with pytest.raises(ValueError, match="table must"):
        client.table("legacy_table")
    snapshot = client.download(tmp_path / "snapshot", resources="alpha")
    offline = Client.from_snapshot(snapshot)
    with pytest.raises(ValueError, match="columns"):
        offline.entities(columns="label")
    with pytest.raises(ValueError, match="Supply"):
        offline.related(resources="alpha")
    with pytest.raises(ValueError, match="non-empty"):
        offline.lookup([])
    relation = offline.entities()
    offline.close()
    offline.close()
    with pytest.raises(ClientError, match="closed"):
        offline.entities()
    with pytest.raises(duckdb.Error):
        relation.fetchall()


def test_missing_url_and_http_failure(service, tmp_path):
    client, _, _, catalog = service
    catalog[0]["files"][0]["url"] = None
    with pytest.raises(ClientError, match="static artifact URLs"):
        client.entities("alpha")
    with pytest.raises(ClientError, match="static download URL"):
        client.download(tmp_path / "no-url", resources="alpha")
    assert not (tmp_path / "no-url").exists()
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(503))) as http:
        with Client(http_client=http) as failed:
            with pytest.raises(httpx.HTTPStatusError):
                failed.resources()


def test_snapshot_survives_move_and_rejects_symlinks(service, tmp_path):
    client, _, _, _ = service
    snapshot = client.download(tmp_path / "snapshot", resources="alpha")
    moved = tmp_path / "moved"
    snapshot.rename(moved)
    with Client.from_snapshot(moved) as offline:
        assert offline.entities().count("*").fetchone() == (3,)
    artifact = moved / "resources/alpha/1.0/entities.parquet"
    outside = tmp_path / "outside.parquet"
    artifact.rename(outside)
    artifact.symlink_to(outside)
    with Client.from_snapshot(moved) as offline:
        with pytest.raises(ClientError, match="escapes"):
            offline.entities()
