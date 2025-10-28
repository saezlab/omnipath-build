from __future__ import annotations

from enum import Enum
from typing import Optional, Type

from pypath.inputs.mitab import (
    MitabInteraction,
    mitab_field_list,
    mitab_parse_identifiers,
    mitab_parse_mi_term,
)

from omnipath_build.utils.cv_term_enums import (
    EntityTypeCv,
    IdentifierNamespaceCv,
    BiologicalRoleCv,
    ExperimentalRoleCv,
    IdentificationMethodCv,
    InteractionTypeCv,
    DetectionMethodCv,
    CausalMechanismCv,
    CausalStatementCv,
    ComplexExpansionCv,
    ReferenceTypeCv,
)
from omnipath_build.utils.silver_schema import Identifier, SilverEntity, SilverInteraction, Reference

MITAB_DB_TO_IDENTIFIER_NAMESPACE: dict[str, IdentifierNamespaceCv] = {
    'uniprotkb': IdentifierNamespaceCv.UNIPROT,
    'chebi': IdentifierNamespaceCv.CHEBI,
    'signor': IdentifierNamespaceCv.SIGNOR,
    'entrez gene/locuslink': IdentifierNamespaceCv.ENTREZ,
    'entrezgene/locuslink': IdentifierNamespaceCv.ENTREZ,
    'entrezgene': IdentifierNamespaceCv.ENTREZ,
    'ensembl': IdentifierNamespaceCv.ENSEMBL,
    'refseq': IdentifierNamespaceCv.REFSEQ,
    'hgnc': IdentifierNamespaceCv.HGNC,
    'hgnc.symbol': IdentifierNamespaceCv.HGNC,
    'pubchem': IdentifierNamespaceCv.PUBCHEM,
    'chembl': IdentifierNamespaceCv.CHEMBL,
    'pdb': IdentifierNamespaceCv.PDB,
    'alphafolddb': IdentifierNamespaceCv.ALPHAFOLDDB,
    'intact': IdentifierNamespaceCv.INTACT,
    'biogrid': IdentifierNamespaceCv.BIOGRID,
    'complexportal': IdentifierNamespaceCv.COMPLEXPORTAL,
    'complex portal': IdentifierNamespaceCv.COMPLEXPORTAL,
}

REFERENCE_DB_TO_TYPE: dict[str, ReferenceTypeCv] = {
    'pubmed': ReferenceTypeCv.PUBMED,
    'pmid': ReferenceTypeCv.PUBMED,
    'doi': ReferenceTypeCv.DOI,
    'pmcid': ReferenceTypeCv.PUBMED_CENTRAL,
    'biorxiv': ReferenceTypeCv.BIORXIV,
}


def _mi_to_enum(
    field: str | None,
    enum_cls: Type[Enum],
) -> Optional[Enum]:
    if not field or field == '-':
        return None
    term = mitab_parse_mi_term(field)
    if not term:
        return None

    mi_id = term.get('mi_id')
    if not mi_id:
        return None

    try:
        return enum_cls(mi_id)
    except ValueError:
        return None


def _parse_identifiers(*fields: str) -> list[Identifier] | None:
    seen: set[tuple[str, str]] = set()
    identifiers: list[Identifier] = []

    for field in fields:
        if not field or field == '-':
            continue
        for item in mitab_parse_identifiers(field):
            database = item.get('database')
            value = item.get('id')
            if not database or not value:
                continue
            namespace = MITAB_DB_TO_IDENTIFIER_NAMESPACE.get(database.lower())
            if not namespace:
                continue
            value = value.strip().strip('"')
            key = (namespace.value, value)
            if key in seen:
                continue
            seen.add(key)
            identifiers.append(Identifier(type=namespace, value=value))

    return identifiers or None


def _parse_synonyms(field: str) -> tuple[str | None, list[str] | None]:
    names = [name for name in mitab_field_list(field) if name]
    if not names:
        return None, None

    name = names[0]
    synonyms = names[1:] if len(names) > 1 else None
    return name, synonyms


def _parse_role(field: str, enum_cls):
    return _mi_to_enum(field, enum_cls)


def _parse_identification_method(field: str) -> IdentificationMethodCv | None:
    return _mi_to_enum(field, IdentificationMethodCv)


def _parse_stoichiometry(field: str) -> float | None:
    if not field or field == '-':
        return None
    parts = field.split('|')
    for part in parts:
        if ':' in part:
            _, value = part.split(':', 1)
        else:
            value = part
        value = value.strip().strip('"')
        try:
            return float(value)
        except ValueError:
            continue
    return None


def _infer_entity_type(
    identifiers: list[Identifier] | None,
    fallback: EntityTypeCv = EntityTypeCv.PROTEIN,
) -> EntityTypeCv:
    if identifiers:
        id_types = {identifier.type for identifier in identifiers}
        if id_types & {
            IdentifierNamespaceCv.CHEBI,
            IdentifierNamespaceCv.PUBCHEM,
        }:
            return EntityTypeCv.SMALL_MOLECULE
    return fallback


