#!/usr/bin/env python3
"""
Generate a Meilisearch dump for portable deployment.

This script:
1. Triggers dump creation via POST /dumps
2. Polls until the dump task completes
3. Copies the dump file to a local directory (from local Meilisearch DB path or Docker)
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
import urllib.request
from pathlib import Path


def get_task_status(meili_url: str, task_uid: int, api_key: str | None) -> dict:
    """Get the status of a Meilisearch task."""
    req = urllib.request.Request(
        url=f'{meili_url}/tasks/{task_uid}',
        method='GET',
    )
    if api_key:
        req.add_header('Authorization', f'Bearer {api_key}')

    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode())


def create_dump(meili_url: str, api_key: str | None) -> int:
    """Trigger dump creation and return the task UID."""
    req = urllib.request.Request(
        url=f'{meili_url}/dumps',
        method='POST',
        data=b'',
        headers={'Content-Type': 'application/json'},
    )
    if api_key:
        req.add_header('Authorization', f'Bearer {api_key}')

    with urllib.request.urlopen(req) as resp:
        result = json.loads(resp.read().decode())
        return result['taskUid']


def wait_for_task(meili_url: str, task_uid: int, api_key: str | None) -> dict:
    """Wait for a task to complete, polling every 5 seconds."""
    while True:
        status = get_task_status(meili_url, task_uid, api_key)

        if status['status'] == 'succeeded':
            return status
        if status['status'] == 'failed':
            raise RuntimeError(f"Dump creation failed: {status.get('error', 'Unknown error')}")
        if status['status'] in ('enqueued', 'processing'):
            print(f"  Task status: {status['status']}...")
            time.sleep(5)
            continue

        raise RuntimeError(f"Unexpected task status: {status['status']}")


def copy_dump_from_local_paths(
    dump_uid: str,
    output_dir: Path,
    db_path: Path | None,
    dump_dir: Path | None,
) -> Path:
    """Resolve/copy the dump file from local filesystem to output_dir.

    Canonical mode uses --dump-dir and --output-dir pointing to the same folder,
    in which case no copy is performed.
    """
    dump_filename = f'{dump_uid}.dump'
    target_path = output_dir / dump_filename

    candidates: list[Path] = []
    if dump_dir is not None:
        candidates.append(dump_dir / dump_filename)
    elif db_path is not None:
        candidates.append(db_path / 'dumps' / dump_filename)

    if not candidates:
        raise RuntimeError('Local dump mode requires --dump-dir or --db-path')

    # Give Meilisearch a grace period to flush dump file.
    for _ in range(60):
        for source_path in candidates:
            if source_path.exists():
                if source_path.resolve() == target_path.resolve():
                    return source_path
                target_path.write_bytes(source_path.read_bytes())
                return target_path
        time.sleep(1)

    candidates_msg = '\n'.join(f'  - {p}' for p in candidates)
    raise RuntimeError(
        'Dump file not found in expected local paths:\n'
        f'{candidates_msg}'
    )


def copy_dump_from_docker(container_name: str, dump_uid: str, output_dir: Path) -> Path:
    """Copy the dump file from Docker container to local filesystem."""
    dump_filename = f'{dump_uid}.dump'
    container_dump_path = f'/meili_data/dumps/{dump_filename}'
    local_dump_path = output_dir / dump_filename

    cmd = ['docker', 'cp', f'{container_name}:{container_dump_path}', str(local_dump_path)]
    print(f"  Running: {' '.join(cmd)}")
    subprocess.run(cmd, check=True)

    return local_dump_path


def find_meilisearch_container() -> str:
    """Find the running Meilisearch container name."""
    result = subprocess.run(
        ['docker', 'ps', '--filter', 'ancestor=getmeili/meilisearch', '--format', '{{.Names}}'],
        capture_output=True,
        text=True,
        check=True,
    )
    containers = result.stdout.strip().split('\n')

    if not containers or containers[0] == '':
        result = subprocess.run(
            ['docker', 'ps', '--filter', 'publish=7700', '--format', '{{.Names}}'],
            capture_output=True,
            text=True,
            check=True,
        )
        containers = result.stdout.strip().split('\n')

    if not containers or containers[0] == '':
        raise RuntimeError(
            'No Meilisearch container found. Provide --db-path for local process mode.'
        )

    return containers[0]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        '--meili-url',
        default='http://127.0.0.1:7700',
        help='URL of the running Meilisearch instance',
    )
    parser.add_argument(
        '--api-key',
        default=None,
        help='Meilisearch API key (can also use MEILISEARCH_API_KEY env var)',
    )
    parser.add_argument(
        '--output-dir',
        type=Path,
        default=Path('data/dumps'),
        help='Directory to save the dump file',
    )
    parser.add_argument(
        '--db-path',
        type=Path,
        default=None,
        help='Local Meilisearch DB path (fallback search: <db-path>/dumps/<uid>.dump)',
    )
    parser.add_argument(
        '--dump-dir',
        type=Path,
        default=None,
        help='Local Meilisearch dump directory (preferred search: <dump-dir>/<uid>.dump)',
    )
    parser.add_argument(
        '--container-name',
        default=None,
        help='Docker container name (auto-detected if not specified)',
    )
    args = parser.parse_args()

    api_key = args.api_key or os.environ.get('MEILISEARCH_API_KEY')
    db_path = args.db_path
    dump_dir = args.dump_dir

    args.output_dir.mkdir(parents=True, exist_ok=True)

    print('=' * 60)
    print('Meilisearch Dump Generator')
    print('=' * 60)

    print('\n1. Creating dump...')
    task_uid = create_dump(args.meili_url, api_key)
    print(f'   Dump task created with UID: {task_uid}')

    print('\n2. Waiting for dump to complete...')
    task_status = wait_for_task(args.meili_url, task_uid, api_key)
    dump_uid = task_status['details']['dumpUid']
    print(f'   Dump completed: {dump_uid}')

    if dump_dir is not None or db_path is not None:
        print('\n3. Resolving dump from local filesystem...')
        if dump_dir is not None:
            print(f'   Dump dir: {dump_dir}')
        if db_path is not None:
            print(f'   DB path: {db_path}')
        dump_path = copy_dump_from_local_paths(
            dump_uid=dump_uid,
            output_dir=args.output_dir,
            db_path=db_path,
            dump_dir=dump_dir,
        )
    else:
        print('\n3. Copying dump from Docker container...')
        container_name = args.container_name or find_meilisearch_container()
        print(f'   Container: {container_name}')
        dump_path = copy_dump_from_docker(container_name, dump_uid, args.output_dir)

    dump_size_mb = dump_path.stat().st_size / (1024 * 1024)

    print('\n' + '=' * 60)
    print(f'✓ Dump saved to: {dump_path}')
    print(f'  Size: {dump_size_mb:.1f} MB')
    print('=' * 60)

    dump_file_marker = args.output_dir / '.dump_file'
    dump_file_marker.write_text(dump_path.name)
    print(f'  Wrote dump filename to: {dump_file_marker}')


if __name__ == '__main__':
    main()
