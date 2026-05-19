"""Write ontology resource artifacts such as OBO files."""

from __future__ import annotations

import sys
from pathlib import Path
import argparse
from collections.abc import Iterable, Sequence

from omnipath_build.resources import ResourceFunction, discover_resources
from pypath.internals.ontology_schema import OntologyTerm
from pypath.inputs_v2.ontology_serializers import format_obo

def write_discovered_ontology_artifacts(
    *,
    output_dir: str | Path = 'data/obo',
    sources: Sequence[str] = (),
    dataset: str | None = None,
    database: str = 'omnipath',
    inputs_package: str = 'pypath.inputs_v2',
    force_refresh: bool = False,
) -> list[Path]:
    """Discover ontology resources and write their configured artifact files."""

    functions = discover_ontology_functions(
        database=database,
        inputs_package=inputs_package,
        sources=sources,
        dataset=dataset,
    )
    output = Path(output_dir)
    paths: list[Path] = []
    for fn in functions:
        raw_dataset = getattr(fn.call, '_raw_dataset', None)
        if raw_dataset is None:
            continue
        try:
            terms = collect_ontology_terms(
                raw_dataset(force_refresh=force_refresh)
            )
            path = write_ontology_obo(fn, terms, output_dir=output)
            print(
                f'[{fn.source}.{fn.function_name}] '
                f'obo={path} terms={len(terms)}',
                flush=True,
            )
            paths.append(path)
        except Exception as exc:
            print(
                '[warning] '
                f'[{fn.source}.{fn.function_name}] ontology artifact failed; '
                f'continuing: {exc.__class__.__name__}: {exc}',
                file=sys.stderr,
                flush=True,
            )
    return paths


def discover_ontology_functions(
    *,
    database: str,
    inputs_package: str,
    sources: Sequence[str],
    dataset: str | None,
) -> list[ResourceFunction]:
    discovered, _ = discover_resources(
        database_name=database,
        inputs_package=inputs_package,
        progress=True,
    )
    source_names = tuple(sources) or tuple(sorted(discovered))
    unknown = [source for source in source_names if source not in discovered]
    if unknown:
        raise ValueError(f'Unknown source(s): {unknown}')

    selected: list[ResourceFunction] = []
    for source in source_names:
        for fn in discovered[source]:
            if fn.function_name == 'resource':
                continue
            if fn.output_kind != 'ontology':
                continue
            if dataset is not None and fn.function_name != dataset:
                continue
            if getattr(fn.call, '_raw_dataset', None) is None:
                continue
            selected.append(fn)
    return selected


def collect_ontology_terms(records: Iterable[object]) -> list[OntologyTerm]:
    terms: list[OntologyTerm] = []
    for record in records:
        value = getattr(record, 'record', record)
        if isinstance(value, OntologyTerm) and value.id:
            terms.append(value)
    return terms


def write_ontology_obo(
    fn: ResourceFunction,
    terms: list[OntologyTerm],
    *,
    output_dir: Path,
) -> Path:
    extension = (getattr(fn, 'file_extension', None) or 'obo').lstrip('.')
    file_stem = getattr(fn, 'file_stem', None) or fn.function_name
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f'{file_stem}.{extension}'
    output_path.write_text(format_obo(fn.document, terms), encoding='utf-8')
    return output_path


def _split_source_names(value: str | None) -> list[str]:
    if not value:
        return []
    return [part for chunk in value.split(',') for part in chunk.split() if part]


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description='Write discovered ontology artifacts such as OBO files.'
    )
    parser.add_argument('--output-dir', default='data/obo')
    parser.add_argument(
        '--sources',
        default=None,
        help='Comma-separated inputs_v2 source names. Omit to write all ontology sources.',
    )
    parser.add_argument(
        '--source',
        action='append',
        default=None,
        help='inputs_v2 source name. Can be repeated.',
    )
    parser.add_argument('--dataset', default=None)
    parser.add_argument('--inputs-package', default='pypath.inputs_v2')
    parser.add_argument('--database', default='omnipath')
    parser.add_argument('--force-refresh', action='store_true')
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    source_names = _split_source_names(args.sources)
    for source in args.source or ():
        source_names.extend(_split_source_names(source))
    paths = write_discovered_ontology_artifacts(
        output_dir=args.output_dir,
        sources=tuple(source_names),
        dataset=args.dataset,
        database=args.database,
        inputs_package=args.inputs_package,
        force_refresh=args.force_refresh,
    )
    print(f'[ontology-artifacts] files={len(paths)}', flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
