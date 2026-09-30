"""Lossless round trips and failed loads are checked against real PostgreSQL."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import uuid

import psycopg
from psycopg import sql
import pyarrow.parquet as pq
import pytest

from omnipath_postgres import loader
from omnipath_postgres.releases import ReleaseValidationError
from test_projection import (
    ENTITY_A,
    ENTITY_B,
    ENTITY_C,
    RELATION,
    QUANTITY,
    fixture_rows,
    write_fixture,
)

pytestmark = pytest.mark.integration


def destination():
    return "test_" + uuid.uuid4().hex


def resource(root, source="signor", version="1.0.0", *, rows=None):
    directory = root / "resources" / source / version
    if rows is None:
        rows = fixture_rows()
    write_fixture(directory, entities=rows[0], relations=rows[1], payloads=rows[2])
    manifest = {
        "schema_version": 1,
        "serving_schema_version": 3,
        "resource": source,
        "version": version,
        "max_records": 20,
        "files": {
            name: {
                "rows": pq.read_metadata(directory / name).num_rows,
                "size_bytes": (directory / name).stat().st_size,
                "sha256": hashlib.sha256((directory / name).read_bytes()).hexdigest(),
            }
            for name in ("entities.parquet", "relations.parquet", "evidence_payloads.parquet")
        },
    }
    (directory / "build_manifest.json").write_text(json.dumps(manifest))
    return directory


def release(root, resources=None):
    path = root / "release.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "version": "2026.09",
                "resources": resources or {"signor": "1.0.0"},
            }
        )
    )
    return path


def query(dsn, schema, statement, params=None):
    with psycopg.connect(dsn) as conn:
        return conn.execute(sql.SQL(statement).format(s=sql.Identifier(schema)), params).fetchall()


def exists(dsn, schema):
    with psycopg.connect(dsn) as conn:
        return conn.execute("SELECT 1 FROM pg_namespace WHERE nspname=%s", (schema,)).fetchone()


def test_measurements_qualifiers_duplicate_occurrences_without_raw_storage(tmp_path, postgres_dsn):
    original = fixture_rows()
    resource(tmp_path, rows=original)
    schema = destination()
    result = loader.load_release(
        tmp_path, release(tmp_path), postgres_dsn, schema=schema, batch_size=1
    )
    assert result.counts == {
        "entities": 3,
        "identifiers": 2,
        "relations": 1,
        "evidence": 2,
        "annotations": 9,
    }
    actual = query(postgres_dsn, schema, "SELECT entity_key, record_json FROM {s}.entities")
    assert dict(actual) == {row["entity_key"]: row for row in original[0]}
    assert (
        query(postgres_dsn, schema, "SELECT record_json FROM {s}.relations")[0][0] == original[1][0]
    )
    assert result.validated_payload_rows == {"signor": 2}
    assert query(postgres_dsn, schema, "SELECT to_regclass(%s)", (schema + ".payloads",)) == [
        (None,)
    ]
    assert (
        query(
            postgres_dsn,
            schema,
            "SELECT column_name FROM information_schema.columns WHERE table_schema=%s AND column_name='payload_json'",
            (schema,),
        )
        == []
    )
    assert query(
        postgres_dsn, schema, "SELECT row_id, upstream_id FROM {s}.evidence ORDER BY ordinal"
    ) == [
        ("00001", "SRC-42"),
        ("00001", "SRC-42"),
    ]
    quantities = query(
        postgres_dsn,
        schema,
        "SELECT owner_kind, quantity, has_numeric_value, has_unit, comparator "
        "FROM {s}.annotation_quantities ORDER BY annotation_id",
    )
    assert len(quantities) == 4
    assert {row[0] for row in quantities} == {"entity", "relation", "evidence"}
    assert all(row[1:] == (QUANTITY, 12.5, "UO:0000061", "<=") for row in quantities)
    assert query(
        postgres_dsn,
        schema,
        "SELECT owner_kind, scope, value FROM {s}.annotations "
        "WHERE term='object_direction_qualifier'",
    ) == [("relation", "relation", "decreased")]
    counts = dict(
        query(
            postgres_dsn, schema, "SELECT entity_id, relation_count FROM {s}.entity_relation_counts"
        )
    )
    assert counts == {ENTITY_A: 1, ENTITY_B: 1, ENTITY_C: 0}
    pins = query(postgres_dsn, schema, "SELECT resource, version FROM {s}.resource_versions")
    assert pins == [("signor", "1.0.0")]
    metadata = query(
        postgres_dsn, schema, "SELECT release_id, manifest_sha256 FROM {s}.release_metadata"
    )
    assert metadata == [("2026.09", result.manifest_sha256)]
    assert (
        query(
            postgres_dsn, schema, "SELECT count(*) FROM pg_indexes WHERE schemaname=%s", (schema,)
        )[0][0]
        >= 20
    )


def test_shared_identity_aggregates_resources_and_qualified_relations_stay_separate(
    tmp_path, postgres_dsn
):
    resource(tmp_path, "signor")
    other = deepcopy(fixture_rows())
    other[0][0]["label"] = "Another source label"
    other[0][0]["identifiers"].append(
        {"ns": "hgnc_symbol", "id": "TP53", "is_canonical": False, "source": "other"}
    )
    other[1][0]["sources"] = ["other"]
    # Same endpoints/predicate, a different qualifier and published relation key.
    qualified = deepcopy(other[1][0])
    qualified["relation_key"] = RELATION + "/qualified"
    qualified["annotations"][1]["value"] = "increased"
    other[1].append(qualified)
    resource(tmp_path, "other", "2.1", rows=other)
    resource(tmp_path, "signor", "9.9", rows=([], [], []))  # Must not choose the newest version.
    schema = destination()
    loader.load_release(
        tmp_path,
        release(tmp_path, {"signor": "1.0.0", "other": "2.1"}),
        postgres_dsn,
        schema=schema,
        batch_size=2,
    )
    assert query(postgres_dsn, schema, "SELECT count(*) FROM {s}.entity") == [(3,)]
    canonical = dict(
        query(postgres_dsn, schema, "SELECT relation_id, evidence_count FROM {s}.relation")
    )
    assert canonical == {RELATION: 4, RELATION + "/qualified": 2}
    assert query(
        postgres_dsn, schema, "SELECT sources FROM {s}.relation WHERE relation_id=%s", (RELATION,)
    ) == [
        (["other", "other-source", "reported-source"],),
    ]
    records = query(
        postgres_dsn,
        schema,
        "SELECT resource_records FROM {s}.relation WHERE relation_id=%s",
        (RELATION,),
    )[0][0]
    assert {record["resource"] for record in records} == {"signor", "other"}
    assert query(
        postgres_dsn,
        schema,
        "SELECT entity_id FROM {s}.entity_identifier_lookup WHERE ns='hgnc_symbol' AND id='TP53'",
    ) == [(ENTITY_A,)]
    assert dict(
        query(
            postgres_dsn, schema, "SELECT entity_id, relation_count FROM {s}.entity_relation_counts"
        )
    ) == {
        ENTITY_A: 2,
        ENTITY_B: 2,
        ENTITY_C: 0,
    }
    assert set(
        query(postgres_dsn, schema, "SELECT resource, version FROM {s}.resource_versions")
    ) == {
        ("signor", "1.0.0"),
        ("other", "2.1"),
    }


def test_self_loop_counted_once_and_empty_resource_loads(tmp_path, postgres_dsn):
    rows = deepcopy(fixture_rows())
    rows[1][0]["object_entity_key"] = ENTITY_A
    rows[1][0]["object_type"] = "protein"
    resource(tmp_path, rows=rows)
    resource(tmp_path, "empty", rows=([], [], []))
    schema = destination()
    loader.load_release(
        tmp_path,
        release(tmp_path, {"signor": "1.0.0", "empty": "1.0.0"}),
        postgres_dsn,
        schema=schema,
    )
    assert dict(
        query(
            postgres_dsn, schema, "SELECT entity_id, relation_count FROM {s}.entity_relation_counts"
        )
    ) == {
        ENTITY_A: 1,
        ENTITY_B: 0,
        ENTITY_C: 0,
    }


@pytest.mark.parametrize(
    "failure",
    ["endpoint", "payload", "payload_owner", "evidence_count", "shared_entity", "shared_relation"],
)
def test_late_validation_failure_rolls_back_entire_schema(tmp_path, postgres_dsn, failure):
    rows = deepcopy(fixture_rows())
    resources = {"signor": "1.0.0"}
    if failure == "endpoint":
        rows[1][0]["object_entity_key"] = "absent"
    elif failure == "payload":
        rows[2][1]["payload_json"] = "{invalid"
    elif failure == "payload_owner":
        rows[2][1]["entity_key"] = "missing payload owner"
    elif failure == "evidence_count":
        rows[1][0]["evidence_count"] = 99
    elif failure.startswith("shared"):
        resource(tmp_path, "other")
        resources["other"] = "1.0.0"
        if failure == "shared_entity":
            rows[0][0]["identifier"] = "different"
        else:
            rows[1][0]["predicate"] = "different"
    resource(tmp_path, rows=rows)
    schema = destination()
    with pytest.raises((ValueError, psycopg.errors.ForeignKeyViolation)):
        loader.load_release(
            tmp_path, release(tmp_path, resources), postgres_dsn, schema=schema, batch_size=1
        )
    assert not exists(postgres_dsn, schema)


def test_corrupt_artifact_fails_before_connecting(tmp_path, monkeypatch):
    directory = resource(tmp_path)
    (directory / "entities.parquet").write_bytes(b"corrupt")

    def unexpected(*args, **kwargs):
        pytest.fail("Corrupt release must not connect")

    monkeypatch.setattr(loader.psycopg, "connect", unexpected)
    with pytest.raises(ReleaseValidationError):
        loader.load_release(tmp_path, release(tmp_path), "unused", schema=destination())


def test_changed_manifest_during_load_rolls_back_without_repinning(
    tmp_path, postgres_dsn, monkeypatch
):
    resource(tmp_path)
    manifest = release(tmp_path)
    original = loader.rebuild_derived

    def change_after_copy(conn, schema):
        original(conn, schema)
        manifest.write_text(manifest.read_text() + "\n")

    monkeypatch.setattr(loader, "rebuild_derived", change_after_copy)
    schema = destination()
    with pytest.raises(ReleaseValidationError, match="changed"):
        loader.load_release(tmp_path, manifest, postgres_dsn, schema=schema)
    assert not exists(postgres_dsn, schema)


def test_repeated_destination_preserves_committed_release(tmp_path, postgres_dsn):
    resource(tmp_path)
    manifest = release(tmp_path)
    schema = destination()
    first = loader.load_release(tmp_path, manifest, postgres_dsn, schema=schema)
    with pytest.raises(ValueError, match="already exists"):
        loader.load_release(tmp_path, manifest, postgres_dsn, schema=schema)
    assert query(postgres_dsn, schema, "SELECT manifest_sha256 FROM {s}.release_metadata") == [
        (first.manifest_sha256,)
    ]
    assert query(postgres_dsn, schema, "SELECT count(*) FROM {s}.evidence") == [(2,)]
    assert query(postgres_dsn, schema, "SELECT to_regclass(%s)", (schema + ".payloads",)) == [
        (None,)
    ]


def test_existing_twenty_record_signor_artifact(tmp_path, postgres_dsn):
    root = Path(__file__).resolve().parents[3] / "data/migration-smoke"
    if not (root / "resources/signor/0.1.1/build_manifest.json").exists():
        pytest.skip("Run make sample VERSION=0.1.1 for the existing bounded build")
    manifest = tmp_path / "release.json"
    manifest.write_text(
        json.dumps({"schema_version": 1, "version": "2026.09", "resources": {"signor": "0.1.1"}})
    )
    schema = destination()
    result = loader.load_release(root, manifest, postgres_dsn, schema=schema, batch_size=3)
    assert result.counts["entities"] == 2
    assert result.counts["relations"] == 2
    assert result.counts["evidence"] == 20
    assert result.validated_payload_rows["signor"] == 20
    assert "payloads" not in result.counts
    original = pq.read_table(root / "resources/signor/0.1.1/relations.parquet").to_pylist()
    assert dict(
        query(postgres_dsn, schema, "SELECT relation_key, record_json FROM {s}.relations")
    ) == {row["relation_key"]: row for row in original}
    assert all(
        len(key) == 64 for (key,) in query(postgres_dsn, schema, "SELECT entity_id FROM {s}.entity")
    )


@pytest.mark.parametrize("other_taxon", ["10090", "", None])
def test_taxon_assertions_are_preserved_and_canonical_projection_is_consensus(
    tmp_path, postgres_dsn, other_taxon
):
    resource(tmp_path)
    rows = deepcopy(fixture_rows())
    rows[0][0]["taxon"] = other_taxon
    rows[1][0]["taxon"] = other_taxon
    resource(tmp_path, "other", rows=rows)
    schema = destination()
    loader.load_release(
        tmp_path,
        release(tmp_path, {"signor": "1.0.0", "other": "1.0.0"}),
        postgres_dsn,
        schema=schema,
    )
    assert query(
        postgres_dsn, schema, "SELECT taxon FROM {s}.entity WHERE entity_id=%s", (ENTITY_A,)
    ) == [("",)]
    assert query(postgres_dsn, schema, "SELECT taxon FROM {s}.relation") == [("",)]
    assert dict(
        query(
            postgres_dsn,
            schema,
            "SELECT resource, taxon FROM {s}.entities WHERE entity_key=%s",
            (ENTITY_A,),
        )
    ) == {
        "other": other_taxon,
        "signor": "9606",
    }
    assert dict(
        query(postgres_dsn, schema, "SELECT resource, record_json->>'taxon' FROM {s}.relations")
    ) == {
        "other": other_taxon,
        "signor": "9606",
    }


def test_shared_key_with_conflicting_qualifiers_rolls_back(tmp_path, postgres_dsn):
    first = deepcopy(fixture_rows())
    first[1][0]["annotations"].append(
        {
            "term": "object_aspect_qualifier",
            "value": "activity",
            "quantity": None,
            "source": "signor",
            "dataset": "fixture",
            "scope": "relation",
        }
    )
    resource(tmp_path, rows=first)
    second = deepcopy(first)
    second[1][0]["annotations"][-1]["value"] = "abundance"
    resource(tmp_path, "other", rows=second)
    schema = destination()
    with pytest.raises(ValueError, match="qualifiers disagree"):
        loader.load_release(
            tmp_path,
            release(tmp_path, {"signor": "1.0.0", "other": "1.0.0"}),
            postgres_dsn,
            schema=schema,
        )
    assert not exists(postgres_dsn, schema)


def test_shared_key_normalizes_qualifier_spelling_and_ignores_ordinary_annotations(
    tmp_path, postgres_dsn
):
    resource(tmp_path)
    rows = deepcopy(fixture_rows())
    rows[1][0]["annotations"][1]["term"] = "biolink:object_direction_qualifier"
    rows[1][0]["annotations"].append(
        {
            "term": "description",
            "value": "Additional source evidence",
            "quantity": None,
            "source": "other",
            "dataset": "fixture",
            "scope": "relation",
        }
    )
    resource(tmp_path, "other", rows=rows)
    schema = destination()
    loader.load_release(
        tmp_path,
        release(tmp_path, {"signor": "1.0.0", "other": "1.0.0"}),
        postgres_dsn,
        schema=schema,
    )
    assert query(postgres_dsn, schema, "SELECT count(*) FROM {s}.relation") == [(1,)]


def test_original_manifests_and_their_checksums_are_recoverable(tmp_path, postgres_dsn):
    directory = resource(tmp_path)
    manifest = release(tmp_path)
    schema = destination()
    loader.load_release(tmp_path, manifest, postgres_dsn, schema=schema)
    rows = query(
        postgres_dsn, schema, "SELECT manifest_text, manifest_sha256 FROM {s}.resource_versions"
    )
    assert rows[0][0] == (directory / "build_manifest.json").read_text()
    assert hashlib.sha256(rows[0][0].encode()).hexdigest() == rows[0][1]
    rows = query(
        postgres_dsn,
        schema,
        "SELECT manifest_text, input_manifest_sha256, manifest_json, manifest_sha256 FROM {s}.release_metadata",
    )
    assert rows[0][0] == manifest.read_text()
    assert hashlib.sha256(rows[0][0].encode()).hexdigest() == rows[0][1]
    canonical = json.dumps(rows[0][2], sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    assert hashlib.sha256(canonical.encode()).hexdigest() == rows[0][3]


def test_cli_loads_release_and_emits_inspection_counts(tmp_path, postgres_dsn):
    import os
    import subprocess
    import sys

    resource(tmp_path)
    manifest = release(tmp_path)
    schema = destination()
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "omnipath_postgres",
            str(manifest),
            "--data-root",
            str(tmp_path),
            "--schema",
            schema,
            "--batch-size",
            "2",
        ],
        env={**os.environ, "OMNIPATH_DATABASE_URL": postgres_dsn},
        text=True,
        capture_output=True,
        check=True,
        timeout=30,
    )
    report = json.loads(result.stdout)
    assert report["resources"] == {"signor": "1.0.0"}
    assert report["counts"]["evidence"] == 2
    assert query(postgres_dsn, schema, "SELECT count(*) FROM {s}.release_metadata") == [(1,)]


def test_join_statistics_available_during_load_and_for_consumers(
    tmp_path, postgres_dsn, monkeypatch
):
    resource(tmp_path)
    schema = destination()
    validate = loader._validate_loaded
    observed = []

    def validate_with_statistics(conn, destination_schema):
        statistics = conn.execute(
            "SELECT DISTINCT tablename FROM pg_stats WHERE schemaname=%s",
            (destination_schema,),
        ).fetchall()
        assert set(loader.COLUMNS) <= {name for (name,) in statistics}
        observed.append(destination_schema)
        validate(conn, destination_schema)

    monkeypatch.setattr(loader, "_validate_loaded", validate_with_statistics)
    loader.load_release(tmp_path, release(tmp_path), postgres_dsn, schema=schema)
    assert observed == [schema]
    statistics = query(
        postgres_dsn,
        schema,
        "SELECT DISTINCT tablename FROM pg_stats WHERE schemaname=%s",
        (schema,),
    )
    assert "entity_relation_counts" in {name for (name,) in statistics}
