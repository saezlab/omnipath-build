from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from pypath.internals.cv_terms import (
    BiologicalRoleCv,
    ExperimentalRoleCv,
    IdentifierNamespaceCv,
    InteractionMetadataCv,
    ParticipantMetadataCv,
)
from pypath.internals.cv_terms.entity_types import EntityTypeCv

from omnipath_build.gold.utils.cv_terms import format_cv_term
from omnipath_build.shared_interaction_schema import (
    NEGATIVE_SIGN_ACCESSIONS,
    POSITIVE_SIGN_ACCESSIONS,
    SOURCE_ROLE_ACCESSIONS,
    TARGET_ROLE_ACCESSIONS,
)

RecordClass = Literal[
    'entity_only',
    'membership_relation',
    'interaction_relation',
    'ontology_term_only',
    'entity_with_ontology_backing',
    'ignored',
]

AnnotationBucket = Literal[
    'record_attribute',
    'subject_attribute',
    'object_attribute',
    'evidence',
    'annotation_relation',
    'ontology_term_only',
    'ignore',
]


INTERACTION_LIKE_TYPES = {
    str(EntityTypeCv.INTERACTION),
    str(EntityTypeCv.REACTION),
    str(EntityTypeCv.CATALYSIS),
    str(EntityTypeCv.CONTROL),
    str(EntityTypeCv.DEGRADATION),
}

MEMBERSHIP_RULES: dict[str, dict[str, str]] = {
    str(EntityTypeCv.COMPLEX): {
        'predicate': 'has_component',
        'relation_category': 'membership',
    },
    str(EntityTypeCv.PROTEIN_FAMILY): {
        'predicate': 'has_member',
        'relation_category': 'membership',
    },
    str(EntityTypeCv.PATHWAY): {
        'predicate': 'has_participant',
        'relation_category': 'membership',
    },
    str(EntityTypeCv.REACTION): {
        'predicate': 'has_participant',
        'relation_category': 'membership',
    },
}

EVIDENCE_IDENTIFIER_TERMS = {
    str(IdentifierNamespaceCv.PUBMED),
    str(IdentifierNamespaceCv.PUBMED_CENTRAL),
    str(IdentifierNamespaceCv.DOI),
    str(IdentifierNamespaceCv.PATENT_NUMBER),
}

ROLE_TERMS = (
    {str(term) for term in BiologicalRoleCv}
    | {str(term) for term in ExperimentalRoleCv}
    | {str(ParticipantMetadataCv.SOURCE), str(ParticipantMetadataCv.TARGET)}
)

SIGN_POSITIVE_TERMS = {str(term) for term in POSITIVE_SIGN_ACCESSIONS}
SIGN_NEGATIVE_TERMS = {str(term) for term in NEGATIVE_SIGN_ACCESSIONS}
ONTOLOGY_IDENTIFIER_TERM = str(IdentifierNamespaceCv.CV_TERM_ACCESSION)
TAXONOMY_IDENTIFIER_TERM = str(IdentifierNamespaceCv.NCBI_TAX_ID)
STOICHIOMETRY_TERM = str(ParticipantMetadataCv.STOICHIOMETRY)
CV_TERM_ENTITY_TYPE = str(getattr(EntityTypeCv, 'CV_TERM', 'CV_TERM'))


@dataclass(frozen=True)
class PredicateRule:
    predicate: str
    relation_category: str


@dataclass(frozen=True)
class AnnotationDisposition:
    bucket: AnnotationBucket
    predicate: str | None = None


@dataclass(frozen=True)
class OntologyDisposition:
    materialize_object_entity: bool
    emit_ontology_term: bool = False


@dataclass(frozen=True)
class ProjectedAttribute:
    term: str
    value: str | None
    unit: str | None


@dataclass(frozen=True)
class AnnotationContext:
    record_class: RecordClass
    parent_type: str | None = None
    is_membership: bool = False
    participant_side: Literal['subject', 'object', 'record'] = 'record'


