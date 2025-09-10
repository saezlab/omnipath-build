-- Gold controlled vocabulary term table
-- Maps to Django model: db.models.ControlledVocabularyTerm
-- This query will be executed by DuckDB and written to gold/data/cv_term.parquet

WITH cv_terms_base AS (
    -- First create base table with IDs
    SELECT
        ROW_NUMBER() OVER (ORDER BY ct.accession) AS id,
        gcn.id AS namespace_id,
        ct.accession,
        ct.name,
        ct.category_accession,
        ct.definition,
        ct.is_obsolete,
        ct.replaced_by_accession,
        ct.comment
    FROM read_parquet('silver/data/cv_term/*.parquet') AS ct
    INNER JOIN read_parquet('gold/data/cv_namespace.parquet') AS gcn ON ct.namespace = gcn.name
),
category_lookup AS (
    -- Create lookup for category IDs
    SELECT DISTINCT 
        accession,
        id AS category_id
    FROM cv_terms_base
    WHERE accession IS NOT NULL
),
replaced_by_lookup AS (
    -- Create lookup for replaced_by IDs
    SELECT DISTINCT
        accession,
        id AS replaced_by_id
    FROM cv_terms_base
    WHERE accession IS NOT NULL
)
-- Final select with efficient lookups
SELECT
    t.id,
    t.namespace_id,
    t.accession,
    t.name,
    cl.category_id,
    t.definition,
    t.is_obsolete,
    rl.replaced_by_id,
    t.comment
FROM cv_terms_base t
LEFT JOIN category_lookup cl ON t.category_accession = cl.accession
LEFT JOIN replaced_by_lookup rl ON t.replaced_by_accession = rl.accession
ORDER BY t.id;
