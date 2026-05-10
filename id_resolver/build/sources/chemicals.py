from __future__ import annotations

from pathlib import Path
from typing import Iterable

import polars as pl

from id_resolver.build.parquet import write_parquet_from_dict_rows
from id_resolver.build.paths import activate_raw_download_data_dir, ensure_chemicals_data_dir
from pypath.inputs_v2.chebi import resource as chebi_resource
from pypath.inputs_v2.hmdb import resource as hmdb_resource
from pypath.inputs_v2.lipidmaps import resource as lipidmaps_resource
from pypath.inputs_v2.swisslipids import resource as swisslipids_resource

CHEMICAL_IDENTIFIER_LOOKUP_SCHEMA: dict[str, pl.DataType] = {
    'source': pl.Utf8,
    'key_type': pl.Utf8,
    'key_value': pl.Utf8,
    'standard_inchi': pl.Utf8,
}

# Backward-compatible aliases for callers that imported the old source-table names.
CHEMICAL_MAPPING_SCHEMA = CHEMICAL_IDENTIFIER_LOOKUP_SCHEMA

CHEMICAL_SOURCES: tuple[str, ...] = (
    'chebi',
    'hmdb',
    'lipidmaps',
    'swisslipids',
)

CHEMICAL_OUTPUT_FILENAMES: dict[str, str] = {
    source: 'chemical_identifier_lookup.parquet'
    for source in CHEMICAL_SOURCES
}
CHEMICAL_IDENTIFIER_LOOKUP_OUTPUT_FILENAME = 'chemical_identifier_lookup.parquet'

_CHEMICAL_RESOURCES = {
    'chebi': chebi_resource,
    'hmdb': hmdb_resource,
    'lipidmaps': lipidmaps_resource,
    'swisslipids': swisslipids_resource,
}


def _validate_chemical_sources(sources: Iterable[str]) -> tuple[str, ...]:
    selected = tuple(sources)
    unsupported = sorted(set(selected) - set(CHEMICAL_SOURCES))
    if unsupported:
        raise ValueError(f'Unsupported chemical source(s): {unsupported}')
    return selected


def _chemical_identifier_rows(
    sources: Iterable[str],
    max_records: int | None = None,
) -> Iterable[dict]:
    for source in _validate_chemical_sources(sources):
        yield from _CHEMICAL_RESOURCES[source].id_translation.raw(max_records=max_records)


def build_chemical_source_id_to_standard_inchi(
    source: str,
    max_records: int | None = None,
) -> pl.DataFrame:
    activate_raw_download_data_dir()
    rows = list(_chemical_identifier_rows([source], max_records=max_records))
    return pl.DataFrame(rows, schema=CHEMICAL_IDENTIFIER_LOOKUP_SCHEMA)


def build_chemical_identifier_lookup(
    sources: Iterable[str] = CHEMICAL_SOURCES,
    max_records: int | None = None,
) -> pl.DataFrame:
    """Build one long chemical resolver table directly from source translation rows."""
    activate_raw_download_data_dir()
    rows = list(_chemical_identifier_rows(sources, max_records=max_records))
    if not rows:
        return pl.DataFrame(schema=CHEMICAL_IDENTIFIER_LOOKUP_SCHEMA)
    return pl.DataFrame(rows, schema=CHEMICAL_IDENTIFIER_LOOKUP_SCHEMA).unique()


def materialize_chemical_source(
    source: str,
    output_dir: str | Path | None = None,
    max_records: int | None = None,
) -> dict[str, int]:
    return materialize_chemical_sources(
        sources=[source],
        output_dir=output_dir,
        max_records=max_records,
    )


def materialize_chemical_sources(
    sources: Iterable[str],
    output_dir: str | Path | None = None,
    max_records: int | None = None,
) -> dict[str, int]:
    selected = _validate_chemical_sources(sources)
    output_dir = Path(output_dir) if output_dir is not None else ensure_chemicals_data_dir()
    output_dir.mkdir(parents=True, exist_ok=True)

    activate_raw_download_data_dir()
    row_count = write_parquet_from_dict_rows(
        _chemical_identifier_rows(selected, max_records=max_records),
        CHEMICAL_IDENTIFIER_LOOKUP_SCHEMA,
        output_dir / CHEMICAL_IDENTIFIER_LOOKUP_OUTPUT_FILENAME,
    )

    return {
        'chemical_identifier_lookup_rows': row_count,
    }
