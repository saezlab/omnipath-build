"""Administrative CLI for the DuckDB/PostgreSQL build pipeline.

Source loading is handled by ``omnipath_build.duckdb_direct_pipeline``. This
module keeps the supporting database, resolver-materialization, source-deletion,
and derived-table maintenance commands.
"""

from __future__ import annotations

import os
import sys
import argparse

import psycopg2

from omnipath_build.db import (
    ensure_schema,
    reset_content_tables,
    sync_resources_table,
    delete_source_content,
    rebuild_bitmap_tables,
    rebuild_derived_tables,
    ensure_deferred_indexes,
    create_secondary_indexes,
    ensure_content_primary_keys,
    drop_deferred_content_indexes,
)
from omnipath_build.resources import discover_resources
from omnipath_build.resolver.mapping_tables import (
    SOURCE_NAMES as RESOLVER_SOURCE_NAMES,
    run_sources as build_resolver_sources,
)

def main(argv: list[str] | None = None) -> int:
    """Run the omnipath_build administrative command line interface."""

    parser = argparse.ArgumentParser(
        prog='omnipath_build',
        description='DuckDB/PostgreSQL pipeline administration.',
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
    init_db.add_argument(
        '--indexes',
        action=argparse.BooleanOptionalAction,
        default=True,
        help='Create deferrable indexes during schema setup.',
    )

    reset_content = subparsers.add_parser('reset-content')
    reset_content.add_argument(
        '--drop-indexes',
        action='store_true',
        help='Drop secondary content indexes after truncating for faster reload.',
    )

    drop_source = subparsers.add_parser('drop-source')
    drop_source.add_argument('--source', required=True)
    drop_source.add_argument(
        '--row-delete',
        action='store_true',
        help='Force row deletion instead of dropping source partitions.',
    )

    build_resolver = subparsers.add_parser('build-resolver')
    build_resolver.add_argument(
        'sources',
        nargs='*',
        choices=RESOLVER_SOURCE_NAMES,
        help='Resolver sources to materialize. Defaults to all sources.',
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
    build_resolver.add_argument(
        '--pubchem-shards',
        type=int,
        default=None,
        help='Optional number of discovered PubChem SDF shards to stream.',
    )
    build_resolver.add_argument(
        '--jobs',
        type=int,
        default=1,
        help='Number of parallel jobs for resolver sources that support it.',
    )
    build_resolver.add_argument(
        '--skip-existing',
        action=argparse.BooleanOptionalAction,
        default=True,
        help='Skip resolver sources already present in the output directory.',
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

    args = parser.parse_args(argv)
    if args.command == 'build-resolver':
        summary = build_resolver_sources(
            sources=args.sources or RESOLVER_SOURCE_NAMES,
            output_dir=args.output_dir,
            taxonomy_ids=args.taxonomy_ids,
            max_records=args.max_records,
            pubchem_url=args.pubchem_url,
            pubchem_shards=args.pubchem_shards,
            jobs=args.jobs,
            skip_existing=args.skip_existing,
        )
        for key, value in summary.items():
            print(f'{key}: {value}', flush=True)
        return 0

    if not args.database_url:
        print('DATABASE_URL is required.', file=sys.stderr)
        return 2

    print(
        f'[omnipath_build] connecting database schema={args.schema}', flush=True
    )
    with psycopg2.connect(args.database_url) as conn:
        print('[omnipath_build] database connected', flush=True)
        if args.command == 'init-db':
            ensure_schema(
                conn,
                schema=args.schema,
                drop_existing=args.drop_existing,
                indexes=args.indexes,
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
            if args.drop_indexes:
                dropped = drop_deferred_content_indexes(
                    conn,
                    schema=args.schema,
                    progress=True,
                )
                print(
                    f'[reset-content] dropped_indexes={len(dropped)}',
                    flush=True,
                )
            return 0
        if args.command == 'drop-source':
            stats = delete_source_content(
                conn,
                schema=args.schema,
                source=args.source,
                drop_partitions=not args.row_delete,
            )
            print(
                '[drop-source] '
                f'source={stats.source} '
                f'source_id={stats.source_id} '
                f'strategy={stats.strategy} '
                f'partitions_dropped={stats.partitions_dropped} '
                f'affected_relations={stats.affected_relations} '
                f'affected_entities={stats.affected_entities} '
                f'affected_identifiers={stats.affected_identifiers} '
                f'deleted_relations={stats.deleted_relations} '
                f'deleted_entities={stats.deleted_entities} '
                f'deleted_identifiers={stats.deleted_identifiers} '
                f'deleted_annotations={stats.deleted_annotations} '
                f'refreshed_relation_counts={stats.refreshed_relation_counts}',
                flush=True,
            )
            return 0
        if args.command == 'derive':
            ensure_content_primary_keys(conn, schema=args.schema, progress=True)
            ensure_schema(conn, schema=args.schema, indexes=False)
            if args.indexes:
                ensure_deferred_indexes(
                    conn,
                    schema=args.schema,
                    progress=True,
                )
                create_secondary_indexes(conn, schema=args.schema)
                print('[derive] indexes=ready', flush=True)
            if args.tables:
                table_stats = rebuild_derived_tables(
                    conn,
                    schema=args.schema,
                )
                discovered, _ = discover_resources(
                    database_name=args.database,
                    inputs_package=args.inputs_package,
                )
                bitmap_stats = None
                if args.bitmaps:
                    bitmap_stats = rebuild_bitmap_tables(
                        conn,
                        schema=args.schema,
                    )
                resource_stats = sync_resources_table(
                    conn,
                    discovered,
                    schema=args.schema,
                    prefer_bitmaps=args.bitmaps,
                )
                print(
                    '[derive] '
                    f'entity_identifier_lookup='
                    f'{table_stats.entity_identifier_lookup} '
                    f'entity_relation_counts='
                    f'{table_stats.entity_relation_counts} '
                    f'ontology_terms={table_stats.ontology_terms} '
                    f'resources={resource_stats.resources}',
                    flush=True,
                )
            elif args.bitmaps:
                bitmap_stats = rebuild_bitmap_tables(conn, schema=args.schema)
            else:
                bitmap_stats = None
            if bitmap_stats is not None:
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

    return 0


if __name__ == '__main__':
    raise SystemExit(main())
