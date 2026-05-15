from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

import psycopg2

from pypath.internals.ontology_schema import OntologyTerm
from pypath.inputs_v2.ontology_serializers import format_obo

from minimal.db import (
    delete_source_content,
    ensure_schema,
    rebuild_bitmap_tables,
    rebuild_derived_tables,
    create_secondary_indexes,
    reset_content_tables,
    sync_resources_table,
)
from minimal.ingest import (
    MinimalIngestor,
    BulkMinimalIngestor,
)
from minimal.loaders import load_ontology_terms, load_resolver_tables
from minimal.canonicalize import canonicalize
from minimal.resolver.mapping_tables import (
    SOURCE_NAMES as RESOLVER_SOURCE_NAMES,
    run_sources as build_resolver_sources,
)
from omnipath_build.silver.build import discover_resources

def main(argv: list[str] | None = None) -> int:
    """Run the minimal direct-to-Postgres command line interface."""

    parser = argparse.ArgumentParser(
        prog='minimal',
        description='Minimal direct-to-Postgres evidence ingest prototype.',
    )
    parser.add_argument(
        '--database-url',
        default=os.environ.get('DATABASE_URL'),
        help='PostgreSQL connection URL. Defaults to DATABASE_URL.',
    )
    parser.add_argument('--schema', default='public')

    subparsers = parser.add_subparsers(dest='command', required=True)
    init_db = subparsers.add_parser('init-db')
    init_db.add_argument('--drop-existing', action='store_true')

    subparsers.add_parser('reset-content')

    build_resolver = subparsers.add_parser('build-resolver')
    build_resolver.add_argument(
        'sources',
        nargs='+',
        choices=RESOLVER_SOURCE_NAMES,
        help='One or more resolver sources to materialize.',
    )
    build_resolver.add_argument('--output-dir', default='data')
    build_resolver.add_argument(
        '--taxonomy-id',
        dest='taxonomy_ids',
        action='append',
        default=None,
        help='Optional UniProt taxonomy filter. Can be repeated.',
    )
    build_resolver.add_argument(
        '--max-records',
        type=int,
        default=None,
        help='Optional cap for chemical source rows in smoke tests.',
    )
    build_resolver.add_argument(
        '--pubchem-url',
        default=None,
        help=(
            'Optional single PubChem SDF .gz URL/path. '
            'Defaults to all current PubChem full-SDF shards.'
        ),
    )

    resolver = subparsers.add_parser('load-resolver')
    resolver.add_argument('--mapping-dir', default='data')
    resolver.add_argument('--batch-size', type=int, default=100_000)
    resolver.add_argument(
        '--drop-existing',
        action=argparse.BooleanOptionalAction,
        default=True,
    )
    resolver.add_argument('--no-indexes', action='store_true')

    canon = subparsers.add_parser('canonicalize')
    canon.add_argument('--source')
    canon.add_argument('--dataset')
    canon.add_argument(
        '--unresolved-only',
        action='store_true',
        help='Reprocess only entities that are missing resolution or not resolved.',
    )
    canon.add_argument(
        '--skip-relations',
        action='store_true',
        help='Resolve entities only and leave relation resolution unchanged.',
    )
    canon.add_argument(
        '--ensure-schema',
        action=argparse.BooleanOptionalAction,
        default=True,
        help='Ensure minimal schema exists before canonicalization.',
    )

    derive = subparsers.add_parser('derive')
    derive.add_argument(
        '--indexes',
        action=argparse.BooleanOptionalAction,
        default=True,
        help='Create secondary indexes.',
    )
    derive.add_argument(
        '--tables',
        action=argparse.BooleanOptionalAction,
        default=True,
        help='Create and populate derived search/count tables.',
    )
    derive.add_argument(
        '--bitmaps',
        action=argparse.BooleanOptionalAction,
        default=True,
        help='Create and populate bitmap tables.',
    )
    derive.add_argument('--inputs-package', default='pypath.inputs_v2')
    derive.add_argument('--database', default='omnipath')

    ingest = subparsers.add_parser('ingest')
    ingest.add_argument(
        '--source',
        help='Optional source to run. Omit to discover all compatible sources.',
    )
    ingest.add_argument('--inputs-package', default='pypath.inputs_v2')
    ingest.add_argument('--database', default='omnipath')
    ingest.add_argument('--force-refresh', action='store_true')
    ingest.add_argument(
        '--ensure-schema',
        action=argparse.BooleanOptionalAction,
        default=True,
        help='Ensure minimal schema exists before ingest.',
    )
    ingest.add_argument(
        '--backend',
        choices=('bulk', 'simple'),
        default='bulk',
        help='Ingest backend. bulk uses COPY staging batches.',
    )
    ingest.add_argument(
        '--batch-size',
        type=int,
        default=50_000,
        help='Source rows per bulk staging flush.',
    )
    ingest.add_argument('--commit-every', type=int, default=1000)
    ingest.add_argument('--progress-every', type=int, default=1000)
    ingest.add_argument(
        '--obo-artifacts',
        action=argparse.BooleanOptionalAction,
        default=True,
        help='Write ontology datasets as OBO artifacts for the API service.',
    )
    ingest.add_argument(
        '--obo-output-dir',
        default='data/obo',
        help='Directory for OBO artifacts written from ontology datasets.',
    )

    args = parser.parse_args(argv)
    if args.command == 'build-resolver':
        summary = build_resolver_sources(
            sources=args.sources,
            output_dir=args.output_dir,
            taxonomy_ids=args.taxonomy_ids,
            max_records=args.max_records,
            pubchem_url=args.pubchem_url,
        )
        for key, value in summary.items():
            print(f'{key}: {value}', flush=True)
        return 0

    if not args.database_url:
        print('DATABASE_URL is required.', file=sys.stderr)
        return 2

    print(f'[minimal] connecting database schema={args.schema}', flush=True)
    with psycopg2.connect(args.database_url) as conn:
        print('[minimal] database connected', flush=True)
        if args.command == 'init-db':
            ensure_schema(
                conn,
                schema=args.schema,
                drop_existing=args.drop_existing,
            )
            return 0
        if args.command == 'reset-content':
            tables = reset_content_tables(conn, schema=args.schema)
            print(
                f'[reset-content] schema={args.schema} tables={len(tables)}',
                flush=True,
            )
            if tables:
                print(
                    '[reset-content] truncated=' + ','.join(tables),
                    flush=True,
                )
            return 0
        if args.command == 'load-resolver':
            stats = load_resolver_tables(
                conn,
                schema=args.schema,
                mapping_dir=args.mapping_dir,
                batch_size=args.batch_size,
                drop_existing=args.drop_existing,
                indexes=not args.no_indexes,
            )
            print(
                f'[resolver] proteins={stats.protein_rows} '
                f'chemicals={stats.chemical_rows}',
                flush=True,
            )
            return 0
        if args.command == 'canonicalize':
            if args.ensure_schema:
                ensure_schema(conn, schema=args.schema, progress=True)
            else:
                print('[minimal] schema check skipped', flush=True)
            stats = canonicalize(
                conn,
                schema=args.schema,
                source=args.source,
                dataset=args.dataset,
                unresolved_only=args.unresolved_only,
                include_relations=not args.skip_relations,
            )
            print(
                '[canonicalize] '
                f'entity_scope={stats.entity_scope} '
                f'candidate_rows={stats.candidate_rows} '
                f'entities={stats.entities} '
                f'entity_status={stats.entity_status} '
                f'relation_scope={stats.relation_scope} '
                f'relations={stats.relations} '
                f'relation_mapped={stats.relation_mapped} '
                f'relation_unmapped={stats.relation_unmapped}',
                flush=True,
            )
            return 0
        if args.command == 'derive':
            ensure_schema(conn, schema=args.schema)
            if args.indexes:
                create_secondary_indexes(conn, schema=args.schema)
                print('[derive] indexes=ready', flush=True)
            if args.tables:
                table_stats = rebuild_derived_tables(conn, schema=args.schema)
                discovered, _ = discover_resources(
                    database_name=args.database,
                    inputs_package=args.inputs_package,
                )
                resource_stats = sync_resources_table(
                    conn,
                    discovered,
                    schema=args.schema,
                )
                print(
                    '[derive] '
                    f'entity_relation_counts='
                    f'{table_stats.entity_relation_counts} '
                    f'ontology_terms={table_stats.ontology_terms} '
                    f'resources={resource_stats.resources}',
                    flush=True,
                )
            if args.bitmaps:
                bitmap_stats = rebuild_bitmap_tables(conn, schema=args.schema)
                print(
                    '[derive] '
                    f'annotation_term_entities='
                    f'{bitmap_stats.annotation_term_entities} '
                    f'annotation_term_relations='
                    f'{bitmap_stats.annotation_term_relations} '
                    f'entity_facets={bitmap_stats.entity_facets} '
                    f'relation_facets={bitmap_stats.relation_facets}',
                    flush=True,
                )
            return 0
        if args.command == 'ingest':
            if args.ensure_schema:
                ensure_schema(conn, schema=args.schema, progress=True)
            else:
                print('[minimal] schema check skipped', flush=True)
            return _handle_ingest(conn, args)

    return 0


