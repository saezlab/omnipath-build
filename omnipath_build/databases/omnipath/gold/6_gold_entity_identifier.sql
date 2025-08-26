-- Gold entity identifier table - All identifiers for entities
-- Maps to Django model: db.models.EntityIdentifier
-- Single source of truth: pulls ONLY from silver.id_mapping table
-- Simple approach: one entity per source_id from silver.id_mapping

CREATE OR REPLACE TABLE gold.entity_identifier AS
WITH expanded_id_mappings AS (
    -- Expand pipe-separated target_ids from silver.id_mapping into individual identifiers
    SELECT 
        s.source_id,
        trim(target_value) as target_id,
        s.mapping_source
    FROM silver.id_mapping s
    CROSS JOIN unnest(string_to_array(s.target_id, '|')) as t(target_value)
    WHERE trim(target_value) IS NOT NULL 
      AND trim(target_value) != ''
      AND trim(target_value) LIKE 'OM%:%'  -- Ensure proper OM prefix format
),

-- Create one entity per unique source_id 
entities AS (
    SELECT DISTINCT
        ROW_NUMBER() OVER (ORDER BY source_id) as entity_id,
        source_id
    FROM expanded_id_mappings
),

-- Collect all identifiers for each entity (source + all targets)
all_entity_identifiers AS (
    -- Include the source identifier itself
    SELECT 
        e.entity_id,
        e.source_id as identifier_value
    FROM entities e
    
    UNION ALL
    
    -- Include all target identifiers
    SELECT 
        e.entity_id,
        m.target_id as identifier_value
    FROM entities e
    JOIN expanded_id_mappings m ON e.source_id = m.source_id
)

SELECT
    ROW_NUMBER() OVER (ORDER BY entity_id, identifier_value) AS id,
    entity_id,
    COALESCE(cv.id, 999) as identifier_type_id,  -- Default to 999 for unknown types
    split_part(identifier_value, ':', 2) as value
FROM all_entity_identifiers aei
LEFT JOIN gold.cv_term cv ON split_part(aei.identifier_value, ':', 1) = cv.accession
ORDER BY entity_id, identifier_type_id, value;