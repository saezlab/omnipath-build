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
from itertools import islice
from pathlib import Path

import duckdb
import psycopg2

from omnipath_build import duckdb_load
from omnipath_build.db.refresh import delete_source_content
from omnipath_build.resources import ResourceFunction, discover_resources


@dataclass(frozen=True)
class DirectCopyStats:
    source_rows: int
    identifiers: int
    annotations: int
    ontology_terms: int
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


@dataclass(frozen=True)
class DirectCopyBatchStats:
    batches: tuple[DirectCopyStats, ...]
    source_rows: int
    identifiers: int
    annotations: int
    ontology_terms: int
    total_seconds: float


@dataclass(frozen=True)
class DiscoveredLoadStats:
    sources: int
    datasets: int
    source_rows: int
    identifiers: int
    annotations: int
    ontology_terms: int
    total_seconds: float


def run_direct_copy_pipeline(
    records: Iterable[object],
    *,
    source: str,
    dataset: str,
    ontology_records: Iterable[object] | None = None,
    ontology_dataset: str = 'ontology',
    ontology_id: str | None = None,
    database_url: str,
    schema: str,
    resolver_dir: str | Path = 'data',
    max_records: int | None = None,
    state_path: str | Path | None = None,
    threads: int = 4,
    drop_load_constraints: bool = True,
    require_empty: bool = True,
    row_offset: int = 0,
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
            row_offset=row_offset,
        )
        ontology_terms = 0
        if ontology_records is not None:
            ontology_terms = duckdb_load.project_ontology_terms(
                con,
                ontology_records,
                source=source,
                dataset=ontology_dataset,
                ontology_id=ontology_id or ontology_dataset,
            )
        projection_seconds = time.perf_counter() - projection_started

        canonicalize_started = time.perf_counter()
        entities, relations, links = duckdb_load._canonicalize_loaded_duckdb(con)
        canonicalize_seconds = time.perf_counter() - canonicalize_started

        prepare_started = time.perf_counter()
        _prepare_postgres_load(
            con,
            database_url=database_url,
            schema=schema,
            require_empty=require_empty,
        )
        postgres_prepare_seconds = time.perf_counter() - prepare_started

        copy_started = time.perf_counter()
        if drop_load_constraints:
            duckdb_load._drop_bulk_load_constraints_and_indexes(
                database_url=database_url,
                schema=schema,
            )
        con.execute(
            f"ATTACH {duckdb_load._sql_literal(database_url)} AS pg (TYPE postgres)"
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
        con.execute('DETACH pg')
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
        ontology_terms=ontology_terms,
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


def run_direct_copy_pipeline_batches(
    records: Iterable[object],
    *,
    source: str,
    dataset: str,
    database_url: str,
    schema: str,
    resolver_dir: str | Path = 'data',
    batch_size: int = 50_000,
    max_records: int | None = None,
    state_path: str | Path | None = None,
    threads: int = 4,
    drop_load_constraints: bool = True,
    require_empty: bool = True,
    progress: bool = True,
) -> DirectCopyBatchStats:
    """Run direct COPY in stable row-offset batches and log progress."""

    started = time.perf_counter()
    if batch_size <= 0:
        raise ValueError('batch_size must be positive')
    iterator = iter(records)
    if max_records is not None:
        iterator = islice(iterator, max_records)

    offset = 0
    batch_no = 0
    batch_stats: list[DirectCopyStats] = []
    if drop_load_constraints:
        duckdb_load._drop_bulk_load_constraints_and_indexes(
            database_url=database_url,
            schema=schema,
        )
    while True:
        batch = list(islice(iterator, batch_size))
        if not batch:
            break
        batch_no += 1
        batch_state_path = _batch_state_path(state_path, batch_no)
        stats = run_direct_copy_pipeline(
            batch,
            source=source,
            dataset=dataset,
            database_url=database_url,
            schema=schema,
            resolver_dir=resolver_dir,
            max_records=None,
            state_path=batch_state_path,
            threads=threads,
            drop_load_constraints=False,
            require_empty=require_empty and batch_no == 1,
            row_offset=offset,
        )
        offset += stats.source_rows
        batch_stats.append(stats)
        if progress:
            print(
                '[duckdb-direct-copy-batch] '
                f'batch={batch_no} '
                f'batch_rows={stats.source_rows} '
                f'cumulative_rows={offset} '
                f'identifiers={stats.identifiers} '
                f'annotations={stats.annotations} '
                f'projection={stats.projection_seconds:.3f}s '
                f'canonicalize={stats.canonicalize_seconds:.3f}s '
                f'copy={stats.copy_seconds:.3f}s '
                f'total={stats.total_seconds:.3f}s',
                flush=True,
            )

    return DirectCopyBatchStats(
        batches=tuple(batch_stats),
        source_rows=sum(stats.source_rows for stats in batch_stats),
        identifiers=sum(stats.identifiers for stats in batch_stats),
        annotations=sum(stats.annotations for stats in batch_stats),
        ontology_terms=sum(stats.ontology_terms for stats in batch_stats),
        total_seconds=time.perf_counter() - started,
    )


def run_chembl_direct_copy_batches(
    *,
    database_url: str,
    schema: str,
    resolver_dir: str | Path = 'data',
    batch_size: int = 50_000,
    max_records: int | None = None,
    state_path: str | Path | None = None,
    threads: int = 4,
    drop_load_constraints: bool = True,
    require_empty: bool = True,
    progress: bool = True,
) -> DirectCopyBatchStats:
    """Run ChEMBL activities through the direct COPY pipeline in batches."""

    from pypath.inputs_v2.chembl import resource as chembl_resource

    resolver_dir = Path(resolver_dir)
    records = chembl_resource.activities(
        chemical_resolver_lookup_path=(
            resolver_dir / 'chemicals' / 'chemical_identifier_lookup.parquet'
        ),
        chemical_resolver_sources=('chebi', 'hmdb', 'chembl'),
    )
    return run_direct_copy_pipeline_batches(
        records,
        source='chembl',
        dataset='activities',
        database_url=database_url,
        schema=schema,
        resolver_dir=resolver_dir,
        batch_size=batch_size,
        max_records=max_records,
        state_path=state_path,
        threads=threads,
        drop_load_constraints=drop_load_constraints,
        require_empty=require_empty,
        progress=progress,
    )


def run_discovered_direct_load(
    *,
    database_url: str,
    schema: str,
    sources: tuple[str, ...] = (),
    dataset: str | None = None,
    database: str = 'omnipath',
    inputs_package: str = 'pypath.inputs_v2',
    resolver_dir: str | Path = 'data',
    batch_size: int = 50_000,
    max_records: int | None = None,
    state_path: str | Path | None = None,
    force_refresh: bool = False,
    threads: int = 4,
    drop_load_constraints: bool = True,
    require_empty: bool = True,
) -> DiscoveredLoadStats:
    """Discover and load source datasets through the DuckDB direct pipeline."""

    started = time.perf_counter()
    selected = _discover_entity_datasets(
        database=database,
        inputs_package=inputs_package,
        sources=sources,
        dataset=dataset,
    )
    selected_by_source: dict[str, list[ResourceFunction]] = {}
    for fn in selected:
        selected_by_source.setdefault(fn.source, []).append(fn)

    with psycopg2.connect(database_url) as conn:
        for source in selected_by_source:
            print(f'[{source}] refresh deleting existing source content', flush=True)
            delete_source_content(conn, schema=schema, source=source)

    if drop_load_constraints:
        duckdb_load._drop_bulk_load_constraints_and_indexes(
            database_url=database_url,
            schema=schema,
        )

    totals = {
        'source_rows': 0,
        'identifiers': 0,
        'annotations': 0,
        'ontology_terms': 0,
    }
    first_dataset = True
    dataset_count = 0
    resolver_dir = Path(resolver_dir)

    for source, functions in selected_by_source.items():
        dataset_names = ','.join(fn.function_name for fn in functions)
        print(
            f'[{source}] duckdb refresh start datasets={len(functions)} '
            f'names={dataset_names}',
            flush=True,
        )
        for fn in functions:
            raw_dataset = getattr(fn.call, '_raw_dataset', None)
            if raw_dataset is None:
                continue
            dataset_count += 1
            records = raw_dataset(
                **_raw_dataset_kwargs(
                    fn,
                    resolver_dir=resolver_dir,
                    force_refresh=force_refresh,
                )
            )
            state = _dataset_state_path(state_path, fn)
            if fn.output_kind == 'ontology':
                stats = run_direct_copy_pipeline(
                    (),
                    ontology_records=records,
                    ontology_dataset=fn.function_name,
                    ontology_id=fn.ontology_id or fn.function_name,
                    source=fn.source,
                    dataset=fn.function_name,
                    database_url=database_url,
                    schema=schema,
                    resolver_dir=resolver_dir,
                    max_records=None,
                    state_path=state,
                    threads=threads,
                    drop_load_constraints=False,
                    require_empty=require_empty and first_dataset,
                )
                totals['source_rows'] += stats.source_rows
                totals['identifiers'] += stats.identifiers
                totals['annotations'] += stats.annotations
                totals['ontology_terms'] += stats.ontology_terms
                print(
                    f'[{fn.source}.{fn.function_name}] '
                    f'ontology_terms={stats.ontology_terms} '
                    f'copy={stats.copy_seconds:.3f}s '
                    f'total={stats.total_seconds:.3f}s',
                    flush=True,
                )
            else:
                stats = run_direct_copy_pipeline_batches(
                    records,
                    source=fn.source,
                    dataset=fn.function_name,
                    database_url=database_url,
                    schema=schema,
                    resolver_dir=resolver_dir,
                    batch_size=batch_size,
                    max_records=max_records,
                    state_path=state,
                    threads=threads,
                    drop_load_constraints=False,
                    require_empty=require_empty and first_dataset,
                )
                totals['source_rows'] += stats.source_rows
                totals['identifiers'] += stats.identifiers
                totals['annotations'] += stats.annotations
                totals['ontology_terms'] += stats.ontology_terms
            first_dataset = False
        print(f'[{source}] duckdb refresh done', flush=True)

    duckdb_load._reset_postgres_sequences(database_url=database_url, schema=schema)
    return DiscoveredLoadStats(
        sources=len(selected_by_source),
        datasets=dataset_count,
        source_rows=totals['source_rows'],
        identifiers=totals['identifiers'],
        annotations=totals['annotations'],
        ontology_terms=totals['ontology_terms'],
        total_seconds=time.perf_counter() - started,
    )


def _discover_entity_datasets(
    *,
    database: str,
    inputs_package: str,
    sources: tuple[str, ...],
    dataset: str | None,
) -> list[ResourceFunction]:
    discovered, _ = discover_resources(
        database_name=database,
        inputs_package=inputs_package,
        progress=True,
    )
    source_names = sources or tuple(sorted(discovered))
    unknown = [source for source in source_names if source not in discovered]
    if unknown:
        raise ValueError(f'Unknown source(s): {", ".join(unknown)}')
    selected = [
        fn
        for source in source_names
        for fn in discovered[source]
        if fn.function_name != 'resource'
        and fn.output_kind in {'entity', 'ontology'}
        and (dataset is None or fn.function_name == dataset)
        and getattr(fn.call, '_raw_dataset', None) is not None
    ]
    if not selected:
        raise ValueError('No matching entity/ontology datasets found.')
    return selected


def _raw_dataset_kwargs(
    fn: ResourceFunction,
    *,
    resolver_dir: Path,
    force_refresh: bool,
) -> dict[str, object]:
    kwargs: dict[str, object] = {'force_refresh': force_refresh}
    if fn.source == 'chembl':
        kwargs.update(
            {
                'chemical_resolver_lookup_path': (
                    resolver_dir
                    / 'chemicals'
                    / 'chemical_identifier_lookup.parquet'
                ),
                'chemical_resolver_sources': ('chebi', 'hmdb', 'chembl'),
            }
        )
    return kwargs


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
    require_empty: bool = True,
) -> DirectCopyStats:
    """Run the focused direct COPY pipeline for UniProt proteins."""

    from pypath.inputs_v2.uniprot import resource as uniprot_resource

    records = uniprot_resource.proteins(
        force_refresh=force_refresh,
        source='uniprot',
        dataset='proteins',
    )
    ontology_records = uniprot_resource.ontology(force_refresh=force_refresh)
    return run_direct_copy_pipeline(
        records,
        source='uniprot',
        dataset='proteins',
        ontology_records=ontology_records,
        ontology_dataset='ontology',
        ontology_id='uniprot_keywords',
        database_url=database_url,
        schema=schema,
        resolver_dir=resolver_dir,
        max_records=max_records,
        state_path=state_path,
        threads=threads,
        drop_load_constraints=drop_load_constraints,
        require_empty=require_empty,
    )


