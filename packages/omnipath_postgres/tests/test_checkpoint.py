"""A committed, unpublished base can retry derivation without Parquet or COPY."""

import json
import shutil
import uuid

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb
import pytest

from omnipath_postgres import loader
from omnipath_postgres.cli import main
from omnipath_postgres.releases import ReleaseValidationError
from test_postgres import exists, query, release, resource

pytestmark = pytest.mark.integration


def failed_derivation(conn, schema):
    # Test rollback of base mutations and derived DDL, rather than failing before
    # any writes. The checkpoint must remain exactly as committed beforehand.
    conn.execute(
        sql.SQL("UPDATE {}.entities SET label='uncommitted change'").format(sql.Identifier(schema))
    )
    conn.execute(
        sql.SQL("CREATE TABLE {}.interrupted_derivation (id integer)").format(
            sql.Identifier(schema)
        )
    )
    raise RuntimeError("interrupted derivation")


def stage(tmp_path, postgres_dsn, monkeypatch, *, audit=False, mixed_case=False):
    resource(tmp_path)
    manifest = release(tmp_path)
    schema = ("Checkpoint_" if mixed_case else "checkpoint_") + uuid.uuid4().hex
    with monkeypatch.context() as patch:
        patch.setattr(loader, "rebuild_derived", failed_derivation)
        with pytest.raises(RuntimeError, match="interrupted derivation"):
            loader.load_release(
                tmp_path,
                manifest,
                postgres_dsn,
                schema=schema,
                checkpoint_base=True,
                validate_source_records=audit,
            )
    return schema, manifest


@pytest.mark.parametrize("checkpoint", [False, True])
def test_derivation_failure_keeps_only_explicitly_checkpointed_base(
    tmp_path, postgres_dsn, monkeypatch, checkpoint
):
    resource(tmp_path)
    monkeypatch.setattr(loader, "rebuild_derived", failed_derivation)
    schema = "checkpoint_" + uuid.uuid4().hex
    with pytest.raises(RuntimeError, match="interrupted derivation"):
        loader.load_release(
            tmp_path, release(tmp_path), postgres_dsn, schema=schema, checkpoint_base=checkpoint
        )
    assert bool(exists(postgres_dsn, schema)) is checkpoint
    if checkpoint:
        assert query(postgres_dsn, schema, "SELECT count(*) FROM {s}.release_metadata") == [(0,)]
        assert query(
            postgres_dsn,
            schema,
            "SELECT format_version,completed_at,counts FROM {s}.base_checkpoint",
        ) == [
            (
                1,
                None,
                {"entities": 3, "identifiers": 2, "relations": 1, "evidence": 2, "annotations": 9},
            )
        ]
        assert query(
            postgres_dsn,
            schema,
            "SELECT count(*) FROM {s}.entities WHERE label='uncommitted change'",
        ) == [(0,)]
        assert query(
            postgres_dsn, schema, "SELECT to_regclass(%s)", (schema + ".interrupted_derivation",)
        ) == [(None,)]
        assert (
            query(
                postgres_dsn,
                schema,
                "SELECT to_regclass(%s)",
                (schema + ".identifiers_lookup_idx",),
            )[0][0]
            is not None
        )


@pytest.mark.parametrize("failure", ["integrity", "indexes", "file_recheck"])
def test_checkpoint_base_failures_leave_no_destination(
    tmp_path, postgres_dsn, monkeypatch, failure
):
    resource(tmp_path)

    def fail(*args, **kwargs):
        raise RuntimeError("base not verified")

    monkeypatch.setattr(
        loader,
        {
            "integrity": "_validate_loaded",
            "indexes": "create_indexes",
            "file_recheck": "verify_release",
        }[failure],
        fail,
    )
    schema = "checkpoint_" + uuid.uuid4().hex
    with pytest.raises(RuntimeError, match="base not verified"):
        loader.load_release(
            tmp_path, release(tmp_path), postgres_dsn, schema=schema, checkpoint_base=True
        )
    assert not exists(postgres_dsn, schema)


