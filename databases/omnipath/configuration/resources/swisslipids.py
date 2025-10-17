from omnipath_build.utils.silver_schema import SilverEntity, IdentifierType
from omnipath_build.utils.identifier_builders import build_identifiers
from omnipath_build.utils.annotation_builders import build_annotations

__all__ = [
    'swisslipids_lipids',
]

# Identifier mapping for SwissLipids
SWISSLIPIDS_IDENTIFIERS = {
    'inchikey': IdentifierType.INCHIKEY,
    'inchi': IdentifierType.INCHI,
    'smiles': IdentifierType.SMILES,
    'chebi': IdentifierType.CHEBI,
    'lipidmaps': IdentifierType.LIPIDMAPS,
    'hmdb': IdentifierType.HMDB,
    'metanetx': IdentifierType.METANETX,
}

def swisslipids_lipids():
    from pypath.inputs.swisslipids import swisslipids_lipids

    for rec in swisslipids_lipids():
        yield SilverEntity(
            source='swisslipids',
            entity_type='compound',
            name=rec.name,
            synonyms=[s.strip() for s in rec.synonyms.split(';') if s.strip()] if rec.synonyms else None,
            identifiers=build_identifiers(
                rec,
                mapping=SWISSLIPIDS_IDENTIFIERS,
                filters={'inchi': lambda x: x != 'InChI=none'},
                accession_attr='id',
            ),
            annotations=build_annotations(
                rec,
                'level',
                'lipid_class',
                'parent',
                'components',
                ('charge', 'charge', None, str),
                ('formula', 'chemical_formula'),
                'exact_mass',
                'abbreviation',
            ),
            references=[pmid for pmid in rec.pmids if pmid and pmid.strip()] if rec.pmids else None,
        )
