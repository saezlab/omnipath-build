-- Gold interactions table - deduplicated and aggregated from silver layer
-- Maps to final analytics-ready interaction data

CREATE OR REPLACE TABLE gold.interactions AS
WITH deduplicated AS (
    SELECT 
        -- Canonical interaction pair (alphabetically sorted)
        CASE 
            WHEN entity_a <= entity_b THEN entity_a 
            ELSE entity_b 
        END AS entity_a,
        CASE 
            WHEN entity_a <= entity_b THEN entity_b 
            ELSE entity_a 
        END AS entity_b,
        
        -- Aggregate source information
        STRING_AGG(DISTINCT source_database, '|' ORDER BY source_database) as sources,
        STRING_AGG(DISTINCT effect, '|' ORDER BY effect) as effects,
        STRING_AGG(DISTINCT mechanism, '|' ORDER BY mechanism) as mechanisms,
        STRING_AGG(DISTINCT pubmed_ids, '|' ORDER BY pubmed_ids) as all_pubmeds,
        
        -- Evidence metrics
        COUNT(*) as evidence_count,
        COUNT(DISTINCT source_database) as source_count,
        MAX(confidence) as max_confidence,
        AVG(confidence) as avg_confidence,
        
        -- Quality indicators
        COUNT(DISTINCT pubmed_ids) as publication_count,
        MAX(loaded_at) as last_updated
        
    FROM silver.interactions
    WHERE entity_a IS NOT NULL AND entity_b IS NOT NULL
    GROUP BY 
        CASE WHEN entity_a <= entity_b THEN entity_a ELSE entity_b END,
        CASE WHEN entity_a <= entity_b THEN entity_b ELSE entity_a END
)
SELECT 
    entity_a,
    entity_b,
    sources,
    effects,
    mechanisms,
    all_pubmeds,
    evidence_count,
    source_count,
    max_confidence,
    ROUND(avg_confidence, 3) as avg_confidence,
    publication_count,
    last_updated,
    
    -- Calculate overall interaction confidence score
    ROUND(
        (max_confidence * 0.4) + 
        (LEAST(source_count / 2.0, 1.0) * 0.3) + 
        (LEAST(evidence_count / 10.0, 1.0) * 0.3), 
        3
    ) as overall_confidence
    
FROM deduplicated
ORDER BY evidence_count DESC, overall_confidence DESC;