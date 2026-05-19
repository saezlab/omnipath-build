"""Materialize resolver source lookup tables as parquet files."""

from __future__ import annotations

from pathlib import Path
import argparse
from collections.abc import Sequence

from omnipath_build.resolver.paths import (
    ensure_data_dir,
    activate_raw_download_data_dir,
)
from omnipath_build.resolver.sources import (
    CHEMICAL_SOURCES,
    materialize_proteins,
    materialize_chemical_sources,
)

SOURCE_NAMES: tuple[str, ...] = (
    'uniprot',
    *CHEMICAL_SOURCES,
)


def build_arg_parser() -> argparse.ArgumentParser:
    """Build the standalone resolver materialization argument parser."""

    parser = argparse.ArgumentParser(
        description='Materialize omnipath_build resolver lookup tables.'
    )
    parser.add_argument(
        'sources',
        nargs='*',
        choices=SOURCE_NAMES,
        help='Resolver sources to materialize. Defaults to all sources.',
    )
    parser.add_argument(
        '--output-dir',
        type=Path,
        default=None,
        help='Base output directory. Defaults to ./data/.',
    )
    parser.add_argument(
        '--taxonomy-id',
        dest='taxonomy_ids',
        action='append',
        default=None,
        help='Optional UniProt taxonomy filter. Can be repeated.',
    )
    parser.add_argument(
        '--max-records',
        type=int,
        default=None,
        help='Optional parser cap for development smoke tests.',
    )
    parser.add_argument(
        '--pubchem-url',
        default=None,
        help=(
            'Optional single PubChem SDF .gz URL/path. '
            'Defaults to all current PubChem full-SDF shards.'
        ),
    )
    parser.add_argument(
        '--pubchem-shards',
        type=int,
        default=None,
        help='Optional number of discovered PubChem SDF shards to stream.',
    )
    parser.add_argument(
        '--skip-existing',
        action=argparse.BooleanOptionalAction,
        default=True,
        help='Skip resolver sources already present in the output directory.',
    )
    return parser


def _output_subdir(base_dir: Path | None, name: str) -> Path | None:
    if base_dir is None:
        return None
    return base_dir / name


def run_sources(
    sources: Sequence[str],
    output_dir: str | Path | None = None,
    taxonomy_ids: Sequence[int | str] | None = None,
    max_records: int | None = None,
    pubchem_url: str | Path | None = None,
    pubchem_shards: int | None = None,
    skip_existing: bool = True,
) -> dict[str, int]:
    """Materialize selected resolver sources and return row-count summaries."""

    base_dir = ensure_data_dir() if output_dir is None else Path(output_dir)
    base_dir.mkdir(parents=True, exist_ok=True)
    activate_raw_download_data_dir()

    summary: dict[str, int] = {}
    selected = list(sources) if sources else list(SOURCE_NAMES)

    if 'uniprot' in selected:
        result = materialize_proteins(
            output_dir=_output_subdir(base_dir, 'proteins'),
            taxonomy_ids=taxonomy_ids,
            skip_existing=skip_existing,
        )
        summary.update(
            {f'uniprot_{key}': value for key, value in result.items()}
        )

    chemical_sources = [
        source for source in selected if source in CHEMICAL_SOURCES
    ]
    if chemical_sources:
        result = materialize_chemical_sources(
            sources=chemical_sources,
            output_dir=_output_subdir(base_dir, 'chemicals'),
            max_records=max_records,
            pubchem_url=pubchem_url,
            pubchem_shards=pubchem_shards,
            skip_existing=skip_existing,
        )
        summary.update(
            {f'chemicals_{key}': value for key, value in result.items()}
        )

    return summary


def main(argv: Sequence[str] | None = None) -> int:
    """Run resolver materialization from command-line arguments."""

    parser = build_arg_parser()
    args = parser.parse_args(argv)

    summary = run_sources(
        sources=args.sources or SOURCE_NAMES,
        output_dir=args.output_dir,
        taxonomy_ids=args.taxonomy_ids,
        max_records=args.max_records,
        pubchem_url=args.pubchem_url,
        pubchem_shards=args.pubchem_shards,
        skip_existing=args.skip_existing,
    )

    for key, value in summary.items():
        print(f'{key}: {value}')

    return 0


if __name__ == '__main__':
    raise SystemExit(main())
