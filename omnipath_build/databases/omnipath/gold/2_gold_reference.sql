-- This query will be executed by DuckDB and written to gold/data/reference.parquet
-- Gold reference table - Literature references
-- Maps to Django model: db.models.Reference


WITH interaction_references AS (
    -- Get all unique PubMed IDs from interactions
    SELECT DISTINCT
        CAST(pubmed_id AS BIGINT) AS pubmed_id,
        NULL AS doi
    FROM read_parquet('silver/data/interactions/*.parquet') AS interactions
    WHERE pubmed_id IS NOT NULL
        AND pubmed_id != ''
        AND REGEXP_MATCHES(pubmed_id, '^\d+$')  -- Only valid numeric PubMed IDs
),

cv_term_references AS (
    -- Extract PubMed IDs from CV term references (pipe-delimited)
    SELECT DISTINCT
        TRY_CAST(ref_id AS BIGINT) AS pubmed_id,
        NULL AS doi
    FROM read_parquet('silver/data/cv_term/*.parquet') AS cv_term
    CROSS JOIN UNNEST(STRING_SPLIT("references", '|')) AS t(ref_id)
    WHERE "references" IS NOT NULL 
        AND "references" != ''
        AND REGEXP_MATCHES(ref_id, '^\d+$')
),

all_references AS (
    SELECT pubmed_id, doi FROM interaction_references
    UNION
    SELECT pubmed_id, doi FROM cv_term_references
),

deduplicated_references AS (
    SELECT
        pubmed_id,
        MAX(doi) AS doi  -- Take any DOI if multiple exist for same PubMed ID
    FROM all_references
    WHERE pubmed_id IS NOT NULL
    GROUP BY pubmed_id
)

SELECT
    ROW_NUMBER() OVER (ORDER BY pubmed_id) AS id,
    pubmed_id,
    doi
FROM deduplicated_references
ORDER BY pubmed_id;
