from omnipath_build.utils.silver_schema import SilverEntity, SilverInteraction

__all__ = [
    'guidetopharma_ligands',
    'guidetopharma_targets',
    'guidetopharma_interactions',
]

def guidetopharma_ligands():
    from pypath.inputs.guidetopharma import ligands

    for ligand_id, rec in ligands().items():
        # Skip protein entities, only include compounds
        if rec.entity_type != 'compound':
            continue

        yield SilverEntity(
            source='guidetopharma',
            entity_type='compound',
            accession=str(ligand_id),
            inchi=rec.inchi,
            smiles=rec.smiles,
            name=rec.name,
            cross_references=[
                {"type": "pubchem_compound", "value": str(rec.pubchem)} if rec.pubchem else None,
                {"type": "chembl", "value": rec.chembl} if rec.chembl else None,
            ],
            annotations=[
                {"term": "iupac_name", "value": rec.iupac} if rec.iupac else None,
                {"term": "subtype", "value": rec.subtype} if rec.subtype else None,
            ],
        )

def guidetopharma_targets():
    from pypath.inputs.guidetopharma import protein_targets

    for target_id, target_list in protein_targets().items():
        for rec in target_list:
            # Only include proteins
            if rec.entity_type != 'protein':
                continue

            yield SilverEntity(
                source='guidetopharma',
                entity_type='protein',
                accession=rec.uniprot,
                cross_references=[
                    {"type": "entrez", "value": str(rec.entrez)} if rec.entrez else None,
                    {"type": "ensembl", "value": rec.ensembl} if rec.ensembl else None,
                    {"type": "refseq", "value": rec.refseq} if rec.refseq else None,
                    {"type": "refseqp", "value": rec.refseqp} if rec.refseqp else None,
                ],
                annotations=[
                    {"term": "organism", "value": str(rec.organism)} if rec.organism else None,
                    {"term": "symbol", "value": rec.symbol} if rec.symbol else None,
                    {"term": "family", "value": rec.family} if rec.family else None,
                    {"term": "target_type", "value": rec.target_type} if rec.target_type else None,
                ],
            )

def guidetopharma_interactions():
    from pypath.inputs.guidetopharma import interactions

    for interaction_rec in interactions():
        # Extract ligand and target identifiers
        ligand_id = interaction_rec.ligand.pubchem if interaction_rec.ligand.pubchem else None
        target_uniprot = interaction_rec.target.uniprot if interaction_rec.target.uniprot else None

        if not ligand_id or not target_uniprot:
            continue

        # Determine sign based on stimulation/inhibition
        sign = None
        if interaction_rec.is_stimulation:
            sign = 'positive'
        elif interaction_rec.is_inhibition:
            sign = 'negative'

        # Extract names
        ligand_name = interaction_rec.ligand.name if hasattr(interaction_rec.ligand, 'name') and interaction_rec.ligand.name else None
        target_symbol = interaction_rec.target.symbol if hasattr(interaction_rec.target, 'symbol') and interaction_rec.target.symbol else None

        yield SilverInteraction(
            source='guidetopharma',
            entity_a_identifier=str(ligand_id),
            entity_a_identifier_type='pubchem_compound',
            entity_a_name=ligand_name,
            entity_b_identifier=target_uniprot,
            entity_b_identifier_type='uniprot',
            entity_b_name=target_symbol,
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
