"""Focused DuckDB -> PostgreSQL direct COPY pipeline.

This module is the clean orchestration entry point for the pipeline we have
been benchmarking:

1. Stream source records into DuckDB evidence tables.
2. Canonicalize and project final load tables in DuckDB.
3. Populate tiny PostgreSQL dimensions.
4. Drop high-volume load constraints/indexes for a fresh schema.
5. COPY projected rows into PostgreSQL, attaching source partitions where
   supported by the lower-level loader.

The low-level projection SQL still lives in ``parquet_duckdb`` while this path
stabilizes. Keeping this file small makes the active benchmark pipeline easy to
read without disturbing the older Parquet-first experiments.
"""

from __future__ import annotations

import argparse
import os
import time
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import duckdb

from omnipath_build import duckdb_load


@dataclass(frozen=True)
class DirectCopyStats:
    source_rows: int
    identifiers: int
    annotations: int
    resolver_seconds: float
    projection_seconds: float
    canonicalize_seconds: float
    postgres_prepare_seconds: float
    copy_seconds: float
    reset_seconds: float
    total_seconds: float
    entities: int
    relations: int
    annotation_relation_links: int


def run_direct_copy_pipeline(
    records: Iterable[object],
    *,
    source: str,
    dataset: str,
    database_url: str,
    schema: str,
    resolver_dir: str | Path = 'data',
    max_records: int | None = None,
    state_path: str | Path | None = None,
    threads: int = 4,
    drop_load_constraints: bool = True,
) -> DirectCopyStats:
    """Run the measured DuckDB canonicalization and direct PostgreSQL COPY load."""

    total_started = time.perf_counter()
    resolver_dir = Path(resolver_dir)
    state_path = _prepare_state_path(state_path)
    con = duckdb.connect(str(state_path) if state_path is not None else ':memory:')
    con.execute(f'SET threads TO {int(threads)}')

    try:
        duckdb_load._create_duckdb_content_uuid_macro(con)

        resolver_started = time.perf_counter()
        duckdb_load._create_duckdb_resolver_views(con, resolver_dir=resolver_dir)
        resolver_seconds = time.perf_counter() - resolver_started

        projection_started = time.perf_counter()
        duckdb_load._create_duckdb_evidence_tables(con)
        projection = duckdb_load.DuckDBEvidenceProjector(con).project_records(
            records,
            source=source,
            dataset=dataset,
            max_records=max_records,
        )
        projection_seconds = time.perf_counter() - projection_started

        canonicalize_started = time.perf_counter()
        entities, relations, links = duckdb_load._canonicalize_loaded_duckdb(con)
        canonicalize_seconds = time.perf_counter() - canonicalize_started

        prepare_started = time.perf_counter()
        _prepare_postgres_load(con, database_url=database_url, schema=schema)
        postgres_prepare_seconds = time.perf_counter() - prepare_started

        copy_started = time.perf_counter()
        if drop_load_constraints:
            duckdb_load._drop_bulk_load_constraints_and_indexes(
                database_url=database_url,
                schema=schema,
            )
        duckdb_load._bulk_copy_evidence(
            con,
            schema=schema,
            database_url=database_url,
        )
        duckdb_load._bulk_copy_canonical(
            con,
            schema=schema,
            database_url=database_url,
        )
        copy_seconds = time.perf_counter() - copy_started
    finally:
        con.close()

    reset_started = time.perf_counter()
    duckdb_load._reset_postgres_sequences(database_url=database_url, schema=schema)
    reset_seconds = time.perf_counter() - reset_started

    return DirectCopyStats(
        source_rows=projection.source_rows,
        identifiers=projection.identifiers,
        annotations=projection.annotations,
        resolver_seconds=resolver_seconds,
        projection_seconds=projection_seconds,
        canonicalize_seconds=canonicalize_seconds,
        postgres_prepare_seconds=postgres_prepare_seconds,
        copy_seconds=copy_seconds,
        reset_seconds=reset_seconds,
        total_seconds=time.perf_counter() - total_started,
        entities=entities,
        relations=relations,
        annotation_relation_links=links,
    )


