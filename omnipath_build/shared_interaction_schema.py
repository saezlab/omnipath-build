"""Shared CV term sets for interpreting interaction direction and roles."""

from __future__ import annotations

from pypath.internals.cv_terms import (
    BiologicalEffectCv,
    BiologicalRoleCv,
    CausalStatementCv,
    InteractionParameterCv,
    LigandTypeCv,
    ParticipantMetadataCv,
    PharmacologicalActionCv,
)


def _optional_accession(enum_cls: type, name: str) -> tuple[str, ...]:
    member = getattr(enum_cls, name, None)
    return () if member is None else (member.value,)


POSITIVE_SIGN_ACCESSIONS = frozenset({
    CausalStatementCv.UP_REGULATES.value,
    CausalStatementCv.UP_REGULATES_ACTIVITY.value,
    CausalStatementCv.UP_REGULATES_QUANTITY.value,
    CausalStatementCv.UP_REGULATES_QUANTITY_BY_EXPRESSION.value,
    CausalStatementCv.UP_REGULATES_QUANTITY_BY_STABILIZATION.value,
    BiologicalEffectCv.UP_REGULATES_ACTIVITY.value,
    BiologicalEffectCv.UP_REGULATES_QUANTITY.value,
    BiologicalRoleCv.STIMULATOR.value,
    PharmacologicalActionCv.AGONIST.value,
    PharmacologicalActionCv.FULL_AGONIST.value,
    PharmacologicalActionCv.PARTIAL_AGONIST.value,
    PharmacologicalActionCv.BIASED_AGONIST.value,
    PharmacologicalActionCv.ACTIVATION.value,
    PharmacologicalActionCv.POSITIVE.value,
    PharmacologicalActionCv.POTENTIATION.value,
    LigandTypeCv.ACTIVATOR.value,
    LigandTypeCv.AGONIST.value,
})

NEGATIVE_SIGN_ACCESSIONS = frozenset({
    CausalStatementCv.DOWN_REGULATES.value,
    CausalStatementCv.DOWN_REGULATES_ACTIVITY.value,
    CausalStatementCv.DOWN_REGULATES_QUANTITY.value,
    CausalStatementCv.DOWN_REGULATES_QUANTITY_BY_DESTABLIZATION.value,
    CausalStatementCv.DOWN_REGULATES_QUANTITY_BY_REPRESSION.value,
    BiologicalEffectCv.DOWN_REGULATES_ACTIVITY.value,
    BiologicalEffectCv.DOWN_REGULATES_QUANTITY.value,
    BiologicalRoleCv.INHIBITOR.value,
    PharmacologicalActionCv.ANTAGONIST.value,
    PharmacologicalActionCv.INVERSE_AGONIST.value,
    PharmacologicalActionCv.INHIBITION.value,
    PharmacologicalActionCv.IRREVERSIBLE_INHIBITION.value,
    PharmacologicalActionCv.FEEDBACK_INHIBITION.value,
    PharmacologicalActionCv.NEGATIVE.value,
    PharmacologicalActionCv.PORE_BLOCKER.value,
    LigandTypeCv.INHIBITOR.value,
    LigandTypeCv.ANTAGONIST.value,
    LigandTypeCv.CHANNEL_BLOCKER.value,
    LigandTypeCv.GATING_INHIBITOR.value,
})

SOURCE_ROLE_ACCESSIONS = frozenset({
    ParticipantMetadataCv.SOURCE.value,
    BiologicalRoleCv.CONTROLLER.value,
    BiologicalRoleCv.REACTANT.value,
    BiologicalRoleCv.TEMPLATE.value,
    BiologicalRoleCv.ENZYME.value,
    BiologicalRoleCv.REGULATOR.value,
    BiologicalRoleCv.INHIBITOR.value,
    BiologicalRoleCv.STIMULATOR.value,
    BiologicalRoleCv.ALLOSTERIC_EFFECTOR.value,
    LigandTypeCv.INHIBITOR.value,
    LigandTypeCv.ACTIVATOR.value,
    LigandTypeCv.AGONIST.value,
    LigandTypeCv.ANTAGONIST.value,
    *_optional_accession(ParticipantMetadataCv, 'LIGAND'),
})

TARGET_ROLE_ACCESSIONS = frozenset({
    ParticipantMetadataCv.TARGET.value,
    BiologicalRoleCv.CONTROLLED.value,
    BiologicalRoleCv.PRODUCT.value,
    BiologicalRoleCv.SUBSTRATE.value,
    BiologicalRoleCv.REGULATOR_TARGET.value,
    *_optional_accession(ParticipantMetadataCv, 'RECEPTOR'),
})

ACTIVATORY_PARAMETER_ACCESSIONS = frozenset({
    InteractionParameterCv.EC50.value,
})

INHIBITORY_PARAMETER_ACCESSIONS = frozenset({
    InteractionParameterCv.KI.value,
    InteractionParameterCv.IC50.value,
})
