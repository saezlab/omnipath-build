class MeilisearchSettings:
    """Meilisearch index configuration settings."""
    
    ENTITIES_SETTINGS = {
        'searchableAttributes': [
            'names',
            'synonyms',
            'gene_symbols',
            'descriptions'
        ],
        'filterableAttributes': [
            'entity_type',
            'sources',
            'ncbi_tax_id'
        ],
        'displayedAttributes': ['*'],
        'rankingRules': [
            'proximity',
            'words',
            'attribute',
            'typo',
            'sort',
            'exactness'
        ]
    }
    
    CV_TERMS_SETTINGS = {
        'searchableAttributes': [
            'name',
            'synonyms',
            'namespace',
            'definition',
            'associated_entity_ids'
        ],
        'filterableAttributes': [
            'namespace',
            'id'
        ],
        'sortableAttributes': [
            'name',
            'namespace'
        ],
        'displayedAttributes': ['*'],
        'rankingRules': [
            'words',
            'typo',
            'proximity',
            'attribute',
            'sort',
            'exactness'
        ]
    }
    
    INTERACTIONS_SETTINGS = {
        'searchableAttributes': [],  # No search for interactions
        'filterableAttributes': [
            'entity_ids',
            'interaction_types_facet',
            'data_sources_facet',
            'interactor_types_facet',
            'detection_methods_facet',
            'causal_statements_facet',
            'causal_mechanisms_facet',
            'signs',
            'consensus_sign',
            'is_directed',
            'consensus_direction',
            'evidence_count'
        ],
        'sortableAttributes': ['evidence_count', 'id'],
        'displayedAttributes': ['*'],
        'pagination': {
            'maxTotalHits': 2000000
        }
    }
