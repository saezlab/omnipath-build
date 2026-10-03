"""Live Parquet resource updates do not mutate a loaded monthly PostgreSQL snapshot."""

import importlib.util
import json
from pathlib import Path
import uuid

import psycopg
from psycopg import sql
import pytest

from omnipath_postgres.compatibility.record_layout.loader import load_release


pytestmark = pytest.mark.integration


def fixture_helpers():
    path = Path(__file__).resolve().parents[2] / "subsets/tests/release_fixture.py"
    spec = importlib.util.spec_from_file_location("monthly_release_fixture", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_version(root, version, marker):
    fixture = fixture_helpers()
    subject = fixture.entity("P04637", "protein", "uniprot", taxon="9606", label=f"TP53 {marker}")
    target = fixture.entity("P38398", "protein", "uniprot", taxon="9606", label="BRCA1")
    edge = fixture.relation(
        subject,
        "affects",
        target,
        source="signor",
        dataset="interactions",
        upstream_id=f"SIGNOR-{marker}",
    )
    raw = fixture.payload(edge, {"version_marker": marker}, source="signor")
    fixture.write_resource(root, "signor", [subject, target], [edge], [raw], version)
    return edge


def database_snapshot(dsn, schema):
    with psycopg.connect(dsn) as conn:
        result = {}
        for table in (
            "entities",
            "identifiers",
            "relations",
            "evidence",
            "annotations",
            "resource_versions",
            "release_metadata",
        ):
            # Bounded fixture rows only; metadata JSONB, original JSON text and
            # loading timestamp are all compared, not just record counts.
            rows = conn.execute(
                sql.SQL("SELECT * FROM {}.{}").format(sql.Identifier(schema), sql.Identifier(table))
            ).fetchall()
            result[table] = sorted(rows, key=repr)
        return result


def test_latest_api_advances_without_changing_loaded_monthly_postgres(tmp_path, postgres_dsn):
    pytest.importorskip("omnipath_api")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient
    from omnipath_api.engine import ParquetServingEngine
    from omnipath_api.releases import ReleaseStore
    from omnipath_api.server import create_app
    from omnipath_api.settings import Settings

    old = write_version(tmp_path, "1.9.0", "old")
    with pytest.warns(UserWarning, match="taxonomy reference"):
        ReleaseStore(tmp_path).publish(
            dict(schema_version=1, version="2026.09", resources={"signor": "1.9.0"})
        )
    manifest_path = tmp_path / "releases/2026.09.json"
    manifest_bytes = manifest_path.read_bytes()
    schema = "monthly_" + uuid.uuid4().hex
    loaded = load_release(tmp_path, manifest_path, postgres_dsn, schema=schema)
    before = database_snapshot(postgres_dsn, schema)
    assert len(before["relations"]) == 1
    assert before["resource_versions"][0][:2] == ("signor", "1.9.0")
    assert before["release_metadata"][0][0] == "2026.09"
    assert json.loads(before["release_metadata"][0][2])["resources"] == {"signor": "1.9.0"}
    engine = ParquetServingEngine(tmp_path)
    with TestClient(create_app(engine=engine, settings=Settings(read_only=True))) as client:
        first = client.get(f"/api/relations/{old['relation_key']}/payloads")
        assert first.status_code == 200
        assert first.json()["payloads"][0]["payload"]["version_marker"] == "old"
        write_version(tmp_path, "1.10.0", "new")
        for release in (None, "latest"):
            params = {"shape": "svelte", **({"release": release} if release else {})}
            catalog = client.get("/api/resources", params=params)
            assert catalog.status_code == 200
            assert [
                (row["resource_id"], row["version"]) for row in catalog.json()["resources"]
            ] == [("signor", "1.10.0")]
            latest = client.get(
                f"/api/relations/{old['relation_key']}/payloads",
                params={"release": release} if release else {},
            )
            assert latest.json()["payloads"][0]["payload"]["version_marker"] == "new"
        pinned = client.get(
            f"/api/relations/{old['relation_key']}/payloads", params={"release": "2026.09"}
        )
        assert pinned.status_code == 200
        assert pinned.json()["payloads"][0]["payload"]["version_marker"] == "old"
    after = database_snapshot(postgres_dsn, schema)
    assert after == before
    assert manifest_path.read_bytes() == manifest_bytes
    assert after["release_metadata"][0][3] == loaded.manifest_sha256
