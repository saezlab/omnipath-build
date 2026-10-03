"""Product publication is pinned, idempotent and atomic."""

import json
import os
import subprocess
import sys
import uuid

import psycopg
from psycopg import sql
import pytest

from omnipath_postgres.compatibility.record_layout.loader import load_release
from omnipath_subsets.compatibility.record_layout import cosmos
from omnipath_subsets.compatibility.record_layout.build import PRODUCTS, build_subsets
from release_fixture import entity, relation, write_release, write_resource

pytestmark = pytest.mark.integration


def prepared(tmp_path, postgres_dsn):
    chemical = entity("15377", aliases=[("inchikey", "XLYOFNOQVPJJNP-UHFFFAOYSA-N")])
    pathway = entity("WP1", "pathway", "wikipathways")
    edge = relation(pathway, "associated_with", chemical, source="wikipathways")
    write_resource(tmp_path, "wikipathways", [chemical, pathway], [edge])
    schema = "test_" + uuid.uuid4().hex
    loaded = load_release(
        tmp_path, write_release(tmp_path, ["wikipathways"]), postgres_dsn, schema=schema
    )
    return schema, loaded


def query(dsn, schema, statement):
    with psycopg.connect(dsn) as conn:
        return conn.execute(sql.SQL(statement).format(s=sql.Identifier(schema))).fetchall()


def test_all_products_share_the_pinned_release_and_rebuild_idempotently(tmp_path, postgres_dsn):
    schema, loaded = prepared(tmp_path, postgres_dsn)
    first = build_subsets(postgres_dsn, schema)
    assert first.release == loaded.release
    assert first.manifest_sha256 == loaded.manifest_sha256
    assert set(first.products) == set(PRODUCTS)
    assert query(postgres_dsn, schema, "SELECT count(*) FROM {s}.metsigdb_membership") == [(1,)]
    before = query(postgres_dsn, schema, "SELECT * FROM {s}.metsigdb_membership")
    build_subsets(postgres_dsn, schema)
    assert query(postgres_dsn, schema, "SELECT * FROM {s}.metsigdb_membership") == before
    assert set(
        query(
            postgres_dsn,
            schema,
            "SELECT product,release_id,manifest_sha256 FROM {s}.subset_build_metadata",
        )
    ) == {(name, loaded.release, loaded.manifest_sha256) for name in PRODUCTS}
    assert query(postgres_dsn, schema, "SELECT count(*) FROM {s}.network_registry") == [(3,)]


def test_later_product_failure_preserves_prior_committed_memberships_and_metadata(
    tmp_path, postgres_dsn, monkeypatch
):
    schema, _ = prepared(tmp_path, postgres_dsn)
    build_subsets(postgres_dsn, schema)
    before = query(postgres_dsn, schema, "SELECT * FROM {s}.subset_build_metadata ORDER BY product")
    members = query(postgres_dsn, schema, "SELECT * FROM {s}.metsigdb_membership")

    def failure(conn, schema):
        raise ValueError("simulated product failure")

    monkeypatch.setattr(cosmos, "rebuild", failure)
    with pytest.raises(ValueError, match="simulated product failure"):
        build_subsets(postgres_dsn, schema)
    assert (
        query(postgres_dsn, schema, "SELECT * FROM {s}.subset_build_metadata ORDER BY product")
        == before
    )
    assert query(postgres_dsn, schema, "SELECT * FROM {s}.metsigdb_membership") == members


@pytest.mark.parametrize("checkpoint", [False, True])
def test_cli_builds_only_requested_products(tmp_path, postgres_dsn, checkpoint):
    schema, loaded = prepared(tmp_path, postgres_dsn)
    output = subprocess.run(
        [
            sys.executable,
            "-m",
            "omnipath_subsets.compatibility.record_layout.cli",
            "build",
            "--schema",
            schema,
            "--products",
            "metsigdb",
            *(["--checkpoint-products"] if checkpoint else []),
        ],
        env={**os.environ, "OMNIPATH_DATABASE_URL": postgres_dsn},
        text=True,
        capture_output=True,
        check=True,
        timeout=30,
    )
    result = json.loads(output.stdout)
    assert result["manifest_sha256"] == loaded.manifest_sha256
    assert set(result["products"]) == {"metsigdb"}
    assert query(postgres_dsn, schema, "SELECT product FROM {s}.subset_build_metadata") == [
        ("metsigdb",)
    ]


