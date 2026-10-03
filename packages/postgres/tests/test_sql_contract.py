"""Shared scalar contracts and transaction ownership for the PostgreSQL projection."""

import re

import pyarrow as pa
import pytest

from omnipath_core.schema import ENTITY_SCHEMA, RELATION_SCHEMA
from omnipath_postgres.compatibility.record_layout.derived import rebuild_derived
from omnipath_postgres.compatibility.record_layout.indexes import create_indexes
from omnipath_postgres.compatibility.record_layout.schema import _TABLES, create_schema


@pytest.mark.parametrize(
    ("table", "shared_schema"), [("entities", ENTITY_SCHEMA), ("relations", RELATION_SCHEMA)]
)
def test_all_shared_scalar_columns_retain_their_types(table, shared_schema):
    columns = dict(
        re.findall(
            r"^\s*(\w+)\s+(text|boolean|integer|bigint|jsonb)\b", _TABLES[table], flags=re.MULTILINE
        )
    )
    expected_types = {
        pa.string(): "text",
        pa.bool_(): "boolean",
        pa.int32(): "integer",
        pa.int64(): "bigint",
    }
    for field in shared_schema:
        if field.type in expected_types:
            assert columns[field.name] == expected_types[field.type]
    assert columns["record_json"] == "jsonb"
    if table == "relations":
        assert columns["sources"] == "jsonb"


@pytest.mark.parametrize("table", ["resource_versions", "release_metadata"])
def test_manifest_storage_retains_input_text_separately_from_query_json(table):
    columns = dict(
        re.findall(r"^\s*(\w+)\s+(text|jsonb)\s+NOT NULL", _TABLES[table], flags=re.MULTILINE)
    )
    assert columns["manifest_json"] == "jsonb"
    assert columns["manifest_text"] == "text"
    assert columns["manifest_sha256"] == "text"
    if table == "release_metadata":
        assert columns["input_manifest_sha256"] == "text"


class RecordingConnection:
    def __init__(self):
        self.statements = []

    def cursor(self, *args, **kwargs):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    rowcount = 0

    def execute(self, statement, *params):
        self.statements.append(
            statement.as_string() if hasattr(statement, "as_string") else statement
        )

    def fetchall(self):
        return []

    def fetchmany(self, *args):
        return []

    def executemany(self, statement, params):
        self.execute(statement)

    def commit(self):
        raise AssertionError("The loader owns the transaction")

    def rollback(self):
        raise AssertionError("The loader owns the transaction")


@pytest.mark.parametrize("operation", [create_schema, create_indexes, rebuild_derived])
def test_projection_sql_quotes_schema_and_leaves_transaction_to_loader(operation):
    conn = RecordingConnection()
    schema = 'release"; DROP SCHEMA public; --'
    operation(conn, schema)
    assert conn.statements
    assert any('"release""; DROP SCHEMA public; --"' in statement for statement in conn.statements)
    for statement in conn.statements:
        assert "CREATE EXTENSION" not in statement
        assert "::uuid" not in statement