NAME_IDENTIFIER_TERMS = {
    str(getattr(IdentifierNamespaceCv, 'NAME', 'NAME')),
    str(getattr(IdentifierNamespaceCv, 'GENE_NAME_PRIMARY', 'GENE_NAME_PRIMARY')),
    str(getattr(IdentifierNamespaceCv, 'SYSTEMATIC_NAME', 'SYSTEMATIC_NAME')),
    str(getattr(IdentifierNamespaceCv, 'SCIENTIFIC_NAME', 'SCIENTIFIC_NAME')),
}

SYNONYM_IDENTIFIER_TERMS = {
    str(getattr(IdentifierNamespaceCv, 'SYNONYM', 'SYNONYM')),
    str(getattr(IdentifierNamespaceCv, 'GENE_NAME_SYNONYM', 'GENE_NAME_SYNONYM')),
    str(getattr(IdentifierNamespaceCv, 'ABBREVIATED_NAME', 'ABBREVIATED_NAME')),
}

DEFINITION_HINT_TERMS = {
    str(getattr(IdentifierNamespaceCv, 'DEFINITION', 'DEFINITION')),
}


def classify_silver_record(row: dict[str, Any]) -> RecordClass:
    row_type = string_or_none(row.get('type'))
    identifiers = row.get('identifiers') or []
    memberships = row.get('membership') or []

    if row_type is None and not identifiers and not memberships:
        return 'ignored'
    if row_type in INTERACTION_LIKE_TYPES and memberships:
        return 'interaction_relation'
    if row_type == CV_TERM_ENTITY_TYPE:
        return 'ontology_term_only'
    if has_ontology_backing(row):
        return 'entity_with_ontology_backing'
    if memberships:
        return 'membership_relation'
    if identifiers or row_type is not None:
        return 'entity_only'
    return 'ignored'


def has_ontology_backing(row: dict[str, Any]) -> bool:
    for ident in row.get('identifiers') or []:
        if string_or_none(ident.get('type')) == ONTOLOGY_IDENTIFIER_TERM and string_or_none(ident.get('value')):
            return True
    return False


def source_identifier_rows(entity_pk: int, row: dict[str, Any], source: str) -> list[dict[str, str | int | None]]:
    out: list[dict[str, str | int | None]] = []
    for ident in row.get('identifiers') or []:
        ident_type = string_or_none(ident.get('type'))
        ident_value = string_or_none(ident.get('value'))
        if ident_value is None:
            continue
        out.append({
            'entity_pk': entity_pk,
            'identifier': ident_value,
            'identifier_type': format_cv_term(ident_type),
            'source': source,
        })
    return out


def predicate_for_membership(parent_type: str | None, membership: dict[str, Any]) -> PredicateRule:
    del membership
    rule = MEMBERSHIP_RULES.get(parent_type or '') or {
        'predicate': 'has_member',
        'relation_category': 'membership',
    }
    return PredicateRule(
        predicate=rule['predicate'],
        relation_category=rule['relation_category'],
    )


def predicate_for_interaction(
    row: dict[str, Any],
    ordered_participants: list[dict[str, Any]],
) -> PredicateRule:
    row_type = string_or_none(row.get('type'))
    annotations = row.get('annotations') or []

    participant_annotations: list[dict[str, Any]] = []
    for p in ordered_participants:
        participant_annotations.extend(p.get('membership_annotations') or [])

    sign = interaction_sign(annotations, participant_annotations)

    if row_type == str(EntityTypeCv.INTERACTION):
        if sign > 0:
            return PredicateRule('positively_regulates', 'interaction')
        if sign < 0:
            return PredicateRule('negatively_regulates', 'interaction')
        return PredicateRule('interacts_with', 'interaction')
    if row_type in {str(EntityTypeCv.CONTROL), str(EntityTypeCv.CATALYSIS), str(EntityTypeCv.DEGRADATION)}:
        if sign > 0:
            return PredicateRule('positively_regulates', 'interaction')
        if sign < 0:
            return PredicateRule('negatively_regulates', 'interaction')
        return PredicateRule('regulates', 'interaction')
    if row_type == str(EntityTypeCv.REACTION):
        if has_role_ordering(ordered_participants):
            return PredicateRule('transforms_to', 'interaction')
        return PredicateRule('interacts_with', 'interaction')
    if sign > 0:
        return PredicateRule('positively_regulates', 'interaction')
    if sign < 0:
        return PredicateRule('negatively_regulates', 'interaction')
    return PredicateRule('related_to', 'interaction')


