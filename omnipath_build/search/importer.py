#!/usr/bin/env python3
"""
Utility to load search datasets (entities and interactions) into Meilisearch
using the upstream meilisearch-importer CLI directly in Parquet mode.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import Iterable

from omnipath_build.search.meilisearch import MeilisearchSettings


def run_importer(
    importer_dir: Path,
    dataset_path: Path,
    meili_url: str,
    index_name: str,
    primary_key: str,
    api_key: str | None,
    batch_size: str | None,
    file_format: str,
) -> None:
    """Execute the meilisearch importer with the generated JSON file."""
    dataset_arg = str(dataset_path.resolve())

    # Find cargo binary (try default location first, then PATH)
    cargo_bin = Path.home() / '.cargo' / 'bin' / 'cargo'
    if not cargo_bin.exists():
        cargo_bin = Path('cargo')  # Fall back to PATH lookup

    cmd: list[str] = [
        str(cargo_bin),
        'run',
        '--release',
        '--',
        '--url',
        meili_url,
        '--index',
        index_name,
        '--primary-key',
        primary_key,
        '--files',
        dataset_arg,
        '--format',
        file_format,
    ]

    if api_key:
        cmd.extend(['--api-key', api_key])

    if batch_size:
        cmd.extend(['--batch-size', batch_size])

    subprocess.run(cmd, cwd=str(importer_dir), check=True)


def apply_settings(
    meili_url: str,
    index_name: str,
    api_key: str | None,
    settings: dict,
) -> None:
    """Apply Meilisearch index settings."""
    payload = json.dumps(settings).encode('utf-8')
    req = urllib.request.Request(
        url=f'{meili_url}/indexes/{index_name}/settings',
        data=payload,
        method='PATCH',
        headers={'Content-Type': 'application/json'},
    )
    if api_key:
        req.add_header('Authorization', f'Bearer {api_key}')
    with urllib.request.urlopen(req) as resp:  # noqa: S310
        if resp.status >= 400:
            raise RuntimeError(
                f'Failed to update settings: {resp.status} {resp.read()}'
            )


def parse_args(argv: Iterable[str]) -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        '--dataset',
        default='entities',
        choices=('entities', 'interactions', 'associations', 'both', 'all'),
        help='Which dataset(s) to import: entities, interactions, associations, both (entities+interactions), or all.',
    )
    parser.add_argument(
        '--entities-parquet-path',
        default='omnipath_build/data/gold/search_entities.parquet',
        type=Path,
        help='Path to search_entities.parquet.',
    )
    parser.add_argument(
        '--interactions-parquet-path',
        default='omnipath_build/data/gold/search_interactions.parquet',
        type=Path,
        help='Path to search_interactions.parquet.',
    )
    parser.add_argument(
        '--associations-parquet-path',
        default='omnipath_build/data/gold/search_associations.parquet',
        type=Path,
        help='Path to search_associations.parquet.',
    )
    parser.add_argument(
        '--importer-path',
        default=Path(__file__).parent.parent / 'meilisearch-importer',
        type=Path,
        help='Location of the meilisearch-importer checkout.',
    )
    parser.add_argument(
        '--meili-url',
        default='http://localhost:7700',
        help='URL of the running Meilisearch instance.',
    )
    parser.add_argument(
        '--entities-index',
        default='search_entities',
        help='Name of the entities Meilisearch index.',
    )
    parser.add_argument(
        '--interactions-index',
        default='search_interactions',
        help='Name of the interactions Meilisearch index.',
    )
    parser.add_argument(
        '--associations-index',
        default='search_associations',
        help='Name of the associations Meilisearch index.',
    )
    parser.add_argument(
        '--api-key',
        default=None,
        help='Optional Meilisearch API key (overrides MEILISEARCH_API_KEY).',
    )
    parser.add_argument(
        '--batch-size',
        default='100MB',
        help="Batch size forwarded to the importer CLI (e.g. '50MB').",
    )
    parser.add_argument(
        '--format',
        default='parquet',
        choices=('parquet', 'ndjson', 'json', 'csv'),
        help='Input format passed directly to meilisearch-importer.',
    )
    parser.add_argument(
        '--skip-settings',
        action='store_true',
        help='Skip updating Meilisearch settings after the import.',
    )

    return parser.parse_args(argv)


def import_dataset(
    dataset_name: str,
    parquet_path: Path,
    index_name: str,
    primary_key: str,
    settings: dict,
    importer_path: Path,
    meili_url: str,
    api_key: str | None,
    batch_size: str,
    file_format: str,
    skip_settings: bool,
) -> None:
    """Import a single dataset into Meilisearch."""
    if not parquet_path.exists():
        raise SystemExit(f'Missing {dataset_name} Parquet input file: {parquet_path}')

    parquet_path = parquet_path.resolve()

    print(f"\n{'=' * 80}")
    print(f'Importing {dataset_name}: {parquet_path} -> {index_name}')
    print('=' * 80)

    run_importer(
        importer_dir=importer_path,
        dataset_path=parquet_path,
        meili_url=meili_url,
        index_name=index_name,
        primary_key=primary_key,
        api_key=api_key,
        batch_size=batch_size,
        file_format=file_format,
    )

    if not skip_settings:
        print(f'Applying {dataset_name} index settings ...')
        apply_settings(
            meili_url=meili_url,
            index_name=index_name,
            api_key=api_key,
            settings=settings,
        )

    print(f'{dataset_name} import completed successfully.')


def main(argv: Iterable[str]) -> None:
    """Main entry point."""
    args = parse_args(argv)
    importer_path: Path = Path(args.importer_path)
    api_key = args.api_key or os.environ.get('MEILISEARCH_API_KEY')

    if not importer_path.exists():
        raise SystemExit(f'Invalid importer path: {importer_path}')

    datasets_to_import = []

    if args.dataset in ('entities', 'both', 'all'):
        datasets_to_import.append(
            {
                'name': 'entities',
                'parquet_path': args.entities_parquet_path,
                'index_name': args.entities_index,
                'primary_key': 'entity_id',
                'settings': MeilisearchSettings.ENTITIES_SETTINGS,
            }
        )

    if args.dataset in ('interactions', 'both', 'all'):
        datasets_to_import.append(
            {
                'name': 'interactions',
                'parquet_path': args.interactions_parquet_path,
                'index_name': args.interactions_index,
                'primary_key': 'interaction_key',
                'settings': MeilisearchSettings.INTERACTIONS_SETTINGS,
            }
        )

    if args.dataset in ('associations', 'all'):
        datasets_to_import.append(
            {
                'name': 'associations',
                'parquet_path': args.associations_parquet_path,
                'index_name': args.associations_index,
                'primary_key': 'association_key',
                'settings': MeilisearchSettings.ASSOCIATIONS_SETTINGS,
            }
        )

    for dataset in datasets_to_import:
        import_dataset(
            dataset_name=dataset['name'],
            parquet_path=dataset['parquet_path'],
            index_name=dataset['index_name'],
            primary_key=dataset['primary_key'],
            settings=dataset['settings'],
            importer_path=importer_path,
            meili_url=args.meili_url,
            api_key=api_key,
            batch_size=args.batch_size,
            file_format=args.format,
            skip_settings=args.skip_settings,
        )

    print(f"\n{'=' * 80}")
    print('All imports completed successfully!')
    print('=' * 80)


if __name__ == '__main__':
    main(sys.argv[1:])
