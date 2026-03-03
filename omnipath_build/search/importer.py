#!/usr/bin/env python3
"""Utility to load search datasets into Meilisearch.

Supports full import and incremental updates.
Incremental mode computes a stable content hash per document and performs:
- deletes = old_keys - new_keys
- upserts = key not in old OR content_hash changed
"""

from __future__ import annotations

import os
import sys
import json
import time
from pathlib import Path
import argparse
import tempfile
import subprocess
import urllib.error
import urllib.request
from collections.abc import Iterable

import polars as pl

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
    """Execute the meilisearch importer CLI."""
    dataset_arg = str(dataset_path.resolve())

    cargo_bin = Path.home() / '.cargo' / 'bin' / 'cargo'
    if not cargo_bin.exists():
        cargo_bin = Path('cargo')

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


def _request_json(
    method: str,
    url: str,
    api_key: str | None,
    payload: dict | list | None = None,
) -> dict:
    data = None
    headers = {'Content-Type': 'application/json'}
    if api_key:
        headers['Authorization'] = f'Bearer {api_key}'
    if payload is not None:
        data = json.dumps(payload).encode('utf-8')

    req = urllib.request.Request(url=url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req) as resp:
            raw = resp.read()
            if not raw:
                return {}
            return json.loads(raw.decode('utf-8'))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode('utf-8', errors='replace')
        raise RuntimeError(f'Meilisearch request failed: {method} {url} -> {exc.code}: {body}') from exc


def _index_exists(meili_url: str, index_name: str, api_key: str | None) -> bool:
    url = f'{meili_url}/indexes/{index_name}'
    req = urllib.request.Request(url=url, method='GET')
    if api_key:
        req.add_header('Authorization', f'Bearer {api_key}')
    try:
        with urllib.request.urlopen(req):
            return True
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return False
        body = exc.read().decode('utf-8', errors='replace')
        raise RuntimeError(f'Meilisearch request failed: GET {url} -> {exc.code}: {body}') from exc


def _wait_for_task(
    meili_url: str,
    task_uid: int,
    api_key: str | None,
    timeout_seconds: int = 3600,
    poll_interval_seconds: float = 0.5,
) -> None:
    deadline = time.time() + timeout_seconds
    task_url = f'{meili_url}/tasks/{task_uid}'

    while time.time() < deadline:
        task = _request_json('GET', task_url, api_key)
        status = task.get('status')
        if status == 'succeeded':
            return
        if status in {'failed', 'canceled'}:
            err = task.get('error')
            raise RuntimeError(f'Meilisearch task {task_uid} {status}: {err}')
        time.sleep(poll_interval_seconds)

    raise TimeoutError(f'Timed out waiting for Meilisearch task {task_uid}')


def _fetch_existing_hashes(
    meili_url: str,
    index_name: str,
    primary_key: str,
    api_key: str | None,
    page_size: int,
) -> pl.DataFrame:
    """Fetch existing {primary_key, content_hash} pairs from Meilisearch."""
    if not _index_exists(meili_url, index_name, api_key):
        return pl.DataFrame(schema={primary_key: pl.Utf8, 'content_hash': pl.Utf8})

    rows: list[dict[str, str | None]] = []
    offset = 0

    while True:
        payload = {
            'fields': [primary_key, 'content_hash'],
            'limit': page_size,
            'offset': offset,
        }
        data = _request_json('POST', f'{meili_url}/indexes/{index_name}/documents/fetch', api_key, payload)
        batch = data.get('results', [])

        if not batch:
            break

        for doc in batch:
            key = doc.get(primary_key)
            if key is None:
                continue
            rows.append({primary_key: str(key), 'content_hash': doc.get('content_hash')})

        if len(batch) < page_size:
            break
        offset += page_size

    if not rows:
        return pl.DataFrame(schema={primary_key: pl.Utf8, 'content_hash': pl.Utf8})

    return (
        pl.DataFrame(rows)
        .with_columns(pl.col(primary_key).cast(pl.Utf8), pl.col('content_hash').cast(pl.Utf8))
        .unique(subset=[primary_key], keep='last')
    )


