from omnipath_build.utils.silver_schema import SilverEntity, SilverInteraction, IdentifierType
from omnipath_build.utils.identifier_builders import build_identifiers
from omnipath_build.utils.annotation_builders import build_annotations

__all__ = [
    'signor_interactions',
    'signor_complexes',
    'signor_protein_families',
    'signor_phenotypes',
    'signor_stimuli',
]

# Identifier mappings for SIGNOR
SIGNOR_PROTEIN_IDENTIFIERS = {
    'uniprot': IdentifierType.UNIPROT,
}


def signor_interactions():
    """
    Yields SIGNOR interactions as SilverInteraction objects.

    SIGNOR provides causal interactions in causalTab (MITAB) format.
    """
    from pypath.inputs.new_signor import signor_interactions as pypath_signor

    for rec in pypath_signor():
        # Extract UniProt IDs from interactors
        # MITAB format: id_a and id_b contain identifiers with prefixes
        def parse_uniprot(identifier: str | None) -> str | None:
            if not identifier:
                return None
            if identifier.startswith('uniprotkb:'):
                return identifier.replace('uniprotkb:', '')
            return None

        entity_a_id = parse_uniprot(getattr(rec, 'id_a', None))
        entity_b_id = parse_uniprot(getattr(rec, 'id_b', None))

        # Skip if we don't have both identifiers
        if not entity_a_id or not entity_b_id:
            continue

        # Determine entity types from MITAB metadata
        def extract_interactor_type(field: str | None) -> str | None:
            if not field or field == '-':
                return None
            # Expected format psi-mi:"MI:xxxx"(term)
            if '(' in field and ')' in field:
                term = field.split('(', 1)[1].split(')', 1)[0].strip()
                return term if term else None
            return field

        entity_a_type_raw = extract_interactor_type(getattr(rec, 'interactor_type_a', None))
        entity_b_type_raw = extract_interactor_type(getattr(rec, 'interactor_type_b', None))

        def interactor_type_to_entity_type(term: str | None) -> str:
            if not term:
                return 'protein'
            term_lower = term.lower()
            if 'protein' in term_lower:
                return 'protein'
            if 'gene' in term_lower:
                return 'gene'
            if 'rna' in term_lower:
                return 'rna'
            if 'complex' in term_lower:
                return 'complex'
            if 'phenotype' in term_lower:
                return 'phenotype'
            if 'small molecule' in term_lower or 'chemical' in term_lower:
                return 'compound'
            return term_lower.replace(' ', '_')

        entity_a_type = interactor_type_to_entity_type(entity_a_type_raw)
        entity_b_type = interactor_type_to_entity_type(entity_b_type_raw)

        from omnipath_build.utils.silver_schema import Identifier

        entity_a_entity = SilverEntity(
            source='signor',
            entity_type=entity_a_type,
            identifiers=[Identifier(type=IdentifierType.UNIPROT, value=entity_a_id)],
            annotations=[{"term": "mitab_interactor_type", "value": entity_a_type_raw}] if entity_a_type_raw else None,
        )

        entity_b_entity = SilverEntity(
            source='signor',
            entity_type=entity_b_type,
            identifiers=[Identifier(type=IdentifierType.UNIPROT, value=entity_b_id)],
            annotations=[{"term": "mitab_interactor_type", "value": entity_b_type_raw}] if entity_b_type_raw else None,
        )

        # Determine interaction type from MITAB field
        interaction_type = None
        if hasattr(rec, 'interaction_type') and rec.interaction_type:
            # Remove PSI-MI prefix if present
            interaction_type = rec.interaction_type.split('(')[-1].rstrip(')') if '(' in rec.interaction_type else rec.interaction_type

        # Determine sign (stimulation vs inhibition)
        sign = None
        if hasattr(rec, 'effect') and rec.effect:
            effect_lower = rec.effect.lower()
            if 'up-regulates' in effect_lower or 'stimulat' in effect_lower or 'activat' in effect_lower:
                sign = 'positive'
            elif 'down-regulates' in effect_lower or 'inhibit' in effect_lower or 'repress' in effect_lower:
                sign = 'negative'

        # Extract PubMed references
        references = []
        if hasattr(rec, 'pmid') and rec.pmid:
            pmids = rec.pmid.split('|') if isinstance(rec.pmid, str) else [rec.pmid]
            for pmid in pmids:
                # Remove pubmed: prefix if present
                pmid_clean = pmid.replace('pubmed:', '').strip()
                if pmid_clean:
                    references.append(pmid_clean)

        # Build interaction annotations - using list comprehension for "key" style annotations
        interaction_annotations = []
        if hasattr(rec, 'mechanism') and rec.mechanism:
            interaction_annotations.append({"key": "mechanism", "value": rec.mechanism})
        if hasattr(rec, 'effect') and rec.effect:
            interaction_annotations.append({"key": "effect", "value": rec.effect})
        if hasattr(rec, 'detection_method') and rec.detection_method:
            interaction_annotations.append({"key": "detection_method", "value": rec.detection_method})
        if hasattr(rec, 'score') and rec.score:
            interaction_annotations.append({"key": "score", "value": str(rec.score)})

        yield SilverInteraction(
            source='signor',
            entity_a=entity_a_entity,
            entity_b=entity_b_entity,
            interaction_type=interaction_type if interaction_type else 'causal_interaction',
            is_directed=True,
            direction='a_to_b',
            sign=sign,
            interaction_annotations=interaction_annotations if interaction_annotations else None,
            references=references if references else None,
        )


