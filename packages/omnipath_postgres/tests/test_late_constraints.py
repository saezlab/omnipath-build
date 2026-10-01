"""Bulk COPY leaves column checks active and restores the full constraint contract."""

from collections import Counter
import json
import shutil
import uuid

import psycopg
from psycopg import sql
import pytest

from omnipath_postgres import cli, loader
from omnipath_postgres.schema import add_base_constraints, create_schema
from test_checkpoint import failed_derivation
from test_postgres import exists, query, release, resource
from test_reactions import reaction_fixture

BASE = ("entities", "relations", "identifiers", "evidence", "annotations")
pytestmark = pytest.mark.integration


def constraints(conn, schema):
    return conn.execute(
        "SELECT t.relname,c.conname,c.contype,pg_get_constraintdef(c.oid),"
        "c.condeferrable,c.condeferred,c.convalidated,c.confmatchtype,c.confupdtype,c.confdeltype "
        "FROM pg_constraint c JOIN pg_class t ON t.oid=c.conrelid "
        "JOIN pg_namespace n ON n.oid=t.relnamespace "
        "WHERE n.nspname=%s AND t.relname=ANY(%s) ORDER BY t.relname,c.conname",
        (schema, list(BASE)),
    ).fetchall()


def assert_keys(conn, schema):
    rows = constraints(conn, schema)
    assert Counter(row[2] for row in rows)["p"] == 5
    fks = [row for row in rows if row[2] == "f"]
    assert len(fks) == 7
    assert all(row[4:] == (True, True, True, "s", "a", "a") for row in fks)
    assert conn.execute(
        "SELECT indisunique,indisvalid,indisready FROM pg_index i "
        "JOIN pg_class t ON t.oid=i.indexrelid JOIN pg_namespace n ON n.oid=t.relnamespace "
        "WHERE n.nspname=%s AND t.relname='annotations_owner_ordinal_idx'",
        (schema,),
    ).fetchone() == (True, True, True)


def catalog(conn, schema):
    # Include NOT NULL/default/sequence and all CHECKs, not just key counts.
    values = [constraints(conn, schema)]
    values.append(
        conn.execute(
            "SELECT t.relname,a.attname,format_type(a.atttypid,a.atttypmod),a.attnotnull,"
            "pg_get_expr(d.adbin,d.adrelid) FROM pg_class t JOIN pg_namespace n ON n.oid=t.relnamespace "
            "JOIN pg_attribute a ON a.attrelid=t.oid LEFT JOIN pg_attrdef d "
            "ON d.adrelid=t.oid AND d.adnum=a.attnum WHERE n.nspname=%s "
            "AND t.relname=ANY(%s) AND a.attnum>0 AND NOT a.attisdropped ORDER BY t.relname,a.attnum",
            (schema, list(BASE)),
        ).fetchall()
    )
    values.append(
        conn.execute(
            "SELECT b.relname,t.relname,pg_get_indexdef(i.indexrelid),i.indisunique,i.indisvalid,i.indisready "
            "FROM pg_index i JOIN pg_class t ON t.oid=i.indexrelid JOIN pg_class b ON b.oid=i.indrelid "
            "JOIN pg_namespace n ON n.oid=b.relnamespace WHERE n.nspname=%s "
            "AND b.relname=ANY(%s) ORDER BY b.relname,t.relname",
            (schema, list(BASE)),
        ).fetchall()
    )
    return json.dumps(values).replace('\\"' + schema + '\\"', "SCHEMA").replace(schema, "SCHEMA")


@pytest.mark.parametrize("empty", [False, True])
@pytest.mark.parametrize("audit", [False, True])
@pytest.mark.parametrize("checkpoint", [False, True])
def test_final_catalog_and_data_equal_immediate_path(
    tmp_path, postgres_dsn, empty, audit, checkpoint
):
    for source in ("first", "second"):
        resource(tmp_path, source, rows=([], [], []) if empty else None)
    manifest = release(tmp_path, {"first": "1.0.0", "second": "1.0.0"})
    destinations = ["Late_" + uuid.uuid4().hex for _ in range(2)]
    results = [
        loader.load_release(
            tmp_path,
            manifest,
            postgres_dsn,
            schema=destination,
            defer_constraints=defer,
            checkpoint_base=checkpoint,
            validate_source_records=audit,
            batch_size=1,
        )
        for destination, defer in zip(destinations, (False, True), strict=True)
    ]
    assert results[0].counts == results[1].counts
    assert results[0].validated_payload_rows == results[1].validated_payload_rows
    assert results[1].validated_payload_rows == (
        {"first": 0 if empty else 2, "second": 0 if empty else 2} if audit else {}
    )
    assert results[1].phase_seconds["base_constraints"] >= 0
    with psycopg.connect(postgres_dsn) as conn:
        assert catalog(conn, destinations[0]) == catalog(conn, destinations[1])
        assert_keys(conn, destinations[1])
        for table in BASE:
            snapshots = [
                conn.execute(
                    sql.SQL(
                        "SELECT to_jsonb(t)-'annotation_id' FROM {}.{} t ORDER BY to_jsonb(t)-'annotation_id'"
                    ).format(sql.Identifier(s), sql.Identifier(table))
                ).fetchall()
                for s in destinations
            ]
            assert snapshots[0] == snapshots[1]


