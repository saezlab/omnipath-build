from omnipath_build.utils.cv_term_enums import (
    IdentifierNamespaceCv,
    EntityTypeCv,
    InteractionTypeCv,
    DetectionMethodCv,
)
from omnipath_build.utils.silver_schema import SilverEntity
from omnipath_build.utils.annotation_builders import build_annotations
from ._mitab_support import mitab_to_silver_interaction

__all__ = [
    'signor_complexes',
    'signor_protein_families',
    'signor_phenotypes',
    'signor_stimuli',
    'signor_interactions',
]

# Identifier mappings for SIGNOR
SIGNOR_PROTEIN_IDENTIFIERS = {
    'uniprot': IdentifierNamespaceCv.UNIPROT,
}

def signor_complexes():
    """
    Yields SIGNOR complexes as SilverEntity objects.
    """
    from pypath.inputs.new_signor import signor_complexes as pypath_complexes
    from omnipath_build.utils.silver_schema import Identifier, Member

    for rec in pypath_complexes():
        # Convert components to Member objects
        members = [
            Member(
                identifier=component,
                identifier_type=IdentifierNamespaceCv.UNIPROT,
            )
            for component in rec.components
        ] if rec.components else None

        yield SilverEntity(
            source='signor',
            entity_type=EntityTypeCv.PROTEIN_COMPLEX,
            identifiers=[
                Identifier(type=IdentifierNamespaceCv.SIGNOR, value=rec.complex_id),
                Identifier(type=IdentifierNamespaceCv.NAME, value=rec.name),
            ],
            members=members,
        )


def signor_protein_families():
    """
    Yields SIGNOR protein families as SilverEntity objects.
    """
    from pypath.inputs.new_signor import signor_protein_families as pypath_families
    from omnipath_build.utils.silver_schema import Identifier, Member

    for rec in pypath_families():
        # Convert members to Member objects
        members = [
            Member(
                identifier=member,
                identifier_type=IdentifierNamespaceCv.UNIPROT,
            )
            for member in rec.members
        ] if rec.members else None

        yield SilverEntity(
            source='signor',
            entity_type=EntityTypeCv.PROTEIN_FAMILY,
            identifiers=[
                Identifier(type=IdentifierNamespaceCv.SIGNOR, value=rec.family_id),
                Identifier(type=IdentifierNamespaceCv.NAME, value=rec.name),
            ],
            members=members,
        )


def signor_phenotypes():
    """
    Yields SIGNOR phenotypes as SilverEntity objects.
    """
    from pypath.inputs.new_signor import signor_phenotypes as pypath_phenotypes
    from omnipath_build.utils.silver_schema import Identifier

    for rec in pypath_phenotypes():
        yield SilverEntity(
            source='signor',
            entity_type=EntityTypeCv.PHENOTYPE,
            identifiers=[
                Identifier(type=IdentifierNamespaceCv.SIGNOR, value=rec.phenotype_id),
                Identifier(type=IdentifierNamespaceCv.NAME, value=rec.name),
            ],
            annotations=build_annotations(rec, 'description'),
        )


def signor_stimuli():
    """
    Yields SIGNOR stimuli as SilverEntity objects.
    """
    from pypath.inputs.new_signor import signor_stimuli as pypath_stimuli
    from omnipath_build.utils.silver_schema import Identifier

    for rec in pypath_stimuli():
        yield SilverEntity(
            source='signor',
            entity_type=EntityTypeCv.STIMULUS,
            identifiers=[
                Identifier(type=IdentifierNamespaceCv.SIGNOR, value=rec.stimulus_id),
                Identifier(type=IdentifierNamespaceCv.NAME, value=rec.name),
            ],
        )


def signor_interactions():
    """
    Yields SIGNOR causal interactions as SilverInteraction objects.
    """
    from pypath.inputs.new_signor import signor_interactions as pypath_interactions

    for record in pypath_interactions():
        yield mitab_to_silver_interaction(
            record,
            source='signor',
            fallback_interaction_type=InteractionTypeCv.FUNCTIONAL_ASSOCIATION,
            fallback_detection_method=DetectionMethodCv.INFERRED_BY_CURATOR,
        )
