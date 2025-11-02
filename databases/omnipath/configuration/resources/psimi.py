from omnipath_build.utils.silver_schema import SilverEntity, Identifier, MemberOf, Reference
from omnipath_build.utils.cv_term_enums import EntityTypeCv, IdentifierNamespaceCv, MembershipRoleCv, ReferenceTypeCv

__all__ = [
    'psimi_ontology',
]

def psimi_ontology():
    from pypath.inputs.psimi import psimi_ontology as pypath_psimi

    for rec in pypath_psimi():
        # Build identifiers list
        identifiers = [
            Identifier(type=IdentifierNamespaceCv.CV_TERM_ACCESSION, value=rec.id)
        ]

        # Add primary name
        if rec.name:
            identifiers.append(
                Identifier(type=IdentifierNamespaceCv.NAME, value=rec.name)
            )

        # Add synonyms
        if rec.synonyms:
            for synonym in rec.synonyms:
                identifiers.append(
                    Identifier(type=IdentifierNamespaceCv.SYNONYM, value=synonym)
                )

        # Add alternative IDs
        if rec.alt_ids:
            for alt_id in rec.alt_ids:
                identifiers.append(
                    Identifier(type=IdentifierNamespaceCv.CV_TERM_ACCESSION, value=alt_id)
                )

        # Build is_member_of list for parent terms
        is_member_of = None
        if rec.parent_ids:
            is_member_of = [
                MemberOf(
                    identifier=parent_id,
                    identifier_type=IdentifierNamespaceCv.CV_TERM_ACCESSION,
                    role=MembershipRoleCv.IS_A
                )
                for parent_id in rec.parent_ids
            ]

        # Add definition as annotation
        annotations = None
        if rec.definition:
            annotations = [{'term': 'definition', 'value': rec.definition}]

        # Add definition references
        references = None
        if rec.definition_refs:
            # definition_refs is a string like "[PMID:12345,PMID:67890]"
            import re
            pmids = re.findall(r'PMID:(\d+)', rec.definition_refs)
            if pmids:
                references = [
                    Reference(type=ReferenceTypeCv.PUBMED, value=pmid)
                    for pmid in pmids
                ]

        yield SilverEntity(
            source='psimi',
            entity_type=EntityTypeCv.CV_TERM,
            identifiers=identifiers,
            is_member_of=is_member_of,
            annotations=annotations,
            references=references,
        )
