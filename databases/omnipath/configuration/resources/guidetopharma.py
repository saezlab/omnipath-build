from omnipath_build.utils.cv_term_enums import (
    IdentifierNamespaceCv,
    EntityTypeCv,
    InteractionTypeCv,
    BiologicalRoleCv,
    DetectionMethodCv,
    CausalStatementCv,
    ReferenceTypeCv,
)
from omnipath_build.utils.silver_schema import (
    SilverInteraction,
    SilverEntity,
    Reference,
)
from omnipath_build.utils.identifier_builders import build_identifiers
from omnipath_build.utils.annotation_builders import build_annotations

__all__ = [
    'guidetopharma_interactions',
]

# Identifier mappings for GuideToPharmacology
GUIDETOPHARMA_IDENTIFIERS = {
    'inchi': IdentifierNamespaceCv.STANDARD_INCHI,
    'smiles': IdentifierNamespaceCv.SMILES,
    'pubchem': IdentifierNamespaceCv.PUBCHEM_COMPOUND,
    'chembl': IdentifierNamespaceCv.CHEMBL,
    'uniprot': IdentifierNamespaceCv.UNIPROT,
    'entrez': IdentifierNamespaceCv.ENTREZ,
    'ensembl': IdentifierNamespaceCv.ENSEMBL,
    'refseq': IdentifierNamespaceCv.REFSEQ,
    'refseqp': IdentifierNamespaceCv.REFSEQ_PROTEIN,
}

# Entity type mapping
ENTITY_TYPE_MAP = {
    'compound': EntityTypeCv.SMALL_MOLECULE,
    'protein': EntityTypeCv.PROTEIN,
    'ligand': EntityTypeCv.SMALL_MOLECULE,
    'target': EntityTypeCv.PROTEIN,
}
def guidetopharma_interactions():
    from pypath.inputs.guidetopharma import interactions

    for interaction_rec in interactions():
        ligand = interaction_rec.ligand
        target = interaction_rec.target

        # Skip if essential participant details missing
        if ligand is None or target is None:
            continue

        # Determine causal statement based on stimulation/inhibition
        causal_statement = None
        if interaction_rec.is_stimulation:
            causal_statement = CausalStatementCv.UP_REGULATES
        elif interaction_rec.is_inhibition:
            causal_statement = CausalStatementCv.DOWN_REGULATES

        ligand_entity = SilverEntity(
            source='guidetopharma',
            entity_type=ENTITY_TYPE_MAP.get(ligand.entity_type, EntityTypeCv.SMALL_MOLECULE),
            biological_role=BiologicalRoleCv.ALLOSTERIC_EFFECTOR,
            name=ligand.name,
            identifiers=build_identifiers(
                ligand,
                mapping=GUIDETOPHARMA_IDENTIFIERS,
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
            entity_type=ENTITY_TYPE_MAP.get(target.entity_type, EntityTypeCv.PROTEIN),
            biological_role=BiologicalRoleCv.REGULATOR_TARGET,
            identifiers=build_identifiers(
                target,
                mapping=GUIDETOPHARMA_IDENTIFIERS,
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
            interaction_type=InteractionTypeCv.FUNCTIONAL_ASSOCIATION,
            detection_method=DetectionMethodCv.INFERRED_BY_CURATOR,
            direction='a_to_b',
            causal_statement=causal_statement,
            interaction_annotations=build_annotations(
                interaction_rec,
                'action',
                'action_type',
                ('endogenous', 'endogenous', None, str),
                'primary_target',
                ('affinity_median', 'affinity_median', None, str),
                ('affinity_high', 'affinity_high', None, str),
                ('affinity_low', 'affinity_low', None, str),
                'affinity_units',
            ),
            references=[Reference(type=ReferenceTypeCv.PUBMED, value=str(interaction_rec.pubmed))] if interaction_rec.pubmed else None,
        )
