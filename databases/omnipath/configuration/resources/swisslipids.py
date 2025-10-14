"""
SwissLipids to silver transformation.

Simple generator that maps PyPath namedtuples to SilverEntity namedtuples.
"""
from omnipath_build.utils.silver_schema import SilverEntity

__all__ = [
    'swisslipids_lipids',
]

def swisslipids_lipids():
    """
    Generator that yields SilverEntity records from SwissLipids PyPath data.

    Yields:
        SilverEntity namedtuples
    """
    from pypath.inputs.swisslipids import swisslipids_lipids

    for rec in swisslipids_lipids():
        yield SilverEntity(
            source='swisslipids',
            entity_type='compound',
            accession=rec.id,
            inchikey=rec.inchikey if rec.inchikey else None,
            inchi=rec.inchi if rec.inchi and rec.inchi != 'InChI=none' else None,
            smiles=rec.smiles if rec.smiles else None,
            name=rec.name,
            synonyms=[s.strip() for s in rec.synonyms.split(';') if s.strip()] if rec.synonyms else None,
            cross_references=[
                {"type": "chebi", "value": rec.chebi} if rec.chebi else None,
                {"type": "lipidmaps", "value": rec.lipidmaps} if rec.lipidmaps else None,
                {"type": "hmdb", "value": rec.hmdb} if rec.hmdb else None,
                {"type": "metanetx", "value": rec.metanetx} if rec.metanetx else None,
            ],
            annotations=[
                {"term": "level", "value": rec.level} if rec.level else None,
                {"term": "lipid_class", "value": rec.lipid_class} if rec.lipid_class else None,
                {"term": "parent", "value": rec.parent} if rec.parent else None,
                {"term": "components", "value": rec.components} if rec.components else None,
                {"term": "charge", "value": str(rec.charge)} if rec.charge else None,
                {"term": "chemical_formula", "value": rec.formula} if rec.formula else None,
                {"term": "exact_mass", "value": rec.exact_mass} if rec.exact_mass else None,
                {"term": "abbreviation", "value": rec.abbreviation} if rec.abbreviation else None,
            ],
            references=[pmid for pmid in rec.pmids if pmid and pmid.strip()] if rec.pmids else None,
        )