def relation_category_for_predicate(predicate: str) -> str:
    if predicate in {'has_component', 'has_member', 'has_participant'}:
        return 'membership'
    if predicate in {'has_annotation', 'associated_with', 'involved_in'}:
        return 'annotation'
    return 'interaction'


def classify_annotation(annotation: dict[str, Any], context: AnnotationContext) -> AnnotationDisposition:
    term = string_or_none(annotation.get('term'))
    value = string_or_none(annotation.get('value'))
    unit = string_or_none(annotation.get('units'))

    if term is None:
        return AnnotationDisposition('ignore')
    if term in ROLE_TERMS:
        if context.participant_side == 'subject':
            return AnnotationDisposition('subject_attribute')
        if context.participant_side == 'object':
            return AnnotationDisposition('object_attribute')
        return AnnotationDisposition('record_attribute')
    if term == STOICHIOMETRY_TERM:
        return AnnotationDisposition('object_attribute')
    if term == TAXONOMY_IDENTIFIER_TERM:
        return AnnotationDisposition('ignore')
    if term in EVIDENCE_IDENTIFIER_TERMS:
        return AnnotationDisposition('evidence')
    if is_pure_ontology_term_annotation(annotation):
        return AnnotationDisposition('annotation_relation', predicate=annotation_predicate(annotation))
    if term == ONTOLOGY_IDENTIFIER_TERM and value and unit is None:
        return AnnotationDisposition('ontology_term_only')
    if context.participant_side == 'subject':
        return AnnotationDisposition('subject_attribute')
    if context.participant_side == 'object':
        return AnnotationDisposition('object_attribute')
    return AnnotationDisposition('record_attribute')


def materialize_ontology_object(annotation: dict[str, Any], context: AnnotationContext) -> OntologyDisposition:
    del context
    if is_pure_ontology_term_annotation(annotation):
        return OntologyDisposition(materialize_object_entity=True)
    return OntologyDisposition(materialize_object_entity=False)


def is_pure_ontology_term_annotation(annotation: dict[str, Any]) -> bool:
    term = string_or_none(annotation.get('term'))
    value = string_or_none(annotation.get('value'))
    unit = string_or_none(annotation.get('units'))
    return term == ONTOLOGY_IDENTIFIER_TERM and value is not None and unit is None


def annotation_predicate(annotation: dict[str, Any]) -> str:
    value = string_or_none(annotation.get('value')) or ''
    prefix = value.split(':', 1)[0].upper() if ':' in value else ''
    if prefix == 'GO':
        return 'has_annotation'
    if prefix in {'HP', 'MONDO'}:
        return 'associated_with'
    value_upper = value.upper()
    if prefix in {'REACTOME', 'WP'} or value_upper.startswith(('WP', 'R-')):
        return 'involved_in'
    return 'has_annotation'


def extract_taxonomy_id(row: dict[str, Any]) -> str | None:
    for ident in row.get('identifiers') or []:
        if string_or_none(ident.get('type')) == TAXONOMY_IDENTIFIER_TERM and string_or_none(ident.get('value')):
            return string_or_none(ident.get('value'))
    for annotation in row.get('annotations') or []:
        if string_or_none(annotation.get('term')) == TAXONOMY_IDENTIFIER_TERM and string_or_none(annotation.get('value')):
            return string_or_none(annotation.get('value'))

    member_tax_ids: set[str] = set()
    for membership in row.get('membership') or []:
        member = membership.get('member') or {}
        for annotation in member.get('annotations') or []:
            if string_or_none(annotation.get('term')) == TAXONOMY_IDENTIFIER_TERM and string_or_none(annotation.get('value')):
                member_tax_ids.add(string_or_none(annotation.get('value')) or '')
    member_tax_ids.discard('')
    if len(member_tax_ids) == 1:
        return next(iter(member_tax_ids))
    return None


def normalize_attribute_term(term: str | None) -> str | None:
    if term is None:
        return None
    if is_cv_term_accession(term):
        return format_cv_term(term)
    return term