@pytest.mark.parametrize("audit", [False, True])
@pytest.mark.parametrize("mixed_case", [False, True])
def test_finish_uses_only_postgres_and_preserves_audit_and_pins(
    tmp_path, postgres_dsn, monkeypatch, audit, mixed_case
):
    schema, manifest = stage(
        tmp_path, postgres_dsn, monkeypatch, audit=audit, mixed_case=mixed_case
    )
    original_manifest = manifest.read_text()
    shutil.rmtree(tmp_path / "resources")
    manifest.unlink()

    def unexpected(*args, **kwargs):
        pytest.fail("Finishing must not read files, open DuckDB or repeat COPY")

    for name in (
        "read_release",
        "verify_release",
        "_copy_projected_table",
        "projection_query",
        "validate_resource",
    ):
        monkeypatch.setattr(loader, name, unexpected)
    monkeypatch.setattr(loader.duckdb, "connect", unexpected)
    result = loader.finish_release(postgres_dsn, schema=schema)
    assert result.resources == {"signor": "1.0.0"}
    assert result.validate_source_records is audit
    assert result.validated_payload_rows == ({"signor": 2} if audit else {})
    assert result.counts["evidence"] == 2
    assert query(
        postgres_dsn, schema, "SELECT manifest_text,manifest_sha256 FROM {s}.release_metadata"
    ) == [(original_manifest, result.manifest_sha256)]
    assert query(
        postgres_dsn, schema, "SELECT completed_at IS NOT NULL FROM {s}.base_checkpoint"
    ) == [(True,)]
    assert result.phase_seconds["resume_validate"] >= 0
    assert result.phase_seconds["derive_and_analyze"] >= 0
    with pytest.raises(ValueError, match="already published"):
        loader.finish_release(postgres_dsn, schema=schema)


def test_base_commit_hook_is_visible_and_session_lock_spans_both_transactions(
    tmp_path, postgres_dsn, monkeypatch
):
    resource(tmp_path)
    schema = "checkpoint_" + uuid.uuid4().hex
    original = loader._finish_checkpoint
    entered = []

    def observe(conn, destination):
        assert conn.info.transaction_status == psycopg.pq.TransactionStatus.IDLE
        assert query(postgres_dsn, destination, "SELECT count(*) FROM {s}.base_checkpoint") == [
            (1,)
        ]
        assert query(postgres_dsn, destination, "SELECT count(*) FROM {s}.release_metadata") == [
            (0,)
        ]
        with psycopg.connect(postgres_dsn) as observer:
            # A legacy transaction-locking loader must also be blocked across
            # the base commit, not merely concurrent new checkpoint callers.
            assert observer.execute(
                "SELECT pg_try_advisory_xact_lock(hashtextextended(%s,0))", (destination,)
            ).fetchone() == (False,)
        entered.append(destination)
        return original(conn, destination)

    monkeypatch.setattr(loader, "_finish_checkpoint", observe)
    loader.load_release(
        tmp_path, release(tmp_path), postgres_dsn, schema=schema, checkpoint_base=True
    )
    assert entered == [schema]
    with psycopg.connect(postgres_dsn) as observer:
        assert observer.execute(
            "SELECT pg_try_advisory_xact_lock(hashtextextended(%s,0))", (schema,)
        ).fetchone() == (True,)


@pytest.mark.parametrize(
    "corruption",
    [
        "canonical_hash",
        "input_hash",
        "pins",
        "counts",
        "audit",
        "resource_hash",
        "resource_checksums",
        "deleted_row",
        "dropped_index",
        "index_shape",
    ],
)
def test_finish_rejects_changed_checkpoint_or_base_before_derivation(
    tmp_path, postgres_dsn, monkeypatch, corruption
):
    schema, _ = stage(tmp_path, postgres_dsn, monkeypatch)
    with psycopg.connect(postgres_dsn) as conn:
        if corruption in {"canonical_hash", "input_hash", "resource_hash"}:
            table, column = {
                "canonical_hash": ("base_checkpoint", "manifest_sha256"),
                "input_hash": ("base_checkpoint", "input_manifest_sha256"),
                "resource_hash": ("resource_versions", "manifest_sha256"),
            }[corruption]
            conn.execute(
                sql.SQL("UPDATE {}.{} SET {}=%s").format(
                    sql.Identifier(schema), sql.Identifier(table), sql.Identifier(column)
                ),
                ("a" * 64,),
            )
        elif corruption == "pins":
            conn.execute(
                sql.SQL("UPDATE {}.base_checkpoint SET resources=%s").format(
                    sql.Identifier(schema)
                ),
                (Jsonb({"signor": "2.0.0"}),),
            )
        elif corruption == "counts":
            conn.execute(
                sql.SQL(
                    "UPDATE {}.base_checkpoint SET counts=jsonb_set(counts,'{{entities}}','999')"
                ).format(sql.Identifier(schema))
            )
        elif corruption == "audit":
            conn.execute(
                sql.SQL("UPDATE {}.base_checkpoint SET validate_source_records=true").format(
                    sql.Identifier(schema)
                )
            )
        elif corruption == "resource_checksums":
            conn.execute(
                sql.SQL("UPDATE {}.resource_versions SET parquet_checksums='{{}}'").format(
                    sql.Identifier(schema)
                )
            )
        elif corruption == "deleted_row":
            conn.execute(
                sql.SQL("DELETE FROM {}.identifiers WHERE ordinal=0").format(sql.Identifier(schema))
            )
        else:
            conn.execute(
                sql.SQL("DROP INDEX {}.identifiers_lookup_idx").format(sql.Identifier(schema))
            )
            if corruption == "index_shape":
                conn.execute(
                    sql.SQL("CREATE INDEX identifiers_lookup_idx ON {}.identifiers(id)").format(
                        sql.Identifier(schema)
                    )
                )

    def unexpected(*args, **kwargs):
        pytest.fail("Invalid checkpoints must fail before derivation")

    monkeypatch.setattr(loader, "rebuild_derived", unexpected)
    with pytest.raises(ValueError, match="Checkpoint|checkpoint"):
        loader.finish_release(postgres_dsn, schema=schema)
    assert query(postgres_dsn, schema, "SELECT count(*) FROM {s}.release_metadata") == [(0,)]
    assert query(postgres_dsn, schema, "SELECT completed_at FROM {s}.base_checkpoint") == [(None,)]


