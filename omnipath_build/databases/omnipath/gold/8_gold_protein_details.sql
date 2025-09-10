-- This query will be executed by DuckDB and written to gold/data/protein_details.parquet
-- Gold protein details table - Additional details about proteins
-- Maps to Django model: db.models.ProteinDetail


WITH protein_details AS (
    SELECT
        ROW_NUMBER() OVER (ORDER BY e.canonical_identifier) AS id,
        ge.id AS entity_id,
        e.length AS sequence_length,
        e.mass AS molecular_weight
    FROM read_parquet('silver/data/entities/*.parquet') AS e
    JOIN read_parquet('gold/data/entity.parquet') AS ge ON e.canonical_identifier = ge.canonical_identifier
    JOIN read_parquet('gold/data/cv_term.parquet') AS ct ON ge.entity_type_id = ct.id
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