def projected_attribute(annotation: dict[str, Any]) -> ProjectedAttribute | None:
    term = normalize_attribute_term(string_or_none(annotation.get('term')))
    if term is None:
        return None
    return ProjectedAttribute(
        term=term,
        value=string_or_none(annotation.get('value')),
        unit=normalize_attribute_term(string_or_none(annotation.get('units'))),
    )


def interaction_sign(
    record_annotations: list[dict[str, Any]],
    participant_annotations: list[dict[str, Any]] | None = None,
) -> int:
    all_annotations = [*record_annotations, *(participant_annotations or [])]
    for annotation in all_annotations:
        term = string_or_none(annotation.get('term'))
        value = (string_or_none(annotation.get('value')) or '').upper()
        if term in SIGN_POSITIVE_TERMS:
            return 1
        if term in SIGN_NEGATIVE_TERMS:
            return -1
        if term == str(InteractionMetadataCv.CONTROL_TYPE):
            if 'ACTIV' in value or 'POSITIVE' in value:
                return 1
            if 'INHIB' in value or 'NEGATIVE' in value or 'REPRESS' in value:
                return -1
    return 0


def order_interaction_participants(participants: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if len(participants) != 2:
        return participants
    first, second = participants
    first_terms = annotation_terms(first.get('membership_annotations') or [])
    second_terms = annotation_terms(second.get('membership_annotations') or [])
    if first_terms & SOURCE_ROLE_ACCESSIONS and second_terms & TARGET_ROLE_ACCESSIONS:
        return [first, second]
    if second_terms & SOURCE_ROLE_ACCESSIONS and first_terms & TARGET_ROLE_ACCESSIONS:
        return [second, first]
    return participants


def has_role_ordering(participants: list[dict[str, Any]]) -> bool:
    if len(participants) != 2:
        return False
    first_terms = annotation_terms(participants[0].get('membership_annotations') or [])
    second_terms = annotation_terms(participants[1].get('membership_annotations') or [])
    return bool(
        (first_terms & SOURCE_ROLE_ACCESSIONS and second_terms & TARGET_ROLE_ACCESSIONS)
        or (second_terms & SOURCE_ROLE_ACCESSIONS and first_terms & TARGET_ROLE_ACCESSIONS)
    )


def annotation_terms(annotations: list[dict[str, Any]]) -> set[str]:
    return {term for annotation in annotations if (term := string_or_none(annotation.get('term'))) is not None}


def infer_ontology_term_row(row: dict[str, Any], source: str) -> dict[str, Any] | None:
    term_id = ontology_term_id_from_row(row)
    if term_id is None:
        return None

    label = None
    synonyms: list[str] = []
    definition = None
    for ident in row.get('identifiers') or []:
        ident_type = string_or_none(ident.get('type'))
        ident_value = string_or_none(ident.get('value'))
        if ident_value is None:
            continue
        if ident_type in NAME_IDENTIFIER_TERMS and label is None:
            label = ident_value
        elif ident_type in SYNONYM_IDENTIFIER_TERMS:
            synonyms.append(ident_value)
        elif ident_type in DEFINITION_HINT_TERMS and definition is None:
            definition = ident_value

    for annotation in row.get('annotations') or []:
        value = string_or_none(annotation.get('value'))
        if value is None:
            continue
        if label is None and not is_cv_term_accession(value):
            label = value
            continue
        if definition is None and len(value) > 80:
            definition = value

    return {
        'term_id': term_id,
        'ontology_prefix': term_id.split(':', 1)[0] if ':' in term_id else None,
        'label': label,
        'definition': definition,
        'synonyms': sorted(set(synonyms)) or None,
        'source': source,
    }


def ontology_term_id_from_row(row: dict[str, Any]) -> str | None:
    for ident in row.get('identifiers') or []:
        if string_or_none(ident.get('type')) == ONTOLOGY_IDENTIFIER_TERM and string_or_none(ident.get('value')):
            return string_or_none(ident.get('value'))
    for annotation in row.get('annotations') or []:
        if is_pure_ontology_term_annotation(annotation):
            return string_or_none(annotation.get('value'))
    return None


def is_cv_term_accession(value: str) -> bool:
    if ':' not in value or value.startswith('http'):
        return False
    prefix, suffix = value.split(':', 1)
    return bool(prefix) and bool(suffix.strip()) and ' ' not in suffix


def string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
