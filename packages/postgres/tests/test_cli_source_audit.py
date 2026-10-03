"""CLI selection reports the actual discarded-source audit mode and counts."""

import json
import uuid

import pytest

from omnipath_postgres.compatibility.record_layout.cli import main
from test_postgres import query, release, resource

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("audit", [False, True])
def test_cli_selects_normal_or_explicit_source_audit(tmp_path, postgres_dsn, capsys, audit):
    resource(tmp_path)
    manifest = release(tmp_path)
    schema = "cli_audit_" + uuid.uuid4().hex
    args = [
        str(manifest),
        "--data-root",
        str(tmp_path),
        "--database-url",
        postgres_dsn,
        "--schema",
        schema,
    ]
    if audit:
        args.append("--validate-source-records")
    assert main(args) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["validate_source_records"] is audit
    assert report["validated_payload_rows"] == ({"signor": 2} if audit else {})
    assert report["counts"]["evidence"] == 2
    assert query(postgres_dsn, schema, "SELECT count(*) FROM {s}.release_metadata") == [(1,)]
