-- Gold controlled vocabulary term hierarchy table
-- Maps parent-child relationships for CV terms

CREATE OR REPLACE TABLE gold.cv_term_hierarchy AS
WITH cv_term_lookup AS (
    -- Get all CV terms with their IDs
    SELECT id, accession
    FROM gold.cv_term
),
hierarchy_expanded AS (
    -- Expand the pipe-delimited is_a field
    SELECT 
        ct.id AS child_id,
        TRIM(parent_acc.value) AS parent_accession
    FROM silver.cv_term sct
    JOIN gold.cv_term ct ON sct.accession = ct.accession
    CROSS JOIN UNNEST(STRING_SPLIT(sct.is_a, '|')) AS parent_acc(value)
    WHERE sct.is_a IS NOT NULL AND sct.is_a != ''
)
SELECT
    ROW_NUMBER() OVER (ORDER BY he.child_id, ctl.id) AS id,
    ctl.id AS parent_id,
    he.child_id AS child_id
FROM hierarchy_expanded he
JOIN cv_term_lookup ctl ON he.parent_accession = ctl.accession
WHERE ctl.id IS NOT NULL
ORDER BY parent_id, child_id;
