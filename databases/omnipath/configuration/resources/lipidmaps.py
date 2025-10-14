"""
LipidMaps to silver transformation.

Simple generator that maps PyPath namedtuples to SilverEntity namedtuples.
"""
from omnipath_build.utils.silver_schema import SilverEntity

__all__ = [
    'lipidmaps_lipids',
]

def lipidmaps_lipids():
    """
    Generator that yields SilverEntity records from LipidMaps PyPath data.

    Yields:
        SilverEntity namedtuples
    """
    from pypath.inputs.lipidmaps import lipidmaps_lipids as pypath_lipids

    for rec in pypath_lipids():
        yield SilverEntity(
            source='lipidmaps',
            entity_type='compound',
            accession=rec.id,
            inchikey=rec.inchikey if rec.inchikey else None,
            inchi=rec.inchi if rec.inchi else None,
            smiles=rec.smiles if rec.smiles else None,
            name=rec.name,
            synonyms=[s.strip() for s in rec.synonyms.split('; ') if s.strip()] if rec.synonyms else None,
            cross_references=[
                {"type": "chebi", "value": rec.chebi} if rec.chebi else None,
                {"type": "pubchem", "value": rec.pubchem} if rec.pubchem else None,
            ],
            annotations=[
                {"term": "category", "value": rec.category} if rec.category else None,
                {"term": "main_class", "value": rec.main_class} if rec.main_class else None,
                {"term": "abbreviation", "value": rec.abbreviation} if rec.abbreviation else None,
                {"term": "chemical_formula", "value": rec.formula} if rec.formula else None,
                {"term": "exact_mass", "value": rec.exact_mass} if rec.exact_mass else None,
                {"term": "iupac_name", "value": rec.iupac} if rec.iupac else None,
            ],
        )
