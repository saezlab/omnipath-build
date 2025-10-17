from omnipath_build.utils.cv_term_enums import IdentifierNamespaceCv
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
    from omnipath_build.utils.silver_schema import Identifier

    for rec in pypath_complexes():
        yield SilverEntity(
            source='signor',
            entity_type='complex',
            name=rec.name,
            identifiers=[Identifier(type=IdentifierNamespaceCv.ACCESSION, value=rec.complex_id)],
            annotations=build_annotations(
                rec,
                ('components', 'components', None, lambda x: ','.join(x)),
                ('components', 'component_count', None, lambda x: str(len(x))),
            ),
        )


def signor_protein_families():
    """
    Yields SIGNOR protein families as SilverEntity objects.
    """
    from pypath.inputs.new_signor import signor_protein_families as pypath_families
    from omnipath_build.utils.silver_schema import Identifier

    for rec in pypath_families():
        yield SilverEntity(
            source='signor',
            entity_type='protein_family',
            name=rec.name,
            identifiers=[Identifier(type=IdentifierNamespaceCv.ACCESSION, value=rec.family_id)],
            annotations=build_annotations(
                rec,
                ('members', 'members', None, lambda x: ','.join(x)),
                ('members', 'member_count', None, lambda x: str(len(x))),
            ),
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
            entity_type='phenotype',
            name=rec.name,
            identifiers=[Identifier(type=IdentifierNamespaceCv.ACCESSION, value=rec.phenotype_id)],
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
            entity_type='stimulus',
            name=rec.name,
            identifiers=[Identifier(type=IdentifierNamespaceCv.ACCESSION, value=rec.stimulus_id)],
            annotations=build_annotations(rec, 'description'),
        )