def test_copy_has_no_base_indexes_but_keeps_column_checks_and_sequences(
    tmp_path, postgres_dsn, monkeypatch
):
    resource(tmp_path)
    original_copy = loader._copy_projected_table
    original_validate = loader._validate_loaded
    copied = []

    def copy(conn, schema, table, *args):
        assert not [row for row in constraints(conn, schema) if row[2] in ("p", "f")]
        assert conn.execute(
            "SELECT count(*) FROM pg_indexes WHERE schemaname=%s AND tablename=ANY(%s)",
            (schema, list(BASE)),
        ).fetchone() == (0,)
        assert conn.execute(
            "SELECT count(*) FROM pg_constraint c JOIN pg_namespace n ON n.oid=c.connamespace "
            "WHERE n.nspname=%s AND c.contype='p'",
            (schema,),
        ).fetchone() == (4,)  # Metadata/checkpoint/ontology keys are retained.
        assert conn.execute(
            "SELECT a.attnotnull,d.adbin IS NOT NULL FROM pg_attribute a JOIN pg_class t ON t.oid=a.attrelid "
            "JOIN pg_namespace n ON n.oid=t.relnamespace JOIN pg_attrdef d ON d.adrelid=t.oid AND d.adnum=a.attnum "
            "WHERE n.nspname=%s AND t.relname='annotations' AND a.attname='annotation_id'",
            (schema,),
        ).fetchone() == (True, True)
        copied.append(table)
        return original_copy(conn, schema, table, *args)

    def validate(conn, schema):
        assert_keys(conn, schema)
        return original_validate(conn, schema)

    monkeypatch.setattr(loader, "_copy_projected_table", copy)
    monkeypatch.setattr(loader, "_validate_loaded", validate)
    loader.load_release(
        tmp_path,
        release(tmp_path),
        postgres_dsn,
        schema="late_" + uuid.uuid4().hex,
        defer_constraints=True,
    )
    assert copied == list(loader.COLUMNS)


def test_fk_validation_has_narrow_real_statistics(tmp_path, postgres_dsn, monkeypatch):
    resource(tmp_path)
    checked = []

    class Cursor:
        def __init__(self, cursor, conn, schema):
            self.cursor, self.conn, self.schema = cursor, conn, schema

        def __enter__(self):
            self.cursor.__enter__()
            return self

        def __exit__(self, *args):
            return self.cursor.__exit__(*args)

        def execute(self, statement):
            rendered = statement.as_string(self.conn)
            if "VALIDATE CONSTRAINT" in rendered:
                stats = self.conn.execute(
                    "SELECT tablename,attname FROM pg_stats WHERE schemaname=%s", (self.schema,)
                ).fetchall()
                assert ("entities", "entity_key") in stats
                assert ("relations", "subject_entity_key") in stats
                assert ("relations", "object_entity_key") in stats
                assert ("evidence", "relation_key") in stats
                assert not any(column in ("record_json", "quantity") for _, column in stats)
                checked.append(rendered)
            return self.cursor.execute(statement)

    class Connection:
        def __init__(self, conn, schema):
            self.conn, self.schema = conn, schema

        def cursor(self):
            return Cursor(self.conn.cursor(), self.conn, self.schema)

    def add(conn, schema):
        add_base_constraints(Connection(conn, schema), schema)

    monkeypatch.setattr(loader, "add_base_constraints", add)
    loader.load_release(
        tmp_path,
        release(tmp_path),
        postgres_dsn,
        schema="late_" + uuid.uuid4().hex,
        defer_constraints=True,
    )
    assert len(checked) == 7