def _selected_resource_functions(
    args: argparse.Namespace,
) -> list[object] | None:
    started = time.perf_counter()
    print(
        '[minimal] discovering input resources '
        f'package={args.inputs_package} database={args.database}',
        flush=True,
    )
    discovered, _ = discover_resources(
        database_name=args.database,
        inputs_package=args.inputs_package,
        progress=True,
    )
    dataset_count = sum(len(functions) for functions in discovered.values())
    print(
        '[minimal] discovery ready '
        f'sources={len(discovered)} resource_functions={dataset_count} '
        f'elapsed={time.perf_counter() - started:.1f}s',
        flush=True,
    )
    source_names = [args.source] if args.source else sorted(discovered)
    unknown = [source for source in source_names if source not in discovered]
    if unknown:
        print(f'Unknown source(s): {", ".join(unknown)}', file=sys.stderr)
        return None

    selected = [
        fn
        for source in source_names
        for fn in discovered[source]
        if fn.function_name != 'resource'
        and fn.output_kind in {'entity', 'ontology'}
        and (
            getattr(args, 'dataset', None) is None
            or fn.function_name == getattr(args, 'dataset')
        )
        and getattr(fn.call, '_raw_dataset', None) is not None
    ]
    if not selected:
        print('No matching entity/ontology datasets found.', file=sys.stderr)
        return None
    print(
        '[minimal] refresh plan '
        f'sources={len(set(fn.source for fn in selected))} '
        f'datasets={len(selected)}',
        flush=True,
    )
    return selected


