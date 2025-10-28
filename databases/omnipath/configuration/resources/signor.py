from omnipath_build.utils.cv_term_enums import (
    IdentifierNamespaceCv,
    EntityTypeCv,
)
from omnipath_build.utils.silver_schema import SilverEntity
from omnipath_build.utils.identifier_builders import build_identifiers
from omnipath_build.utils.annotation_builders import build_annotations

__all__ = [
    'signor_complexes',
    'signor_protein_families',
    'signor_phenotypes',
    'signor_stimuli',
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
            name=rec.name,
            identifiers=[Identifier(type=IdentifierNamespaceCv.SIGNOR, value=rec.complex_id)],
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
            name=rec.name,
            identifiers=[Identifier(type=IdentifierNamespaceCv.SIGNOR, value=rec.family_id)],
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
            name=rec.name,
            identifiers=[Identifier(type=IdentifierNamespaceCv.SIGNOR, value=rec.phenotype_id)],
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
            name=rec.name,
            identifiers=[Identifier(type=IdentifierNamespaceCv.SIGNOR, value=rec.stimulus_id)],
        )
