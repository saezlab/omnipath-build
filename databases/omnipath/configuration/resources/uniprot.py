from omnipath_build.utils.cv_term_enums import (
    IdentifierNamespaceCv,
    EntityTypeCv,
)
from omnipath_build.utils.silver_schema import SilverEntity, Identifier, Reference
from omnipath_build.utils.annotation_builders import build_annotations

__all__ = [
    'uniprot_proteins',
]


def uniprot_proteins():
    """
    Yields UniProt protein records as SilverEntity objects.

    Converts comprehensive UniProt data including:
    - Primary and secondary identifiers
    - Gene names
    - Protein annotations (function, PTMs, subcellular location, etc.)
    - Cross-references to external databases
    - Literature references
    """
    from pypath.inputs.new_uniprot import uniprot_data
    import re

    for rec in uniprot_data():
        # Build comprehensive identifier list
        identifiers = []

        # Primary UniProt accession
        if rec.accession:
            identifiers.append(Identifier(
                type=IdentifierNamespaceCv.UNIPROT,
                value=rec.accession
            ))

        # Entry name
        if rec.entry_name:
            identifiers.append(Identifier(
                type=IdentifierNamespaceCv.UNIPROT,
                value=rec.entry_name
            ))

        # Gene names - primary
        if rec.gene_primary:
            identifiers.append(Identifier(
                type=IdentifierNamespaceCv.GENE_NAME_PRIMARY,
                value=rec.gene_primary
            ))
            # Also add as HGNC_SYMBOL for compatibility
            identifiers.append(Identifier(
                type=IdentifierNamespaceCv.HGNC_SYMBOL,
                value=rec.gene_primary
            ))

        # Gene name synonyms
        if rec.gene_synonym:
            for synonym in rec.gene_synonym.split():
                synonym = synonym.strip()
                if synonym:
                    identifiers.append(Identifier(
                        type=IdentifierNamespaceCv.GENE_NAME_SYNONYM,
                        value=synonym
                    ))

        # Add cross-references as identifiers
        xref_mappings = [
            (rec.xref_ensembl, IdentifierNamespaceCv.ENSEMBL),
            (rec.xref_refseq, IdentifierNamespaceCv.REFSEQ),
            (rec.xref_pdb, IdentifierNamespaceCv.PDB),
            (rec.xref_alphafolddb, IdentifierNamespaceCv.ALPHAFOLDDB),
            (rec.xref_kegg, IdentifierNamespaceCv.KEGG),
            (rec.xref_chembl, IdentifierNamespaceCv.CHEMBL),
            (rec.xref_signor, IdentifierNamespaceCv.SIGNOR),
            (rec.xref_intact, IdentifierNamespaceCv.INTACT),
            (rec.xref_biogrid, IdentifierNamespaceCv.BIOGRID),
            (rec.xref_complexportal, IdentifierNamespaceCv.COMPLEXPORTAL),
        ]

        for xref_value, id_type in xref_mappings:
            if xref_value:
                # Handle multiple values (semicolon-separated)
                for value in xref_value.split(';'):
                    value = value.strip()
                    if value:
                        identifiers.append(Identifier(
                            type=id_type,
                            value=value
                        ))

        # Parse protein name and extract synonyms from parentheses
        primary_name = None
        protein_synonyms = []

        if rec.protein_name:
            # Extract primary name (text before first parenthesis)
            primary_name_match = re.match(r'^([^(]+)', rec.protein_name)
            if primary_name_match:
                primary_name = primary_name_match.group(1).strip()

            # Extract all text within parentheses as protein name synonyms
            synonym_matches = re.findall(r'\(([^)]+)\)', rec.protein_name)
            for synonym in synonym_matches:
                synonym = synonym.strip()
                if synonym:
                    protein_synonyms.append(synonym)

        # Build comprehensive annotations
        annotations = build_annotations(
            rec,
            ('length', 'protein_length', 'aa'),
            ('mass', 'molecular_mass', 'Da'),
            ('cc_function', 'function'),
            ('cc_subcellular_location', 'subcellular_location'),
            ('cc_ptm', 'post_translational_modification'),
            ('cc_disease', 'disease_involvement'),
            ('cc_pathway', 'pathway'),
            ('cc_activity_regulation', 'activity_regulation'),
            ('ft_mutagen', 'mutagenesis'),
            ('ft_transmem', 'transmembrane'),
            ('protein_families', 'protein_family'),
            ('keyword', 'keywords'),
            ('ec', 'ec_number'),
            ('go', 'gene_ontology'),
        )

        # Build references from PubMed IDs
        references = None
        if rec.lit_pubmed_id:
            references = []
            for pmid in rec.lit_pubmed_id.split(';'):
                pmid = pmid.strip()
                if pmid:
                    references.append(Reference(
                        type='pmid',
                        value=pmid
                    ))

        yield SilverEntity(
            source='uniprot',
            entity_type=EntityTypeCv.PROTEIN,
            identifiers=identifiers if identifiers else None,
            name=primary_name,
            synonyms=protein_synonyms if protein_synonyms else None,
            annotations=annotations,
            references=references,
        )
