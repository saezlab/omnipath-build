from omnipath_build.utils.silver_schema import SilverEntity

__all__ = [
    'hmdb_entities',
]

def hmdb_entities():
    from pypath.inputs.hmdb.metabolites import compounds_for_metabo

    for rec in compounds_for_metabo():
        yield SilverEntity(
            source='hmdb',
            entity_type='compound',
            accession=rec.accession,
            inchikey=rec.inchikey,
            inchi=rec.inchi,
            smiles=rec.smiles,
            name=rec.traditional_iupac,
            synonyms=rec.synonyms,
            identifiers=[
                {"type": "chebi", "value": f"CHEBI:{rec.chebi_id}"} if rec.chebi_id else None,
                {"type": "pubchem_compound", "value": rec.pubchem_compound_id} if rec.pubchem_compound_id else None,
                {"type": "kegg_compound", "value": rec.kegg_id} if rec.kegg_id else None,
                {"type": "drugbank", "value": rec.drugbank_id} if rec.drugbank_id else None,
                {"type": "cas", "value": rec.cas_registry_number} if rec.cas_registry_number else None,
            ],
            annotations=[
                {"term": "monoisotopic_molecular_weight", "value": rec.monisotopic_molecular_weight, "units": "Da"} if rec.monisotopic_molecular_weight else None,
                {"term": "average_molecular_weight", "value": rec.average_molecular_weight, "units": "Da"} if rec.average_molecular_weight else None,
                {"term": "chemical_formula", "value": rec.chemical_formula} if rec.chemical_formula else None,
                {"term": "iupac_name", "value": rec.iupac_name} if rec.iupac_name else None,
            ],
            references=rec.general_references,
        )