def _parse_references(field: str) -> list[Reference] | None:
    if not field or field == '-':
        return None

    references: list[Reference] = []

    for item in field.split('|'):
        if not item or item == '-':
            continue
        if ':' in item:
            db, value = item.split(':', 1)
        else:
            db, value = 'pubmed', item
        db_lower = db.strip().lower()
        ref_type = REFERENCE_DB_TO_TYPE.get(db_lower)
        if not ref_type:
            continue
        value = value.strip().strip('"')
        if not value:
            continue
        references.append(Reference(type=ref_type, value=value))

    return references or None


def _parse_key_value_annotations(field: str) -> list[dict] | None:
    if not field or field == '-':
        return None

    annotations: list[dict] = []

    for item in field.split('|'):
        if not item or item == '-':
            continue
        if ':' in item:
            key, value = item.split(':', 1)
        else:
            key, value = 'annotation', item
        value = value.strip().strip('"')
        key = key.strip().strip('"')
        if not value:
            continue
        annotations.append({'key': key, 'value': value})

    return annotations or None


def _parse_interaction_id_values(field: str) -> list[str]:
    if not field or field == '-':
        return []

    values: list[str] = []
    for item in field.split('|'):
        if not item or item == '-':
            continue
        if ':' in item:
            _, value = item.split(':', 1)
        else:
            value = item
        value = value.strip().strip('"')
        if value:
            values.append(value)
    return values


def _build_entity(record: MitabInteraction, role_prefix: str, source: str) -> SilverEntity:
    identifiers = _parse_identifiers(
        getattr(record, f'id_{role_prefix}', None),
        getattr(record, f'alt_ids_{role_prefix}', None),
        getattr(record, f'xrefs_{role_prefix}', None),
    )

    entity_type = _mi_to_enum(
        getattr(record, f'interactor_type_{role_prefix}', None),
        EntityTypeCv,
    )
    if not entity_type:
        entity_type = _infer_entity_type(identifiers)

    name, synonyms = _parse_synonyms(getattr(record, f'aliases_{role_prefix}', None))

    # Add name and synonyms to identifiers list
    if name:
        identifiers.append(Identifier(type=IdentifierNamespaceCv.NAME, value=name))
    if synonyms:
        for syn in synonyms:
            identifiers.append(Identifier(type=IdentifierNamespaceCv.SYNONYM, value=syn))

    return SilverEntity(
        source=source,
        entity_type=entity_type,
        identifiers=identifiers if identifiers else None,
        biological_role=_parse_role(
            getattr(record, f'biological_role_{role_prefix}', None),
            BiologicalRoleCv,
        ),
        experimental_role=_parse_role(
            getattr(record, f'experimental_role_{role_prefix}', None),
            ExperimentalRoleCv,
        ),
        stoichiometry=_parse_stoichiometry(getattr(record, f'stoichiometry_{role_prefix}', None)),
        identification_method=_parse_identification_method(
            getattr(record, f'identification_method_{role_prefix}', None),
        ),
    )


def mitab_to_silver_interaction(
    record: MitabInteraction,
    source: str,
    *,
    direction_mode: str = 'undirected',
    fallback_interaction_type: InteractionTypeCv | None = None,
    fallback_detection_method: DetectionMethodCv | None = None,
) -> SilverInteraction:
    entity_a = _build_entity(record, 'a', source)
    entity_b = _build_entity(record, 'b', source)

    interaction_type = _mi_to_enum(record.interaction_types, InteractionTypeCv)
    if interaction_type is None:
        interaction_type = fallback_interaction_type

    detection_method = _mi_to_enum(record.detection_methods, DetectionMethodCv)
    if detection_method is None:
        detection_method = fallback_detection_method

    causal_statement = _mi_to_enum(record.causal_statement, CausalStatementCv)
    causal_mechanism = _mi_to_enum(record.causal_regulatory_mechanism, CausalMechanismCv)
    complex_expansion = _mi_to_enum(record.complex_expansion, ComplexExpansionCv)

    direction: Optional[str]
    if direction_mode == 'causal':
        direction = 'a_to_b' if causal_statement else 'undirected'

    annotations: list[dict] = []
    for field in (
        record.confidence_scores,
        record.annotations_interaction,
        record.parameters,
    ):
        parsed = _parse_key_value_annotations(field)
        if parsed:
            annotations.extend(parsed)

    for interaction_id in _parse_interaction_id_values(record.interaction_ids):
        annotations.append({'key': 'interaction_id', 'value': interaction_id})

    annotations = annotations or None

    references = _parse_references(record.pmids)

    sentence = None
    if annotations:
        for annotation in annotations:
            if annotation.get('key', '').lower() == 'comment':
                sentence = annotation.get('value')
                break

    return SilverInteraction(
        source=source,
        entity_a=entity_a,
        entity_b=entity_b,
        interaction_type=interaction_type,
        detection_method=detection_method,
        direction=direction,
        causal_mechanism=causal_mechanism,
        causal_statement=causal_statement,
        sentence=sentence,
        complex_expansion=complex_expansion,
        interaction_annotations=annotations,
        references=references,
    )
