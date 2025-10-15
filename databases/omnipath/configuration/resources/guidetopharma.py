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
            identifiers=[
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

            # Use first available identifier as accession
            accession = rec.uniprot or rec.ensembl or rec.entrez or rec.refseqp or rec.refseq

            if not accession:
                continue

            yield SilverEntity(
                source='guidetopharma',
                entity_type='protein',
                accession=accession,
                identifiers=[
                    {"type": "uniprot", "value": rec.uniprot} if rec.uniprot else None,
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

        # Use first available identifier for target (coalescing)
        target = interaction_rec.target

        # Handle different target types (protein vs compound)
        if target.entity_type == 'protein':
            target_id = target.uniprot or target.ensembl or target.entrez or target.refseqp or target.refseq
            # Determine the identifier type based on which one we got
            if target.uniprot:
                target_id_type = 'uniprot'
            elif target.ensembl:
                target_id_type = 'ensembl'
            elif target.entrez:
                target_id_type = 'entrez'
            elif target.refseqp:
                target_id_type = 'refseqp'
            elif target.refseq:
                target_id_type = 'refseq'
            else:
                target_id_type = None
        elif target.entity_type == 'compound':
            target_id = target.pubchem or target.chembl
            if target.pubchem:
                target_id_type = 'pubchem_compound'
            elif target.chembl:
                target_id_type = 'chembl'
            else:
                target_id_type = None
        else:
            continue

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
            entity_a_identifier_type='pubchem_compound',
            entity_b_identifier=target_id,
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
