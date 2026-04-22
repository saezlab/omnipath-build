from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from omnipath_build.gold.build_entities import build_entities
from omnipath_build.gold.build_relations import build_relations


def _run_combine(
    output_root: Path,
    combined_output_dir: Path | None,
    results: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Run the combine step if any source succeeded."""
    successful = [r for r in results if 'error' not in r]
    if not successful:
        print('COMBINE: no successful sources, skipping combine')
        return None

    print('\n========== COMBINE ==========')
    try:
        from omnipath_build.gold.new_combine import build_combined_parquets

        combine_summary = build_combined_parquets(
            gold_root=output_root,
            output_dir=combined_output_dir or (output_root.parent / 'combined_new'),
        )
        print(f'Combine complete: {combine_summary["row_counts"]}')
        return combine_summary
    except Exception as e:
        print(f'COMBINE ERROR: {e}', file=sys.stderr)
        import traceback

        traceback.print_exc()
        return None


def _run_postgres_load(
    combined_output_dir: Path,
    postgres_uri: str | None,
    postgres_schema: str,
    postgres_drop_existing: bool,
) -> None:
    """Run the postgres load step if URI is provided."""
    if not postgres_uri:
        return

    print('\n========== POSTGRES LOAD ==========')
    try:
        from omnipath_build.postgres_new_combined import load_combined_schema_to_postgres

        load_combined_schema_to_postgres(
            output_dir=combined_output_dir,
            postgres_uri=postgres_uri,
            schema=postgres_schema,
            drop_existing=postgres_drop_existing,
        )
        print('Postgres load complete')
    except Exception as e:
        print(f'POSTGRES LOAD ERROR: {e}', file=sys.stderr)
        import traceback

        traceback.print_exc()


def resolve_silver_version(silver_source_dir: Path) -> Path:
    """Read the latest version file and return the actual silver data directory."""
    latest_file = silver_source_dir / 'latest'
    if latest_file.exists():
        latest_data = json.loads(latest_file.read_text())
        version = latest_data.get('version', '1')
        version_dir = silver_source_dir / str(version)
        if version_dir.exists():
            return version_dir
    # Fallback: look for numeric directories
    for subdir in sorted(silver_source_dir.iterdir()):
        if subdir.is_dir() and subdir.name.isdigit():
            return subdir
    raise FileNotFoundError(f"No silver data found in {silver_source_dir}")


def run_b3_pipeline(
    *,
    source: str,
    silver_dir: Path,
    mapping_dir: Path,
    output_dir: Path,
) -> dict[str, Any]:
    """Run the full B3 pipeline (build_entities + build_relations) for a single source."""
    output_dir.mkdir(parents=True, exist_ok=True)
    entities_dir = output_dir / 'entities'
    relations_dir = output_dir / 'relations'

    print(f'[{source}] Step 1/2: building entities (extract + canonicalize + dedup)')
    entity_summary = build_entities(
        silver_dir=silver_dir,
        mapping_dir=mapping_dir,
        output_dir=entities_dir,
        source_name=source,
    )

    entity_map_path = entities_dir / 'entity_map.parquet'
    if not entity_map_path.exists():
        print(f'[{source}] WARNING: no entity_map.parquet produced; skipping relations')
        return {
            'source': source,
            'output_dir': str(output_dir),
            'entity_summary': entity_summary,
            'relation_summary': None,
        }

    print(f'[{source}] Step 2/2: building relations')
    relation_summary = build_relations(
        silver_dir=silver_dir,
        entity_map_path=entity_map_path,
        output_dir=relations_dir,
        source_name=source,
    )

    return {
        'source': source,
        'output_dir': str(output_dir),
        'entity_summary': entity_summary,
        'relation_summary': relation_summary,
    }


def run_all_sources(
    silver_root: Path,
    mapping_dir: Path,
    output_root: Path,
    sources: list[str] | None = None,
    combine: bool = True,
    combined_output_dir: Path | None = None,
    postgres_uri: str | None = None,
    postgres_schema: str = 'public',
    postgres_drop_existing: bool = False,
) -> list[dict[str, Any]]:
    """Run the B3 pipeline on all (or selected) sources under silver_root."""
    if sources is None:
        sources = sorted(
            p.name for p in silver_root.iterdir()
            if p.is_dir() and (p / 'latest').exists()
        )

    results: list[dict[str, Any]] = []
    for source in sources:
        silver_source_dir = silver_root / source
        try:
            silver_data_dir = resolve_silver_version(silver_source_dir)
        except FileNotFoundError as e:
            print(f'[{source}] SKIP: {e}')
            continue

        # Skip sources with no actual data parquet files
        parquet_files = [
            p for p in silver_data_dir.glob('*.parquet')
            if p.name != 'resource.parquet'
        ]
        if not parquet_files:
            print(f'[{source}] SKIP: no data parquet files in {silver_data_dir}')
            continue

        output_dir = output_root / source
        print(f'\n========== {source} ==========')
        try:
            result = run_b3_pipeline(
                source=source,
                silver_dir=silver_data_dir,
                mapping_dir=mapping_dir,
                output_dir=output_dir,
            )
            results.append(result)
            print(f'[{source}] pipeline complete')
            print(f'  entities: {result["entity_summary"]}')
            if result['relation_summary']:
                print(f'  relations: {result["relation_summary"]}')
        except Exception as e:
            print(f'[{source}] ERROR: {e}', file=sys.stderr)
            import traceback
            traceback.print_exc()
            results.append({
                'source': source,
                'error': str(e),
            })

    if combine:
        _run_combine(output_root, combined_output_dir, results)
        final_combined_dir = combined_output_dir or (output_root.parent / 'combined_new')
        _run_postgres_load(final_combined_dir, postgres_uri, postgres_schema, postgres_drop_existing)

    return results


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description='Run the B3 entity-first pipeline for silver sources.'
    )
    parser.add_argument(
        '--silver-root',
        type=Path,
        default=Path('data_v2/silver'),
        help='Root directory containing per-source silver directories.',
    )
    parser.add_argument(
        '--mapping-dir',
        type=Path,
        default=Path('id_resolver/data'),
        help='Resolver mapping directory.',
    )
    parser.add_argument(
        '--output-root',
        type=Path,
        required=True,
        help='Root directory for pipeline output (e.g. data_v2/gold_new).',
    )
    parser.add_argument(
        '--sources',
        nargs='+',
        default=None,
        help='Specific source names to process. Defaults to all sources.',
    )
    parser.add_argument(
        '--combine',
        action=argparse.BooleanOptionalAction,
        default=True,
        help='Run the combine step after all sources (default: on).',
    )
    parser.add_argument(
        '--combined-output-dir',
        type=Path,
        default=None,
        help='Directory to write combined artifacts (default: <output-root>/../combined_new).',
    )
    parser.add_argument(
        '--postgres-uri',
        type=str,
        default=None,
        help='Postgres URI to load combined artifacts into (e.g. postgresql://user:pass@host/db).',
    )
    parser.add_argument(
        '--postgres-schema',
        type=str,
        default='public',
        help='Postgres schema to load into (default: public).',
    )
    parser.add_argument(
        '--postgres-drop-existing',
        action='store_true',
        default=False,
        help='Drop existing tables before loading into Postgres.',
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    results = run_all_sources(
        silver_root=args.silver_root,
        mapping_dir=args.mapping_dir,
        output_root=args.output_root,
        sources=args.sources,
        combine=args.combine,
        combined_output_dir=args.combined_output_dir,
        postgres_uri=args.postgres_uri,
        postgres_schema=args.postgres_schema,
        postgres_drop_existing=args.postgres_drop_existing,
    )

    print('\n========== SUMMARY ==========')
    total_entities = 0
    total_relations = 0
    for result in results:
        source = result['source']
        if 'error' in result:
            print(f'  {source}: ERROR - {result["error"]}')
            continue
        entity_count = result['entity_summary'].get('entity_count', 0)
        relation_count = result['relation_summary'].get('relation_count', 0) if result['relation_summary'] else 0
        total_entities += entity_count
        total_relations += relation_count
        print(f'  {source}: {entity_count} entities, {relation_count} relations')

    print(f'\nTotal: {total_entities} entities, {total_relations} relations across {len(results)} sources')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
