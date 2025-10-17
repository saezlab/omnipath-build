from omnipath_build.utils.silver_schema import SilverEntity, IdentifierType
from omnipath_build.utils.identifier_builders import build_identifiers
from omnipath_build.utils.annotation_builders import build_annotations

__all__ = [
    'hmdb_entities',
]

# Identifier mapping for HMDB
HMDB_IDENTIFIERS = {
    'inchikey': IdentifierType.INCHIKEY,
    'inchi': IdentifierType.INCHI,
    'smiles': IdentifierType.SMILES,
    'chebi_id': IdentifierType.CHEBI,
    'pubchem_compound_id': IdentifierType.PUBCHEM,
    'kegg_id': IdentifierType.KEGG,
    'drugbank_id': IdentifierType.DRUGBANK,
    'cas_registry_number': IdentifierType.CAS,
}

def hmdb_entities():
    from pypath.inputs.hmdb.metabolites import compounds_for_metabo

    for rec in compounds_for_metabo():
        yield SilverEntity(
            source='hmdb',
            entity_type='compound',
            name=rec.traditional_iupac,
            synonyms=rec.synonyms,
            identifiers=build_identifiers(
                rec,
                mapping=HMDB_IDENTIFIERS,
                transformers={'chebi_id': lambda x: f"CHEBI:{x}"},
                accession_attr='accession',
            ),
            annotations=build_annotations(
                rec,
                ('monisotopic_molecular_weight', 'monoisotopic_molecular_weight', 'Da'),
                ('average_molecular_weight', 'average_molecular_weight', 'Da'),
                'chemical_formula',
                'iupac_name',
            ),
            references=rec.general_references,
        )
