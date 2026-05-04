from __future__ import annotations

import re
from typing import Callable

from pypath.internals.cv_terms import IdentifierNamespaceCv
from pypath.internals.silver_schema import Entity as SilverEntity, Membership

ValidationFn = Callable[[str], bool]


def _regex(pattern: str, *, flags: int = 0) -> ValidationFn:
    compiled = re.compile(pattern, flags)
    return lambda value: bool(compiled.fullmatch(value))


def _non_empty(value: str) -> bool:
    return bool(value and value.strip())


UNIPROT_ACCESSION_RE = re.compile(
    r'^(?:'
    r'[OPQ][0-9][A-Z0-9]{3}[0-9]'
    r'|'
    r'[A-NR-Z][0-9](?:[A-Z][A-Z0-9]{2}[0-9]){1,2}'
    r')(?:-\d+)?$'
)

ENSEMBL_RE = re.compile(r'^ENS[A-Z0-9]*\d+(?:\.\d+)?$')
REFSEQ_PROTEIN_RE = re.compile(r'^(?:[NXYZAW]P|XP|YP|WP|NP|AP|XP)_\d+(?:\.\d+)?$')
REFSEQ_GENERIC_RE = re.compile(r'^[A-Z]{2}_[0-9]+(?:\.\d+)?$')
UNIPROT_ENTRY_NAME_RE = re.compile(r'^[A-Z0-9]+_[A-Z0-9]+$')
INCHI_KEY_RE = re.compile(r'^[A-Z]{14}-[A-Z]{10}-[A-Z]$')
CAS_RE = re.compile(r'^\d{1,7}-\d{2}-\d$')

