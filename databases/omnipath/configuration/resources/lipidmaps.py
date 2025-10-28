from omnipath_build.utils.cv_term_enums import (
    IdentifierNamespaceCv,
    EntityTypeCv,
)
from omnipath_build.utils.silver_schema import SilverEntity, Identifier
from omnipath_build.utils.identifier_builders import build_identifiers
from omnipath_build.utils.annotation_builders import build_annotations

__all__ = [
    'lipidmaps_lipids',
]

# Identifier mapping for LipidMaps
LIPIDMAPS_IDENTIFIERS = {
    'id': IdentifierNamespaceCv.LIPIDMAPS,
    'name': IdentifierNamespaceCv.NAME,
    'inchikey': IdentifierNamespaceCv.STANDARD_INCHI_KEY,
    'inchi': IdentifierNamespaceCv.STANDARD_INCHI,
    'smiles': IdentifierNamespaceCv.SMILES,
    'chebi': IdentifierNamespaceCv.CHEBI,
    'pubchem': IdentifierNamespaceCv.PUBCHEM,
}

def lipidmaps_lipids():
    from pypath.inputs.lipidmaps import lipidmaps_lipids as pypath_lipids
    for rec in pypath_lipids():
        # Parse synonyms from semicolon-separated string
        synonyms = [s.strip() for s in rec.synonyms.split('; ') if s.strip()] if rec.synonyms else None

        # Build base identifiers
        identifiers = build_identifiers(rec, mapping=LIPIDMAPS_IDENTIFIERS) or []

        # Add synonyms as identifiers
        if synonyms:
            identifiers.extend([Identifier(type=IdentifierNamespaceCv.SYNONYM, value=syn) for syn in synonyms])

        yield SilverEntity(
            source='lipidmaps',
            entity_type=EntityTypeCv.LIPID,
            identifiers=identifiers if identifiers else None,
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
