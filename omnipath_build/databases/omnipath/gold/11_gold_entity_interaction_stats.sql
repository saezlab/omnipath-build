-- Gold entity interaction statistics table - Pre-computed statistics for entities
-- Analytical table for quick lookups and performance

CREATE OR REPLACE TABLE gold.entity_interaction_stats AS
WITH interaction_counts AS (
    -- Count interactions per entity
    SELECT
        entity_id,
        COUNT(DISTINCT interaction_id) AS interaction_count,
        COUNT(DISTINCT other_entity_id) AS partner_count,
        COUNT(DISTINCT data_source_id) AS source_count
    FROM (
        -- Get all interactions where entity is participant A
        SELECT 
            ic.entity_a_id AS entity_id,
            ic.id AS interaction_id,
            ic.entity_b_id AS other_entity_id,
            ie.data_source_id
        FROM gold.interaction_canonical ic
        JOIN gold.interaction_evidence ie ON ic.id = ie.interaction_id
        
        UNION ALL
        
        -- Get all interactions where entity is participant B
        SELECT 
            ic.entity_b_id AS entity_id,
            ic.id AS interaction_id,
            ic.entity_a_id AS other_entity_id,
            ie.data_source_id
        FROM gold.interaction_canonical ic
        JOIN gold.interaction_evidence ie ON ic.id = ie.interaction_id
    ) all_interactions
    GROUP BY entity_id
),

entity_stats AS (
    SELECT
        e.id AS entity_id,
        e.canonical_identifier,
        ct_type.name AS entity_type,
        COALESCE(ic.interaction_count, 0) AS interaction_count,
        COALESCE(ic.partner_count, 0) AS partner_count,
        COALESCE(ic.source_count, 0) AS source_count,
        -- Calculate hub score (entities with many partners)
        CASE
            WHEN COALESCE(ic.partner_count, 0) >= 100 THEN 'major_hub'
            WHEN COALESCE(ic.partner_count, 0) >= 50 THEN 'hub'
            WHEN COALESCE(ic.partner_count, 0) >= 10 THEN 'intermediate'
            ELSE 'peripheral'
        END AS hub_category
    FROM gold.entity e
    LEFT JOIN gold.cv_term ct_type ON e.entity_type_id = ct_type.id
    LEFT JOIN interaction_counts ic ON e.id = ic.entity_id
)

SELECT
    ROW_NUMBER() OVER (ORDER BY interaction_count DESC, entity_id) AS id,
    entity_id,
    canonical_identifier,
    entity_type,
    interaction_count,
    partner_count,
    source_count,
    hub_category
FROM entity_stats
ORDER BY interaction_count DESC, entity_id;