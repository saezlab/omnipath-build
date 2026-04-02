from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence

from id_resolver.build.paths import activate_raw_download_data_dir, ensure_data_dir
from id_resolver.build.sources.chemicals import CHEMICAL_SOURCES, materialize_chemical_source
from id_resolver.build.sources.proteins import materialize_proteins

SOURCE_NAMES: tuple[str, ...] = (
    'uniprot',
    *CHEMICAL_SOURCES,
)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description='Materialize standalone ID resolver mapping tables for individual authoritative sources.'
    )
    parser.add_argument(
        'sources',
        nargs='+',
        choices=SOURCE_NAMES,
        help='One or more sources to materialize.',
    )
    parser.add_argument(
        '--output-dir',
        type=Path,
        default=None,
        help='Base output directory. Defaults to id_resolver/data/.',
    )
    parser.add_argument(
        '--taxonomy-id',
        dest='taxonomy_ids',
        action='append',
        default=None,
        help='Optional UniProt taxonomy filter. Can be provided multiple times.',
    )
    parser.add_argument(
        '--max-records',
        type=int,
        default=None,
        help='Optional parser cap for development/smoke tests where supported.',
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
) -> dict[str, int]:
    base_dir = ensure_data_dir() if output_dir is None else Path(output_dir)
    base_dir.mkdir(parents=True, exist_ok=True)
    activate_raw_download_data_dir()

    summary: dict[str, int] = {}

    for source in sources:
        if source == 'uniprot':
            result = materialize_proteins(
                output_dir=_output_subdir(base_dir, 'proteins'),
                taxonomy_ids=taxonomy_ids,
            )
        else:
            result = materialize_chemical_source(
                source=source,
                output_dir=_output_subdir(base_dir, 'chemicals'),
                max_records=max_records,
            )

        summary.update({f'{source}_{key}': value for key, value in result.items()})

    return summary


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    summary = run_sources(
        sources=args.sources,
        output_dir=args.output_dir,
        taxonomy_ids=args.taxonomy_ids,
        max_records=args.max_records,
    )

    for key, value in summary.items():
        print(f'{key}: {value}')

    return 0


if __name__ == '__main__':
    raise SystemExit(main())
