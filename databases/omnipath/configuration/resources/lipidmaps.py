from omnipath_build.utils.silver_schema import SilverEntity, IdentifierType
from omnipath_build.utils.identifier_builders import build_identifiers
from omnipath_build.utils.annotation_builders import build_annotations

__all__ = [
    'lipidmaps_lipids',
]

# Identifier mapping for LipidMaps
LIPIDMAPS_IDENTIFIERS = {
    'id': IdentifierType.LIPIDMAPS,
    'inchikey': IdentifierType.INCHIKEY,
    'inchi': IdentifierType.INCHI,
    'smiles': IdentifierType.SMILES,
    'chebi': IdentifierType.CHEBI,
    'pubchem': IdentifierType.PUBCHEM,
}

def lipidmaps_lipids():
    from pypath.inputs.lipidmaps import lipidmaps_lipids as pypath_lipids
    for rec in pypath_lipids():
        yield SilverEntity(
            source='lipidmaps',
            entity_type='compound',
            name=rec.name,
            synonyms=[s.strip() for s in rec.synonyms.split('; ') if s.strip()] if rec.synonyms else None,
            identifiers=build_identifiers(rec, mapping=LIPIDMAPS_IDENTIFIERS, accession_attr='id'),
            annotations=build_annotations(
                rec,
                'category',
                'main_class',
                'abbreviation',
                ('formula', 'chemical_formula'),
                'exact_mass',
                ('iupac', 'iupac_name'),
            ),
        )
