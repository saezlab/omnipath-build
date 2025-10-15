-- Gold entity evidence and entity identifier tables
-- Creates both entity_evidence and entity_identifier records from silver_entities
-- Each silver entity becomes one entity_evidence record
-- All identifiers (accession, cross-refs, structural, names, synonyms) become entity_identifier records

-- First, create entity_evidence.parquet
COPY (
    SELECT
        ROW_NUMBER() OVER (ORDER BY source, accession) AS id,
        NULL AS entity_id,  -- Will be filled later when we create entity table and group entities
        source AS source_id,  -- Will need to map to source table later
        annotations
    FROM read_parquet('../../databases/omnipath/data/*/*/silver/silver_entities.parquet', hive_partitioning=false, filename=true, union_by_name=true)
    WHERE accession IS NOT NULL
    ORDER BY id
) TO 'entity_evidence.parquet' (FORMAT PARQUET);

-- Second, create entity_identifier.parquet with all identifiers
COPY (
    WITH cv_term_data AS (
        -- Load CV terms for identifier type lookups
        SELECT id, accession
        FROM read_parquet('cv_term.parquet')
    ),

    entity_evidence_with_ids AS (
        -- Load entity evidence to get IDs
        SELECT
            id AS entity_evidence_id,
            source_id
        FROM read_parquet('entity_evidence.parquet')
    ),

    silver_entities_numbered AS (
        -- Number the silver entities to match evidence IDs
        SELECT
            ROW_NUMBER() OVER (ORDER BY source, accession) AS row_num,
            source,
            accession,
            entity_type,
            inchikey,
            smiles,
            inchi,
            cross_references,
            name,
            synonyms
        FROM read_parquet('../../databases/omnipath/data/*/*/silver/silver_entities.parquet', hive_partitioning=false, filename=true, union_by_name=true)
        WHERE accession IS NOT NULL
    ),

    silver_entities_with_evidence_id AS (
        -- Join silver entities with their evidence IDs
        SELECT
            ee.entity_evidence_id,
            ee.source_id,
            se.source,
            se.accession,
            se.entity_type,
            se.inchikey,
            se.smiles,
            se.inchi,
            se.cross_references,
            se.name,
            se.synonyms
        FROM silver_entities_numbered se
        JOIN entity_evidence_with_ids ee ON ee.entity_evidence_id = se.row_num
    ),

    -- Extract source accession identifier
    source_accession_identifiers AS (
        SELECT
            entity_evidence_id,
            NULL AS entity_id,  -- Will be filled later
            accession AS identifier,
            source_id,
            NULL AS identifier_type_id,  -- Source itself is the type
            'source_accession' AS identifier_kind
        FROM silver_entities_with_evidence_id
    ),

    -- Extract cross-reference identifiers
    cross_reference_identifiers AS (
        SELECT
            entity_evidence_id,
            NULL AS entity_id,
            xref.value AS identifier,
            source_id,
            cv.id AS identifier_type_id,
            'cross_reference' AS identifier_kind
        FROM silver_entities_with_evidence_id
        CROSS JOIN UNNEST(cross_references) AS t(xref)
        LEFT JOIN cv_term_data cv ON xref.type = cv.accession
        WHERE cross_references IS NOT NULL
    ),

    -- Extract structural identifiers (InChIKey, SMILES, InChI)
    structural_identifiers AS (
        SELECT entity_evidence_id, NULL AS entity_id, inchikey AS identifier, source_id, NULL AS identifier_type_id, 'inchikey' AS identifier_kind
        FROM silver_entities_with_evidence_id WHERE inchikey IS NOT NULL
        UNION ALL
        SELECT entity_evidence_id, NULL AS entity_id, smiles AS identifier, source_id, NULL AS identifier_type_id, 'smiles' AS identifier_kind
        FROM silver_entities_with_evidence_id WHERE smiles IS NOT NULL
        UNION ALL
        SELECT entity_evidence_id, NULL AS entity_id, inchi AS identifier, source_id, NULL AS identifier_type_id, 'inchi' AS identifier_kind
        FROM silver_entities_with_evidence_id WHERE inchi IS NOT NULL
    ),

    -- Extract name as identifier
    name_identifiers AS (
        SELECT
            entity_evidence_id,
            NULL AS entity_id,
            name AS identifier,
            source_id,
            NULL AS identifier_type_id,
            'name' AS identifier_kind
        FROM silver_entities_with_evidence_id
        WHERE name IS NOT NULL
    ),

    -- Extract synonyms as identifiers
    synonym_identifiers AS (
        SELECT
            entity_evidence_id,
            NULL AS entity_id,
            syn AS identifier,
            source_id,
            NULL AS identifier_type_id,
            'synonym' AS identifier_kind
        FROM silver_entities_with_evidence_id
        CROSS JOIN UNNEST(synonyms) AS t(syn)
        WHERE synonyms IS NOT NULL
    ),

    -- Combine all identifiers
    all_identifiers AS (
        SELECT * FROM source_accession_identifiers
        UNION ALL
        SELECT * FROM cross_reference_identifiers
        UNION ALL
        SELECT * FROM structural_identifiers
        UNION ALL
        SELECT * FROM name_identifiers
        UNION ALL
        SELECT * FROM synonym_identifiers
    )

    SELECT
        ROW_NUMBER() OVER (ORDER BY entity_evidence_id, identifier_kind, identifier) AS id,
        entity_id,
        identifier,
        source_id,
        identifier_type_id,
        identifier_kind
    FROM all_identifiers
    ORDER BY entity_evidence_id, identifier_kind, identifier
) TO 'entity_identifier.parquet' (FORMAT PARQUET);
