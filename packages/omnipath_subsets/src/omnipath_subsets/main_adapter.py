"""Selected product rebuilds over the aligned current-main PostgreSQL layout."""

from contextlib import closing
from copy import deepcopy
from time import perf_counter

import psycopg2
from psycopg2 import sql
from psycopg2.extras import Json

from .build import BuildResult, PRODUCTS


def is_main_layout(connection, schema: str) -> bool:
    """Inspect one tiny catalog marker using the existing psycopg3 connection."""
    return bool(connection.execute(
        "SELECT EXISTS(SELECT 1 FROM information_schema.tables "
        "WHERE table_schema=%s AND table_name='parquet_release')", (schema,)
    ).fetchone()[0])


def _identity(connection, schema, expected=None):
    with connection.cursor() as cursor:
        cursor.execute(sql.SQL("LOCK TABLE {}.parquet_release IN SHARE MODE").format(
            sql.Identifier(schema)
        ))
        cursor.execute(sql.SQL(
            "SELECT version,manifest_sha256 FROM {}.parquet_release WHERE singleton"
        ).format(sql.Identifier(schema)))
        rows = cursor.fetchall()
    if len(rows) != 1:
        raise ValueError("Main subset build requires exactly one loaded release")
    identity = tuple(rows[0])
    if expected is not None and identity != expected:
        raise ValueError("Loaded release changed during main subset build")
    return identity


def _ready(connection, schema):
    with connection.cursor() as cursor:
        cursor.execute(sql.SQL("SELECT phase FROM {}.parquet_phase").format(sql.Identifier(schema)))
        phases = {row[0] for row in cursor.fetchall()}
    if not {"base", "derived"}.issubset(phases):
        raise ValueError("Main subset build requires committed base and shared derivations")


def _metadata(connection, schema):
    with connection.cursor() as cursor:
        cursor.execute(sql.SQL("""CREATE TABLE IF NOT EXISTS {}.subset_build_metadata (
            product text PRIMARY KEY, release_id text NOT NULL,
            manifest_sha256 text NOT NULL, stats jsonb NOT NULL,
            built_at timestamptz NOT NULL DEFAULT now())""").format(sql.Identifier(schema)))


def _publish(connection, schema, product, identity, outcome, seconds):
    """Prepare all product markers in the same transaction as its tables."""
    _identity(connection, schema, identity)
    with connection.cursor() as cursor:
        cursor.execute(sql.SQL("""INSERT INTO {}.subset_build_metadata
            (product,release_id,manifest_sha256,stats) VALUES(%s,%s,%s,%s)
            ON CONFLICT(product) DO UPDATE SET release_id=EXCLUDED.release_id,
            manifest_sha256=EXCLUDED.manifest_sha256,stats=EXCLUDED.stats,built_at=now()""").format(
            sql.Identifier(schema)
        ), (product, *identity, Json(outcome["result"])))
        cursor.execute(sql.SQL("""INSERT INTO {}.parquet_phase(phase,seconds,result)
            VALUES(%s,%s,%s) ON CONFLICT(phase) DO UPDATE SET seconds=EXCLUDED.seconds,
            result=EXCLUDED.result,completed_at=now()""").format(sql.Identifier(schema)),
            (product, seconds, Json(outcome)))
        cursor.execute(sql.SQL("""UPDATE {}.parquet_release SET
            phase_seconds=phase_seconds || jsonb_build_object(%s::text,%s::double precision),
            status=CASE WHEN (SELECT count(*) FROM {}.parquet_phase WHERE phase=ANY(%s))=3
                        THEN 'complete' ELSE %s END WHERE singleton""").format(
            sql.Identifier(schema), sql.Identifier(schema)
        ), (product, seconds, list(PRODUCTS), product))
    _identity(connection, schema, identity)


def _fresh_worker(connection, products):
    # MetSigDB's temp_buffers setting must precede any product temporary table.
    # This dedicated worker has never run shared derivations or an earlier run.
    if "metsigdb" in products:
        from omnipath_postgres.main_compat.metsigdb.build import _widen_temp_buffers
        with connection.cursor() as cursor:
            _widen_temp_buffers(cursor)


def build_main_subsets(
    database_url,
    schema,
    *,
    products,
    checkpoint_products,
    on_product_committed=None,
):
    """Keep historical API semantics while reusing main's scientific builders.

    Selected products are explicitly rebuilt, even if already recorded. The
    schema session advisory lock matches the aligned importer, spans worker
    commits and callbacks, and is released by closing its owner connection.
    Product-internal commits are suppressed by main's CommitDeferredConnection;
    actual commits publish tables and both metadata interfaces together.
    """
    from omnipath_postgres.main_compat.pipeline import run_main_product

    results, timings = {}, {}
    with closing(psycopg2.connect(database_url)) as owner:
        with owner.cursor() as cursor:
            cursor.execute("SELECT pg_try_advisory_lock(hashtextextended(%s,0))",
                           ("omnipath:main-parquet:" + schema,))
            if not cursor.fetchone()[0]:
                raise ValueError("Another migration owns this schema")
        identity = _identity(owner, schema)
        _ready(owner, schema)
        owner.commit()  # Retain session lock, release the publication table lock.
        if checkpoint_products:
            for product in products:
                with closing(psycopg2.connect(database_url)) as worker:
                    try:
                        _fresh_worker(worker, (product,))
                        _identity(worker, schema, identity)
                        _ready(worker, schema)
                        _metadata(worker, schema)
                        started = perf_counter()
                        outcome = run_main_product(worker, product, schema=schema)
                        seconds = round(perf_counter() - started, 6)
                        _publish(worker, schema, product, identity, outcome, seconds)
                        worker.commit()
                    except BaseException:
                        worker.rollback()
                        raise
                results[product], timings[product] = outcome["result"], seconds
                if on_product_committed is not None:
                    on_product_committed(product, BuildResult(
                        *identity, schema, deepcopy(results), timings.copy()
                    ))
        else:
            with closing(psycopg2.connect(database_url)) as worker:
                try:
                    _fresh_worker(worker, products)
                    _identity(worker, schema, identity)
                    _ready(worker, schema)
                    _metadata(worker, schema)
                    for product in products:
                        started = perf_counter()
                        outcome = run_main_product(worker, product, schema=schema)
                        seconds = round(perf_counter() - started, 6)
                        _publish(worker, schema, product, identity, outcome, seconds)
                        results[product], timings[product] = outcome["result"], seconds
                    worker.commit()
                except BaseException:
                    worker.rollback()
                    raise
    return BuildResult(*identity, schema, results, timings)