def _handle_ingest(
    conn: psycopg2.extensions.connection,
    args: argparse.Namespace,
) -> int:
    selected = _selected_resource_functions(args)
    if selected is None:
        return 2

    selected_by_source: dict[str, list[object]] = {}
    for fn in selected:
        selected_by_source.setdefault(fn.source, []).append(fn)

    for source, functions in selected_by_source.items():
        source_started = time.perf_counter()
        dataset_names = ','.join(fn.function_name for fn in functions)
        print(
            f'[{source}] refresh start datasets={len(functions)} '
            f'names={dataset_names}',
            flush=True,
        )
        delete_started = time.perf_counter()
        print(f'[{source}] refresh deleting existing source content', flush=True)
        delete_source_content(conn, schema=args.schema, source=source)
        print(
            f'[{source}] refresh delete done '
            f'elapsed={time.perf_counter() - delete_started:.1f}s',
            flush=True,
        )
        for fn in functions:
            raw_dataset = getattr(fn.call, '_raw_dataset', None)
            if raw_dataset is None:
                continue
            dataset_started = time.perf_counter()
            print(
                f'[{fn.source}.{fn.function_name}] stream start '
                f'kind={fn.output_kind}',
                flush=True,
            )
            records = raw_dataset(force_refresh=args.force_refresh)
            if fn.output_kind == 'ontology':
                terms = _collect_ontology_terms(records)
                if args.obo_artifacts:
                    obo_path = _write_ontology_obo(
                        fn,
                        terms,
                        output_dir=Path(args.obo_output_dir),
                    )
                    print(
                        f'[{fn.source}.{fn.function_name}] obo={obo_path}',
                        flush=True,
                    )
                stats = load_ontology_terms(
                    conn,
                    terms,
                    schema=args.schema,
                    ontology_id=fn.ontology_id or fn.function_name,
                    batch_size=args.batch_size,
                    progress_every=args.progress_every,
                )
                print(
                    f'[{fn.source}.{fn.function_name}] '
                    f'ontology_terms={stats.terms} '
                    f'annotations={stats.annotations}',
                    flush=True,
                )
                print(
                    f'[{fn.source}.{fn.function_name}] stream done '
                    f'elapsed={time.perf_counter() - dataset_started:.1f}s',
                    flush=True,
                )
                continue

            ingestor = (
                BulkMinimalIngestor(conn, schema=args.schema)
                if args.backend == 'bulk'
                else MinimalIngestor(conn, schema=args.schema)
            )
            if args.backend == 'bulk':
                stats = ingestor.ingest_records(
                    records,
                    source=fn.source,
                    dataset=fn.function_name,
                    batch_size=args.batch_size,
                    progress_every=args.progress_every,
                )
            else:
                stats = ingestor.ingest_records(
                    records,
                    source=fn.source,
                    dataset=fn.function_name,
                    commit_every=args.commit_every,
                    progress_every=args.progress_every,
                )
            print(
                f'[{fn.source}.{fn.function_name}] '
                f'rows={stats.source_rows} '
                f'entities={stats.entity_evidence} '
                f'relations={stats.relation_evidence} '
                f'identifiers={stats.identifiers} '
                f'annotations={stats.annotations}',
                flush=True,
            )
            print(
                f'[{fn.source}.{fn.function_name}] stream done '
                f'elapsed={time.perf_counter() - dataset_started:.1f}s',
                flush=True,
            )
        print(
            f'[{source}] refresh done '
            f'elapsed={time.perf_counter() - source_started:.1f}s',
            flush=True,
        )
    return 0


def _collect_ontology_terms(records: object) -> list[OntologyTerm]:
    terms: list[OntologyTerm] = []
    for record in records:
        value = getattr(record, 'record', record)
        if isinstance(value, OntologyTerm) and value.id:
            terms.append(value)
    return terms


def _write_ontology_obo(
    fn: object,
    terms: list[OntologyTerm],
    *,
    output_dir: Path,
) -> Path:
    extension = (getattr(fn, 'file_extension', None) or 'obo').lstrip('.')
    file_stem = getattr(fn, 'file_stem', None) or getattr(fn, 'function_name')
    document = getattr(fn, 'document')
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f'{file_stem}.{extension}'
    output_path.write_text(format_obo(document, terms), encoding='utf-8')
    return output_path


if __name__ == '__main__':
    raise SystemExit(main())
