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
            'entity_id',
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
    
    INTERACTIONS_SETTINGS = {
        'searchableAttributes': [],  # No text search for interactions
        'filterableAttributes': [
            # Entity filtering (works on both members)
            'member_a_id',
            'member_b_id',
            'member_types',  # Array of strings - can filter on entity types

            # Direction/Sign (flattened from directions array)
            'has_direction',  # Boolean: len(directions) > 0
            'has_positive_sign',  # Boolean: any sign == 1 or sign == 0 (mixed)
            'has_negative_sign',  # Boolean: any sign == -1 or sign == 0 (mixed)

            # Evidence annotations (flattened)
            'interaction_annotation_terms',  # Array of term IDs/labels from all evidence
        ],
        'sortableAttributes': [],
        'displayedAttributes': ['*'],
        'pagination': {
            'maxTotalHits': 2000000
        }
    }
