"""Discarded source bodies are audited only on request; artifact checks stay mandatory."""

from collections import Counter
from copy import deepcopy
import hashlib
import json
import uuid

import pytest

from omnipath_postgres.compatibility.record_layout import loader
from omnipath_postgres.releases import ReleaseValidationError
from test_postgres import exists, fixture_rows, query, release, resource
from test_reactions import reaction_fixture


def unexpected_audit(*args, **kwargs):
    pytest.fail("Default imports must not decode or query discarded raw records")


def prohibit_audit(monkeypatch):
    for name in (
        "iter_validated_payloads",
        "_validate_payload_owners",
        "_validate_reaction_payloads",
    ):
        monkeypatch.setattr(loader, name, unexpected_audit)


@pytest.mark.integration
@pytest.mark.parametrize("source", ["signor", "rhea"])
def test_default_skips_malformed_and_orphan_raw_but_keeps_exact_stored_records(
    tmp_path, postgres_dsn, monkeypatch, source
):
    rows = reaction_fixture() if source == "rhea" else fixture_rows()
    rows[2][0]["payload_json"] = "{invalid discarded JSON"
    rows[2][1]["relation_key"] = "absent discarded raw owner"
    rows[2][1]["entity_key"] = None
    directory = resource(tmp_path, source, rows=rows)
    original = loader._analyze_tables
    analyses = []

    def analyze(conn, schema, tables, *, columns=None):
        assert columns is None  # Audit-specific narrow statistics are unnecessary.
        analyses.append(tuple(tables))
        return original(conn, schema, tables, columns=columns)

    monkeypatch.setattr(loader, "_analyze_tables", analyze)
    prohibit_audit(monkeypatch)
    schema = "source_audit_" + uuid.uuid4().hex
    result = loader.load_release(
        tmp_path, release(tmp_path, {source: "1.0.0"}), postgres_dsn, schema=schema
    )
    assert result.validate_source_records is False
    assert result.validated_payload_rows == {}
    assert not any(name.startswith("analyze_payload_lookup_") for name in result.phase_seconds)
    assert len(analyses) == 2  # Stored-row validation and populated derived tables.
    for table, records, key in (
        ("entities", rows[0], "entity_key"),
        ("relations", rows[1], "relation_key"),
    ):
        assert dict(
            query(postgres_dsn, schema, f"SELECT {key},record_json FROM {{s}}.{table}")
        ) == {record[key]: record for record in records}
        assert result.counts[table] == len(records)
    assert (
        result.counts["evidence"]
        == query(postgres_dsn, schema, "SELECT count(*) FROM {s}.evidence")[0][0]
    )
    metadata = query(
        postgres_dsn, schema, "SELECT manifest_json,parquet_checksums FROM {s}.resource_versions"
    )[0]
    original_manifest = json.loads((directory / "build_manifest.json").read_text())
    assert metadata[0] == original_manifest
    assert metadata[0]["files"]["evidence_payloads.parquet"]["rows"] == 2
    assert (
        metadata[1]["evidence_payloads.parquet"]
        == hashlib.sha256((directory / "evidence_payloads.parquet").read_bytes()).hexdigest()
    )
    if source == "rhea":
        assert query(postgres_dsn, schema, "SELECT count(*) FROM {s}.reaction_context") == [(1,)]
        assert query(postgres_dsn, schema, "SELECT count(*) FROM {s}.reaction_participant") == [
            (2,)
        ]


def mutate_raw_file(path):
    # Change a data-page byte, leaving file size and the complete footer intact.
    content = bytearray(path.read_bytes())
    content[4] ^= 1
    path.write_bytes(content)


@pytest.mark.parametrize("audit", [False, True])
def test_both_modes_check_raw_checksum_before_connecting(tmp_path, monkeypatch, audit):
    directory = resource(tmp_path)
    mutate_raw_file(directory / "evidence_payloads.parquet")
    prohibit_audit(monkeypatch)

    def unexpected_connection(*args, **kwargs):
        pytest.fail("A mismatched raw artifact checksum must fail before connecting")

    monkeypatch.setattr(loader.psycopg, "connect", unexpected_connection)
    with pytest.raises(ReleaseValidationError, match="checksum"):
        loader.load_release(
            tmp_path,
            release(tmp_path),
            "unused",
            schema="raw_checksum",
            validate_source_records=audit,
        )


@pytest.mark.integration
@pytest.mark.parametrize("audit", [False, True])
def test_both_modes_recheck_raw_checksum_before_atomic_commit(
    tmp_path, postgres_dsn, monkeypatch, audit
):
    directory = resource(tmp_path)
    original = loader.rebuild_derived

    def mutate_after_copy(conn, schema):
        original(conn, schema)
        mutate_raw_file(directory / "evidence_payloads.parquet")

    if not audit:
        prohibit_audit(monkeypatch)
    monkeypatch.setattr(loader, "rebuild_derived", mutate_after_copy)
    schema = "source_audit_" + uuid.uuid4().hex
    with pytest.raises(ReleaseValidationError, match="checksum"):
        loader.load_release(
            tmp_path,
            release(tmp_path),
            postgres_dsn,
            schema=schema,
            validate_source_records=audit,
        )
    assert not exists(postgres_dsn, schema)
    assert not list(tmp_path.glob(".postgres-stage-*"))


@pytest.mark.integration
@pytest.mark.parametrize("problem", ["malformed_body", "missing_owner"])
def test_requested_audit_rejects_discarded_raw_problems_atomically(tmp_path, postgres_dsn, problem):
    rows = deepcopy(fixture_rows())
    if problem == "malformed_body":
        rows[2][0]["payload_json"] = "{invalid discarded JSON"
        message = "Invalid payload_json"
    else:
        rows[2][0]["relation_key"] = "absent discarded raw owner"
        message = "absent relation owner"
    resource(tmp_path, rows=rows)
    schema = "source_audit_" + uuid.uuid4().hex
    with pytest.raises(ValueError, match=message):
        loader.load_release(
            tmp_path,
            release(tmp_path),
            postgres_dsn,
            schema=schema,
            validate_source_records=True,
        )
    assert not exists(postgres_dsn, schema)
    assert not list(tmp_path.glob(".postgres-stage-*"))


@pytest.mark.integration
def test_audit_mode_does_not_change_stored_projection_or_derived_context(tmp_path, postgres_dsn):
    resource(tmp_path, "rhea", rows=reaction_fixture())
    manifest = release(tmp_path, {"rhea": "1.0.0"})
    schemas = ["source_audit_" + uuid.uuid4().hex for _ in range(2)]
    results = [
        loader.load_release(
            tmp_path, manifest, postgres_dsn, schema=schema, validate_source_records=audit
        )
        for schema, audit in zip(schemas, (False, True), strict=True)
    ]
    assert results[0].counts == results[1].counts
    assert results[0].validate_source_records is False
    assert results[0].validated_payload_rows == {}
    assert results[1].validate_source_records is True
    assert results[1].validated_payload_rows == {"rhea": 2}
    for table, columns in loader.COLUMNS.items():
        observed = [
            Counter(
                json.dumps(row, sort_keys=True, ensure_ascii=False)
                for row in query(
                    postgres_dsn, schema, "SELECT " + ",".join(columns) + " FROM {s}." + table
                )
            )
            for schema in schemas
        ]
        assert observed[0] == observed[1], table
    for table in ("reaction_context", "reaction_participant"):
        assert query(
            postgres_dsn, schemas[0], "SELECT * FROM {s}." + table + " ORDER BY 1"
        ) == query(postgres_dsn, schemas[1], "SELECT * FROM {s}." + table + " ORDER BY 1")
