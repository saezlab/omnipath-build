-- Gold entity evidence and entity identifier tables
-- Creates both entity_evidence and entity_identifier records from silver_entities
-- Each silver entity becomes one entity_evidence record
-- Member entities (from the members field) also become entity_evidence records
-- All identifiers (accession, cross-refs, structural, names, synonyms, member references) become entity_identifier records

-- First, create entity_evidence.parquet
COPY (
    WITH regular_entities_numbered AS (
        -- Regular entities with accession
        SELECT
            ROW_NUMBER() OVER (ORDER BY source, accession) AS row_num,
            source,
            accession,
            annotations,
            'regular' AS entity_kind,
            NULL AS parent_row_num
        FROM read_parquet('../../databases/omnipath/data/*/*/silver/silver_entities.parquet', hive_partitioning=false, filename=true, union_by_name=true)
        WHERE accession IS NOT NULL
    ),

    parent_entities_for_members AS (
        -- Get parent entities that have members, with their row numbers
        SELECT
            ROW_NUMBER() OVER (ORDER BY source, accession) AS parent_row_num,
            source,
            accession,
            members
        FROM read_parquet('../../databases/omnipath/data/*/*/silver/silver_entities.parquet', hive_partitioning=false, filename=true, union_by_name=true)
        WHERE accession IS NOT NULL AND members IS NOT NULL
    ),

    member_entities_with_parent AS (
        -- Member entities extracted from the members field, linked to parent row_num
        SELECT
            p.parent_row_num,
            p.source,
            NULL AS accession,  -- Members don't have their own accession in parent record
            [{'key': 'stoichiometry', 'value': member.value}, {'key': 'role', 'value': member.value}] AS annotations,
            'member' AS entity_kind
        FROM parent_entities_for_members p
        CROSS JOIN UNNEST(p.members) AS t(member)
    ),

    member_entities_numbered AS (
        -- Assign row numbers to member entities (after regular entities)
        SELECT
            (SELECT COUNT(*) FROM regular_entities_numbered) + ROW_NUMBER() OVER (ORDER BY parent_row_num, source) AS row_num,
            source,
            accession,
            annotations,
            entity_kind,
            parent_row_num
        FROM member_entities_with_parent
    ),

    all_entities AS (
        SELECT * FROM regular_entities_numbered
        UNION ALL
        SELECT * FROM member_entities_numbered
    )

    SELECT
        row_num AS id,
        NULL AS entity_id,  -- Will be filled later when we create entity table and group entities
        source AS source_id,  -- Will need to map to source table later
        parent_row_num AS parent_entity_evidence_id,  -- Links member to parent entity evidence
        annotations
    FROM all_entities
    ORDER BY id
) TO 'entity_evidence.parquet' (FORMAT PARQUET);

