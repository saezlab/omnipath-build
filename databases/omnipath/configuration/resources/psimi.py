"""
PSI-MI ontology to silver transformation.

Simple generator that maps PyPath namedtuples to SilverCvTerm namedtuples.
"""
from omnipath_build.utils.silver_schema import SilverCvTerm

__all__ = [
    'psimi_ontology',
]

def psimi_ontology():
    """
    Generator that yields SilverCvTerm records from PSI-MI PyPath data.

    Yields:
        SilverCvTerm namedtuples
    """
    from pypath.inputs.psimi import psimi_ontology as pypath_psimi

    for rec in pypath_psimi():
        yield SilverCvTerm(
            source='psimi',
            term_accession=rec.id,
            namespace='PSI-MI',
            term_name=rec.name,
            term_definition=rec.definition if rec.definition else None,
            term_definition_refs=[rec.definition_refs] if rec.definition_refs else None,
            term_synonyms=[s.strip() for s in rec.synonyms.split(',')] if rec.synonyms else None,
            term_parent_accessions=rec.parent_ids if rec.parent_ids else None,
            term_parent_names=rec.parent_names if rec.parent_names else None,
            term_alt_ids=rec.alt_ids if rec.alt_ids else None,
        )
