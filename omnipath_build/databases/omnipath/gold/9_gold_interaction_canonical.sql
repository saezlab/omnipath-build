-- Optimized gold_interaction_canonical.sql with aggregated statistics
-- Maps to Django model: db.models.Interaction
-- Includes useful aggregated metrics while maintaining performance

CREATE OR REPLACE TABLE gold.interaction_canonical AS
WITH entity_lookup AS (
    -- Create a minimal lookup table with just id and canonical_identifier
    SELECT id, canonical_identifier
    FROM gold.entity
),
interaction_pairs AS (
    -- Get all unique interaction pairs with their evidence
    SELECT 
        i.entity_a,
        i.entity_b,
        e1.id as entity_a_id,
        e2.id as entity_b_id,
        i.source_identifier,
        i.data_source,
        i.pubmed_id,
        i.interaction_type,
        (i.causal_mechanism IS NOT NULL OR i.causal_statement IS NOT NULL) AS is_directed
    FROM silver.interactions i
    INNER JOIN entity_lookup e1 ON i.entity_a = e1.canonical_identifier
    INNER JOIN entity_lookup e2 ON i.entity_b = e2.canonical_identifier
),
normalized_pairs AS (
    -- Normalize entity order and aggregate statistics
    SELECT
        LEAST(entity_a_id, entity_b_id) AS entity_a_id,
        GREATEST(entity_a_id, entity_b_id) AS entity_b_id,
        COUNT(DISTINCT source_identifier) AS evidence_count,
        COUNT(DISTINCT data_source) AS source_count,
        COUNT(DISTINCT pubmed_id) AS publication_count,
        STRING_AGG(DISTINCT data_source, '|' ORDER BY data_source) AS data_sources,
        STRING_AGG(DISTINCT interaction_type, '|' ORDER BY interaction_type) AS interaction_types,
        BOOL_OR(is_directed) AS has_directed_evidence,
    FROM interaction_pairs
    GROUP BY LEAST(entity_a_id, entity_b_id), GREATEST(entity_a_id, entity_b_id)
)
SELECT
    ROW_NUMBER() OVER (ORDER BY entity_a_id, entity_b_id) AS id,
    entity_a_id,
    entity_b_id,
    evidence_count,
    source_count,
    publication_count,
    data_sources,
    interaction_types,
    has_directed_evidence,
FROM normalized_pairs
ORDER BY entity_a_id, entity_b_id;