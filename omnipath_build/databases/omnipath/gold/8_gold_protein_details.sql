-- Gold protein details table - Additional details about proteins
-- Maps to Django model: db.models.ProteinDetail

CREATE OR REPLACE TABLE gold.protein_details AS
WITH protein_details AS (
    SELECT
        ROW_NUMBER() OVER (ORDER BY e.canonical_identifier) AS id,
        ge.id AS entity_id,
        e.length AS sequence_length,
        e.mass AS molecular_weight
    FROM silver.entities e
    JOIN gold.entity ge ON e.canonical_identifier = ge.canonical_identifier
    JOIN gold.cv_term ct ON ge.entity_type_id = ct.id
    WHERE ct.accession = 'MI:0326'  -- protein
        AND (e.length IS NOT NULL OR e.mass IS NOT NULL)
)

SELECT
    id,
    entity_id,
    sequence_length,
    molecular_weight
FROM protein_details
ORDER BY entity_id;
