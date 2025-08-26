-- Gold data quality metrics table - Monitor data completeness and quality
-- Helps identify potential data issues and track improvements

CREATE OR REPLACE TABLE gold.data_quality_metrics AS
WITH entity_quality AS (
    SELECT
        'Entities without descriptions' AS metric,
        COUNT(*) AS count,
        ROUND(100.0 * COUNT(*) / (SELECT COUNT(*) FROM gold.entity), 2) AS percentage
    FROM gold.entity
    WHERE description IS NULL OR TRIM(description) = ''
    
    UNION ALL
    
    SELECT
        'Entities without taxonomy' AS metric,
        COUNT(*) AS count,
        ROUND(100.0 * COUNT(*) / (SELECT COUNT(*) FROM gold.entity), 2) AS percentage
    FROM gold.entity
    WHERE ncbi_tax_id_id IS NULL
    
    UNION ALL
    
    SELECT
        'Entities from interactions only' AS metric,
        COUNT(*) AS count,
        ROUND(100.0 * COUNT(*) / (SELECT COUNT(*) FROM gold.entity), 2) AS percentage
    FROM gold.entity
    WHERE description = 'Entity from interaction data'
),

interaction_quality AS (
    SELECT
        'Interactions without publications' AS metric,
        COUNT(DISTINCT ic.id) AS count,
        ROUND(100.0 * COUNT(DISTINCT ic.id) / (SELECT COUNT(*) FROM gold.interaction_canonical), 2) AS percentage
    FROM gold.interaction_canonical ic
    LEFT JOIN (
        SELECT interaction_id, COUNT(DISTINCT reference_id) as pub_count
        FROM gold.interaction_evidence
        WHERE reference_id IS NOT NULL
        GROUP BY interaction_id
    ) ie ON ic.id = ie.interaction_id
    WHERE ie.pub_count IS NULL OR ie.pub_count = 0
    
    UNION ALL
    
    SELECT
        'Interactions from single source' AS metric,
        COUNT(DISTINCT ic.id) AS count,
        ROUND(100.0 * COUNT(DISTINCT ic.id) / (SELECT COUNT(*) FROM gold.interaction_canonical), 2) AS percentage
    FROM gold.interaction_canonical ic
    JOIN (
        SELECT interaction_id, COUNT(DISTINCT data_source_id) as source_count
        FROM gold.interaction_evidence
        GROUP BY interaction_id
        HAVING COUNT(DISTINCT data_source_id) = 1
    ) ie ON ic.id = ie.interaction_id
    
),

evidence_quality AS (
    SELECT
        'Evidence without references' AS metric,
        COUNT(*) AS count,
        ROUND(100.0 * COUNT(*) / (SELECT COUNT(*) FROM gold.interaction_evidence), 2) AS percentage
    FROM gold.interaction_evidence
    WHERE reference_id IS NULL
    
    UNION ALL
    
    SELECT
        'Evidence without interaction type' AS metric,
        COUNT(*) AS count,
        ROUND(100.0 * COUNT(*) / (SELECT COUNT(*) FROM gold.interaction_evidence), 2) AS percentage
    FROM gold.interaction_evidence
    WHERE interaction_type_id IS NULL
    
    UNION ALL
    
    SELECT
        'Evidence without data source' AS metric,
        COUNT(*) AS count,
        ROUND(100.0 * COUNT(*) / (SELECT COUNT(*) FROM gold.interaction_evidence), 2) AS percentage
    FROM gold.interaction_evidence
    WHERE data_source_id IS NULL
),

identifier_quality AS (
    SELECT
        'Entities with single identifier' AS metric,
        COUNT(DISTINCT entity_id) AS count,
        ROUND(100.0 * COUNT(DISTINCT entity_id) / (SELECT COUNT(*) FROM gold.entity), 2) AS percentage
    FROM (
        SELECT entity_id, COUNT(*) AS id_count
        FROM gold.entity_identifier
        GROUP BY entity_id
        HAVING COUNT(*) = 1
    ) single_id_entities
),

complex_quality AS (
    SELECT
        'Complexes without members' AS metric,
        COUNT(*) AS count,
        ROUND(100.0 * COUNT(*) / NULLIF((
            SELECT COUNT(*) 
            FROM gold.entity e
            JOIN gold.cv_term ct ON e.entity_type_id = ct.id
            WHERE ct.accession = 'MI:0314'
        ), 0), 2) AS percentage
    FROM gold.entity e
    JOIN gold.cv_term ct ON e.entity_type_id = ct.id
    WHERE ct.accession = 'MI:0314'  -- complex
    AND NOT EXISTS (
        SELECT 1 FROM gold.entity_membership em
        WHERE em.parent_entity_id = e.id
    )
),

all_metrics AS (
    SELECT * FROM entity_quality
    UNION ALL
    SELECT * FROM interaction_quality
    UNION ALL
    SELECT * FROM evidence_quality
    UNION ALL
    SELECT * FROM identifier_quality
    UNION ALL
    SELECT * FROM complex_quality
)

SELECT
    ROW_NUMBER() OVER (ORDER BY percentage DESC) AS id,
    metric,
    count,
    percentage,
    CASE
        WHEN percentage >= 50 THEN 'critical'
        WHEN percentage >= 20 THEN 'warning'
        ELSE 'ok'
    END AS severity,
    CURRENT_TIMESTAMP AS last_updated
FROM all_metrics
ORDER BY percentage DESC;
