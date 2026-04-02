from __future__ import annotations

from pathlib import Path
from typing import Iterable

import polars as pl

from id_resolver.build.parquet import write_parquet_from_dict_rows
from id_resolver.build.paths import activate_raw_download_data_dir, ensure_proteins_data_dir
from pypath.inputs_v2.uniprot import resource as uniprot_resource

PROTEIN_REFERENCE_MAPPING_SCHEMA: dict[str, pl.DataType] = {
    'key_type': pl.Utf8,
    'key_value': pl.Utf8,
    'taxonomy_id': pl.Utf8,
    'primary_uniprot': pl.Utf8,
}

UNIPROT_SECONDARY_MAPPING_SCHEMA: dict[str, pl.DataType] = {
    'secondary_uniprot': pl.Utf8,
    'primary_uniprot': pl.Utf8,
}

PROTEIN_SOURCE = 'uniprot'
PROTEIN_REFERENCE_OUTPUT_FILENAME = 'protein_reference_to_uniprot.parquet'
UNIPROT_SECONDARY_OUTPUT_FILENAME = 'uniprot_secondary_to_primary.parquet'


def build_protein_reference_to_uniprot(
    taxonomy_ids: Iterable[int | str] | None = None,
) -> pl.DataFrame:
    activate_raw_download_data_dir()
    rows = list(uniprot_resource.reference_id_translation.raw(taxonomy_ids=taxonomy_ids))

    return pl.DataFrame(rows, schema=PROTEIN_REFERENCE_MAPPING_SCHEMA)


def build_uniprot_secondary_to_primary() -> pl.DataFrame:
    activate_raw_download_data_dir()
    rows = list(uniprot_resource.secondary_to_primary.raw())

    return pl.DataFrame(rows, schema=UNIPROT_SECONDARY_MAPPING_SCHEMA)


def materialize_proteins(
    output_dir: str | Path | None = None,
    taxonomy_ids: Iterable[int | str] | None = None,
) -> dict[str, int]:
    output_dir = Path(output_dir) if output_dir is not None else ensure_proteins_data_dir()
    output_dir.mkdir(parents=True, exist_ok=True)

    activate_raw_download_data_dir()
    protein_reference_rows = write_parquet_from_dict_rows(
        uniprot_resource.reference_id_translation.raw(taxonomy_ids=taxonomy_ids),
        PROTEIN_REFERENCE_MAPPING_SCHEMA,
        output_dir / PROTEIN_REFERENCE_OUTPUT_FILENAME,
    )
    uniprot_secondary_rows = write_parquet_from_dict_rows(
        uniprot_resource.secondary_to_primary.raw(),
        UNIPROT_SECONDARY_MAPPING_SCHEMA,
        output_dir / UNIPROT_SECONDARY_OUTPUT_FILENAME,
    )

    return {
        'protein_reference_rows': protein_reference_rows,
        'uniprot_secondary_rows': uniprot_secondary_rows,
    }