def test_deferred_audit_uses_cached_flags_after_all_copies(tmp_path, postgres_dsn, monkeypatch):
    resource(tmp_path, "rhea", rows=reaction_fixture())
    resource(tmp_path, "signor")
    copied, validated, audited = [], [], []
    original_copy, original_validation = loader._copy_projected_table, loader.validate_resource
    original_audit = loader._audit_resource_records

    def copy(conn, schema, table, *args):
        copied.append(table)
        return original_copy(conn, schema, table, *args)

    def validation(con, directory):
        validated.append(directory.parent.name)
        return original_validation(con, directory)

    def audit(conn, schema, resource_pin, flag, batch_size, timings, *, analyze=True):
        assert len(copied) == 10
        assert_keys(conn, schema)
        assert analyze is False
        assert batch_size == 1
        audited.append((resource_pin.source, flag))
        return original_audit(
            conn, schema, resource_pin, flag, batch_size, timings, analyze=analyze
        )

    monkeypatch.setattr(loader, "_copy_projected_table", copy)
    monkeypatch.setattr(loader, "validate_resource", validation)
    monkeypatch.setattr(loader, "_audit_resource_records", audit)
    result = loader.load_release(
        tmp_path,
        release(tmp_path, {"rhea": "1.0.0", "signor": "1.0.0"}),
        postgres_dsn,
        schema="late_" + uuid.uuid4().hex,
        defer_constraints=True,
        validate_source_records=True,
        batch_size=1,
    )
    assert Counter(validated) == {"rhea": 1, "signor": 1}
    assert dict(audited) == {"rhea": True, "signor": False}
    assert result.validated_payload_rows == {"rhea": 2, "signor": 2}
    assert "analyze_deferred_audit_lookup" in result.phase_seconds
    assert not any(key.startswith("analyze_payload_lookup_") for key in result.phase_seconds)


@pytest.mark.parametrize(
    "problem",
    [
        "duplicate_entity",
        "duplicate_relation",
        "duplicate_identifier",
        "duplicate_evidence",
        "duplicate_annotation_id",
        "duplicate_annotation_owner",
        "missing_subject",
        "missing_object",
        "missing_entity_resource",
        "missing_relation_resource",
        "missing_identifier",
        "missing_evidence",
        "missing_resource",
        "missing_annotation_owner",
        "null_annotation_id",
        "negative_ordinal",
        "bad_evidence_scope",
    ],
)
def test_invalid_base_cannot_checkpoint(tmp_path, postgres_dsn, monkeypatch, problem):
    resource(tmp_path)
    original = loader._copy_projected_table
    table, mutation = {
        "duplicate_entity": (
            "entities",
            "INSERT INTO {s}.entities SELECT * FROM {s}.entities LIMIT 1",
        ),
        "duplicate_relation": (
            "relations",
            "INSERT INTO {s}.relations SELECT * FROM {s}.relations LIMIT 1",
        ),
        "duplicate_identifier": (
            "identifiers",
            "INSERT INTO {s}.identifiers SELECT * FROM {s}.identifiers LIMIT 1",
        ),
        "duplicate_evidence": (
            "evidence",
            "INSERT INTO {s}.evidence SELECT * FROM {s}.evidence LIMIT 1",
        ),
        "duplicate_annotation_id": (
            "annotations",
            "INSERT INTO {s}.annotations SELECT * FROM {s}.annotations LIMIT 1",
        ),
        "duplicate_annotation_owner": (
            "annotations",
            "INSERT INTO {s}.annotations (resource,version,owner_kind,owner_key,evidence_ordinal,ordinal,term,value,quantity,source,dataset,scope) SELECT resource,version,owner_kind,owner_key,evidence_ordinal,ordinal,term,value,quantity,source,dataset,scope FROM {s}.annotations LIMIT 1",
        ),
        "missing_subject": ("relations", "UPDATE {s}.relations SET subject_entity_key='absent'"),
        "missing_object": ("relations", "UPDATE {s}.relations SET object_entity_key='absent'"),
        "missing_entity_resource": ("entities", "UPDATE {s}.entities SET resource='absent'"),
        "missing_relation_resource": ("relations", "UPDATE {s}.relations SET resource='absent'"),
        "missing_identifier": ("identifiers", "UPDATE {s}.identifiers SET entity_key='absent'"),
        "missing_evidence": ("evidence", "UPDATE {s}.evidence SET relation_key='absent'"),
        "missing_resource": ("annotations", "UPDATE {s}.annotations SET resource='absent'"),
        "missing_annotation_owner": (
            "annotations",
            "UPDATE {s}.annotations SET owner_key='absent' WHERE owner_kind='entity'",
        ),
        "null_annotation_id": ("annotations", "UPDATE {s}.annotations SET annotation_id=NULL"),
        "negative_ordinal": ("identifiers", "UPDATE {s}.identifiers SET ordinal=-1"),
        "bad_evidence_scope": (
            "annotations",
            "UPDATE {s}.annotations SET evidence_ordinal=NULL WHERE owner_kind='evidence'",
        ),
    }[problem]

    def copy(conn, schema, name, *args):
        result = original(conn, schema, name, *args)
        if name == table:
            conn.execute(sql.SQL(mutation).format(s=sql.Identifier(schema)))
        return result

    def unexpected(*args):
        pytest.fail("Invalid base must fail before derivation/checkpoint commit")

    monkeypatch.setattr(loader, "_copy_projected_table", copy)
    monkeypatch.setattr(loader, "rebuild_derived", unexpected)
    schema = "late_" + uuid.uuid4().hex
    with pytest.raises((psycopg.Error, ValueError)):
        loader.load_release(
            tmp_path,
            release(tmp_path),
            postgres_dsn,
            schema=schema,
            defer_constraints=True,
            checkpoint_base=True,
        )
    assert not exists(postgres_dsn, schema)