VALIDATORS: dict[str, ValidationFn] = {
    str(IdentifierNamespaceCv.UNIPROT): lambda value: bool(UNIPROT_ACCESSION_RE.fullmatch(value)),
    str(IdentifierNamespaceCv.UNIPROT_TREMBL): lambda value: bool(UNIPROT_ACCESSION_RE.fullmatch(value)),
    str(IdentifierNamespaceCv.UNIPARC): _regex(r'^UPI[A-F0-9]{10}$'),
    str(IdentifierNamespaceCv.UNIPROT_ENTRY_NAME): lambda value: bool(UNIPROT_ENTRY_NAME_RE.fullmatch(value)),
    str(IdentifierNamespaceCv.ENTREZ): _regex(r'^\d+$'),
    str(IdentifierNamespaceCv.ENSEMBL): lambda value: bool(ENSEMBL_RE.fullmatch(value)),
    str(IdentifierNamespaceCv.HGNC): _regex(r'^(?:HGNC:)?\d+$'),
    str(IdentifierNamespaceCv.REFSEQ): lambda value: bool(REFSEQ_GENERIC_RE.fullmatch(value)),
    str(IdentifierNamespaceCv.REFSEQ_PROTEIN): lambda value: bool(REFSEQ_PROTEIN_RE.fullmatch(value)),
    str(IdentifierNamespaceCv.CHEBI): _regex(r'^(?:CHEBI:)?\d+$'),
    str(IdentifierNamespaceCv.PUBCHEM): _regex(r'^\d+$'),
    str(IdentifierNamespaceCv.PUBCHEM_COMPOUND): _regex(r'^\d+$'),
    str(IdentifierNamespaceCv.CHEMBL): _regex(r'^CHEMBL\d+$'),
    str(IdentifierNamespaceCv.CHEMBL_COMPOUND): _regex(r'^CHEMBL\d+$'),
    str(IdentifierNamespaceCv.CHEMBL_TARGET): _regex(r'^CHEMBL\d+$'),
    str(IdentifierNamespaceCv.DRUGBANK): _regex(r'^DB\d+$'),
    str(IdentifierNamespaceCv.KEGG_COMPOUND): _regex(r'^(?:C|D|G)\d{5}$'),
    str(IdentifierNamespaceCv.CAS): lambda value: bool(CAS_RE.fullmatch(value)),
    str(IdentifierNamespaceCv.PDB): _regex(r'^[0-9][A-Z0-9]{3}$'),
    str(IdentifierNamespaceCv.ALPHAFOLDDB): _regex(r'^AF-[A-Z0-9]+-F\d+$'),
    str(IdentifierNamespaceCv.INTACT): _regex(r'^(?:EBI-|IM-).+$'),
    str(IdentifierNamespaceCv.BIOGRID): _regex(r'^\d+$'),
    str(IdentifierNamespaceCv.COMPLEXPORTAL): _regex(r'^CPX-\d+$', flags=re.IGNORECASE),
    str(IdentifierNamespaceCv.MIRBASE): _regex(r'^(?:MI|MIMAT)\d+$'),
    str(IdentifierNamespaceCv.RNACENTRAL): _regex(r'^URS[0-9A-F]{10}(?:_\d+)?$'),
    str(IdentifierNamespaceCv.GENBANK_NUCL_GI): _regex(r'^\d+$'),
    str(IdentifierNamespaceCv.GENBANK_PROTEIN_GI): _regex(r'^\d+$'),
    str(IdentifierNamespaceCv.GENBANK_IDENTIFIER): _non_empty,
    str(IdentifierNamespaceCv.LIPIDMAPS): _regex(r'^LM[A-Z0-9]+$'),
    str(IdentifierNamespaceCv.HMDB): _regex(r'^HMDB\d{5,8}$'),
    str(IdentifierNamespaceCv.METANETX): _regex(r'^MNXM\d+$'),
    str(IdentifierNamespaceCv.BINDINGDB): _regex(r'^\d+$'),
    str(IdentifierNamespaceCv.GUIDETOPHARMA): _regex(r'^\d+$'),
    str(IdentifierNamespaceCv.SWISSLIPIDS): _regex(r'^(?:SLM|SLN|SLS):\d+$'),
    str(IdentifierNamespaceCv.CORUM): _regex(r'^\d+$'),
    str(IdentifierNamespaceCv.ZINC): _regex(r'^ZINC\d+$', flags=re.IGNORECASE),
    str(IdentifierNamespaceCv.REACTOME_STABLE_ID): _regex(r'^R-[A-Z]{3}-\d+(?:-\d+)?$'),
    str(IdentifierNamespaceCv.REACTOME_ID): _regex(r'^\d+$'),
    # CV term accessions are an umbrella identifier namespace used for many
    # ontology-native IDs. Most OBO IDs are CURIE-like (GO:..., HP:..., MI:...),
    # but pathway ontologies use native compact accessions such as WP1 or
    # R-HSA-199420. Require a single non-empty token, but do not require a colon.
    str(IdentifierNamespaceCv.CV_TERM_ACCESSION): _regex(r'^\S+$'),
    str(IdentifierNamespaceCv.NCBI_TAX_ID): _regex(r'^-?\d+$'),
    str(IdentifierNamespaceCv.SMILES): _non_empty,
    str(IdentifierNamespaceCv.STANDARD_INCHI): lambda value: value.startswith('InChI='),
    str(IdentifierNamespaceCv.STANDARD_INCHI_KEY): lambda value: bool(INCHI_KEY_RE.fullmatch(value)),
    str(IdentifierNamespaceCv.PUBMED): _regex(r'^\d+$'),
    str(IdentifierNamespaceCv.PUBMED_CENTRAL): _regex(r'^(?:PMC)?\d+$'),
    str(IdentifierNamespaceCv.DOI): _regex(r'^(?:(?:https?://(?:dx\.)?doi\.org/)|doi:)?10\.\S+$', flags=re.IGNORECASE),
    str(IdentifierNamespaceCv.PATENT_NUMBER): _non_empty,
    str(IdentifierNamespaceCv.PHENOL_EXPLORER): _regex(r'^\d+$'),
    str(IdentifierNamespaceCv.FOODB): _regex(r'^(?:FOOD|FDB|FOODB)\d+$'),
    str(IdentifierNamespaceCv.PTFI): _non_empty,
    str(IdentifierNamespaceCv.FOODON): _regex(r'^FOODON:\d+$'),
    str(IdentifierNamespaceCv.CELLINKER): _non_empty,
    str(IdentifierNamespaceCv.RECON2): _non_empty,
    str(IdentifierNamespaceCv.CELLPHONEDB): _non_empty,
    str(IdentifierNamespaceCv.CELLCHAT): _non_empty,
    str(IdentifierNamespaceCv.MEBOCOST): _non_empty,
    str(IdentifierNamespaceCv.WIKIPATHWAYS): _regex(r'^WP\d+$'),
    str(IdentifierNamespaceCv.WIKIPATHWAYS_VERSION): _regex(r'^WP\d+(?:_r\d+)?$'),
    str(IdentifierNamespaceCv.CHEMBL_INTERNAL_ID): _regex(r'^\d+$'),
    str(IdentifierNamespaceCv.CHEMBL_ASSAY): _regex(r'^CHEMBL\d+$'),
    str(IdentifierNamespaceCv.CHEMBL_DOCUMENT): _regex(r'^CHEMBL\d+$'),
    str(IdentifierNamespaceCv.CHEMBL_MECHANISM): _regex(r'^\d+$'),
    str(IdentifierNamespaceCv.CHEMBL_ACTIVITY): _regex(r'^\d+$'),
    str(IdentifierNamespaceCv.CHEMBL_COMPONENT_ID): _regex(r'^\d+$'),
}


