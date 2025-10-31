-- Gold controlled vocabulary term hierarchy table
-- Maps parent-child relationships for CV terms

COPY (
    WITH cv_term_data AS (
        -- Load the cv_term parquet we just created
        SELECT id, accession
        FROM read_parquet('cv_term.parquet')
    ),
    hierarchy_expanded AS (
        -- Expand the term_parent_accessions array field
        SELECT
            ct.id AS child_id,
            parent_acc AS parent_accession
        FROM read_parquet('../../databases/omnipath/data/*/*/silver/silver_cv_terms.parquet', hive_partitioning=false, filename=true, union_by_name=true) sct
        JOIN cv_term_data ct ON sct.term_accession = ct.accession
        CROSS JOIN UNNEST(sct.term_parent_accessions) AS t(parent_acc)
        WHERE sct.term_parent_accessions IS NOT NULL
    )
    SELECT
        ROW_NUMBER() OVER (ORDER BY he.child_id, ctl.id) AS id,
        ctl.id AS parent_id,
        he.child_id AS child_id
    FROM hierarchy_expanded he
    JOIN cv_term_data ctl ON he.parent_accession = ctl.accession
    WHERE ctl.id IS NOT NULL
    ORDER BY parent_id, child_id
) TO 'cv_term_hierarchy.parquet' (FORMAT PARQUET);
