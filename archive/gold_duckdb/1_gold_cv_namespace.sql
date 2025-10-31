-- Gold controlled vocabulary namespace table

COPY (
    WITH namespaces AS (
        SELECT 'Gene Ontology' as name, 'Gene Ontology' as description
        UNION ALL

        SELECT 'PSI-MI' as name, 'Molecular Interaction Controlled Vocabulary' as description
        UNION ALL

        SELECT 'UniProt Keywords' as name, 'UniProt Keywords Controlled Vocabulary' as description
        UNION ALL

        SELECT 'OmniPath' as name, 'OmniPath Controlled Vocabulary' as description
    ),
    numbered_namespaces AS (
        SELECT DISTINCT
            ROW_NUMBER() OVER (ORDER BY name) AS id,
            name,
            description,
            NULL::BIGINT AS reference_id  -- Would need to be linked properly in production
        FROM namespaces
        WHERE name IS NOT NULL
    )
    SELECT
        id,
        name,
        description,
        reference_id
    FROM numbered_namespaces
    ORDER BY name
) TO 'cv_namespace.parquet' (FORMAT PARQUET);
