from omnipath_build.utils.silver_schema import SilverEntity, SilverInteraction

__all__ = [
    'bindingdb',
]

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

    seen_ligands = set()
    seen_targets = set()

    for interaction in interactions():
        ligand = interaction.ligand
        target = interaction.target

        result = {}

        # Add ligand entity if not seen before
        if ligand.inchi_key and ligand.inchi_key not in seen_ligands:
            seen_ligands.add(ligand.inchi_key)

            result['bindingdb_ligands'] = SilverEntity(
                source='bindingdb',
                entity_type='compound',
                accession=ligand.inchi_key,
                inchikey=ligand.inchi_key,
                inchi=ligand.inchi,
                smiles=ligand.smiles,
                name=ligand.name,
                identifiers=[
                    {"type": "pubchem_compound", "value": str(ligand.pubchem)} if ligand.pubchem else None,
                ],
            )

        # Add target entity if not seen before and has UniProt
        if target.uniprot and target.uniprot not in seen_targets:
            seen_targets.add(target.uniprot)

            result['bindingdb_targets'] = SilverEntity(
                source='bindingdb',
                entity_type='protein',
                accession=target.uniprot,
                name=target.name,
                identifiers=[
                    {"type": "ncbi_tax_id", "value": str(target.ncbi_tax_id)} if target.ncbi_tax_id else None,
                ],
                annotations=[
                    {"term": "organism", "value": target.organism} if target.organism else None,
                    {"term": "regions_mutations", "value": str(target.regions_mutations)} if target.regions_mutations else None,
                ],
            )

        # Add interaction if both identifiers are present
        if ligand.inchi_key and target.uniprot:
            result['bindingdb_interactions'] = SilverInteraction(
                source='bindingdb',
                entity_a_identifier=ligand.inchi_key,
                entity_a_identifier_type='inchikey',
                entity_b_identifier=target.uniprot,
                entity_b_identifier_type='uniprot',
                interaction_type='binding',
                is_directed=False,
                interaction_annotations=[
                    {"key": "target_organism", "value": target.organism} if target.organism else None,
                    {"key": "regions_mutations", "value": str(target.regions_mutations)} if target.regions_mutations else None,
                ],
            )

        # Only yield if we have at least one record
        if result:
            yield result
