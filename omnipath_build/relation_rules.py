"""Rules for projecting silver entity structures into graph predicates.

The ingest phase receives heterogeneous pypath entity records: protein
complexes, pathways, reactions, causal interactions, associations, and
ontology-like annotations all encode relationships in slightly different ways.
This module converts those structures into a small relation vocabulary used by
``relation_evidence`` and later by canonical graph relations.

The rules are conservative. Explicit source/target participant annotations
drive ordering when available. Positive and negative effect annotations drive
regulation predicates. Records without enough semantic detail fall back to
association or generic interaction predicates instead of inventing direction.
"""

from __future__ import annotations

from typing import Any
from dataclasses import dataclass
from collections.abc import Iterable

from pypath.internals.cv_terms import (
    CvEnum,
    BiologicalRoleCv,
    ExperimentalRoleCv,
    IdentifierNamespaceCv,
    InteractionMetadataCv,
    ParticipantMetadataCv,
)
from pypath.internals.cv_terms.entity_types import EntityTypeCv
from omnipath_build.shared_interaction_schema import (
    SOURCE_ROLE_ACCESSIONS,
    TARGET_ROLE_ACCESSIONS,
    NEGATIVE_SIGN_ACCESSIONS,
    POSITIVE_SIGN_ACCESSIONS,
)

def _iter_cv_subclasses(base: type) -> Iterable[type]:
    for subcls in base.__subclasses__():
        yield subcls
        yield from _iter_cv_subclasses(subcls)


def _humanize_enum_name(name: str) -> str:
    return name.replace('_', ' ').title()


def _build_cv_label_map() -> dict[str, str]:
    labels: dict[str, str] = {}
    for enum_cls in _iter_cv_subclasses(CvEnum):
        for member in enum_cls:
            labels.setdefault(str(member), _humanize_enum_name(member.name))
    return labels


CV_LABELS = _build_cv_label_map()


def format_cv_term(accession: str | None) -> str | None:
    """Return a CV accession with its label when the label is known."""

    if accession is None:
        return None
    label = CV_LABELS.get(accession)
    if label is None:
        return accession
    return f'{accession}:{label}'


INTERACTION_LIKE_TYPES = {
    str(EntityTypeCv.INTERACTION),
    str(EntityTypeCv.ASSOCIATION),
    str(EntityTypeCv.REACTION),
    str(EntityTypeCv.CATALYSIS),
    str(EntityTypeCv.CONTROL),
    str(EntityTypeCv.DEGRADATION),
    str(EntityTypeCv.TRANSPORT),
}

ASSOCIATION_PREDICATE = 'associated_with'
ASSOCIATION_CATEGORY = 'association'
INTERACTION_CATEGORY = 'interaction'
TRANSPORT_PREDICATE = 'transports'
TRANSPORTER_ENTITY_TYPES = {
    str(EntityTypeCv.PROTEIN),
    str(EntityTypeCv.GENE),
    str(EntityTypeCv.COMPLEX),
    str(EntityTypeCv.PROTEIN_FAMILY),
}

