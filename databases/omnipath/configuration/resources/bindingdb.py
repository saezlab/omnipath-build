from omnipath_build.utils.silver_schema import SilverEntity, SilverInteraction, IdentifierType
from omnipath_build.utils.identifier_builders import build_identifiers
from omnipath_build.utils.annotation_builders import build_annotations

__all__ = [
    'bindingdb',
]

# Identifier mappings for BindingDB
BINDINGDB_LIGAND_IDENTIFIERS = {
    'inchi_key': IdentifierType.INCHIKEY,
    'inchi': IdentifierType.INCHI,
    'smiles': IdentifierType.SMILES,
    'pubchem': IdentifierType.PUBCHEM_COMPOUND,
}

BINDINGDB_TARGET_IDENTIFIERS = {
    'uniprot': IdentifierType.UNIPROT,
}

def bindingdb():
    """
    Yields ligands, targets, and interactions from BindingDB in a single pass.

    Yields dictionaries with keys:
    - 'bindingdb_ligands': SilverEntity for compounds
    - 'bindingdb_targets': SilverEntity for proteins
    - 'bindingdb_interactions': SilverInteraction for binding interactions

    The loader can route each record type to its appropriate output file.
    """
    from pypath.inputs.bindingdb import interactions

    for interaction in interactions():
        ligand = interaction.ligand
        target = interaction.target

        result = {}

        # Add interaction if both identifiers are present
        if ligand.inchi_key and target.uniprot:
            result['bindingdb_interactions'] = SilverInteraction(
                source='bindingdb',
                entity_a=SilverEntity(
                    source='bindingdb',
                    entity_type='compound',
                    name=ligand.name,
                    identifiers=build_identifiers(
                        ligand,
                        mapping=BINDINGDB_LIGAND_IDENTIFIERS,
                        transformers={'pubchem': str},
                        accession_attr='inchi_key',
                    ),
                ),
                entity_b=SilverEntity(
                    source='bindingdb',
                    entity_type='protein',
                    name=target.name,
                    identifiers=build_identifiers(
                        target,
                        mapping=BINDINGDB_TARGET_IDENTIFIERS,
                        accession_attr='uniprot',
                    ),
                    annotations=build_annotations(
                        target,
                        ('ncbi_tax_id', 'ncbi_tax_id', None, str),
                        'organism',
                        ('regions_mutations', 'regions_mutations', None, str),
                    ),
                ),
                interaction_type='binding',
                is_directed=False,
                interaction_annotations=build_annotations(
                    target,
                    ('organism', 'target_organism'),
                    ('regions_mutations', 'regions_mutations', None, str),
                ),
            )

        # Only yield if we have at least one record
        if result:
            yield result