def signor_complexes():
    """
    Yields SIGNOR complexes as SilverEntity objects.
    """
    from pypath.inputs.new_signor import signor_complexes as pypath_complexes
    from omnipath_build.utils.silver_schema import Identifier

    for rec in pypath_complexes():
        yield SilverEntity(
            source='signor',
            entity_type='complex',
            name=rec.name,
            identifiers=[Identifier(type=IdentifierType.ACCESSION, value=rec.complex_id)],
            annotations=build_annotations(
                rec,
                ('components', 'components', None, lambda x: ','.join(x)),
                ('components', 'component_count', None, lambda x: str(len(x))),
            ),
        )


def signor_protein_families():
    """
    Yields SIGNOR protein families as SilverEntity objects.
    """
    from pypath.inputs.new_signor import signor_protein_families as pypath_families
    from omnipath_build.utils.silver_schema import Identifier

    for rec in pypath_families():
        yield SilverEntity(
            source='signor',
            entity_type='protein_family',
            name=rec.name,
            identifiers=[Identifier(type=IdentifierType.ACCESSION, value=rec.family_id)],
            annotations=build_annotations(
                rec,
                ('members', 'members', None, lambda x: ','.join(x)),
                ('members', 'member_count', None, lambda x: str(len(x))),
            ),
        )


def signor_phenotypes():
    """
    Yields SIGNOR phenotypes as SilverEntity objects.
    """
    from pypath.inputs.new_signor import signor_phenotypes as pypath_phenotypes
    from omnipath_build.utils.silver_schema import Identifier

    for rec in pypath_phenotypes():
        yield SilverEntity(
            source='signor',
            entity_type='phenotype',
            name=rec.name,
            identifiers=[Identifier(type=IdentifierType.ACCESSION, value=rec.phenotype_id)],
            annotations=build_annotations(rec, 'description'),
        )


def signor_stimuli():
    """
    Yields SIGNOR stimuli as SilverEntity objects.
    """
    from pypath.inputs.new_signor import signor_stimuli as pypath_stimuli
    from omnipath_build.utils.silver_schema import Identifier

    for rec in pypath_stimuli():
        yield SilverEntity(
            source='signor',
            entity_type='stimulus',
            name=rec.name,
            identifiers=[Identifier(type=IdentifierType.ACCESSION, value=rec.stimulus_id)],
            annotations=build_annotations(rec, 'description'),
        )
