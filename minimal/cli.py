from __future__ import annotations

import os
import sys
import argparse

import psycopg2

from minimal.db import (
    ensure_schema,
    rebuild_bitmap_tables,
    rebuild_derived_tables,
    create_secondary_indexes,
)
from minimal.ingest import (
    MinimalIngestor,
    BulkMinimalIngestor,
    sync_source_snapshot,
)
from minimal.loaders import load_ontology_terms, load_resolver_tables
from minimal.canonicalize import canonicalize
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
    parser.add_argument('--schema', default='minimal')

    subparsers = parser.add_subparsers(dest='command', required=True)
    init_db = subparsers.add_parser('init-db')
    init_db.add_argument('--drop-existing', action='store_true')

    resolver = subparsers.add_parser('load-resolver')
    resolver.add_argument('--mapping-dir', default='id_resolver/data')
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

    ingest = subparsers.add_parser('ingest')
    ingest.add_argument('--source', required=True)
    ingest.add_argument('--dataset')
    ingest.add_argument('--inputs-package', default='pypath.inputs_v2')
    ingest.add_argument('--database', default='omnipath')
    ingest.add_argument('--raw-records-root')
    ingest.add_argument('--force-refresh', action='store_true')
    ingest.add_argument(
        '--full-current',
        action='store_true',
        help=(
            'Ingest all current source rows from the snapshot instead of only '
            'the snapshot delta. Use this explicitly when bootstrapping '
            'minimal tables from an existing raw snapshot.'
        ),
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
        default=5000,
        help='Source rows per bulk staging flush.',
    )
    ingest.add_argument('--commit-every', type=int, default=1000)
    ingest.add_argument('--progress-every', type=int, default=1000)

    args = parser.parse_args(argv)
    if not args.database_url:
        print('DATABASE_URL is required.', file=sys.stderr)
        return 2

    with psycopg2.connect(args.database_url) as conn:
        if args.command == 'init-db':
            ensure_schema(
                conn,
                schema=args.schema,
                drop_existing=args.drop_existing,
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
            ensure_schema(conn, schema=args.schema)
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
                print(
                    '[derive] '
                    f'entity_relation_counts='
                    f'{table_stats.entity_relation_counts} '
                    f'ontology_terms={table_stats.ontology_terms}',
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
            ensure_schema(conn, schema=args.schema)
            return _handle_ingest(conn, args)

    return 0


def _handle_ingest(
    conn: psycopg2.extensions.connection,
    args: argparse.Namespace,
) -> int:
    discovered, _ = discover_resources(
        database_name=args.database,
        inputs_package=args.inputs_package,
    )
    if args.source not in discovered:
        print(f'Unknown source: {args.source}', file=sys.stderr)
        return 2

    selected = [
        fn
        for fn in discovered[args.source]
        if fn.function_name != 'resource'
        and fn.output_kind in {'entity', 'ontology'}
        and (args.dataset is None or fn.function_name == args.dataset)
    ]
    if not selected:
        print('No matching entity/ontology datasets found.', file=sys.stderr)
        return 2

    for fn in selected:
        raw_dataset = getattr(fn.call, '_raw_dataset', None)
        if raw_dataset is None:
            continue
        snapshot = raw_dataset.preparse(
            source=fn.source,
            dataset=fn.function_name,
            raw_records_root=args.raw_records_root,
            force_refresh=args.force_refresh,
        )
        if fn.output_kind == 'entity':
            sync_stats = sync_source_snapshot(
                conn,
                schema=args.schema,
                source=fn.source,
                dataset=fn.function_name,
                snapshot_id=snapshot.snapshot_id,
                records_path=snapshot.records_path,
                delta_path=snapshot.delta_path,
            )
            print(
                f'[{fn.source}.{fn.function_name}] '
                f'current_rows={sync_stats.current_rows} '
                f'removed_rows={sync_stats.removed_rows}',
                flush=True,
            )
        records = raw_dataset(
            source=fn.source,
            dataset=fn.function_name,
            raw_records_root=args.raw_records_root,
            use_preparse=True,
            raw_snapshot=snapshot,
            changed_only=fn.output_kind == 'entity' and not args.full_current,
        )
        if fn.output_kind == 'ontology':
            stats = load_ontology_terms(
                conn,
                records,
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
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
