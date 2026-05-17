from __future__ import annotations

from pathlib import Path
from typing import Iterable

import polars as pl
from omnipath_build.resolver.identifier_types import (
    IDENTIFIER_TYPE_SCHEMA,
    identifier_type_id,
    identifier_type_rows,
)
from omnipath_build.resolver.paths import (
    activate_raw_download_data_dir,
    ensure_proteins_data_dir,
)
from pypath.internals.cv_terms import (
    IdentifierNamespaceCv,
    cv_term_label_accession,
)
from pypath.inputs_v2.uniprot import resource as uniprot_resource

PROTEIN_IDENTIFIER_LOOKUP_SCHEMA: dict[str, pl.DataType] = {
    'key_identifier_type_id': pl.UInt32,
    'key_value': pl.Utf8,
    'taxonomy_id': pl.Utf8,
    'canonical_identifier_type_id': pl.UInt32,
    'canonical_identifier': pl.Utf8,
}

UNIPROT_TYPE = cv_term_label_accession(IdentifierNamespaceCv.UNIPROT)
KEY_TYPE_ALIASES = {
    'MI:1097:Uniprot': UNIPROT_TYPE,
    'MI:0476:Ensembl': cv_term_label_accession(IdentifierNamespaceCv.ENSEMBL),
    'MI:0477:Entrez': cv_term_label_accession(IdentifierNamespaceCv.ENTREZ),
    'MI:1095:HGNC': cv_term_label_accession(IdentifierNamespaceCv.HGNC),
    'OM:0200:Gene Name Primary': cv_term_label_accession(
        IdentifierNamespaceCv.GENE_NAME_PRIMARY
    ),
    'OM:0201:Gene Name Synonym': cv_term_label_accession(
        IdentifierNamespaceCv.GENE_NAME_SYNONYM
    ),
    'OM:0221:Uniprot Entry Name': cv_term_label_accession(
        IdentifierNamespaceCv.UNIPROT_ENTRY_NAME
    ),
}
PROTEIN_IDENTIFIER_LOOKUP_OUTPUT_FILENAME = 'protein_identifier_lookup.parquet'
PROTEIN_IDENTIFIER_LOOKUP_AMBIGUOUS_OUTPUT_FILENAME = (
    'protein_identifier_lookup_ambiguous.parquet'
)
IDENTIFIER_TYPE_OUTPUT_FILENAME = 'identifier_type.parquet'


def _protein_identifier_rows(
    taxonomy_ids: Iterable[int | str] | None = None,
) -> Iterable[dict]:
    primary_taxonomy: dict[str, set[str]] = {}

    for row in uniprot_resource.reference_id_translation.raw(
        taxonomy_ids=taxonomy_ids
    ):
        primary_uniprot = row.get('primary_uniprot')
        if primary_uniprot:
            primary_uniprot = str(primary_uniprot)
            taxonomy_id = row.get('taxonomy_id')
            if taxonomy_id:
                primary_taxonomy.setdefault(primary_uniprot, set()).add(
                    str(taxonomy_id)
                )
        key_type = KEY_TYPE_ALIASES.get(row.get('key_type'), row.get('key_type'))
        yield {
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
        if primary_uniprot is None:
            continue
        primary_uniprot = str(primary_uniprot)
        taxonomy_id = _single_taxonomy_id(primary_taxonomy.get(primary_uniprot))
        if taxonomy_id is None:
            continue
        yield {
            'key_type': UNIPROT_TYPE,
            'key_value': row.get('secondary_uniprot'),
            'taxonomy_id': taxonomy_id,
            'primary_uniprot': primary_uniprot,
            'mapping_type': 'uniprot_secondary',
        }


def _single_taxonomy_id(values: set[str] | None) -> str | None:
    if not values or len(values) != 1:
        return None
    return next(iter(values))


def build_protein_identifier_lookup(
    taxonomy_ids: Iterable[int | str] | None = None,
) -> pl.DataFrame:
    activate_raw_download_data_dir()
    return _split_protein_identifier_lookup(
        _protein_identifier_rows(taxonomy_ids=taxonomy_ids)
    )[0]


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
    lookup, ambiguous, identifier_types = _split_protein_identifier_lookup(
        _protein_identifier_rows(taxonomy_ids=taxonomy_ids)
    )
    lookup.write_parquet(output_dir / PROTEIN_IDENTIFIER_LOOKUP_OUTPUT_FILENAME)
    ambiguous.write_parquet(
        output_dir / PROTEIN_IDENTIFIER_LOOKUP_AMBIGUOUS_OUTPUT_FILENAME
    )
    identifier_types.write_parquet(output_dir / IDENTIFIER_TYPE_OUTPUT_FILENAME)

    return {
        'protein_identifier_lookup_rows': lookup.height,
        'protein_identifier_lookup_ambiguous_rows': ambiguous.height,
        'identifier_type_rows': identifier_types.height,
    }


def _split_protein_identifier_lookup(
    rows: Iterable[dict],
) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    normalized_rows: list[dict[str, object]] = []
    type_names = {UNIPROT_TYPE}
    for row in rows:
        key_type = row.get('key_type')
        if key_type is None:
            continue
        key_type = str(key_type)
        type_names.add(key_type)
        normalized_rows.append(
            {
                'key_identifier_type_id': identifier_type_id(key_type),
                'key_value': row.get('key_value'),
                'taxonomy_id': row.get('taxonomy_id'),
                'canonical_identifier_type_id': identifier_type_id(UNIPROT_TYPE),
                'canonical_identifier': row.get('primary_uniprot'),
            }
        )

    identifier_types = pl.DataFrame(
        identifier_type_rows(type_names),
        schema=IDENTIFIER_TYPE_SCHEMA,
    )
    if not normalized_rows:
        empty = pl.DataFrame(schema=PROTEIN_IDENTIFIER_LOOKUP_SCHEMA)
        return empty, empty, identifier_types

    lookup = (
        pl.DataFrame(normalized_rows, schema=PROTEIN_IDENTIFIER_LOOKUP_SCHEMA)
        .filter(
            pl.col('key_value').is_not_null()
            & (pl.col('key_value') != '')
            & pl.col('canonical_identifier').is_not_null()
            & (pl.col('canonical_identifier') != '')
        )
        .unique()
    )
    ambiguous_keys = (
        lookup.group_by(
            [
                'key_identifier_type_id',
                'key_value',
                'taxonomy_id',
                'canonical_identifier_type_id',
            ]
        )
        .agg(
            pl.col('canonical_identifier')
            .n_unique()
            .alias('canonical_identifier_count')
        )
        .filter(pl.col('canonical_identifier_count') > 1)
        .select(
            [
                'key_identifier_type_id',
                'key_value',
                'taxonomy_id',
                'canonical_identifier_type_id',
            ]
        )
    )
    if ambiguous_keys.is_empty():
        return (
            lookup,
            pl.DataFrame(schema=PROTEIN_IDENTIFIER_LOOKUP_SCHEMA),
            identifier_types,
        )

    join_keys = [
        'key_identifier_type_id',
        'key_value',
        'taxonomy_id',
        'canonical_identifier_type_id',
    ]
    ambiguous = lookup.join(ambiguous_keys, on=join_keys, how='semi')
    unambiguous = lookup.join(ambiguous_keys, on=join_keys, how='anti')
    return unambiguous, ambiguous, identifier_types
