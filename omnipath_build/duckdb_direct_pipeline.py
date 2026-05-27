"""Focused DuckDB -> PostgreSQL COPY pipeline.

This module is the clean orchestration entry point for the pipeline we have
been benchmarking:

1. Stream source records into DuckDB evidence tables.
2. Canonicalize and project final load tables in DuckDB.
3. Populate tiny PostgreSQL dimensions.
4. Drop high-volume load constraints/indexes for a fresh schema.
5. COPY projected rows into PostgreSQL, attaching source partitions where
   supported by the lower-level loader.

The low-level projection SQL lives in ``duckdb_load``. Keeping this file small
makes the active load pipeline easy to read.
"""

from __future__ import annotations

import os
import sys
import time
import shutil
import json
from pathlib import Path
import argparse
import tempfile
from itertools import islice
from dataclasses import dataclass
from collections.abc import Iterable
from concurrent.futures import ProcessPoolExecutor, as_completed

import duckdb
import psycopg2

from omnipath_build import duckdb_load
from omnipath_build.resources import ResourceFunction, discover_resources
from omnipath_build.db.refresh import source_has_content, delete_source_content
from omnipath_build.ontology_artifacts import (
    write_ontology_obo,
    collect_ontology_terms,
)

DEFAULT_LOAD_EXCLUDED_SOURCES = frozenset({'rampdb'})
PREPARSE_SHARD_CACHE_VERSION = 'raw_shards_v1'


@dataclass(frozen=True)
class DirectCopyStats:
    """Timing and row counts for one COPY load."""

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
class DirectStageStats:
    """Projected/canonicalized DuckDB state ready for serial PostgreSQL COPY."""

    source: str
    dataset: str
    state_path: str
    source_rows: int
    identifiers: int
    annotations: int
    ontology_terms: int
    resolver_seconds: float
    projection_seconds: float
    canonicalize_seconds: float
    total_seconds: float
    entities: int
    relations: int
    annotation_relation_links: int


@dataclass(frozen=True)
class DirectCopyBatchStats:
    """Aggregated counts for a batched COPY load."""

    batches: tuple[DirectCopyStats, ...]
    source_rows: int
    identifiers: int
    annotations: int
    ontology_terms: int
    total_seconds: float


@dataclass(frozen=True)
class StagedDatasetTask:
    """A dataset or temporary raw-record shard unit of parallel staging work."""

    source: str
    dataset: str
    output_kind: str
    database: str
    inputs_package: str
    resolver_dir: str
    batch_size: int
    max_records: int | None
    state_path: str
    force_refresh: bool
    obo_artifacts: bool
    obo_output_dir: str
    threads: int
    raw_shard_path: str | None = None
    row_offset: int = 0
    shard_rows: int = 0
    shard_index: int | None = None


@dataclass(frozen=True)
class StagedDatasetPrepareTask:
    """A dataset unit that prepares cached preparse shards."""

    source: str
    dataset: str
    database: str
    inputs_package: str
    resolver_dir: str
    batch_size: int
    max_records: int | None
    force_refresh: bool
    preparse_dir: str


@dataclass(frozen=True)
class StagedRawShard:
    """A temporary raw-record shard ready for worker staging."""

    path: str
    row_offset: int
    rows: int
    shard_index: int


@dataclass(frozen=True)
class StagedDatasetPrepareResult:
    """Prepared preparse shards for one dataset."""

    source: str
    dataset: str
    shards: tuple[StagedRawShard, ...]
    preparse_dir: str
    reused: bool


@dataclass(frozen=True)
class StagedDatasetTaskResult:
    """Staging output for one dataset task."""

    source: str
    dataset: str
    failed: bool
    source_rows: int
    identifiers: int
    annotations: int
    ontology_terms: int
    total_seconds: float
    stages: tuple[DirectStageStats, ...]


@dataclass(frozen=True)
class DiscoveredLoadStats:
    """Aggregated counts for discovered source loading."""

    sources: int
    skipped_sources: int
    datasets: int
    failed_sources: int
    failed_datasets: int
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
            f'ATTACH {duckdb_load._sql_literal(database_url)} AS pg (TYPE postgres)'
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


