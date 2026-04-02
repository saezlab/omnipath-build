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

CHEMICAL_MAPPING_SCHEMA: dict[str, pl.DataType] = {
    'source': pl.Utf8,
    'key_type': pl.Utf8,
    'key_value': pl.Utf8,
    'standard_inchi': pl.Utf8,
}


CHEMICAL_SOURCES: tuple[str, ...] = (
    'chebi',
    'hmdb',
    'lipidmaps',
    'swisslipids',
)

CHEMICAL_OUTPUT_FILENAMES: dict[str, str] = {
    source: f'{source}.parquet'
    for source in CHEMICAL_SOURCES
}

_CHEMICAL_RESOURCES = {
    'chebi': chebi_resource,
    'hmdb': hmdb_resource,
    'lipidmaps': lipidmaps_resource,
    'swisslipids': swisslipids_resource,
}


def build_chemical_source_id_to_standard_inchi(
    source: str,
    max_records: int | None = None,
) -> pl.DataFrame:
    activate_raw_download_data_dir()
    resource = _CHEMICAL_RESOURCES[source]
    rows = list(resource.id_translation.raw(max_records=max_records))
    return pl.DataFrame(rows, schema=CHEMICAL_MAPPING_SCHEMA)


def materialize_chemical_source(
    source: str,
    output_dir: str | Path | None = None,
    max_records: int | None = None,
) -> dict[str, int]:
    if source not in _CHEMICAL_RESOURCES:
        raise ValueError(f'Unsupported chemical source: {source}')

    output_dir = Path(output_dir) if output_dir is not None else ensure_chemicals_data_dir()
    output_dir.mkdir(parents=True, exist_ok=True)

    activate_raw_download_data_dir()
    row_count = write_parquet_from_dict_rows(
        _CHEMICAL_RESOURCES[source].id_translation.raw(max_records=max_records),
        CHEMICAL_MAPPING_SCHEMA,
        output_dir / CHEMICAL_OUTPUT_FILENAMES[source],
    )

    return {
        'chemical_reference_rows': row_count,
    }


def materialize_chemical_sources(
    sources: Iterable[str],
    output_dir: str | Path | None = None,
    max_records: int | None = None,
) -> dict[str, int]:
    summary: dict[str, int] = {}
    for source in sources:
        result = materialize_chemical_source(
            source=source,
            output_dir=output_dir,
            max_records=max_records,
        )
        summary.update({f'{source}_{key}': value for key, value in result.items()})
    return summary