SKIP_VALIDATION: frozenset[str] = frozenset({
    str(IdentifierNamespaceCv.KEGG),
    str(IdentifierNamespaceCv.IMEX),
    str(IdentifierNamespaceCv.DIP),
    str(IdentifierNamespaceCv.FLYBASE),
    str(IdentifierNamespaceCv.RFAM),
    str(IdentifierNamespaceCv.IPI),
    str(IdentifierNamespaceCv.BIND),
    str(IdentifierNamespaceCv.GENE_NAME_PRIMARY),
    str(IdentifierNamespaceCv.GENE_NAME_SYNONYM),
    str(IdentifierNamespaceCv.NAME),
    str(IdentifierNamespaceCv.SYNONYM),
    str(IdentifierNamespaceCv.SYSTEMATIC_NAME),
    str(IdentifierNamespaceCv.ABBREVIATED_NAME),
    str(IdentifierNamespaceCv.IUPAC_NAME),
    str(IdentifierNamespaceCv.IUPAC_TRADITIONAL_NAME),
    str(IdentifierNamespaceCv.MOLECULAR_FORMULA),
    str(IdentifierNamespaceCv.INN),
    str(IdentifierNamespaceCv.SCIENTIFIC_NAME),
})


def _identifier_type_string(value: object) -> str:
    if isinstance(value, IdentifierNamespaceCv):
        return str(value)
    return str(value)


def _validate_single_identifier(identifier_type: object, identifier_value: object, *, context: str) -> None:
    if identifier_value is None:
        return
    type_str = _identifier_type_string(identifier_type)
    if type_str in SKIP_VALIDATION:
        return

    validator = VALIDATORS.get(type_str)
    if validator is None:
        return

    value_str = str(identifier_value).strip()
    if not validator(value_str):
        raise ValueError(
            f'Invalid identifier shape in {context}: type={type_str!r}, value={value_str!r}'
        )


def validate_entity_identifier_shapes(entity: SilverEntity, *, context: str) -> None:
    for identifier in entity.identifiers or []:
        _validate_single_identifier(identifier.type, identifier.value, context=context)

    for idx, membership in enumerate(entity.membership or []):
        validate_entity_identifier_shapes(
            membership.member,
            context=f'{context}.membership[{idx}]',
        )
