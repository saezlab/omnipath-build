#!/usr/bin/env python3
"""Build source provenance/search documents from per-source silver metadata + reports."""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from typing import Any

import polars as pl

logger = logging.getLogger(__name__)

SOURCE_NAME_TYPE_ID = 'OM:0202'
SOURCE_ACCESSION_TYPE_ID = 'OM:0204'

ANN_LICENSE = 'OM:0851'
ANN_UPDATE_CATEGORY = 'OM:0852'
ANN_URL = 'OM:0853'
ANN_DESCRIPTION = 'OM:0854'
ANN_PUBMED = 'MI:0446'


def _first_identifier(identifiers: list[dict[str, Any]] | None, type_id: str) -> str | None:
    if not identifiers:
        return None
    for ident in identifiers:
        if not ident:
            continue
        if str(ident.get('type')) == type_id:
            value = ident.get('value')
            return None if value is None else str(value)
    return None


def _annotation_values(annotations: list[dict[str, Any]] | None, term_id: str) -> list[str]:
    values: list[str] = []
    if not annotations:
        return values
    for ann in annotations:
        if not ann:
            continue
        if str(ann.get('term')) == term_id and ann.get('value') is not None:
            values.append(str(ann.get('value')))
    return values


def _annotation_first(annotations: list[dict[str, Any]] | None, term_id: str) -> str | None:
    vals = _annotation_values(annotations, term_id)
    return vals[0] if vals else None


def _read_resource_metadata(resource_parquet: Path) -> dict[str, Any]:
    if not resource_parquet.exists():
        return {}

    df = pl.read_parquet(resource_parquet)
    if len(df) == 0:
        return {}

    row = df.row(0, named=True)
    identifiers = row.get('identifiers') or []
    annotations = row.get('annotations') or []

    source_name = _first_identifier(identifiers, SOURCE_NAME_TYPE_ID)
    source_accession = _first_identifier(identifiers, SOURCE_ACCESSION_TYPE_ID)
    source_ref = (
        f'{source_name}:{source_accession}'
        if source_name and source_accession
        else None
    )

    return {
        'source_name': source_name,
        'source_accession': source_accession,
        'source_ref': source_ref,
        'license_cv': _annotation_first(annotations, ANN_LICENSE),
        'update_category_cv': _annotation_first(annotations, ANN_UPDATE_CATEGORY),
        'resource_url': _annotation_first(annotations, ANN_URL),
        'resource_description': _annotation_first(annotations, ANN_DESCRIPTION),
        'pubmed': _annotation_values(annotations, ANN_PUBMED),
    }


def _read_report(report_json: Path) -> dict[str, Any]:
    if not report_json.exists():
        return {
            'finished_at': None,
            'function_records': [],
            'function_names': [],
            'total_records': None,
        }

    report = json.loads(report_json.read_text(encoding='utf-8'))
    function_records_raw = ((report.get('silver') or {}).get('function_records') or {})

    function_records = [
        {'function': str(k), 'records': int(v)}
        for k, v in sorted(function_records_raw.items())
    ]

    return {
        'finished_at': report.get('finished_at'),
        'function_records': function_records,
        'function_names': [fr['function'] for fr in function_records],
        'total_records': sum(fr['records'] for fr in function_records),
    }


def build_sources(per_source_root: Path, output: Path) -> Path:
    logger.info('Building sources table from %s', per_source_root)

    reports_dir = per_source_root / 'reports'
    source_dirs = [
        p for p in sorted(per_source_root.iterdir())
        if p.is_dir() and p.name != 'reports'
    ]

    rows: list[dict[str, Any]] = []

    for source_dir in source_dirs:
        source = source_dir.name.split('__', 1)[1].replace('__', '.') if '__' in source_dir.name else source_dir.name

        resource_meta = _read_resource_metadata(source_dir / 'silver' / 'resource.parquet')
        report_meta = _read_report(reports_dir / f'{source_dir.name}.json')

        source_ref = resource_meta.get('source_ref')
        if not source_ref:
            source_accession = resource_meta.get('source_accession')
            source_name = resource_meta.get('source_name') or source
            source_ref = f'{source_name}:{source_accession}' if source_accession else source_name

        rows.append({
            'source_ref': source_ref,
            'source': source,
            **resource_meta,
            **report_meta,
            'function_records_json': json.dumps(report_meta['function_records'], separators=(',', ':')),
        })

    if not rows:
        df = pl.DataFrame({
            'source_ref': pl.Series([], dtype=pl.Utf8),
            'source': pl.Series([], dtype=pl.Utf8),
            'source_name': pl.Series([], dtype=pl.Utf8),
            'source_accession': pl.Series([], dtype=pl.Utf8),
            'license_cv': pl.Series([], dtype=pl.Utf8),
            'update_category_cv': pl.Series([], dtype=pl.Utf8),
            'resource_url': pl.Series([], dtype=pl.Utf8),
            'resource_description': pl.Series([], dtype=pl.Utf8),
            'pubmed': pl.Series([], dtype=pl.List(pl.Utf8)),
            'finished_at': pl.Series([], dtype=pl.Utf8),
            'function_records': pl.Series([], dtype=pl.List(pl.Struct([
                pl.Field('function', pl.Utf8),
                pl.Field('records', pl.Int64),
            ]))),
            'function_names': pl.Series([], dtype=pl.List(pl.Utf8)),
            'total_records': pl.Series([], dtype=pl.Int64),
            'function_records_json': pl.Series([], dtype=pl.Utf8),
        })
    else:
        df = pl.DataFrame(rows).sort('source_ref')

    output.parent.mkdir(parents=True, exist_ok=True)
    df.write_parquet(output)
    logger.info('Wrote %s rows to %s', len(df), output)
    return output


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        '--per-source-root',
        type=Path,
        required=True,
        help='Per-source build root (contains source dirs and reports/).',
    )
    parser.add_argument(
        '--output',
        type=Path,
        required=True,
        help='Output parquet path (e.g. build/combined/search/search_sources.parquet).',
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
    build_sources(args.per_source_root, args.output)


if __name__ == '__main__':
    main()
