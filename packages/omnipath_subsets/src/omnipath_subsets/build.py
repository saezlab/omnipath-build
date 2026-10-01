"""Release-pinned product builds, atomic by default or checkpointed per product."""

from copy import deepcopy
from dataclasses import dataclass
from time import perf_counter
from typing import Callable

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


def _release_identity(conn, namespace, expected=None):
    # Keep this tiny publication marker stable until this transaction commits,
    # including against writers that do not use the schema advisory lock.
    conn.execute(sql.SQL("LOCK TABLE {}.release_metadata IN SHARE MODE").format(namespace))
    releases = conn.execute(
        sql.SQL("SELECT release_id,manifest_sha256 FROM {}.release_metadata").format(namespace)
    ).fetchall()
    if expected is not None and releases != [expected]:
        raise ValueError("Loaded release changed during subset build")
    if len(releases) != 1:
        raise ValueError("Subset build requires exactly one loaded release")
    return releases[0]


def _metadata_table(conn, namespace):
    conn.execute(
        sql.SQL("""
            CREATE TABLE IF NOT EXISTS {s}.subset_build_metadata (
                product text PRIMARY KEY, release_id text NOT NULL,
                manifest_sha256 text NOT NULL, stats jsonb NOT NULL,
                built_at timestamptz NOT NULL DEFAULT now()
            )
        """).format(s=namespace)
    )


def _build_product(conn, schema, product, builder, identity):
    namespace = sql.Identifier(schema)
    _release_identity(conn, namespace, identity)
    started = perf_counter()
    stats = builder(conn, schema)
    seconds = round(perf_counter() - started, 6)
    conn.execute(
        sql.SQL("""
            INSERT INTO {s}.subset_build_metadata(product,release_id,manifest_sha256,stats)
            VALUES (%s,%s,%s,%s)
            ON CONFLICT(product) DO UPDATE SET
                release_id=EXCLUDED.release_id,manifest_sha256=EXCLUDED.manifest_sha256,
                stats=EXCLUDED.stats,built_at=now()
        """).format(s=namespace),
        (product, *identity, Jsonb(stats)),
    )
    _release_identity(conn, namespace, identity)
    return stats, seconds


def build_subsets(
    database_url: str,
    schema: str,
    *,
    products=PRODUCTS,
    checkpoint_products: bool = False,
    on_product_committed: Callable[[str, BuildResult], None] | None = None,
) -> BuildResult:
    """Build selected products, with optional durable commits after each one.

    Default mode keeps all selected products in one transaction. Checkpoint mode
    preserves earlier commits if a later product fails. Explicitly select the
    remaining products to resume; existing product metadata is not auto-skipped.
    A session advisory lock spans every product transaction and callback.

    The optional callback requires checkpoint mode and receives (product,
    BuildResult snapshot) only after that product's tables and metadata commit.
    Callback failures propagate after preserving the durable product, so database
    metadata remains authoritative if observer persistence fails.
    """
    validate_schema(schema)
    products = tuple(products)
    if not products or len(set(products)) != len(products) or set(products) - set(PRODUCTS):
        raise ValueError(f"Choose distinct products from {', '.join(PRODUCTS)}")
    if type(checkpoint_products) is not bool:
        raise ValueError("checkpoint_products must be a boolean")
    if on_product_committed is not None:
        if not callable(on_product_committed):
            raise ValueError("on_product_committed must be callable")
        if not checkpoint_products:
            raise ValueError("on_product_committed requires checkpoint_products=True")
    from . import cosmos, metsigdb, network_views

    builders = {
        "metsigdb": metsigdb.rebuild,
        "network_views": network_views.rebuild,
        "cosmos": cosmos.rebuild,
    }
    namespace = sql.Identifier(schema)
    results, timings = {}, {}
    with psycopg.connect(database_url, autocommit=True) as conn:
        if checkpoint_products:
            conn.execute("SELECT pg_advisory_lock(hashtextextended(%s, 0))", (schema,))
            identity = None
            for product in products:
                with conn.transaction():
                    identity = _release_identity(conn, namespace, identity)
                    _metadata_table(conn, namespace)
                    stats, seconds = _build_product(
                        conn, schema, product, builders[product], identity
                    )
                # Do not report a product until the transaction has really committed.
                results[product], timings[product] = stats, seconds
                if on_product_committed is not None:
                    on_product_committed(
                        product,
                        BuildResult(*identity, schema, deepcopy(results), timings.copy()),
                    )
        else:
            with conn.transaction():
                conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (schema,))
                identity = _release_identity(conn, namespace)
                _metadata_table(conn, namespace)
                for product in products:
                    results[product], timings[product] = _build_product(
                        conn, schema, product, builders[product], identity
                    )
    return BuildResult(*identity, schema, results, timings)