def run_uniprot_direct_copy_pipeline(
    *,
    database_url: str,
    schema: str,
    resolver_dir: str | Path = 'data',
    max_records: int | None = 50_000,
    state_path: str | Path | None = None,
    force_refresh: bool = False,
    threads: int = 4,
    drop_load_constraints: bool = True,
) -> DirectCopyStats:
    """Run the focused direct COPY pipeline for UniProt proteins."""

    from pypath.inputs_v2.uniprot import resource as uniprot_resource

    records = uniprot_resource.proteins(
        force_refresh=force_refresh,
        source='uniprot',
        dataset='proteins',
    )
    return run_direct_copy_pipeline(
        records,
        source='uniprot',
        dataset='proteins',
        database_url=database_url,
        schema=schema,
        resolver_dir=resolver_dir,
        max_records=max_records,
        state_path=state_path,
        threads=threads,
        drop_load_constraints=drop_load_constraints,
    )


def _prepare_postgres_load(
    con: duckdb.DuckDBPyConnection,
    *,
    database_url: str,
    schema: str,
) -> None:
    con.execute('LOAD postgres')
    con.execute(
        f"ATTACH {duckdb_load._sql_literal(database_url)} AS pg (TYPE postgres)"
    )
    duckdb_load._bulk_load_create_views_from_loaded_tables(con)
    duckdb_load._bulk_load_assert_empty(con, schema)
    duckdb_load._bulk_load_small_dimensions(con, schema)
    duckdb_load._bulk_load_materialize_dimensions(con, schema)
    con.execute('DETACH pg')


def _prepare_state_path(state_path: str | Path | None) -> Path | None:
    if state_path is None:
        return None
    path = Path(state_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    return path


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description='Focused DuckDB direct COPY pipeline benchmark.'
    )
    parser.add_argument(
        '--database-url',
        default=os.environ.get('DATABASE_URL'),
        help='PostgreSQL URL. Defaults to DATABASE_URL.',
    )
    parser.add_argument('--schema', default='public')
    parser.add_argument('--resolver-dir', default='data')
    parser.add_argument('--max-records', type=int, default=50_000)
    parser.add_argument('--state-path', default=None)
    parser.add_argument('--threads', type=int, default=4)
    parser.add_argument('--force-refresh', action='store_true')
    parser.add_argument(
        '--keep-load-constraints',
        action='store_true',
        help='Do not drop high-volume load constraints/indexes before COPY.',
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    if not args.database_url:
        raise SystemExit('--database-url or DATABASE_URL is required')

    stats = run_uniprot_direct_copy_pipeline(
        database_url=args.database_url,
        schema=args.schema,
        resolver_dir=args.resolver_dir,
        max_records=args.max_records,
        state_path=args.state_path,
        force_refresh=args.force_refresh,
        threads=args.threads,
        drop_load_constraints=not args.keep_load_constraints,
    )
    print(
        '[duckdb-direct-copy] '
        f'source_rows={stats.source_rows} '
        f'identifiers={stats.identifiers} '
        f'annotations={stats.annotations} '
        f'resolver={stats.resolver_seconds:.3f}s '
        f'projection={stats.projection_seconds:.3f}s '
        f'canonicalize={stats.canonicalize_seconds:.3f}s '
        f'postgres_prepare={stats.postgres_prepare_seconds:.3f}s '
        f'copy={stats.copy_seconds:.3f}s '
        f'reset={stats.reset_seconds:.3f}s '
        f'total={stats.total_seconds:.3f}s '
        f'entities={stats.entities} '
        f'relations={stats.relations} '
        f'annotation_relation_links={stats.annotation_relation_links}',
        flush=True,
    )
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