def test_failed_finish_can_retry_without_changing_checkpoint(tmp_path, postgres_dsn, monkeypatch):
    schema, _ = stage(tmp_path, postgres_dsn, monkeypatch)
    before = query(postgres_dsn, schema, "SELECT * FROM {s}.base_checkpoint")
    with monkeypatch.context() as patch:
        patch.setattr(loader, "rebuild_derived", failed_derivation)
        with pytest.raises(RuntimeError, match="interrupted derivation"):
            loader.finish_release(postgres_dsn, schema=schema)
    assert query(postgres_dsn, schema, "SELECT * FROM {s}.base_checkpoint") == before
    assert query(
        postgres_dsn, schema, "SELECT count(*) FROM {s}.entities WHERE label='uncommitted change'"
    ) == [(0,)]
    assert loader.finish_release(postgres_dsn, schema=schema).resources == {"signor": "1.0.0"}


def test_cli_finish_accepts_no_manifest_or_data_root(tmp_path, postgres_dsn, monkeypatch, capsys):
    schema, _ = stage(tmp_path, postgres_dsn, monkeypatch)
    shutil.rmtree(tmp_path / "resources")
    assert main(["--finish", "--database-url", postgres_dsn, "--schema", schema]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["resources"] == {"signor": "1.0.0"}
    assert result["validate_source_records"] is False


def test_checkpoint_rechecks_actual_manifest_before_base_commit(
    tmp_path, postgres_dsn, monkeypatch
):
    resource(tmp_path)
    manifest = release(tmp_path)
    original = loader.create_indexes

    def mutate_after_indexes(conn, schema):
        original(conn, schema)
        manifest.write_text(manifest.read_text() + "\n")

    monkeypatch.setattr(loader, "create_indexes", mutate_after_indexes)
    schema = "checkpoint_" + uuid.uuid4().hex
    with pytest.raises(ReleaseValidationError, match="changed"):
        loader.load_release(tmp_path, manifest, postgres_dsn, schema=schema, checkpoint_base=True)
    assert not exists(postgres_dsn, schema)


def test_finish_rejects_completed_atomic_release_and_missing_destination(tmp_path, postgres_dsn):
    resource(tmp_path)
    schema = "checkpoint_" + uuid.uuid4().hex
    loaded = loader.load_release(tmp_path, release(tmp_path), postgres_dsn, schema=schema)
    with pytest.raises(ValueError, match="already published"):
        loader.finish_release(postgres_dsn, schema=schema)
    assert query(postgres_dsn, schema, "SELECT manifest_sha256 FROM {s}.release_metadata") == [
        (loaded.manifest_sha256,)
    ]
    absent = "checkpoint_" + uuid.uuid4().hex
    with pytest.raises(ValueError, match="no resumable base checkpoint"):
        loader.finish_release(postgres_dsn, schema=absent)
    assert not exists(postgres_dsn, absent)


def test_cli_checkpoint_base_loads_and_publishes(tmp_path, postgres_dsn, capsys):
    resource(tmp_path)
    schema = "checkpoint_" + uuid.uuid4().hex
    assert (
        main(
            [
                str(release(tmp_path)),
                "--data-root",
                str(tmp_path),
                "--database-url",
                postgres_dsn,
                "--schema",
                schema,
                "--checkpoint-base",
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["counts"]["entities"] == 3
    assert query(
        postgres_dsn, schema, "SELECT completed_at IS NOT NULL FROM {s}.base_checkpoint"
    ) == [(True,)]
    assert query(postgres_dsn, schema, "SELECT count(*) FROM {s}.release_metadata") == [(1,)]
