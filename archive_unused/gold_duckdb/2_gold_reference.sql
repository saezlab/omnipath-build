-- Gold reference table - Literature references
-- Maps to Django model: db.models.Reference

COPY (
    WITH cv_term_references AS (
        -- Extract reference identifiers from CV term definition references (array field)
        SELECT DISTINCT
            ref_id AS identifier
        FROM read_parquet('../../databases/omnipath/data/*/*/silver/silver_cv_terms.parquet')
        CROSS JOIN UNNEST(term_definition_refs) AS t(ref_id)
        WHERE term_definition_refs IS NOT NULL
    ),

    entity_references AS (
        -- Extract reference identifiers from entity references (array field)
        SELECT DISTINCT
            ref_id AS identifier
        FROM read_parquet('../../databases/omnipath/data/*/*/silver/silver_entities.parquet')
        CROSS JOIN UNNEST("references") AS t(ref_id)
        WHERE "references" IS NOT NULL
    ),

    all_references AS (
        SELECT identifier FROM cv_term_references
        UNION ALL
        SELECT identifier FROM entity_references
    ),

    deduplicated_references AS (
        SELECT DISTINCT
            identifier
        FROM all_references
        WHERE identifier IS NOT NULL
    )

    SELECT
        ROW_NUMBER() OVER (ORDER BY identifier) AS id,
        identifier
    FROM deduplicated_references
    ORDER BY identifier
) TO 'reference.parquet' (FORMAT PARQUET);