-- Second, create entity_identifier.parquet with all identifiers
COPY (
    WITH entity_evidence_with_ids AS (
        -- Load entity evidence to get IDs
        SELECT
            id AS entity_evidence_id,
            source_id
        FROM read_parquet('entity_evidence.parquet')
    ),

    regular_entities_for_identifiers AS (
        -- Number the regular silver entities to match evidence IDs
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
            synonyms,
            NULL AS member_id,
            NULL AS member_id_type,
            'regular' AS entity_kind
        FROM read_parquet('../../databases/omnipath/data/*/*/silver/silver_entities.parquet', hive_partitioning=false, filename=true, union_by_name=true)
        WHERE accession IS NOT NULL
    ),

    parent_entities_for_member_identifiers AS (
        -- Get parent entities that have members, with their row numbers
        SELECT
            ROW_NUMBER() OVER (ORDER BY source, accession) AS parent_row_num,
            source,
            accession,
            members
        FROM read_parquet('../../databases/omnipath/data/*/*/silver/silver_entities.parquet', hive_partitioning=false, filename=true, union_by_name=true)
        WHERE accession IS NOT NULL AND members IS NOT NULL
    ),

    member_entities_for_identifiers AS (
        -- Extract member entities with parent context
        SELECT
            p.parent_row_num,
            p.source,
            member.value AS member_id,
            member.key AS member_id_type
        FROM parent_entities_for_member_identifiers p
        CROSS JOIN UNNEST(p.members) AS t(member)
    ),

    member_entities_numbered AS (
        -- Assign row numbers to member entities (after regular entities)
        SELECT
            (SELECT COUNT(*) FROM regular_entities_for_identifiers) + ROW_NUMBER() OVER (ORDER BY parent_row_num, source) AS row_num,
            source,
            NULL AS accession,
            NULL AS entity_type,
            NULL AS inchikey,
            NULL AS smiles,
            NULL AS inchi,
            NULL AS cross_references,
            NULL AS name,
            NULL AS synonyms,
            member_id,
            member_id_type,
            'member' AS entity_kind
        FROM member_entities_for_identifiers
    ),

    all_entities_numbered AS (
        SELECT * FROM regular_entities_for_identifiers
        UNION ALL
        SELECT * FROM member_entities_numbered
    ),

    silver_entities_with_evidence_id AS (
        -- Join all entities (regular + member) with their evidence IDs
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
            se.synonyms,
            se.member_id,
            se.member_id_type,
            se.entity_kind
        FROM all_entities_numbered se
        JOIN entity_evidence_with_ids ee ON ee.entity_evidence_id = se.row_num
    ),

    -- Extract source accession identifier (only for regular entities)
    source_accession_identifiers AS (
        SELECT
            entity_evidence_id,
            NULL AS entity_id,  -- Will be filled later
            accession AS identifier,
            source_id,
            source AS identifier_type_name,
            'source_accession' AS identifier_kind
        FROM silver_entities_with_evidence_id
        WHERE entity_kind = 'regular' AND accession IS NOT NULL
    ),

    -- Extract member reference identifiers (only for member entities)
    member_identifiers AS (
        SELECT
            entity_evidence_id,
            NULL AS entity_id,
            member_id AS identifier,
            source_id,
            member_id_type AS identifier_type_name,
            'member_reference' AS identifier_kind
        FROM silver_entities_with_evidence_id
        WHERE entity_kind = 'member' AND member_id IS NOT NULL
    ),

    -- Extract cross-reference identifiers
    cross_reference_identifiers AS (
        SELECT
            entity_evidence_id,
            NULL AS entity_id,
            xref.value AS identifier,
            source_id,
            xref.type AS identifier_type_name,
            'cross_reference' AS identifier_kind
        FROM silver_entities_with_evidence_id
        CROSS JOIN UNNEST(cross_references) AS t(xref)
        WHERE cross_references IS NOT NULL
    ),

    -- Extract structural identifiers (InChIKey, SMILES, InChI)
    structural_identifiers AS (
        SELECT entity_evidence_id, NULL AS entity_id, inchikey AS identifier, source_id, 'inchikey' AS identifier_type_name, 'inchikey' AS identifier_kind
        FROM silver_entities_with_evidence_id WHERE inchikey IS NOT NULL
        UNION ALL
        SELECT entity_evidence_id, NULL AS entity_id, smiles AS identifier, source_id, 'smiles' AS identifier_type_name, 'smiles' AS identifier_kind
        FROM silver_entities_with_evidence_id WHERE smiles IS NOT NULL
        UNION ALL
        SELECT entity_evidence_id, NULL AS entity_id, inchi AS identifier, source_id, 'inchi' AS identifier_type_name, 'inchi' AS identifier_kind
        FROM silver_entities_with_evidence_id WHERE inchi IS NOT NULL
    ),

    -- Extract name as identifier
    name_identifiers AS (
        SELECT
            entity_evidence_id,
            NULL AS entity_id,
            name AS identifier,
            source_id,
            'name' AS identifier_type_name,
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
            'synonym' AS identifier_type_name,
            'synonym' AS identifier_kind
        FROM silver_entities_with_evidence_id
        CROSS JOIN UNNEST(synonyms) AS t(syn)
        WHERE synonyms IS NOT NULL
    ),

    -- Combine all identifiers
    all_identifiers AS (
        SELECT * FROM source_accession_identifiers
        UNION ALL
        SELECT * FROM member_identifiers
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
        entity_evidence_id,
        entity_id,
        identifier,
        source_id,
        identifier_type_name,
        identifier_kind
    FROM all_identifiers
    ORDER BY entity_evidence_id, identifier_kind, identifier
) TO 'entity_identifier.parquet' (FORMAT PARQUET);
