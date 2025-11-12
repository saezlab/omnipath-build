-- Gold controlled vocabulary term table
-- Maps to Django model: db.models.ControlledVocabularyTerm

COPY (
    WITH cv_namespace_data AS (
        -- Load the namespace parquet we just created
        SELECT id, name
        FROM read_parquet('cv_namespace.parquet')
    ),

    cv_terms_base AS (
        -- First create base table with IDs, using new silver schema fields
        SELECT
            ROW_NUMBER() OVER (ORDER BY ct.term_accession) AS id,
            gcn.id AS namespace_id,
            ct.term_accession AS accession,
            ct.term_name AS name,
            NULL AS category_id,  -- Not in silver schema yet
            ct.term_definition AS definition,
            NULL AS is_obsolete,  -- Not in silver schema yet
            NULL AS replaced_by_id,  -- Not in silver schema yet
            NULL AS comment  -- Not in silver schema yet
        FROM read_parquet('../../databases/omnipath/data/*/*/silver/silver_cv_terms.parquet', hive_partitioning=false, filename=true, union_by_name=true) ct
        INNER JOIN cv_namespace_data gcn ON ct.namespace = gcn.name
    )

    -- Final select
    SELECT
        id,
        namespace_id,
        accession,
        name,
        category_id,
        definition,
        is_obsolete,
        replaced_by_id,
        comment
    FROM cv_terms_base
    ORDER BY id
) TO 'cv_term.parquet' (FORMAT PARQUET);
