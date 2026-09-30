"""Product publication is pinned, idempotent and atomic."""

import json
import os
import subprocess
import sys
import uuid

import psycopg
from psycopg import sql
import pytest

from omnipath_postgres.loader import load_release
from omnipath_subsets import cosmos
from omnipath_subsets.build import PRODUCTS, build_subsets
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


def test_cli_builds_only_requested_products(tmp_path, postgres_dsn):
    schema, loaded = prepared(tmp_path, postgres_dsn)
    output = subprocess.run(
        [
            sys.executable,
            "-m",
            "omnipath_subsets",
            "build",
            "--schema",
            schema,
            "--products",
            "metsigdb",
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
