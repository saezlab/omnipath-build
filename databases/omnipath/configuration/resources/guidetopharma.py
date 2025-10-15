from omnipath_build.utils.silver_schema import SilverEntity, SilverInteraction

__all__ = [
    'guidetopharma_ligands',
    'guidetopharma_targets',
    'guidetopharma_interactions',
]

def guidetopharma_ligands():
    from pypath.inputs.guidetopharma import ligands

    for ligand_id, rec in ligands().items():
        yield SilverEntity(
            source='guidetopharma',
            entity_type=rec.entity_type,
            accession=str(ligand_id),
            inchi=getattr(rec, 'inchi', None),
            smiles=getattr(rec, 'smiles', None),
            name=rec.name,
            identifiers=[
                {"type": "pubchem_compound", "value": str(rec.pubchem)} if getattr(rec, 'pubchem', None) else None,
                {"type": "chembl", "value": rec.chembl} if getattr(rec, 'chembl', None) else None,
                {"type": "uniprot", "value": rec.uniprot} if getattr(rec, 'uniprot', None) else None,
                {"type": "entrez", "value": str(rec.entrez)} if getattr(rec, 'entrez', None) else None,
                {"type": "ensembl", "value": rec.ensembl} if getattr(rec, 'ensembl', None) else None,
                {"type": "refseq", "value": rec.refseq} if getattr(rec, 'refseq', None) else None,
                {"type": "refseqp", "value": rec.refseqp} if getattr(rec, 'refseqp', None) else None,
            ],
            annotations=[
                {"term": "iupac_name", "value": rec.iupac} if getattr(rec, 'iupac', None) else None,
                {"term": "subtype", "value": rec.subtype} if getattr(rec, 'subtype', None) else None,
                {"term": "organism", "value": str(rec.organism)} if getattr(rec, 'organism', None) else None,
                {"term": "symbol", "value": rec.symbol} if getattr(rec, 'symbol', None) else None,
                {"term": "family", "value": rec.family} if getattr(rec, 'family', None) else None,
            ],
        )

def guidetopharma_targets():
    from pypath.inputs.guidetopharma import protein_targets

    for target_id, target_list in protein_targets().items():
        for rec in target_list:
            yield SilverEntity(
                source='guidetopharma',
                entity_type=rec.entity_type,
                accession=str(target_id),
                inchi=getattr(rec, 'inchi', None),
                smiles=getattr(rec, 'smiles', None),
                name=getattr(rec, 'name', None),
                identifiers=[
                    {"type": "uniprot", "value": rec.uniprot} if getattr(rec, 'uniprot', None) else None,
                    {"type": "entrez", "value": str(rec.entrez)} if getattr(rec, 'entrez', None) else None,
                    {"type": "ensembl", "value": rec.ensembl} if getattr(rec, 'ensembl', None) else None,
                    {"type": "refseq", "value": rec.refseq} if getattr(rec, 'refseq', None) else None,
                    {"type": "refseqp", "value": rec.refseqp} if getattr(rec, 'refseqp', None) else None,
                    {"type": "pubchem_compound", "value": str(rec.pubchem)} if getattr(rec, 'pubchem', None) else None,
                    {"type": "chembl", "value": rec.chembl} if getattr(rec, 'chembl', None) else None,
                ],
                annotations=[
                    {"term": "organism", "value": str(rec.organism)} if getattr(rec, 'organism', None) else None,
                    {"term": "symbol", "value": rec.symbol} if getattr(rec, 'symbol', None) else None,
                    {"term": "family", "value": rec.family} if getattr(rec, 'family', None) else None,
                    {"term": "target_type", "value": rec.target_type} if getattr(rec, 'target_type', None) else None,
                    {"term": "iupac_name", "value": rec.iupac} if getattr(rec, 'iupac', None) else None,
                ],
            )

def guidetopharma_interactions():
    from pypath.inputs.guidetopharma import interactions

    for interaction_rec in interactions():
        ligand = interaction_rec.ligand
        target = interaction_rec.target

        # Get first available identifier for ligand
        ligand_id = getattr(ligand, 'pubchem', None) or getattr(ligand, 'uniprot', None) or getattr(ligand, 'ensembl', None) or getattr(ligand, 'entrez', None)
        ligand_id_type = (
            'pubchem_compound' if getattr(ligand, 'pubchem', None) else
            'uniprot' if getattr(ligand, 'uniprot', None) else
            'ensembl' if getattr(ligand, 'ensembl', None) else
            'entrez' if getattr(ligand, 'entrez', None) else None
        )

        # Get first available identifier for target
        target_id = getattr(target, 'uniprot', None) or getattr(target, 'ensembl', None) or getattr(target, 'entrez', None) or getattr(target, 'pubchem', None) or getattr(target, 'chembl', None)
        target_id_type = (
            'uniprot' if getattr(target, 'uniprot', None) else
            'ensembl' if getattr(target, 'ensembl', None) else
            'entrez' if getattr(target, 'entrez', None) else
            'pubchem_compound' if getattr(target, 'pubchem', None) else
            'chembl' if getattr(target, 'chembl', None) else None
        )

        if not ligand_id or not target_id:
            continue

        # Determine sign based on stimulation/inhibition
        sign = None
        if interaction_rec.is_stimulation:
            sign = 'positive'
        elif interaction_rec.is_inhibition:
            sign = 'negative'

        yield SilverInteraction(
            source='guidetopharma',
            entity_a_identifier=str(ligand_id),
            entity_a_identifier_type=ligand_id_type,
            entity_b_identifier=str(target_id),
            entity_b_identifier_type=target_id_type,
            interaction_type=interaction_rec.action if interaction_rec.action else None,
            is_directed=True,
            direction='a_to_b',
            sign=sign,
            interaction_annotations=[
                {"key": "action_type", "value": interaction_rec.action_type} if interaction_rec.action_type else None,
                {"key": "endogenous", "value": str(interaction_rec.endogenous)} if interaction_rec.endogenous is not None else None,
                {"key": "primary_target", "value": interaction_rec.primary_target} if interaction_rec.primary_target else None,
                {"key": "affinity_median", "value": str(interaction_rec.affinity_median)} if interaction_rec.affinity_median else None,
                {"key": "affinity_high", "value": str(interaction_rec.affinity_high)} if interaction_rec.affinity_high else None,
                {"key": "affinity_low", "value": str(interaction_rec.affinity_low)} if interaction_rec.affinity_low else None,
                {"key": "affinity_units", "value": interaction_rec.affinity_units} if interaction_rec.affinity_units else None,
            ],
            references=[str(interaction_rec.pubmed)] if interaction_rec.pubmed else None,
        )
