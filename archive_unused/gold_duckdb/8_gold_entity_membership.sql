-- Gold entity membership table
--
-- This script creates the entity_membership table by linking parent entities to their member entities.
-- The relationships are tracked via parent_entity_evidence_id in the entity_evidence table.
--
-- Each row represents a membership relationship with:
--   - parent_entity_id: The complex/parent entity
--   - member_entity_id: The member entity
--   - stoichiometry: How many copies of the member (extracted from annotations)
--   - role: The role of the member in the complex (extracted from annotations)

COPY (
    WITH member_evidence AS (
        -- Get entity evidence records that are members (have a parent)
        SELECT
            id AS member_entity_evidence_id,
            entity_id AS member_entity_id,
            parent_entity_evidence_id,
            source_id,
            annotations
        FROM read_parquet('entity_evidence.parquet')
        WHERE parent_entity_evidence_id IS NOT NULL
    ),

    parent_evidence AS (
        -- Get parent entity evidence records
        SELECT
            id AS parent_entity_evidence_id,
            entity_id AS parent_entity_id
        FROM read_parquet('entity_evidence.parquet')
    ),

    membership_with_annotations AS (
        -- Join members to their parents
        SELECT
            p.parent_entity_id,
            m.member_entity_id,
            m.source_id,
            m.annotations
        FROM member_evidence m
        JOIN parent_evidence p ON p.parent_entity_evidence_id = m.parent_entity_evidence_id
    ),

    membership_parsed AS (
        -- Extract stoichiometry and role from annotations
        SELECT
            parent_entity_id,
            member_entity_id,
            source_id,
            (
                SELECT a.value
                FROM UNNEST(annotations) AS t(a)
                WHERE a.key = 'stoichiometry'
                LIMIT 1
            ) AS stoichiometry_str,
            (
                SELECT a.value
                FROM UNNEST(annotations) AS t(a)
                WHERE a.key = 'role'
                LIMIT 1
            ) AS role
        FROM membership_with_annotations
    )

    SELECT
        ROW_NUMBER() OVER (ORDER BY parent_entity_id, member_entity_id, source_id) AS id,
        parent_entity_id,
        member_entity_id,
        source_id,
        TRY_CAST(stoichiometry_str AS INTEGER) AS stoichiometry,
        role
    FROM membership_parsed
    ORDER BY id
) TO 'entity_membership.parquet' (FORMAT PARQUET);
