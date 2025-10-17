from omnipath_build.utils.cv_term_enums import IdentifierNamespaceCv
from omnipath_build.utils.silver_schema import SilverEntity, SilverInteraction
from omnipath_build.utils.identifier_builders import build_identifiers
from omnipath_build.utils.annotation_builders import build_annotations

__all__ = [
    'guidetopharma_interactions',
]

# Identifier mappings for GuideToPharmacology
GUIDETOPHARMA_LIGAND_IDENTIFIERS = {
    'inchi': IdentifierNamespaceCv.INCHI,
    'smiles': IdentifierNamespaceCv.SMILES,
    'pubchem': IdentifierNamespaceCv.PUBCHEM_COMPOUND,
    'chembl': IdentifierNamespaceCv.CHEMBL,
    'uniprot': IdentifierNamespaceCv.UNIPROT,
    'entrez': IdentifierNamespaceCv.ENTREZ,
    'ensembl': IdentifierNamespaceCv.ENSEMBL,
    'refseq': IdentifierNamespaceCv.REFSEQ,
    'refseqp': IdentifierNamespaceCv.REFSEQ_PROTEIN,
}

GUIDETOPHARMA_TARGET_IDENTIFIERS = {
    'inchi': IdentifierNamespaceCv.INCHI,
    'smiles': IdentifierNamespaceCv.SMILES,
    'uniprot': IdentifierNamespaceCv.UNIPROT,
    'ensembl': IdentifierNamespaceCv.ENSEMBL,
    'entrez': IdentifierNamespaceCv.ENTREZ,
    'pubchem': IdentifierNamespaceCv.PUBCHEM_COMPOUND,
    'chembl': IdentifierNamespaceCv.CHEMBL,
    'refseq': IdentifierNamespaceCv.REFSEQ,
    'refseqp': IdentifierNamespaceCv.REFSEQ_PROTEIN,
}

def guidetopharma_interactions():
    from pypath.inputs.guidetopharma import interactions

    for interaction_rec in interactions():
        ligand = interaction_rec.ligand
        target = interaction_rec.target

        # Skip if essential participant details missing
        if ligand is None or target is None:
            continue

        # Determine sign based on stimulation/inhibition
        sign = None
        if interaction_rec.is_stimulation:
            sign = 'positive'
        elif interaction_rec.is_inhibition:
            sign = 'negative'

        ligand_entity = SilverEntity(
            source='guidetopharma',
            entity_type=ligand.entity_type or 'ligand',
            name=ligand.name,
            identifiers=build_identifiers(
                ligand,
                mapping=GUIDETOPHARMA_LIGAND_IDENTIFIERS,
                transformers={'pubchem': str, 'entrez': str},
            ),
            annotations=build_annotations(
                ligand,
                ('iupac', 'iupac_name'),
                'subtype',
                ('organism', 'organism', None, str),
                'symbol',
                'family',
            ),
        )

        target_entity = SilverEntity(
            source='guidetopharma',
            entity_type=target.entity_type or 'target',
            name=target.name,
            identifiers=build_identifiers(
                target,
                mapping=GUIDETOPHARMA_TARGET_IDENTIFIERS,
                transformers={'entrez': str, 'pubchem': str},
            ),
            annotations=build_annotations(
                target,
                ('organism', 'organism', None, str),
                'symbol',
                'family',
                'target_type',
                ('iupac', 'iupac_name'),
            ),
        )

        yield SilverInteraction(
            source='guidetopharma',
            entity_a=ligand_entity,
            entity_b=target_entity,
            interaction_type=interaction_rec.action if interaction_rec.action else None,
            direction='a_to_b',
            sign=sign,
            interaction_annotations=build_annotations(
                interaction_rec,
                'action_type',
                ('endogenous', 'endogenous', None, str),
                'primary_target',
                ('affinity_median', 'affinity_median', None, str),
                ('affinity_high', 'affinity_high', None, str),
                ('affinity_low', 'affinity_low', None, str),
                'affinity_units',
            ),
            references=[str(interaction_rec.pubmed)] if interaction_rec.pubmed else None,
        )