@pytest.mark.parametrize("products", [[], ["unknown"], ["metsigdb", "metsigdb"]])
def test_invalid_product_selection_fails_before_connecting(products, monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail("Invalid product selection must not connect")

    monkeypatch.setattr(psycopg, "connect", unexpected)
    with pytest.raises(ValueError):
        build_subsets("unused", "test_release", products=products)


@pytest.mark.parametrize("failed_product", ["network_views", "cosmos"])
@pytest.mark.parametrize("failure", ["error", "interrupt", "sql_cancel"])
def test_checkpoint_failure_rolls_back_only_current_product(
    tmp_path, postgres_dsn, monkeypatch, failed_product, failure
):
    from omnipath_subsets.compatibility.record_layout import network_views

    schema, loaded = prepared(tmp_path, postgres_dsn)
    module = network_views if failed_product == "network_views" else cosmos
    original = module.rebuild
    committed = []

    def fail(conn, destination):
        original(conn, destination)
        conn.execute(
            sql.SQL("DELETE FROM {}.metsigdb_membership").format(sql.Identifier(destination))
        )
        conn.execute(
            sql.SQL("CREATE TABLE {}.interrupted_product (id integer)").format(
                sql.Identifier(destination)
            )
        )
        if failure == "interrupt":
            raise KeyboardInterrupt("cancelled product")
        if failure == "sql_cancel":
            conn.execute("SET LOCAL statement_timeout='1ms'")
            conn.execute("SELECT pg_sleep(0.05)")
        raise ValueError("failed product")

    monkeypatch.setattr(module, "rebuild", fail)
    error = {
        "error": ValueError,
        "interrupt": KeyboardInterrupt,
        "sql_cancel": psycopg.errors.QueryCanceled,
    }[failure]
    with pytest.raises(error):
        build_subsets(
            postgres_dsn,
            schema,
            checkpoint_products=True,
            on_product_committed=lambda product, result: committed.append(product),
        )
    expected = ["metsigdb"] if failed_product == "network_views" else ["metsigdb", "network_views"]
    assert committed == expected
    assert set(
        query(
            postgres_dsn,
            schema,
            "SELECT product,release_id,manifest_sha256 FROM {s}.subset_build_metadata",
        )
    ) == {(product, loaded.release, loaded.manifest_sha256) for product in expected}
    assert query(postgres_dsn, schema, "SELECT count(*) FROM {s}.metsigdb_membership") == [(1,)]
    assert query(
        postgres_dsn, schema, "SELECT to_regclass('" + schema + ".interrupted_product')"
    ) == [(None,)]
    missing = "network_registry" if failed_product == "network_views" else "cosmos_edge"
    assert query(postgres_dsn, schema, "SELECT to_regclass('" + schema + "." + missing + "')") == [
        (None,)
    ]
    with psycopg.connect(postgres_dsn) as observer:
        assert observer.execute(
            "SELECT pg_try_advisory_xact_lock(hashtextextended(%s,0))", (schema,)
        ).fetchone() == (True,)


def test_fresh_atomic_build_still_rolls_back_all_products(tmp_path, postgres_dsn, monkeypatch):
    schema, _ = prepared(tmp_path, postgres_dsn)

    def failure(conn, destination):
        raise ValueError("failed last product")

    monkeypatch.setattr(cosmos, "rebuild", failure)
    with pytest.raises(ValueError, match="failed last product"):
        build_subsets(postgres_dsn, schema)
    for table in ("subset_build_metadata", "metsigdb_membership", "network_registry"):
        assert query(
            postgres_dsn, schema, "SELECT to_regclass('" + schema + "." + table + "')"
        ) == [(None,)]


def test_checkpoint_callbacks_observe_durable_products_and_held_lock(tmp_path, postgres_dsn):
    schema, loaded = prepared(tmp_path, postgres_dsn)
    observed = []

    def committed(product, result):
        observed.append(product)
        assert result.release == loaded.release
        assert result.manifest_sha256 == loaded.manifest_sha256
        assert list(result.products) == observed
        assert set(result.phase_seconds) == set(observed)
        with psycopg.connect(postgres_dsn) as observer:
            assert observer.execute(
                "SELECT pg_try_advisory_xact_lock(hashtextextended(%s,0))", (schema,)
            ).fetchone() == (False,)
            records = observer.execute(
                sql.SQL("SELECT product,stats FROM {}.subset_build_metadata").format(
                    sql.Identifier(schema)
                )
            ).fetchall()
            assert dict(records) == result.products
        # The observer may manipulate its snapshot without changing later snapshots
        # or the BuildResult returned by the orchestrator.
        result.products[product]["observer_mutation"] = True
        result.phase_seconds[product] = -1

    result = build_subsets(
        postgres_dsn, schema, checkpoint_products=True, on_product_committed=committed
    )
    assert observed == list(PRODUCTS)
    assert all("observer_mutation" not in stats for stats in result.products.values())
    assert all(seconds >= 0 for seconds in result.phase_seconds.values())
    with psycopg.connect(postgres_dsn) as observer:
        assert observer.execute(
            "SELECT pg_try_advisory_xact_lock(hashtextextended(%s,0))", (schema,)
        ).fetchone() == (True,)


def test_checkpoint_callback_failure_keeps_committed_metadata_and_stops_next_product(
    tmp_path, postgres_dsn
):
    schema, _ = prepared(tmp_path, postgres_dsn)

    def observer_failure(product, result):
        assert product == "metsigdb"
        raise RuntimeError("observer persistence failed")

    with pytest.raises(RuntimeError, match="observer persistence failed"):
        build_subsets(
            postgres_dsn, schema, checkpoint_products=True, on_product_committed=observer_failure
        )
    assert query(postgres_dsn, schema, "SELECT product FROM {s}.subset_build_metadata") == [
        ("metsigdb",)
    ]
    assert query(postgres_dsn, schema, "SELECT count(*) FROM {s}.metsigdb_membership") == [(1,)]
    assert query(postgres_dsn, schema, "SELECT to_regclass('" + schema + ".network_registry')") == [
        (None,)
    ]


def test_checkpoint_resume_builds_only_explicit_remaining_products(
    tmp_path, postgres_dsn, monkeypatch
):
    from omnipath_subsets.compatibility.record_layout import metsigdb, network_views

    schema, _ = prepared(tmp_path, postgres_dsn)

    def failure(*args):
        raise ValueError("last product failed")

    with monkeypatch.context() as patch:
        patch.setattr(cosmos, "rebuild", failure)
        with pytest.raises(ValueError, match="last product failed"):
            build_subsets(postgres_dsn, schema, checkpoint_products=True)
    before = query(postgres_dsn, schema, "SELECT * FROM {s}.subset_build_metadata ORDER BY product")
    members = query(postgres_dsn, schema, "SELECT * FROM {s}.metsigdb_membership")

    def unexpected(*args):
        pytest.fail("Resume must not rebuild products not selected")

    monkeypatch.setattr(metsigdb, "rebuild", unexpected)
    monkeypatch.setattr(network_views, "rebuild", unexpected)
    committed = []
    result = build_subsets(
        postgres_dsn,
        schema,
        products=["cosmos"],
        checkpoint_products=True,
        on_product_committed=lambda product, snapshot: committed.append(
            (product, list(snapshot.products))
        ),
    )
    assert set(result.products) == {"cosmos"}
    assert committed == [("cosmos", ["cosmos"])]
    after = query(
        postgres_dsn,
        schema,
        "SELECT * FROM {s}.subset_build_metadata WHERE product<>'cosmos' ORDER BY product",
    )
    assert after == before
    assert query(postgres_dsn, schema, "SELECT * FROM {s}.metsigdb_membership") == members


@pytest.mark.parametrize("mutation", ["digest", "second_release"])
def test_checkpoint_release_mutation_rolls_back_current_product(
    tmp_path, postgres_dsn, monkeypatch, mutation
):
    from omnipath_subsets.compatibility.record_layout import network_views

    schema, loaded = prepared(tmp_path, postgres_dsn)
    original = network_views.rebuild

    def mutate(conn, destination):
        result = original(conn, destination)
        if mutation == "digest":
            conn.execute(
                sql.SQL("UPDATE {}.release_metadata SET manifest_sha256=%s").format(
                    sql.Identifier(destination)
                ),
                ("f" * 64,),
            )
        else:
            conn.execute(
                sql.SQL(
                    "INSERT INTO {}.release_metadata SELECT release_id||'-second',manifest_json,manifest_text,manifest_sha256,input_manifest_sha256,loaded_at FROM {}.release_metadata"
                ).format(sql.Identifier(destination), sql.Identifier(destination))
            )
        return result

    monkeypatch.setattr(network_views, "rebuild", mutate)
    with pytest.raises(ValueError, match="Loaded release changed"):
        build_subsets(postgres_dsn, schema, checkpoint_products=True)
    assert query(
        postgres_dsn, schema, "SELECT release_id,manifest_sha256 FROM {s}.release_metadata"
    ) == [(loaded.release, loaded.manifest_sha256)]
    assert query(postgres_dsn, schema, "SELECT product FROM {s}.subset_build_metadata") == [
        ("metsigdb",)
    ]
    assert query(postgres_dsn, schema, "SELECT to_regclass('" + schema + ".network_registry')") == [
        (None,)
    ]


def test_checkpoint_rechecks_release_between_product_transactions(tmp_path, postgres_dsn):
    schema, _ = prepared(tmp_path, postgres_dsn)

    def external_change(product, result):
        with psycopg.connect(postgres_dsn) as observer:
            observer.execute(
                sql.SQL("UPDATE {}.release_metadata SET release_id='different release'").format(
                    sql.Identifier(schema)
                )
            )

    with pytest.raises(ValueError, match="Loaded release changed"):
        build_subsets(
            postgres_dsn, schema, checkpoint_products=True, on_product_committed=external_change
        )
    assert query(postgres_dsn, schema, "SELECT product FROM {s}.subset_build_metadata") == [
        ("metsigdb",)
    ]
    assert query(postgres_dsn, schema, "SELECT to_regclass('" + schema + ".network_registry')") == [
        (None,)
    ]


@pytest.mark.parametrize(
    "options",
    [
        {"checkpoint_products": 1},
        {"checkpoint_products": None},
        {"checkpoint_products": "true"},
        {"checkpoint_products": True, "on_product_committed": "callback"},
        {"on_product_committed": lambda product, result: None},
    ],
)
def test_invalid_checkpoint_selection_fails_before_connecting(monkeypatch, options):
    def unexpected(*args, **kwargs):
        pytest.fail("Invalid checkpoint selection must not connect")

    monkeypatch.setattr(psycopg, "connect", unexpected)
    with pytest.raises(ValueError):
        build_subsets("unused", "test_release", **options)
