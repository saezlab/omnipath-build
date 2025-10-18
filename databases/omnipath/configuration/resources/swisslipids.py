from omnipath_build.utils.cv_term_enums import (
    IdentifierNamespaceCv,
    EntityTypeCv,
    ReferenceTypeCv,
)
from omnipath_build.utils.silver_schema import SilverEntity, Reference
from omnipath_build.utils.identifier_builders import build_identifiers
from omnipath_build.utils.annotation_builders import build_annotations

__all__ = [
    'swisslipids_lipids',
]

# Identifier mapping for SwissLipids
SWISSLIPIDS_IDENTIFIERS = {
    'id': IdentifierNamespaceCv.SWISSLIPIDS,
    'inchikey': IdentifierNamespaceCv.STANDARD_INCHI_KEY,
    'inchi': IdentifierNamespaceCv.STANDARD_INCHI,
    'smiles': IdentifierNamespaceCv.SMILES,
    'chebi': IdentifierNamespaceCv.CHEBI,
    'lipidmaps': IdentifierNamespaceCv.LIPIDMAPS,
    'hmdb': IdentifierNamespaceCv.HMDB,
    'metanetx': IdentifierNamespaceCv.METANETX,
}

def swisslipids_lipids():
    from pypath.inputs.swisslipids import swisslipids_lipids

    for rec in swisslipids_lipids():
        yield SilverEntity(
            source='swisslipids',
            entity_type=EntityTypeCv.LIPID,
            name=rec.name,
            synonyms=[s.strip() for s in rec.synonyms.split(';') if s.strip()] if rec.synonyms else None,
            identifiers=build_identifiers(
                rec,
                mapping=SWISSLIPIDS_IDENTIFIERS,
                filters={'inchi': lambda x: x != 'InChI=none'},
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
            references=[Reference(type=ReferenceTypeCv.PUBMED, value=pmid) for pmid in rec.pmids if pmid and pmid.strip()] if rec.pmids else None,
        )