def _prepare_postgres_load(
    con: duckdb.DuckDBPyConnection,
    *,
    database_url: str,
    schema: str,
    require_empty: bool = True,
) -> None:
    con.execute('LOAD postgres')
    con.execute(
        f"ATTACH {duckdb_load._sql_literal(database_url)} AS pg (TYPE postgres)"
    )
    duckdb_load._bulk_load_create_views_from_loaded_tables(con)
    if require_empty:
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


def _batch_state_path(state_path: str | Path | None, batch_no: int) -> Path | None:
    if state_path is None:
        return None
    path = Path(state_path)
    return path.with_name(f'{path.stem}_batch_{batch_no:05d}{path.suffix}')


def _dataset_state_path(
    state_path: str | Path | None,
    fn: ResourceFunction,
) -> Path | None:
    if state_path is None:
        return None
    path = Path(state_path)
    slug = f'{fn.source}_{fn.function_name}'.replace('-', '_')
    return path.with_name(f'{path.stem}_{slug}{path.suffix}')


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
    parser.add_argument(
        '--resource',
        choices=('uniprot', 'chembl-activities'),
        default=None,
        help='Legacy single-resource shortcut. Prefer --sources/--dataset.',
    )
    parser.add_argument(
        '--sources',
        default=None,
        help='Comma-separated inputs_v2 source names. Omit to load all discovered sources.',
    )
    parser.add_argument(
        '--source',
        action='append',
        default=None,
        help='inputs_v2 source name. Can be repeated.',
    )
    parser.add_argument('--dataset', default=None)
    parser.add_argument('--inputs-package', default='pypath.inputs_v2')
    parser.add_argument('--database', default='omnipath')
    parser.add_argument('--resolver-dir', default='data')
    parser.add_argument('--max-records', type=int, default=50_000)
    parser.add_argument(
        '--batch-size',
        type=int,
        default=None,
        help='Run records in stable row-offset batches of this size.',
    )
    parser.add_argument('--state-path', default=None)
    parser.add_argument('--threads', type=int, default=4)
    parser.add_argument('--force-refresh', action='store_true')
    parser.add_argument(
        '--keep-load-constraints',
        action='store_true',
        help='Do not drop high-volume load constraints/indexes before COPY.',
    )
    parser.add_argument(
        '--append',
        action='store_true',
        help='Append into a non-empty schema instead of requiring empty content tables.',
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    if not args.database_url:
        raise SystemExit('--database-url or DATABASE_URL is required')
    max_records = (
        None
        if args.max_records is not None and args.max_records <= 0
        else args.max_records
    )

    if args.resource is None:
        source_names = _split_source_names(args.sources)
        for source in args.source or ():
            source_names.extend(_split_source_names(source))
        stats = run_discovered_direct_load(
            database_url=args.database_url,
            schema=args.schema,
            sources=tuple(source_names),
            dataset=args.dataset,
            database=args.database,
            inputs_package=args.inputs_package,
            resolver_dir=args.resolver_dir,
            batch_size=args.batch_size or 50_000,
            max_records=max_records,
            state_path=args.state_path,
            force_refresh=args.force_refresh,
            threads=args.threads,
            drop_load_constraints=not args.keep_load_constraints,
            require_empty=not args.append,
        )
        print(
            '[duckdb-direct-load] '
            f'sources={stats.sources} '
            f'datasets={stats.datasets} '
            f'source_rows={stats.source_rows} '
            f'identifiers={stats.identifiers} '
            f'annotations={stats.annotations} '
            f'ontology_terms={stats.ontology_terms} '
            f'total={stats.total_seconds:.3f}s',
            flush=True,
        )
        return 0

    if args.resource == 'chembl-activities':
        if args.force_refresh:
            raise SystemExit('--force-refresh is not supported for ChEMBL activities')
        if args.batch_size is not None:
            batch_stats = run_chembl_direct_copy_batches(
                database_url=args.database_url,
                schema=args.schema,
                resolver_dir=args.resolver_dir,
                batch_size=args.batch_size,
                max_records=max_records,
                state_path=args.state_path,
                threads=args.threads,
                drop_load_constraints=not args.keep_load_constraints,
                require_empty=not args.append,
            )
            print(
                '[duckdb-direct-copy-batches] '
                f'batches={len(batch_stats.batches)} '
                f'source_rows={batch_stats.source_rows} '
                f'identifiers={batch_stats.identifiers} '
                f'annotations={batch_stats.annotations} '
                f'ontology_terms={batch_stats.ontology_terms} '
                f'total={batch_stats.total_seconds:.3f}s',
                flush=True,
            )
            return 0
        from pypath.inputs_v2.chembl import resource as chembl_resource

        stats = run_direct_copy_pipeline(
            chembl_resource.activities(
                chemical_resolver_lookup_path=(
                    Path(args.resolver_dir)
                    / 'chemicals'
                    / 'chemical_identifier_lookup.parquet'
                ),
                chemical_resolver_sources=('chebi', 'hmdb', 'chembl'),
            ),
            source='chembl',
            dataset='activities',
            database_url=args.database_url,
            schema=args.schema,
            resolver_dir=args.resolver_dir,
            max_records=max_records,
            state_path=args.state_path,
            threads=args.threads,
            drop_load_constraints=not args.keep_load_constraints,
            require_empty=not args.append,
        )
    else:
        stats = run_uniprot_direct_copy_pipeline(
            database_url=args.database_url,
            schema=args.schema,
            resolver_dir=args.resolver_dir,
            max_records=max_records,
            state_path=args.state_path,
            force_refresh=args.force_refresh,
            threads=args.threads,
            drop_load_constraints=not args.keep_load_constraints,
            require_empty=not args.append,
        )
    print(
        '[duckdb-direct-copy] '
        f'source_rows={stats.source_rows} '
        f'identifiers={stats.identifiers} '
        f'annotations={stats.annotations} '
        f'ontology_terms={stats.ontology_terms} '
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


def _split_source_names(value: str | None) -> list[str]:
    if not value:
        return []
    return [
        part.strip()
        for chunk in value.split(',')
        for part in chunk.split()
        if part.strip()
    ]


if __name__ == '__main__':
    raise SystemExit(main())