@pytest.mark.parametrize("problem", ["malformed_raw", "missing_raw_owner", "hash_mismatch"])
def test_deferred_requested_audit_still_rejects_raw_failures(tmp_path, postgres_dsn, problem):
    rows = reaction_fixture()
    if problem == "malformed_raw":
        rows[2][0]["payload_json"] = "{invalid JSON"
    elif problem == "missing_raw_owner":
        rows[2][0]["relation_key"] = "absent"
    else:
        rows[2][0]["payload_json"] = '{"changed":true}'
    resource(tmp_path, "rhea", rows=rows)
    schema = "late_" + uuid.uuid4().hex
    with pytest.raises(ValueError):
        loader.load_release(
            tmp_path,
            release(tmp_path, {"rhea": "1.0.0"}),
            postgres_dsn,
            schema=schema,
            defer_constraints=True,
            checkpoint_base=True,
            validate_source_records=True,
        )
    assert not exists(postgres_dsn, schema)


def test_late_constraints_checkpoint_finishes_without_files_or_copy(
    tmp_path, postgres_dsn, monkeypatch
):
    resource(tmp_path, "rhea", rows=reaction_fixture())
    manifest = release(tmp_path, {"rhea": "1.0.0"})
    schema = "Late_" + uuid.uuid4().hex
    with monkeypatch.context() as patch:
        patch.setattr(loader, "rebuild_derived", failed_derivation)
        with pytest.raises(RuntimeError, match="interrupted derivation"):
            loader.load_release(
                tmp_path,
                manifest,
                postgres_dsn,
                schema=schema,
                defer_constraints=True,
                checkpoint_base=True,
                validate_source_records=True,
            )
    with psycopg.connect(postgres_dsn) as conn:
        assert_keys(conn, schema)
    assert query(postgres_dsn, schema, "SELECT count(*) FROM {s}.release_metadata") == [(0,)]
    assert query(
        postgres_dsn, schema, "SELECT count(*) FROM {s}.entities WHERE label='uncommitted change'"
    ) == [(0,)]
    shutil.rmtree(tmp_path / "resources")
    manifest.unlink()

    def unexpected(*args, **kwargs):
        pytest.fail("Retry must use PostgreSQL alone")

    for name in (
        "_copy_projected_table",
        "read_release",
        "verify_release",
        "validate_resource",
        "add_base_constraints",
    ):
        monkeypatch.setattr(loader, name, unexpected)
    result = loader.finish_release(postgres_dsn, schema=schema)
    assert result.validated_payload_rows == {"rhea": 2}
    assert result.phase_seconds["base_constraints"] >= 0
    assert query(postgres_dsn, schema, "SELECT count(*) FROM {s}.reaction_context") == [(1,)]
    assert query(postgres_dsn, schema, "SELECT count(*) FROM {s}.release_metadata") == [(1,)]


def test_direct_constraint_helpers_quote_schema_and_do_not_commit(postgres_dsn):
    schema = 'Late_"; SELECT 1; --' + uuid.uuid4().hex
    with psycopg.connect(postgres_dsn) as conn:
        create_schema(conn, schema, defer_constraints=True)
        add_base_constraints(conn, schema)
        assert_keys(conn, schema)
        conn.rollback()
    assert not exists(postgres_dsn, schema)


def test_cli_forwards_bulk_load_and_checkpoint_options(monkeypatch, capsys):
    from omnipath_postgres.loader import LoadResult

    calls = []

    def load(*args, **kwargs):
        calls.append(kwargs)
        return LoadResult("test", "release", "hash", {}, {}, False, {}, {})

    monkeypatch.setattr(cli, "load_release", load)
    assert (
        cli.main(
            [
                "release.json",
                "--data-root",
                "data",
                "--database-url",
                "unused",
                "--defer-constraints",
                "--checkpoint-base",
            ]
        )
        == 0
    )
    assert calls[0]["defer_constraints"] is True
    assert calls[0]["checkpoint_base"] is True
    capsys.readouterr()
