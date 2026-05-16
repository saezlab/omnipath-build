from __future__ import annotations

import polars as pl

from pypath.internals.cv_terms import IdentifierNamespaceCv, cv_term_label_accession


IDENTIFIER_TYPE_SCHEMA: dict[str, pl.DataType] = {
    'identifier_type_id': pl.UInt32,
    'name': pl.Utf8,
}

FALLBACK_IDENTIFIER_TYPE = 'Fallback'

IDENTIFIER_TYPE_NAMES: tuple[str, ...] = (
    cv_term_label_accession(IdentifierNamespaceCv.UNIPROT),
    cv_term_label_accession(IdentifierNamespaceCv.ENSEMBL),
    cv_term_label_accession(IdentifierNamespaceCv.ENTREZ),
    cv_term_label_accession(IdentifierNamespaceCv.HGNC),
    cv_term_label_accession(IdentifierNamespaceCv.GENE_NAME_PRIMARY),
    cv_term_label_accession(IdentifierNamespaceCv.GENE_NAME_SYNONYM),
    cv_term_label_accession(IdentifierNamespaceCv.UNIPROT_ENTRY_NAME),
    cv_term_label_accession(IdentifierNamespaceCv.CHEBI),
    cv_term_label_accession(IdentifierNamespaceCv.HMDB),
    cv_term_label_accession(IdentifierNamespaceCv.LIPIDMAPS),
    cv_term_label_accession(IdentifierNamespaceCv.SWISSLIPIDS),
    cv_term_label_accession(IdentifierNamespaceCv.PUBCHEM_COMPOUND),
    cv_term_label_accession(IdentifierNamespaceCv.STANDARD_INCHI_KEY),
    cv_term_label_accession(IdentifierNamespaceCv.CV_TERM_ACCESSION),
    FALLBACK_IDENTIFIER_TYPE,
    cv_term_label_accession(IdentifierNamespaceCv.NAME),
)

IDENTIFIER_TYPE_IDS: dict[str, int] = {
    name: index for index, name in enumerate(IDENTIFIER_TYPE_NAMES, start=1)
}


def identifier_type_id(name: str) -> int:
    try:
        return IDENTIFIER_TYPE_IDS[name]
    except KeyError as error:
        raise ValueError(f'Unknown resolver identifier type: {name!r}') from error


def identifier_type_rows(names: set[str] | None = None) -> list[dict[str, object]]:
    selected = set(IDENTIFIER_TYPE_NAMES if names is None else names)
    return [
        {'identifier_type_id': identifier_type_id(name), 'name': name}
        for name in IDENTIFIER_TYPE_NAMES
        if name in selected
    ]
