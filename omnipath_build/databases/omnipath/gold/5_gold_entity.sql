-- Gold entity table using CV term accessions from silver
-- Maps to Django model: db.models.Entity

CREATE OR REPLACE TABLE gold.entity AS
WITH silver_entities AS (
    -- All entities from the silver layer (already have CV term accessions)
    SELECT
        ROW_NUMBER() OVER (ORDER BY e.canonical_identifier) AS id,
        e.canonical_identifier,
        cit.id AS canonical_identifier_type_id,
        et.id AS entity_type_id,
        nt.id AS ncbi_tax_id_id,
        e.description,
        e.alt_id
    FROM silver.entities e
    LEFT JOIN gold.cv_term cit 
        ON cit.accession = e.canonical_identifier_type
    LEFT JOIN gold.cv_term et 
        ON et.accession = e.entity_type
    LEFT JOIN gold.cv_term nt 
        ON nt.accession = e.ncbi_tax_id
),

interaction_only_entities AS (
    -- Extract entities referenced in interactions but not in the entity table
    -- Now using entity types from silver layer (either provided or inferred)
    SELECT DISTINCT
        entity_id,
        entity_type,
        -- For ID type, prefer the explicitly mapped type over generic inference
        COALESCE(entity_id_type, 
            CASE 
                WHEN entity_id LIKE '%:%' THEN 'OM00015'  -- default to uniprot for prefixed IDs
                ELSE 'OM00015'  -- default to uniprot
            END
        ) AS canonical_identifier_type
    FROM (
        SELECT 
            entity_a AS entity_id,
            entity_a_type AS entity_type,
            entity_a_id_type AS entity_id_type
        FROM silver.interactions
        WHERE entity_a_type IS NOT NULL
        UNION
        SELECT 
            entity_b AS entity_id,
            entity_b_type AS entity_type,
            entity_b_id_type AS entity_id_type
        FROM silver.interactions
        WHERE entity_b_type IS NOT NULL
    ) all_interaction_entities
    WHERE entity_id NOT IN (SELECT canonical_identifier FROM silver.entities)
    AND entity_id IS NOT NULL 
    AND TRIM(entity_id) != ''
),

new_entities AS (
    -- Create new entity records for interaction-only entities with CV term FKs
    SELECT
        (SELECT COALESCE(MAX(id), 0) FROM silver_entities) + 
        ROW_NUMBER() OVER (ORDER BY ioe.entity_id) AS id,
        ioe.entity_id AS canonical_identifier,
        cit.id AS canonical_identifier_type_id,
        et.id AS entity_type_id,
        NULL AS ncbi_tax_id_id,
        'Entity from interaction data' AS description,
        NULL AS alt_id
    FROM interaction_only_entities ioe
    LEFT JOIN gold.cv_term cit 
        ON cit.accession = ioe.canonical_identifier_type
    LEFT JOIN gold.cv_term et 
        ON et.accession = ioe.entity_type
),

all_entities AS (
    SELECT * FROM silver_entities
    WHERE canonical_identifier_type_id IS NOT NULL 
        AND entity_type_id IS NOT NULL
    UNION ALL
    SELECT * FROM new_entities
    WHERE canonical_identifier_type_id IS NOT NULL 
        AND entity_type_id IS NOT NULL
)

SELECT
    id,
    canonical_identifier,
    canonical_identifier_type_id,
    entity_type_id,
    ncbi_tax_id_id,
    description,
    alt_id
FROM all_entities
ORDER BY id;