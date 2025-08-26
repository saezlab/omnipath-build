-- Gold entity membership table - Links entities in parent-member relationships
-- Maps to Django model: db.models.EntityMembership

CREATE OR REPLACE TABLE gold.entity_membership AS
WITH complex_members AS (
    -- Extract members from the members column (format: "P84022:1|Q13485:2")
    SELECT
        ge_parent.id AS parent_entity_id,
        SPLIT_PART(member_pair, ':', 1) AS member_id,
        TRY_CAST(SPLIT_PART(member_pair, ':', 2) AS INTEGER) AS stoichiometry
    FROM silver.entities e
    JOIN gold.entity ge_parent ON e.canonical_identifier = ge_parent.canonical_identifier
    CROSS JOIN UNNEST(STRING_SPLIT(e.members, '|')) AS t(member_pair)
    WHERE e.members IS NOT NULL AND e.members != ''
        AND e.entity_type IN ('MI:0314', 'OM00014')  -- complex or protein family
),

memberships AS (
    SELECT
        ROW_NUMBER() OVER (ORDER BY cm.parent_entity_id, cm.member_id) AS id,
        cm.parent_entity_id,
        ge_member.id AS member_entity_id,
        COALESCE(cm.stoichiometry, 1) AS stoichiometry
    FROM complex_members cm
    JOIN gold.entity ge_member ON cm.member_id = ge_member.canonical_identifier
    WHERE cm.member_id IS NOT NULL AND cm.member_id != ''
)

SELECT
    id,
    parent_entity_id,
    member_entity_id,
    stoichiometry
FROM memberships
ORDER BY parent_entity_id, member_entity_id;