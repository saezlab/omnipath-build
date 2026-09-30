"""Atomic orchestration of products from a fixed, already loaded release."""

from dataclasses import dataclass
from time import perf_counter

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

from omnipath_postgres.loader import validate_schema

PRODUCTS = ("metsigdb", "network_views", "cosmos")


@dataclass(frozen=True)
class BuildResult:
    release: str
    manifest_sha256: str
    schema: str
    products: dict
    phase_seconds: dict[str, float]


def build_subsets(database_url: str, schema: str, *, products=PRODUCTS) -> BuildResult:
    """Build selected products in one transaction; failures preserve prior products."""
    validate_schema(schema)
    products = tuple(products)
    if not products or len(set(products)) != len(products) or set(products) - set(PRODUCTS):
        raise ValueError(f"Choose distinct products from {', '.join(PRODUCTS)}")
    from . import cosmos, metsigdb, network_views

    builders = {
        "metsigdb": metsigdb.rebuild,
        "network_views": network_views.rebuild,
        "cosmos": cosmos.rebuild,
    }
    namespace = sql.Identifier(schema)
    results, timings = {}, {}
    with psycopg.connect(database_url, autocommit=True) as conn, conn.transaction():
        with conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (schema,))
            cur.execute(
                sql.SQL("SELECT release_id,manifest_sha256 FROM {}.release_metadata").format(
                    namespace
                )
            )
            releases = cur.fetchall()
            if len(releases) != 1:
                raise ValueError("Subset build requires exactly one loaded release")
            release_id, digest = releases[0]
            cur.execute(
                sql.SQL("""
                CREATE TABLE IF NOT EXISTS {s}.subset_build_metadata (
                    product text PRIMARY KEY, release_id text NOT NULL,
                    manifest_sha256 text NOT NULL, stats jsonb NOT NULL,
                    built_at timestamptz NOT NULL DEFAULT now()
                )
            """).format(s=namespace)
            )
        for product in products:
            started = perf_counter()
            results[product] = builders[product](conn, schema)
            timings[product] = round(perf_counter() - started, 6)
            with conn.cursor() as cur:
                cur.execute(
                    sql.SQL("""
                    INSERT INTO {s}.subset_build_metadata(product,release_id,manifest_sha256,stats)
                    VALUES (%s,%s,%s,%s)
                    ON CONFLICT(product) DO UPDATE SET
                        release_id=EXCLUDED.release_id,manifest_sha256=EXCLUDED.manifest_sha256,
                        stats=EXCLUDED.stats,built_at=now()
                """).format(s=namespace),
                    (product, release_id, digest, Jsonb(results[product])),
                )
        with conn.cursor() as cur:
            cur.execute(
                sql.SQL("SELECT release_id,manifest_sha256 FROM {}.release_metadata").format(
                    namespace
                )
            )
            if cur.fetchall() != releases:
                raise ValueError("Loaded release changed during subset build")
    return BuildResult(release_id, digest, schema, results, timings)
