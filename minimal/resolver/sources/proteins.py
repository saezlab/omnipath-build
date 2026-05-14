from __future__ import annotations

from pathlib import Path
from typing import Iterable

import polars as pl

from minimal.resolver.parquet import write_parquet_from_dict_rows
from minimal.resolver.paths import (
    activate_raw_download_data_dir,
    ensure_proteins_data_dir,
)
from pypath.inputs_v2.uniprot import resource as uniprot_resource

PROTEIN_IDENTIFIER_LOOKUP_SCHEMA: dict[str, pl.DataType] = {
    'source': pl.Utf8,
    'key_type': pl.Utf8,
    'key_value': pl.Utf8,
    'taxonomy_id': pl.Utf8,
    'primary_uniprot': pl.Utf8,
    'mapping_type': pl.Utf8,
}

PROTEIN_SOURCE = 'uniprot'
UNIPROT_TYPE = 'MI:1097:Uniprot'
PROTEIN_IDENTIFIER_LOOKUP_OUTPUT_FILENAME = 'protein_identifier_lookup.parquet'


def _protein_identifier_rows(
    taxonomy_ids: Iterable[int | str] | None = None,
) -> Iterable[dict]:
    primary_uniprots: set[str] = set()

    for row in uniprot_resource.reference_id_translation.raw(
        taxonomy_ids=taxonomy_ids
    ):
        primary_uniprot = row.get('primary_uniprot')
        if primary_uniprot:
            primary_uniprots.add(str(primary_uniprot))
        key_type = row.get('key_type')
        yield {
            'source': PROTEIN_SOURCE,
            'key_type': key_type,
            'key_value': row.get('key_value'),
            'taxonomy_id': row.get('taxonomy_id'),
            'primary_uniprot': primary_uniprot,
            'mapping_type': (
                'uniprot_primary'
                if key_type == UNIPROT_TYPE
                else 'uniprot_reference'
            ),
        }

    for row in uniprot_resource.secondary_to_primary.raw():
        primary_uniprot = row.get('primary_uniprot')
        if primary_uniprot:
            primary_uniprots.add(str(primary_uniprot))
        yield {
            'source': PROTEIN_SOURCE,
            'key_type': UNIPROT_TYPE,
            'key_value': row.get('secondary_uniprot'),
            'taxonomy_id': None,
            'primary_uniprot': primary_uniprot,
            'mapping_type': 'uniprot_secondary',
        }

    for primary_uniprot in sorted(primary_uniprots):
        yield {
            'source': PROTEIN_SOURCE,
            'key_type': UNIPROT_TYPE,
            'key_value': primary_uniprot,
            'taxonomy_id': None,
            'primary_uniprot': primary_uniprot,
            'mapping_type': 'uniprot_primary',
        }


def build_protein_identifier_lookup(
    taxonomy_ids: Iterable[int | str] | None = None,
) -> pl.DataFrame:
    activate_raw_download_data_dir()
    rows = list(_protein_identifier_rows(taxonomy_ids=taxonomy_ids))
    if not rows:
        return pl.DataFrame(schema=PROTEIN_IDENTIFIER_LOOKUP_SCHEMA)
    return pl.DataFrame(rows, schema=PROTEIN_IDENTIFIER_LOOKUP_SCHEMA).unique()


def materialize_proteins(
    output_dir: str | Path | None = None,
    taxonomy_ids: Iterable[int | str] | None = None,
) -> dict[str, int]:
    output_dir = (
        Path(output_dir)
        if output_dir is not None
        else ensure_proteins_data_dir()
    )
    output_dir.mkdir(parents=True, exist_ok=True)

    activate_raw_download_data_dir()
    row_count = write_parquet_from_dict_rows(
        _protein_identifier_rows(taxonomy_ids=taxonomy_ids),
        PROTEIN_IDENTIFIER_LOOKUP_SCHEMA,
        output_dir / PROTEIN_IDENTIFIER_LOOKUP_OUTPUT_FILENAME,
    )

    return {'protein_identifier_lookup_rows': row_count}