MEMBERSHIP_RULES: dict[str, str] = {
    str(EntityTypeCv.COMPLEX): 'has_member',
    str(EntityTypeCv.PROTEIN_FAMILY): 'has_member',
    str(EntityTypeCv.PATHWAY): 'has_participant',
    str(EntityTypeCv.REACTION): 'has_participant',
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


@dataclass(frozen=True)
class PredicateRule:
    """Chosen relation predicate and its high-level category."""

    predicate: str
    relation_category: str


def predicate_for_membership(
    parent_type: str | None,
    membership: dict[str, Any],
) -> PredicateRule:
    """Return the relation predicate for parent/member structures."""

    del membership
    return PredicateRule(
        MEMBERSHIP_RULES.get(parent_type or '', 'has_member'),
        ASSOCIATION_CATEGORY,
    )


def predicate_for_interaction(
    row: dict[str, Any],
    ordered_participants: list[dict[str, Any]],
) -> PredicateRule:
    """Infer an interaction predicate from entity type, roles, and sign."""

    row_type = string_or_none(row.get('type'))
    row_type_accession = entity_type_accession(row_type)
    annotations = row.get('annotations') or []

    participant_annotations: list[dict[str, Any]] = []
    for participant in ordered_participants:
        participant_annotations.extend(
            participant.get('membership_annotations') or []
        )

    sign = interaction_sign(annotations, participant_annotations)

    if row_type_accession == str(EntityTypeCv.INTERACTION):
        if sign > 0:
            return PredicateRule('positively_regulates', INTERACTION_CATEGORY)
        if sign < 0:
            return PredicateRule('negatively_regulates', INTERACTION_CATEGORY)
        return PredicateRule('interacts_with', INTERACTION_CATEGORY)
    if row_type_accession == str(EntityTypeCv.ASSOCIATION):
        return PredicateRule(ASSOCIATION_PREDICATE, ASSOCIATION_CATEGORY)
    if row_type_accession in {
        str(EntityTypeCv.CONTROL),
        str(EntityTypeCv.CATALYSIS),
        str(EntityTypeCv.DEGRADATION),
    }:
        if sign > 0:
            return PredicateRule('positively_regulates', INTERACTION_CATEGORY)
        if sign < 0:
            return PredicateRule('negatively_regulates', INTERACTION_CATEGORY)
        return PredicateRule('regulates', INTERACTION_CATEGORY)
    if row_type_accession == str(EntityTypeCv.REACTION):
        if has_role_ordering(ordered_participants):
            return PredicateRule('transforms_to', INTERACTION_CATEGORY)
        return PredicateRule('interacts_with', INTERACTION_CATEGORY)
    if row_type_accession == str(EntityTypeCv.TRANSPORT):
        return PredicateRule(TRANSPORT_PREDICATE, INTERACTION_CATEGORY)
    if sign > 0:
        return PredicateRule('positively_regulates', INTERACTION_CATEGORY)
    if sign < 0:
        return PredicateRule('negatively_regulates', INTERACTION_CATEGORY)
    return PredicateRule('related_to', INTERACTION_CATEGORY)


def order_relation_participants(
    row: dict[str, Any],
    participants: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Order participants for relation projection."""

    row_type = entity_type_accession(string_or_none(row.get('type')))
    if row_type == str(EntityTypeCv.TRANSPORT):
        return order_transport_participants(participants)
    return order_interaction_participants(participants)


def order_transport_participants(
    participants: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Put the transporter-like participant before the transported substrate."""

    if len(participants) != 2:
        return participants
    transporter_indexes = [
        index
        for index, participant in enumerate(participants)
        if entity_type_accession(string_or_none(participant.get('entity_type')))
        in TRANSPORTER_ENTITY_TYPES
    ]
    if len(transporter_indexes) != 1:
        return order_interaction_participants(participants)
    transporter_index = transporter_indexes[0]
    if transporter_index == 0:
        return participants
    return [participants[1], participants[0]]


def annotation_predicate(annotation: dict[str, Any]) -> str:
    """Return the predicate used for an ontology-valued annotation."""

    value = string_or_none(annotation.get('value')) or ''
    prefix = value.split(':', 1)[0].upper() if ':' in value else ''
    value_upper = value.upper()
    if prefix in {'REACTOME', 'WP'} or value_upper.startswith(('WP', 'R-')):
        return 'involved_in'
    return ASSOCIATION_PREDICATE


def interaction_sign(
    record_annotations: list[dict[str, Any]],
    participant_annotations: list[dict[str, Any]] | None = None,
) -> int:
    """Return ``1`` for activating, ``-1`` for inhibitory, or ``0`` unknown."""

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


def order_interaction_participants(
    participants: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Order two participants by source/target role annotations when present."""

    if len(participants) != 2:
        return participants
    first, second = participants
    first_terms = annotation_terms(first.get('membership_annotations') or [])
    second_terms = annotation_terms(second.get('membership_annotations') or [])
    if (
        first_terms & SOURCE_ROLE_ACCESSIONS
        and second_terms & TARGET_ROLE_ACCESSIONS
    ):
        return [first, second]
    if (
        second_terms & SOURCE_ROLE_ACCESSIONS
        and first_terms & TARGET_ROLE_ACCESSIONS
    ):
        return [second, first]
    return participants


def has_role_ordering(participants: list[dict[str, Any]]) -> bool:
    """Return whether two participants carry complementary source/target roles."""

    if len(participants) != 2:
        return False
    first_terms = annotation_terms(
        participants[0].get('membership_annotations') or []
    )
    second_terms = annotation_terms(
        participants[1].get('membership_annotations') or []
    )
    return bool(
        (
            first_terms & SOURCE_ROLE_ACCESSIONS
            and second_terms & TARGET_ROLE_ACCESSIONS
        )
        or (
            second_terms & SOURCE_ROLE_ACCESSIONS
            and first_terms & TARGET_ROLE_ACCESSIONS
        )
    )


def annotation_terms(annotations: list[dict[str, Any]]) -> set[str]:
    """Return non-empty annotation term identifiers."""

    return {
        term
        for annotation in annotations
        if (term := string_or_none(annotation.get('term'))) is not None
    }


def entity_type_accession(entity_type: str | None) -> str | None:
    """Return the accession part of an entity type label."""

    if entity_type is None:
        return None
    parts = entity_type.split(':', 2)
    if len(parts) >= 2 and parts[1].isdigit():
        return f'{parts[0]}:{parts[1]}'
    if len(parts) == 3 and parts[2].isdigit():
        return f'{parts[1]}:{parts[2]}'
    return entity_type


def string_or_none(value: object) -> str | None:
    """Normalize blank values to ``None`` and non-blank values to text."""

    if value is None:
        return None
    text = str(value).strip()
    return text or None