def stage_direct_copy_pipeline(
    records: Iterable[object],
    *,
    source: str,
    dataset: str,
    state_path: str | Path,
    ontology_records: Iterable[object] | None = None,
    ontology_dataset: str = 'ontology',
    ontology_id: str | None = None,
    resolver_dir: str | Path = 'data',
    max_records: int | None = None,
    threads: int = 4,
    row_offset: int = 0,
) -> DirectStageStats:
    """Project and canonicalize one dataset into a reusable DuckDB state file."""

    total_started = time.perf_counter()
    resolver_dir = Path(resolver_dir)
    prepared_state_path = _prepare_state_path(state_path)
    if prepared_state_path is None:
        raise ValueError('state_path is required for staged load')

    con = duckdb.connect(str(prepared_state_path))
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
    finally:
        con.close()

    return DirectStageStats(
        source=source,
        dataset=dataset,
        state_path=str(prepared_state_path),
        source_rows=projection.source_rows,
        identifiers=projection.identifiers,
        annotations=projection.annotations,
        ontology_terms=ontology_terms,
        resolver_seconds=resolver_seconds,
        projection_seconds=projection_seconds,
        canonicalize_seconds=canonicalize_seconds,
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
    """Run COPY in stable row-offset batches and log progress."""

    started = time.perf_counter()
    if batch_size <= 0:
        raise ValueError('batch_size must be positive')
    iterator = iter(records)
    if max_records is not None:
        iterator = islice(iterator, max_records)

    row_offset = 0
    projected_rows = 0
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
            row_offset=row_offset,
        )
        row_offset += len(batch)
        projected_rows += stats.source_rows
        batch_stats.append(stats)
        if progress:
            print(
                '[load-batch] '
                f'batch={batch_no} '
                f'batch_rows={stats.source_rows} '
                f'cumulative_rows={projected_rows} '
                f'row_offset={row_offset} '
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


def stage_direct_copy_pipeline_batches(
    records: Iterable[object],
    *,
    source: str,
    dataset: str,
    state_path: str | Path,
    resolver_dir: str | Path = 'data',
    batch_size: int = 50_000,
    max_records: int | None = None,
    threads: int = 4,
    progress: bool = True,
) -> tuple[DirectStageStats, ...]:
    """Project and canonicalize record batches into DuckDB state files."""

    if batch_size <= 0:
        raise ValueError('batch_size must be positive')
    iterator = iter(records)
    if max_records is not None:
        iterator = islice(iterator, max_records)

    row_offset = 0
    projected_rows = 0
    batch_no = 0
    stage_stats: list[DirectStageStats] = []
    while True:
        batch = list(islice(iterator, batch_size))
        if not batch:
            break
        batch_no += 1
        batch_state_path = _batch_state_path(state_path, batch_no)
        if batch_state_path is None:
            raise ValueError('state_path is required for staged load')
        stats = stage_direct_copy_pipeline(
            batch,
            source=source,
            dataset=dataset,
            state_path=batch_state_path,
            resolver_dir=resolver_dir,
            max_records=None,
            threads=threads,
            row_offset=row_offset,
        )
        row_offset += len(batch)
        projected_rows += stats.source_rows
        stage_stats.append(stats)
        if progress:
            print(
                '[load-stage-batch] '
                f'source={source} '
                f'dataset={dataset} '
                f'batch={batch_no} '
                f'batch_rows={stats.source_rows} '
                f'cumulative_rows={projected_rows} '
                f'row_offset={row_offset} '
                f'projection={stats.projection_seconds:.3f}s '
                f'canonicalize={stats.canonicalize_seconds:.3f}s '
                f'total={stats.total_seconds:.3f}s',
                flush=True,
            )

    return tuple(stage_stats)


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
    """Run ChEMBL activities through the COPY pipeline in batches."""

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
    obo_artifacts: bool = True,
    obo_output_dir: str | Path = 'data/obo',
    threads: int = 4,
    drop_load_constraints: bool = True,
    require_empty: bool = True,
    reload_existing: bool = False,
    stage_jobs: int = 1,
    staging_dir: str | Path | None = None,
) -> DiscoveredLoadStats:
    """Discover and load source datasets through the DuckDB/PostgreSQL pipeline."""

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

    skipped_sources = 0
    with psycopg2.connect(database_url) as conn:
        if reload_existing:
            for source in selected_by_source:
                print(
                    f'[{source}] reload deleting existing source content',
                    flush=True,
                )
                delete_source_content(conn, schema=schema, source=source)
        else:
            load_selected_by_source: dict[str, list[ResourceFunction]] = {}
            for source, functions in selected_by_source.items():
                if source_has_content(conn, schema=schema, source=source):
                    skipped_sources += 1
                    print(
                        f'[{source}] load skip existing source content',
                        flush=True,
                    )
                    continue
                load_selected_by_source[source] = functions
            selected_by_source = load_selected_by_source

    if not selected_by_source:
        return DiscoveredLoadStats(
            sources=0,
            skipped_sources=skipped_sources,
            datasets=0,
            failed_sources=0,
            failed_datasets=0,
            source_rows=0,
            identifiers=0,
            annotations=0,
            ontology_terms=0,
            total_seconds=time.perf_counter() - started,
        )

    if stage_jobs > 1:
        return _run_discovered_direct_load_staged(
            selected_by_source=selected_by_source,
            skipped_sources=skipped_sources,
            started=started,
            database_url=database_url,
            schema=schema,
            database=database,
            inputs_package=inputs_package,
            resolver_dir=resolver_dir,
            batch_size=batch_size,
            max_records=max_records,
            force_refresh=force_refresh,
            obo_artifacts=obo_artifacts,
            obo_output_dir=obo_output_dir,
            threads=threads,
            drop_load_constraints=drop_load_constraints,
            require_empty=require_empty,
            stage_jobs=stage_jobs,
            staging_dir=staging_dir,
        )

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
    failed_datasets = 0
    failed_sources = 0
    resolver_dir = Path(resolver_dir)

    for source, functions in selected_by_source.items():
        source_succeeded = False
        source_failed = False
        dataset_names = ','.join(fn.function_name for fn in functions)
        print(
            f'[{source}] duckdb load start datasets={len(functions)} '
            f'names={dataset_names}',
            flush=True,
        )
        for fn in functions:
            raw_dataset = getattr(fn.call, '_raw_dataset', None)
            if raw_dataset is None:
                continue
            try:
                records = raw_dataset(
                    **_raw_dataset_kwargs(
                        fn,
                        resolver_dir=resolver_dir,
                        force_refresh=force_refresh,
                        max_records=max_records,
                    )
                )
                state = _dataset_state_path(state_path, fn)
                if fn.output_kind == 'ontology':
                    terms = collect_ontology_terms(records)
                    if obo_artifacts:
                        obo_path = write_ontology_obo(
                            fn,
                            terms,
                            output_dir=Path(obo_output_dir),
                        )
                        print(
                            f'[{fn.source}.{fn.function_name}] obo={obo_path}',
                            flush=True,
                        )
                    stats = run_direct_copy_pipeline(
                        (),
                        ontology_records=terms,
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
                dataset_count += 1
                source_succeeded = True
                first_dataset = False
            except Exception as exc:  # noqa: BLE001
                failed_datasets += 1
                source_failed = True
                _warn_dataset_failed(fn, exc)
                continue
        if source_failed and not source_succeeded:
            failed_sources += 1
        print(f'[{source}] duckdb load done', flush=True)

    duckdb_load._reset_postgres_sequences(database_url=database_url, schema=schema)
    return DiscoveredLoadStats(
        sources=len(selected_by_source),
        skipped_sources=skipped_sources,
        datasets=dataset_count,
        failed_sources=failed_sources,
        failed_datasets=failed_datasets,
        source_rows=totals['source_rows'],
        identifiers=totals['identifiers'],
        annotations=totals['annotations'],
        ontology_terms=totals['ontology_terms'],
        total_seconds=time.perf_counter() - started,
    )


def _run_discovered_direct_load_staged(
    *,
    selected_by_source: dict[str, list[ResourceFunction]],
    skipped_sources: int,
    started: float,
    database_url: str,
    schema: str,
    database: str,
    inputs_package: str,
    resolver_dir: str | Path,
    batch_size: int,
    max_records: int | None,
    force_refresh: bool,
    obo_artifacts: bool,
    obo_output_dir: str | Path,
    threads: int,
    drop_load_constraints: bool,
    require_empty: bool,
    stage_jobs: int,
    staging_dir: str | Path | None,
) -> DiscoveredLoadStats:
    """Stage sources in parallel, then COPY each staged file serially."""

    if staging_dir is None:
        with tempfile.TemporaryDirectory(prefix='omnipath-build-stage-') as tmpdir:
            return _run_discovered_direct_load_staged_in_dir(
                selected_by_source=selected_by_source,
                skipped_sources=skipped_sources,
                started=started,
                database_url=database_url,
                schema=schema,
                database=database,
                inputs_package=inputs_package,
                resolver_dir=resolver_dir,
                batch_size=batch_size,
                max_records=max_records,
                force_refresh=force_refresh,
                obo_artifacts=obo_artifacts,
                obo_output_dir=obo_output_dir,
                threads=threads,
                drop_load_constraints=drop_load_constraints,
                require_empty=require_empty,
                stage_jobs=stage_jobs,
                staging_dir=Path(tmpdir),
            )

    run_dir = Path(staging_dir) / f'load_{int(time.time())}_{os.getpid()}'
    return _run_discovered_direct_load_staged_in_dir(
        selected_by_source=selected_by_source,
        skipped_sources=skipped_sources,
        started=started,
        database_url=database_url,
        schema=schema,
        database=database,
        inputs_package=inputs_package,
        resolver_dir=resolver_dir,
        batch_size=batch_size,
        max_records=max_records,
        force_refresh=force_refresh,
        obo_artifacts=obo_artifacts,
        obo_output_dir=obo_output_dir,
        threads=threads,
        drop_load_constraints=drop_load_constraints,
        require_empty=require_empty,
        stage_jobs=stage_jobs,
        staging_dir=run_dir,
    )


def _run_discovered_direct_load_staged_in_dir(
    *,
    selected_by_source: dict[str, list[ResourceFunction]],
    skipped_sources: int,
    started: float,
    database_url: str,
    schema: str,
    database: str,
    inputs_package: str,
    resolver_dir: str | Path,
    batch_size: int,
    max_records: int | None,
    force_refresh: bool,
    obo_artifacts: bool,
    obo_output_dir: str | Path,
    threads: int,
    drop_load_constraints: bool,
    require_empty: bool,
    stage_jobs: int,
    staging_dir: Path,
) -> DiscoveredLoadStats:
    staging_dir.mkdir(parents=True, exist_ok=True)
    _scheduler_log(
        'start',
        staging_dir=str(staging_dir),
        workers=stage_jobs,
    )

    tasks, prepared_shards, failed_dataset_keys = _prepare_staged_dataset_tasks(
        selected_by_source=selected_by_source,
        database=database,
        inputs_package=inputs_package,
        resolver_dir=resolver_dir,
        batch_size=batch_size,
        max_records=max_records,
        force_refresh=force_refresh,
        obo_artifacts=obo_artifacts,
        obo_output_dir=obo_output_dir,
        threads=threads,
        staging_dir=staging_dir,
        stage_jobs=stage_jobs,
    )
    _scheduler_log(
        'queue_ready',
        datasets=len(prepared_shards),
        tasks=len(tasks),
        failed_datasets=len(failed_dataset_keys),
    )

    results_by_index: dict[int, StagedDatasetTaskResult] = {}
    with ProcessPoolExecutor(max_workers=stage_jobs) as executor:
        for index, task in enumerate(tasks):
            _scheduler_log(
                'task_submit',
                task=index,
                source=task.source,
                dataset=task.dataset,
                kind=task.output_kind,
                shard='-' if task.shard_index is None else task.shard_index,
                rows=task.shard_rows or '-',
            )
        future_by_index = {
            executor.submit(_stage_dataset_task_worker, task): index
            for index, task in enumerate(tasks)
        }
        for future in as_completed(future_by_index):
            index = future_by_index[future]
            task = tasks[index]
            try:
                result = future.result()
            except Exception as exc:  # noqa: BLE001
                print(
                    '[warning] '
                    f'[{task.source}.{task.dataset}] staging failed; '
                    'continuing: '
                    f'{exc.__class__.__name__}: {exc}',
                    file=sys.stderr,
                    flush=True,
                )
                result = StagedDatasetTaskResult(
                    source=task.source,
                    dataset=task.dataset,
                    failed=True,
                    source_rows=0,
                    identifiers=0,
                    annotations=0,
                    ontology_terms=0,
                    total_seconds=0,
                    stages=(),
                )
            results_by_index[index] = result
            if result.failed:
                failed_dataset_keys.add((result.source, result.dataset))
            _scheduler_log(
                'task_done',
                task=index,
                source=task.source,
                dataset=task.dataset,
                shard=_task_part_label(task),
                failed=int(result.failed),
                stages=len(result.stages),
                rows=result.source_rows,
                seconds=f'{result.total_seconds:.3f}',
            )

    if drop_load_constraints:
        duckdb_load._drop_bulk_load_constraints_and_indexes(
            database_url=database_url,
            schema=schema,
        )

    first_stage = True
    successful_dataset_keys: set[tuple[str, str]] = set()
    for index, _task in enumerate(tasks):
        result = results_by_index[index]
        dataset_key = (result.source, result.dataset)
        if result.failed or dataset_key in failed_dataset_keys:
            continue
        successful_dataset_keys.add(dataset_key)
        for stage in result.stages:
            _scheduler_log(
                'copy_start',
                source=stage.source,
                dataset=stage.dataset,
                rows=stage.source_rows,
            )
            copy_seconds = copy_staged_direct_load(
                stage,
                database_url=database_url,
                schema=schema,
                require_empty=require_empty and first_stage,
            )
            first_stage = False
            _scheduler_log(
                'copy_done',
                source=stage.source,
                dataset=stage.dataset,
                rows=stage.source_rows,
                seconds=f'{copy_seconds:.3f}',
            )
    duckdb_load._reset_postgres_sequences(database_url=database_url, schema=schema)

    all_dataset_keys = {
        (source, fn.function_name)
        for source, functions in selected_by_source.items()
        for fn in functions
        if getattr(fn.call, '_raw_dataset', None) is not None
    }
    successful_dataset_keys = successful_dataset_keys - failed_dataset_keys
    results = tuple(results_by_index.values())
    return DiscoveredLoadStats(
        sources=len(selected_by_source),
        skipped_sources=skipped_sources,
        datasets=len(successful_dataset_keys),
        failed_sources=sum(
            1
            for source in selected_by_source
            if not any(key[0] == source for key in successful_dataset_keys)
            and any(key[0] == source for key in failed_dataset_keys)
        ),
        failed_datasets=len(failed_dataset_keys & all_dataset_keys),
        source_rows=sum(result.source_rows for result in results),
        identifiers=sum(result.identifiers for result in results),
        annotations=sum(result.annotations for result in results),
        ontology_terms=sum(result.ontology_terms for result in results),
        total_seconds=time.perf_counter() - started,
    )


def _prepare_staged_dataset_tasks(
    *,
    selected_by_source: dict[str, list[ResourceFunction]],
    database: str,
    inputs_package: str,
    resolver_dir: str | Path,
    batch_size: int,
    max_records: int | None,
    force_refresh: bool,
    obo_artifacts: bool,
    obo_output_dir: str | Path,
    threads: int,
    staging_dir: Path,
    stage_jobs: int,
) -> tuple[
    list[StagedDatasetTask],
    dict[tuple[str, str], StagedDatasetPrepareResult],
    set[tuple[str, str]],
]:
    """Create round-robin dataset tasks for the shared LOAD_JOBS pool."""

    resolver_dir = Path(resolver_dir)
    task_groups: list[list[StagedDatasetTask]] = []
    prepared_shards: dict[tuple[str, str], StagedDatasetPrepareResult] = {}
    failed_dataset_keys: set[tuple[str, str]] = set()
    prepare_tasks: list[StagedDatasetPrepareTask] = []
    source_functions: dict[tuple[str, str], ResourceFunction] = {}

    for source, functions in selected_by_source.items():
        state_dir = staging_dir / _path_slug(source)
        state_dir.mkdir(parents=True, exist_ok=True)
        for fn in functions:
            dataset_key = (fn.source, fn.function_name)
            source_functions[dataset_key] = fn
            raw_dataset = getattr(fn.call, '_raw_dataset', None)
            if raw_dataset is None:
                continue
            base_state = state_dir / (
                f'{_path_slug(fn.source)}_{_path_slug(fn.function_name)}.duckdb'
            )
            if fn.output_kind == 'ontology':
                task_groups.append(
                    [
                        StagedDatasetTask(
                            source=fn.source,
                            dataset=fn.function_name,
                            output_kind=fn.output_kind,
                            database=database,
                            inputs_package=inputs_package,
                            resolver_dir=str(resolver_dir),
                            batch_size=batch_size,
                            max_records=max_records,
                            state_path=str(base_state),
                            force_refresh=force_refresh,
                            obo_artifacts=obo_artifacts,
                            obo_output_dir=str(obo_output_dir),
                            threads=threads,
                        )
                    ]
                )
                continue

            prepare_tasks.append(
                StagedDatasetPrepareTask(
                    source=fn.source,
                    dataset=fn.function_name,
                    database=database,
                    inputs_package=inputs_package,
                    resolver_dir=str(resolver_dir),
                    batch_size=batch_size,
                    max_records=max_records,
                    force_refresh=force_refresh,
                    preparse_dir=str(
                        _preparse_cache_dir(
                            fn,
                            batch_size=batch_size,
                            max_records=max_records,
                        )
                    ),
                )
            )

    prepared_by_key: dict[tuple[str, str], StagedDatasetPrepareResult] = {}
    if prepare_tasks:
        _scheduler_log('preparse_start', datasets=len(prepare_tasks))
        with ProcessPoolExecutor(max_workers=stage_jobs) as executor:
            future_by_task = {
                executor.submit(_prepare_preparse_shards_worker, task): task
                for task in prepare_tasks
            }
            for future in as_completed(future_by_task):
                task = future_by_task[future]
                dataset_key = (task.source, task.dataset)
                fn = source_functions[dataset_key]
                try:
                    prepared = future.result()
                except Exception as exc:  # noqa: BLE001
                    failed_dataset_keys.add(dataset_key)
                    _warn_dataset_failed(fn, exc)
                    continue
                prepared_by_key[dataset_key] = prepared
                _scheduler_log(
                    'preparse_done',
                    source=prepared.source,
                    dataset=prepared.dataset,
                    shards=len(prepared.shards),
                    rows=sum(shard.rows for shard in prepared.shards),
                    reused=int(prepared.reused),
                    path=prepared.preparse_dir,
                )

    for prepare_task in prepare_tasks:
        dataset_key = (prepare_task.source, prepare_task.dataset)
        prepared = prepared_by_key.get(dataset_key)
        if prepared is None:
            continue
        fn = source_functions[dataset_key]
        prepared_shards[dataset_key] = prepared
        base_state = staging_dir / _path_slug(fn.source) / (
            f'{_path_slug(fn.source)}_{_path_slug(fn.function_name)}.duckdb'
        )
        task_groups.append(
            [
                StagedDatasetTask(
                    source=fn.source,
                    dataset=fn.function_name,
                    output_kind=fn.output_kind,
                    database=database,
                    inputs_package=inputs_package,
                    resolver_dir=str(resolver_dir),
                    batch_size=batch_size,
                    max_records=None,
                    state_path=str(
                        base_state.with_name(
                            f'{base_state.stem}_shard_{shard.shard_index:05d}'
                            f'{base_state.suffix}'
                        )
                    ),
                    force_refresh=force_refresh,
                    obo_artifacts=obo_artifacts,
                    obo_output_dir=str(obo_output_dir),
                    threads=threads,
                    raw_shard_path=shard.path,
                    row_offset=shard.row_offset,
                    shard_rows=shard.rows,
                    shard_index=shard.shard_index,
                )
                for shard in prepared.shards
            ]
        )

    tasks: list[StagedDatasetTask] = []
    max_group_len = max((len(group) for group in task_groups), default=0)
    for index in range(max_group_len):
        for group in task_groups:
            if index < len(group):
                tasks.append(group[index])
    return tasks, prepared_shards, failed_dataset_keys


def _scheduler_log(event: str, **fields: object) -> None:
    details = ' '.join(f'{key}={value}' for key, value in fields.items())
    print(
        f'[load-scheduler] event={event}' + (f' {details}' if details else ''),
        flush=True,
    )


def _task_part_label(task: StagedDatasetTask) -> str:
    if task.shard_index is None:
        return '-'
    return str(task.shard_index)


def _preparse_cache_dir(
    fn: ResourceFunction,
    *,
    batch_size: int,
    max_records: int | None,
) -> Path:
    max_part = 'all' if max_records is None else f'max_{max_records}'
    return (
        _pypath_data_dir()
        / _path_slug(fn.source)
        / 'preparse'
        / _path_slug(fn.function_name)
        / f'batch_{batch_size}_{max_part}'
    )


def _pypath_data_dir() -> Path:
    configured = os.environ.get('PYPATH_DOWNLOAD_DATADIR')
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parents[1] / 'pypath-data'


def copy_staged_direct_load(
    stage: DirectStageStats,
    *,
    database_url: str,
    schema: str,
    require_empty: bool,
) -> float:
    """COPY one staged DuckDB state file into PostgreSQL."""

    started = time.perf_counter()
    con = duckdb.connect(stage.state_path, read_only=False)
    try:
        _prepare_postgres_load(
            con,
            database_url=database_url,
            schema=schema,
            require_empty=require_empty,
        )
        con.execute(
            f'ATTACH {duckdb_load._sql_literal(database_url)} AS pg (TYPE postgres)'
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
    finally:
        con.close()
    return time.perf_counter() - started


def _prepare_preparse_shards_worker(
    task: StagedDatasetPrepareTask,
) -> StagedDatasetPrepareResult:
    started = time.perf_counter()
    preparse_dir = Path(task.preparse_dir)
    if not task.force_refresh:
        cached = _read_preparse_shards(task, preparse_dir)
        if cached is not None:
            print(
                '[load-preparse] '
                f'source={task.source} dataset={task.dataset} '
                f'cache=hit shards={len(cached.shards)} '
                f'rows={sum(shard.rows for shard in cached.shards)} '
                f'path={preparse_dir}',
                flush=True,
            )
            return cached

    selected = _discover_entity_datasets(
        database=task.database,
        inputs_package=task.inputs_package,
        sources=(task.source,),
        dataset=task.dataset,
    )
    if len(selected) != 1:
        raise ValueError(
            f'Expected one dataset for {task.source}.{task.dataset}; '
            f'found {len(selected)}'
        )
    fn = selected[0]
    raw_dataset = getattr(fn.call, '_raw_dataset', None)
    if raw_dataset is None:
        raise ValueError(f'{task.source}.{task.dataset} has no raw dataset')

    tmp_dir = preparse_dir.with_name(f'.{preparse_dir.name}.tmp.{os.getpid()}')
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir)
    tmp_dir.mkdir(parents=True, exist_ok=True)
    raw_rows = raw_dataset.raw(
        **_raw_dataset_kwargs(
            fn,
            resolver_dir=Path(task.resolver_dir),
            force_refresh=task.force_refresh,
            max_records=task.max_records,
        )
    )
    if task.max_records is not None:
        raw_rows = islice(raw_rows, task.max_records)

    shards: list[StagedRawShard] = []
    row_offset = 0
    try:
        for shard_index, batch in enumerate(
            _iter_sized_batches(raw_rows, task.batch_size)
        ):
            shard_path = tmp_dir / f'shard_{shard_index:05d}.parquet'
            _write_raw_shard(batch, shard_path)
            shards.append(
                StagedRawShard(
                    path=str(preparse_dir / shard_path.name),
                    row_offset=row_offset,
                    rows=len(batch),
                    shard_index=shard_index,
                )
            )
            row_offset += len(batch)
            print(
                '[load-preparse] '
                f'source={fn.source} dataset={fn.function_name} '
                f'cache=build shard={shard_index} shard_rows={len(batch)} '
                f'total_rows={row_offset} '
                f'seconds={time.perf_counter() - started:.1f}',
                flush=True,
            )
        _write_preparse_manifest(
            tmp_dir,
            task=task,
            shards=shards,
        )
        if preparse_dir.exists():
            shutil.rmtree(preparse_dir)
        tmp_dir.replace(preparse_dir)
        print(
            '[load-preparse] '
            f'source={fn.source} dataset={fn.function_name} '
            f'cache=published shards={len(shards)} rows={row_offset} '
            f'path={preparse_dir} '
            f'seconds={time.perf_counter() - started:.1f}',
            flush=True,
        )
    except Exception:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise

    return StagedDatasetPrepareResult(
        source=fn.source,
        dataset=fn.function_name,
        shards=tuple(shards),
        preparse_dir=str(preparse_dir),
        reused=False,
    )


def _read_preparse_shards(
    task: StagedDatasetPrepareTask,
    preparse_dir: Path,
) -> StagedDatasetPrepareResult | None:
    manifest_path = preparse_dir / 'manifest.json'
    if not manifest_path.exists():
        return None
    try:
        manifest = json.loads(manifest_path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    if not _preparse_manifest_matches(task, manifest):
        return None

    shards: list[StagedRawShard] = []
    for item in manifest.get('shards') or []:
        try:
            shard_index = int(item['shard_index'])
            filename = str(item['filename'])
            shard_path = preparse_dir / filename
            rows = int(item['rows'])
            row_offset = int(item['row_offset'])
        except (KeyError, TypeError, ValueError):
            return None
        if not shard_path.exists():
            return None
        shards.append(
            StagedRawShard(
                path=str(shard_path),
                row_offset=row_offset,
                rows=rows,
                shard_index=shard_index,
            )
        )
    if not shards:
        return None
    return StagedDatasetPrepareResult(
        source=task.source,
        dataset=task.dataset,
        shards=tuple(shards),
        preparse_dir=str(preparse_dir),
        reused=True,
    )


def _preparse_manifest_matches(
    task: StagedDatasetPrepareTask,
    manifest: dict[str, object],
) -> bool:
    return (
        manifest.get('version') == PREPARSE_SHARD_CACHE_VERSION
        and manifest.get('source') == task.source
        and manifest.get('dataset') == task.dataset
        and manifest.get('batch_size') == task.batch_size
        and manifest.get('max_records') == task.max_records
    )


def _write_preparse_manifest(
    preparse_dir: Path,
    *,
    task: StagedDatasetPrepareTask,
    shards: list[StagedRawShard],
) -> None:
    payload = {
        'version': PREPARSE_SHARD_CACHE_VERSION,
        'source': task.source,
        'dataset': task.dataset,
        'batch_size': task.batch_size,
        'max_records': task.max_records,
        'rows': sum(shard.rows for shard in shards),
        'shards': [
            {
                'filename': Path(shard.path).name,
                'row_offset': shard.row_offset,
                'rows': shard.rows,
                'shard_index': shard.shard_index,
            }
            for shard in shards
        ],
    }
    (preparse_dir / 'manifest.json').write_text(
        json.dumps(payload, indent=2, sort_keys=True) + '\n'
    )


def _iter_sized_batches(
    rows: Iterable[dict[str, object]],
    batch_size: int,
) -> Iterable[list[dict[str, object]]]:
    iterator = iter(rows)
    while True:
        batch = list(islice(iterator, batch_size))
        if not batch:
            break
        yield batch


def _write_raw_shard(rows: list[dict[str, object]], path: Path) -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq

    names: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for name in row:
            if name not in seen:
                names.append(name)
                seen.add(name)
    normalized = [
        {name: _stringify_if_unsupported(row.get(name)) for name in names}
        for row in rows
    ]
    table = pa.Table.from_pylist(normalized)
    table = table.cast(_schema_with_storable_nulls(table.schema), safe=False)
    pq.write_table(table, path, compression='zstd', use_dictionary=True)


def _stringify_if_unsupported(value: object) -> object:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, bytes):
        return value.decode('utf-8', errors='replace')
    if isinstance(value, list | tuple):
        return [_stringify_if_unsupported(item) for item in value]
    if isinstance(value, dict):
        return {str(k): _stringify_if_unsupported(v) for k, v in value.items()}
    return str(value)


def _schema_with_storable_nulls(schema: object) -> object:
    import pyarrow as pa

    fields = [
        pa.field(
            field.name,
            pa.string() if pa.types.is_null(field.type) else field.type,
        )
        for field in schema
    ]
    return pa.schema(fields)


def _iter_raw_shard_records(raw_dataset: object, shard_path: str) -> Iterable[object]:
    import pyarrow.parquet as pq

    parquet_file = pq.ParquetFile(shard_path)
    for batch in parquet_file.iter_batches(batch_size=10_000):
        for row in batch.to_pylist():
            yield raw_dataset.mapper(row)


def _stage_dataset_task_worker(task: StagedDatasetTask) -> StagedDatasetTaskResult:
    started = time.perf_counter()
    selected = _discover_entity_datasets(
        database=task.database,
        inputs_package=task.inputs_package,
        sources=(task.source,),
        dataset=task.dataset,
    )
    if len(selected) != 1:
        raise ValueError(
            f'Expected one dataset for {task.source}.{task.dataset}; '
            f'found {len(selected)}'
        )
    fn = selected[0]
    raw_dataset = getattr(fn.call, '_raw_dataset', None)
    if raw_dataset is None:
        raise ValueError(f'{task.source}.{task.dataset} has no raw dataset')

    resolver_dir = Path(task.resolver_dir)
    if fn.output_kind == 'ontology':
        records = raw_dataset(
            **_raw_dataset_kwargs(
                fn,
                resolver_dir=resolver_dir,
                force_refresh=task.force_refresh,
                max_records=task.max_records,
            )
        )
        terms = collect_ontology_terms(records)
        if task.obo_artifacts:
            obo_path = write_ontology_obo(
                fn,
                terms,
                output_dir=Path(task.obo_output_dir),
            )
            print(
                f'[{fn.source}.{fn.function_name}] obo={obo_path}',
                flush=True,
            )
        stages = (
            stage_direct_copy_pipeline(
                (),
                ontology_records=terms,
                ontology_dataset=fn.function_name,
                ontology_id=fn.ontology_id or fn.function_name,
                source=fn.source,
                dataset=fn.function_name,
                state_path=task.state_path,
                resolver_dir=resolver_dir,
                max_records=None,
                threads=task.threads,
            ),
        )
    else:
        if task.raw_shard_path is None:
            records = raw_dataset(
                **_raw_dataset_kwargs(
                    fn,
                    resolver_dir=resolver_dir,
                    force_refresh=task.force_refresh,
                    max_records=task.max_records,
                )
            )
        else:
            records = _iter_raw_shard_records(raw_dataset, task.raw_shard_path)
        stages = (
            stage_direct_copy_pipeline(
                records,
                source=fn.source,
                dataset=fn.function_name,
                state_path=task.state_path,
                resolver_dir=resolver_dir,
                max_records=None,
                threads=task.threads,
                row_offset=task.row_offset,
            ),
        )

    return StagedDatasetTaskResult(
        source=fn.source,
        dataset=fn.function_name,
        failed=False,
        source_rows=sum(stage.source_rows for stage in stages),
        identifiers=sum(stage.identifiers for stage in stages),
        annotations=sum(stage.annotations for stage in stages),
        ontology_terms=sum(stage.ontology_terms for stage in stages),
        total_seconds=time.perf_counter() - started,
        stages=tuple(stages),
    )


def _path_slug(value: str) -> str:
    return ''.join(
        character if character.isalnum() or character in {'-', '_'} else '_'
        for character in value
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
    source_names = sources or tuple(
        source
        for source in sorted(discovered)
        if source not in DEFAULT_LOAD_EXCLUDED_SOURCES
    )
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
    max_records: int | None = None,
) -> dict[str, object]:
    kwargs: dict[str, object] = {'force_refresh': force_refresh}
    if max_records is not None:
        kwargs['max_records'] = max_records
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


def _warn_dataset_failed(fn: ResourceFunction, exc: Exception) -> None:
    print(
        '[warning] '
        f'[{fn.source}.{fn.function_name}] load failed; continuing: '
        f'{exc.__class__.__name__}: {exc}',
        file=sys.stderr,
        flush=True,
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
    require_empty: bool = True,
) -> DirectCopyStats:
    """Run the focused COPY pipeline for UniProt proteins."""

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
        f'ATTACH {duckdb_load._sql_literal(database_url)} AS pg (TYPE postgres)'
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
    """Build the DuckDB/PostgreSQL load argument parser."""

    parser = argparse.ArgumentParser(
        description='Focused DuckDB/PostgreSQL COPY pipeline.'
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
    parser.add_argument(
        '--stage-jobs',
        type=int,
        default=1,
        help=(
            'Use one shared worker pool for staged DuckDB work. Workers are '
            'assigned across sources first, then across temporary raw-record '
            'shards within a source when sharded work is available. PostgreSQL '
            'COPY remains serial.'
        ),
    )
    parser.add_argument(
        '--staging-dir',
        default=None,
        help=(
            'Directory for staged DuckDB files when --stage-jobs is greater '
            'than 1. Defaults to a temporary directory.'
        ),
    )
    parser.add_argument('--force-refresh', action='store_true')
    parser.add_argument(
        '--reload-existing',
        action='store_true',
        help='Delete and refresh selected sources instead of skipping them.',
    )
    parser.add_argument(
        '--obo-artifacts',
        action=argparse.BooleanOptionalAction,
        default=True,
        help='Write ontology datasets as OBO artifacts for the API service.',
    )
    parser.add_argument(
        '--obo-output-dir',
        default='data/obo',
        help='Directory for OBO artifacts written from ontology datasets.',
    )
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
    """Run the DuckDB/PostgreSQL load command line interface."""

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
            obo_artifacts=args.obo_artifacts,
            obo_output_dir=args.obo_output_dir,
            threads=args.threads,
            drop_load_constraints=not args.keep_load_constraints,
            require_empty=not args.append,
            reload_existing=args.reload_existing,
            stage_jobs=args.stage_jobs,
            staging_dir=args.staging_dir,
        )
        print(
            '[load] '
            f'sources={stats.sources} '
            f'skipped_sources={stats.skipped_sources} '
            f'datasets={stats.datasets} '
            f'failed_sources={stats.failed_sources} '
            f'failed_datasets={stats.failed_datasets} '
            f'source_rows={stats.source_rows} '
            f'identifiers={stats.identifiers} '
            f'annotations={stats.annotations} '
            f'ontology_terms={stats.ontology_terms} '
            f'total={stats.total_seconds:.3f}s',
            flush=True,
        )
        if stats.failed_sources or stats.failed_datasets:
            return 1
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
                '[load-batches] '
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
        '[load] '
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
