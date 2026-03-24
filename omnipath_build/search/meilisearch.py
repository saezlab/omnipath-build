"""Meilisearch index configuration settings."""


class MeilisearchSettings:
    """Meilisearch index configuration settings."""

    ENTITIES_SETTINGS = {
        'searchableAttributes': ['names', 'synonyms', 'gene_symbols', 'descriptions'],
        'filterableAttributes': [
            'entity_id',
            'entity_type',
            'sources',
            'ncbi_tax_id',
            'ontology_terms',
        ],
        'displayedAttributes': [
            'entity_id',
            'entity_type',
            'names',
            'synonyms',
            'gene_symbols',
            'descriptions',
            'sources',
            'identifiers',
            'ncbi_tax_id',
            'num_interactions',
        ],
        'rankingRules': [
            'proximity',
            'words',
            'attribute',
            'typo',
            'sort',
            'exactness',
            'num_interactions:desc',
        ],
        'faceting': {
            'maxValuesPerFacet': 100,
            'sortFacetValuesBy': {
                '*': 'alpha',
                'ontology_terms': 'count',
            },
        },
        'pagination': {'maxTotalHits': 2000000},
    }

    INTERACTIONS_SETTINGS = {
        'searchableAttributes': [],  # No text search for interactions
        'filterableAttributes': [
            # Numeric interaction identifier (for export/subsetting pipelines)
            'interaction_id',
            # Entity filtering (works on both members)
            'member_a_id',
            'member_b_id',
            'interaction_type',  # Canonical pair type (e.g., Protein|Protein)
            'is_directed',
            'sign',
            # Evidence annotations (flattened)
            'interaction_annotation_terms',  # Array of interaction-level term IDs/labels
            'participant_annotation_terms',
            # Source filtering
            'sources',
        ],
        'sortableAttributes': [],
        'rankingRules': [
            'evidence_count:desc',
        ],
        'displayedAttributes': ['*'],
        'pagination': {'maxTotalHits': 2000000},
    }

    ASSOCIATIONS_SETTINGS = {
        'searchableAttributes': [
            'parent_name',
            'member_name',
        ],
        'filterableAttributes': [
            # Numeric association identifier (for export/subsetting pipelines)
            'association_id',
            # Entity filtering
            'parent_entity_id',
            'parent_entity_type',
            'member_entity_id',
            'member_entity_type',
            # Source filtering
            'sources',
            # Annotation terms (flattened for filtering)
            'association_annotation_terms',
        ],
        'sortableAttributes': [
            'parent_name',
            'member_name',
        ],
        'displayedAttributes': ['*'],
        'pagination': {'maxTotalHits': 2000000},
    }

    SOURCES_SETTINGS = {
        'searchableAttributes': [
            'source_ref',
            'source',
            'source_name',
            'resource_description',
        ],
        'filterableAttributes': [
            'source_ref',
            'source',
            'license_cv',
            'update_category_cv',
            'content_category_cv_terms',
            'finished_at',
        ],
        'sortableAttributes': [
            'source_ref',
            'source',
            'total_records',
            'finished_at',
        ],
        'displayedAttributes': ['*'],
        'pagination': {'maxTotalHits': 2000000},
    }