def _delete_documents(
    meili_url: str,
    index_name: str,
    ids: list[str],
    api_key: str | None,
    delete_batch_size: int,
) -> None:
    if not ids:
        return

    task_uids: list[int] = []
    for start in range(0, len(ids), delete_batch_size):
        chunk = ids[start:start + delete_batch_size]
        task = _request_json(
            'POST',
            f'{meili_url}/indexes/{index_name}/documents/delete-batch',
            api_key,
            chunk,
        )
        task_uid = task.get('taskUid')
        if task_uid is not None:
            task_uids.append(int(task_uid))

    for task_uid in task_uids:
        _wait_for_task(meili_url, task_uid, api_key)


def _content_hash_expr(semantic_columns: list[str]) -> pl.Expr:
    """Fast, deterministic row hash over semantic columns."""
    return pl.struct([pl.col(c) for c in semantic_columns]).hash(seed=0).cast(pl.UInt64).cast(pl.Utf8).alias('content_hash')


def _pick_primary_key(parquet_path: Path, candidates: list[str]) -> str:
    schema = pl.scan_parquet(parquet_path).collect_schema()
    names = set(schema.names())
    for candidate in candidates:
        if candidate in names:
            return candidate
    raise SystemExit(
        f'None of primary key candidates {candidates} found in {parquet_path}. '\
        f'Available columns: {sorted(names)}'
    )


