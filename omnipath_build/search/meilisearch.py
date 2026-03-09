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
            'cv_terms_go',
            'cv_terms_mi',
            'cv_terms_om',
            'cv_terms_hp',
            'cv_terms_kw',
        ],
        'displayedAttributes': ['*'],
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
                'cv_terms_go': 'count',
                'cv_terms_mi': 'count',
                'cv_terms_om': 'count',
                'cv_terms_hp': 'count',
                'cv_terms_kw': 'count',
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
            # Direction/Sign (flattened from directions array)
            'has_direction',  # Boolean: len(directions) > 0
            'has_positive_sign',  # Boolean: any sign == 1 or sign == 0 (mixed)
            'has_negative_sign',  # Boolean: any sign == -1 or sign == 0 (mixed)
            # Evidence annotations (flattened)
            'interaction_annotation_terms',  # Array of interaction-level term IDs/labels
            'participant_annotation_terms_go',
            'participant_annotation_terms_mi',
            'participant_annotation_terms_om',
            'participant_annotation_terms_hp',
            'participant_annotation_terms_kw',
            # Source filtering
            'sources',
        ],
        'sortableAttributes': [],
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
