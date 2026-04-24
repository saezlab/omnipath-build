from __future__ import annotations

import argparse
import json
import logging
import re
import shutil
import tempfile
import urllib.request
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import polars as pl

from omnipath_build.gold.utils.table_schema import (
    ONTOLOGY_TERM_SCHEMA,
    aggregate_unique_string_lists,
    empty_frame,
)

logger = logging.getLogger(__name__)

EXTERNAL_ONTOLOGIES = {
    'go': 'https://purl.obolibrary.org/obo/go.obo',
    'hp': 'https://purl.obolibrary.org/obo/hp.obo',
}


def _parse_obo_definition(value: str) -> str:
    match = re.match(r'^"(.*)"(?:\s*\[.*\])?$', value)
    if match:
        return match.group(1).replace('\\"', '"')
    return value


def _parse_obo_synonym(value: str) -> str:
    match = re.match(r'^"(.*?)"(?:\s|$)', value)
    if match:
        return match.group(1).replace('\\"', '"')
    return value


def _iter_obo_terms(obo_path: Path) -> Iterable[dict[str, Any]]:
    ontology_id: str | None = None
    in_term = False
    current: dict[str, Any] = {}

    with obo_path.open('r', encoding='utf-8', errors='ignore') as handle:
        for raw_line in handle:
            line = raw_line.rstrip('\n')
            stripped = line.strip()

            if not stripped:
                if in_term:
                    row = _finalize_obo_term(current, ontology_id)
                    if row is not None:
                        yield row
                    in_term = False
                    current = {}
                continue

            if not in_term and ontology_id is None and stripped.startswith('ontology:'):
                ontology_id = stripped.partition(':')[2].strip() or None
                continue

            if stripped == '[Term]':
                if in_term:
                    row = _finalize_obo_term(current, ontology_id)
                    if row is not None:
                        yield row
                in_term = True
                current = {}
                continue

            if not in_term or stripped.startswith('['):
                if in_term:
                    row = _finalize_obo_term(current, ontology_id)
                    if row is not None:
                        yield row
                    in_term = False
                    current = {}
                continue

            key, sep, value = stripped.partition(':')
            if not sep:
                continue
            key = key.strip()
            value = value.strip()

            if key == 'id' and 'id' not in current:
                current['id'] = value
            elif key == 'name' and 'name' not in current:
                current['name'] = value
            elif key == 'def' and 'def' not in current:
                current['def'] = _parse_obo_definition(value)
            elif key == 'synonym':
                current.setdefault('synonyms', []).append(_parse_obo_synonym(value))

    if in_term:
        row = _finalize_obo_term(current, ontology_id)
        if row is not None:
            yield row


def _finalize_obo_term(
    current: dict[str, Any],
    ontology_id: str | None,
) -> dict[str, Any] | None:
    term_id = current.get('id')
    if term_id is None:
        return None
    prefix = term_id.split(':', 1)[0] if ':' in term_id else None
    return {
        'term_id': term_id,
        'ontology_prefix': prefix.lower() if prefix else ontology_id,
        'label': current.get('name'),
        'definition': current.get('def'),
        'synonyms': current.get('synonyms') or [],
    }


def _collect_obo_terms(obo_path: Path, source: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for term in _iter_obo_terms(obo_path):
        term['sources'] = [source]
        rows.append(term)
    return rows


def _discover_source_obo_files(source_root: Path) -> list[tuple[Path, str]]:
    discovered: list[tuple[Path, str]] = []
    source_root = Path(source_root)
    for obo_path in sorted(source_root.rglob('*.obo')):
        try:
            source = obo_path.relative_to(source_root).parts[0]
        except IndexError:
            source = obo_path.parent.name
        discovered.append((obo_path, source))
    return discovered


def _download_external_ontologies(download_dir: Path) -> list[tuple[Path, str]]:
    downloaded: list[tuple[Path, str]] = []
    for name, url in EXTERNAL_ONTOLOGIES.items():
        destination = download_dir / f'{name}.obo'
        logger.info('Downloading ontology artifact %s -> %s', url, destination)
        request = urllib.request.Request(url, headers={'User-Agent': 'omnipath-build/1.0'})
        with urllib.request.urlopen(request) as response, destination.open('wb') as handle:
            shutil.copyfileobj(response, handle)
        downloaded.append((destination, name))
    return downloaded


def build_ontology_terms_dataframe(
    source_root: str | Path,
) -> tuple[pl.DataFrame, list[tuple[Path, str]]]:
    """Build ontology terms DataFrame from OBO files under source_root."""
    source_root = Path(source_root)

    # 1. Discover source OBO files
    obo_files = _discover_source_obo_files(source_root)

    # 2. Download external ontologies to a temp dir and parse immediately
    with tempfile.TemporaryDirectory() as tmpdir:
        external_obo_files = _download_external_ontologies(Path(tmpdir))
        obo_files.extend(external_obo_files)

        # 3. Parse all OBO terms
        obo_rows: list[dict[str, Any]] = []
        for obo_path, source in obo_files:
            obo_rows.extend(_collect_obo_terms(obo_path, source))

    if not obo_rows:
        return empty_frame(ONTOLOGY_TERM_SCHEMA), obo_files

    obo_df = pl.DataFrame(obo_rows)
    combined = (
        obo_df
        .group_by('term_id')
        .agg([
            pl.col('ontology_prefix').drop_nulls().first().alias('ontology_prefix'),
            pl.col('label').drop_nulls().first().alias('label'),
            pl.col('definition').drop_nulls().first().alias('definition'),
            aggregate_unique_string_lists('synonyms'),
            aggregate_unique_string_lists('sources'),
        ])
        .select(list(ONTOLOGY_TERM_SCHEMA.keys()))
        .sort('term_id')
    )
    return combined, obo_files


def build_ontology_terms(
    source_root: str | Path,
    output_dir: str | Path,
) -> dict[str, Any]:
    """Build combined ontology_term.parquet from OBO files under source_root."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    combined, obo_files = build_ontology_terms_dataframe(source_root)
    combined.write_parquet(output_dir / 'ontology_term.parquet')

    summary = {
        'source_root': str(source_root),
        'output_dir': str(output_dir),
        'obo_files': [str(p) for p, _ in obo_files],
        'term_count': int(combined.height),
    }
    (output_dir / 'ontology_build_summary.json').write_text(
        json.dumps(summary, indent=2) + '\n',
        encoding='utf-8',
    )
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description='Build ontology_term.parquet from OBO files under a source root.',
    )
    parser.add_argument(
        '--source-root',
        type=Path,
        default=Path('data/silver'),
        help='Root directory to scan for OBO files (default: data/silver)',
    )
    parser.add_argument(
        '--output-dir',
        type=Path,
        default=Path('data/combined'),
        help='Directory to write ontology_term.parquet (default: data/combined)',
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    summary = build_ontology_terms(
        source_root=args.source_root,
        output_dir=args.output_dir,
    )
    print(f"Built {summary['term_count']} ontology terms from {len(summary['obo_files'])} OBO files")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