def _build_incremental_payload(
    parquet_path: Path,
    primary_key: str,
    old_hashes: pl.DataFrame,
    ignored_hash_columns: set[str],
    output_upserts_path: Path,
) -> tuple[list[str], int]:
    """Compute delete keys and write changed/new docs as NDJSON.

    Returns (delete_ids, upsert_count).
    """
    dataset_scan = pl.scan_parquet(parquet_path)
    schema = dataset_scan.collect_schema()
    columns = schema.names()

    semantic_columns = [
        c for c in columns
        if c not in ignored_hash_columns and c not in {primary_key, 'content_hash'}
    ]
    if not semantic_columns:
        raise SystemExit(f'No semantic columns left for hashing in {parquet_path}')

    new_hashes_lf = dataset_scan.select([
        pl.col(primary_key).cast(pl.Utf8).alias(primary_key),
        _content_hash_expr(semantic_columns),
    ])

    old_hashes_lf = old_hashes.lazy().select([
        pl.col(primary_key).cast(pl.Utf8).alias(primary_key),
        pl.col('content_hash').cast(pl.Utf8).alias('old_content_hash'),
    ])

    delete_ids = (
        old_hashes_lf
        .join(new_hashes_lf.select(primary_key), on=primary_key, how='anti')
        .collect()
        .get_column(primary_key)
        .to_list()
    )

    upsert_keys_df = (
        new_hashes_lf
        .join(old_hashes_lf, on=primary_key, how='left')
        .filter(
            pl.col('old_content_hash').is_null()
            | (pl.col('content_hash') != pl.col('old_content_hash'))
        )
        .select(primary_key)
        .collect()
    )

    upsert_count = upsert_keys_df.height
    if upsert_count > 0:
        (
            dataset_scan
            .with_columns(_content_hash_expr(semantic_columns))
            .join(upsert_keys_df.lazy(), on=primary_key, how='semi')
            .sink_ndjson(output_upserts_path)
        )

    return delete_ids, upsert_count


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
    with urllib.request.urlopen(req) as resp:
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
        choices=('entities', 'interactions', 'associations', 'sources', 'both', 'all'),
        help='Which dataset(s) to import: entities, interactions, associations, sources, both (entities+interactions), or all.',
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
        '--sources-parquet-path',
        default='omnipath_build/data/gold/search_sources.parquet',
        type=Path,
        help='Path to search_sources.parquet.',
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
        '--sources-index',
        default='search_sources',
        help='Name of the sources Meilisearch index.',
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
        help='Input format passed directly to meilisearch-importer for full reindex mode.',
    )
    parser.add_argument(
        '--full-reindex',
        action='store_true',
        help='Disable incremental mode and import the full input file.',
    )
    parser.add_argument(
        '--fetch-page-size',
        default=10000,
        type=int,
        help='Page size for fetching existing {primary_key,content_hash} from Meilisearch.',
    )
    parser.add_argument(
        '--delete-batch-size',
        default=10000,
        type=int,
        help='Batch size for Meilisearch delete-batch requests.',
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
    primary_key_candidates: list[str],
    hash_ignore_columns: set[str],
    settings: dict,
    importer_path: Path,
    meili_url: str,
    api_key: str | None,
    batch_size: str,
    file_format: str,
    full_reindex: bool,
    fetch_page_size: int,
    delete_batch_size: int,
    skip_settings: bool,
) -> None:
    """Import a single dataset into Meilisearch."""
    if not parquet_path.exists():
        raise SystemExit(f'Missing {dataset_name} Parquet input file: {parquet_path}')

    parquet_path = parquet_path.resolve()
    primary_key = _pick_primary_key(parquet_path, primary_key_candidates)

    print(f"\n{'=' * 80}")
    print(f'Importing {dataset_name}: {parquet_path} -> {index_name} (pk={primary_key})')
    print('=' * 80)

    if full_reindex:
        print('Mode: full reindex')
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
    else:
        print('Mode: incremental (hash diff)')
        old_hashes = _fetch_existing_hashes(
            meili_url=meili_url,
            index_name=index_name,
            primary_key=primary_key,
            api_key=api_key,
            page_size=fetch_page_size,
        )

        with tempfile.TemporaryDirectory(prefix=f'meili-{dataset_name}-') as tmpdir:
            upserts_path = Path(tmpdir) / f'{dataset_name}_upserts.ndjson'
            delete_ids, upsert_count = _build_incremental_payload(
                parquet_path=parquet_path,
                primary_key=primary_key,
                old_hashes=old_hashes,
                ignored_hash_columns=hash_ignore_columns,
                output_upserts_path=upserts_path,
            )

            print(
                f'Incremental summary for {dataset_name}: '
                f'deletes={len(delete_ids)} upserts={upsert_count} '
                f'(existing={old_hashes.height})'
            )

            if delete_ids:
                print(f'Deleting {len(delete_ids)} stale documents...')
                _delete_documents(
                    meili_url=meili_url,
                    index_name=index_name,
                    ids=delete_ids,
                    api_key=api_key,
                    delete_batch_size=delete_batch_size,
                )

            if upsert_count > 0:
                print(f'Upserting {upsert_count} changed/new documents...')
                run_importer(
                    importer_dir=importer_path,
                    dataset_path=upserts_path,
                    meili_url=meili_url,
                    index_name=index_name,
                    primary_key=primary_key,
                    api_key=api_key,
                    batch_size=batch_size,
                    file_format='ndjson',
                )
            else:
                print('No upserts required.')

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
                'primary_key_candidates': ['entity_key', 'entity_id'],
                'hash_ignore_columns': set(),
                'settings': MeilisearchSettings.ENTITIES_SETTINGS,
            }
        )

    if args.dataset in ('interactions', 'both', 'all'):
        datasets_to_import.append(
            {
                'name': 'interactions',
                'parquet_path': args.interactions_parquet_path,
                'index_name': args.interactions_index,
                'primary_key_candidates': ['interaction_key'],
                # interaction_id is row-index based and not semantic
                'hash_ignore_columns': {'interaction_id'},
                'settings': MeilisearchSettings.INTERACTIONS_SETTINGS,
            }
        )

    if args.dataset in ('associations', 'all'):
        datasets_to_import.append(
            {
                'name': 'associations',
                'parquet_path': args.associations_parquet_path,
                'index_name': args.associations_index,
                'primary_key_candidates': ['association_key'],
                # association_id is row-index based and not semantic
                'hash_ignore_columns': {'association_id'},
                'settings': MeilisearchSettings.ASSOCIATIONS_SETTINGS,
            }
        )

    if args.dataset in ('sources', 'all'):
        datasets_to_import.append(
            {
                'name': 'sources',
                'parquet_path': args.sources_parquet_path,
                'index_name': args.sources_index,
                'primary_key_candidates': ['source_ref'],
                'hash_ignore_columns': set(),
                'settings': MeilisearchSettings.SOURCES_SETTINGS,
            }
        )

    for dataset in datasets_to_import:
        import_dataset(
            dataset_name=dataset['name'],
            parquet_path=dataset['parquet_path'],
            index_name=dataset['index_name'],
            primary_key_candidates=dataset['primary_key_candidates'],
            hash_ignore_columns=dataset['hash_ignore_columns'],
            settings=dataset['settings'],
            importer_path=importer_path,
            meili_url=args.meili_url,
            api_key=api_key,
            batch_size=args.batch_size,
            file_format=args.format,
            full_reindex=args.full_reindex,
            fetch_page_size=args.fetch_page_size,
            delete_batch_size=args.delete_batch_size,
            skip_settings=args.skip_settings,
        )

    print(f"\n{'=' * 80}")
    print('All imports completed successfully!')
    print('=' * 80)


if __name__ == '__main__':
    main(sys.argv[1:])
