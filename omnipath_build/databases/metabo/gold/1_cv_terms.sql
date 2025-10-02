-- ============================================================================
-- CV TERMS PROCESSING
-- ============================================================================
-- Populates namespaces and CV terms from silver_cv_terms table
-- Extracts additional terms from silver_entities and silver_interactions
-- ============================================================================

-- ============================================================================
-- 1. POPULATE NAMESPACES
-- ============================================================================

INSERT INTO cv_namespace (name)
SELECT DISTINCT namespace
FROM silver_cv_terms
WHERE namespace IS NOT NULL
ON CONFLICT (name) DO NOTHING;

-- ============================================================================
-- 2. POPULATE CV TERMS FROM SILVER_CV_TERMS
-- ============================================================================

INSERT INTO cv_term (namespace_id, accession, name, description)
SELECT
    ns.id,
    sct.term_accession,
    sct.term_name,
    sct.term_definition
FROM silver_cv_terms sct
JOIN cv_namespace ns ON ns.name = sct.namespace
WHERE sct.term_name IS NOT NULL
ON CONFLICT (namespace_id, name) DO UPDATE
SET
    accession = COALESCE(EXCLUDED.accession, cv_term.accession),
    description = COALESCE(EXCLUDED.description, cv_term.description);

-- ============================================================================
-- 3. EXTRACT MISSING CV TERMS FROM SILVER TABLES
-- ============================================================================

-- Create function to extract terms from silver data
CREATE OR REPLACE FUNCTION extract_cv_terms_from_silver()
RETURNS TABLE(
    terms_extracted INT
) AS $$
DECLARE
    v_extracted INT := 0;
    v_omnipath_ns_id INT;
BEGIN
    -- Get or create OmniPath namespace
    INSERT INTO cv_namespace (name)
    VALUES ('OmniPath')
    ON CONFLICT (name) DO NOTHING
    RETURNING id INTO v_omnipath_ns_id;

    IF v_omnipath_ns_id IS NULL THEN
        SELECT id INTO v_omnipath_ns_id FROM cv_namespace WHERE name = 'OmniPath';
    END IF;

    -- Extract entity_type terms
    WITH entity_type_terms AS (
        INSERT INTO cv_term (namespace_id, name)
        SELECT DISTINCT
            v_omnipath_ns_id,
            entity_type
        FROM silver_entities
        WHERE entity_type IS NOT NULL
        AND NOT EXISTS (
            SELECT 1 FROM cv_term ct
            JOIN cv_namespace ns ON ct.namespace_id = ns.id
            WHERE ns.name = 'OmniPath' AND ct.name = entity_type
        )
        ON CONFLICT (namespace_id, name) DO NOTHING
        RETURNING id
    ),
    -- Extract identifier_type terms
    identifier_type_terms AS (
        INSERT INTO cv_term (namespace_id, name)
        SELECT DISTINCT
            v_omnipath_ns_id,
            identifier_type
        FROM silver_entities
        WHERE identifier_type IS NOT NULL
        AND NOT EXISTS (
            SELECT 1 FROM cv_term ct
            JOIN cv_namespace ns ON ct.namespace_id = ns.id
            WHERE ns.name = 'OmniPath' AND ct.name = identifier_type
        )
        ON CONFLICT (namespace_id, name) DO NOTHING
        RETURNING id
    ),
    -- Extract additional identifier types from JSONB
    additional_identifier_terms AS (
        INSERT INTO cv_term (namespace_id, name)
        SELECT DISTINCT
            v_omnipath_ns_id,
            jsonb_array_elements(additional_identifiers)->>'type'
        FROM silver_entities
        WHERE additional_identifiers IS NOT NULL
        AND NOT EXISTS (
            SELECT 1 FROM cv_term ct
            JOIN cv_namespace ns ON ct.namespace_id = ns.id
            WHERE ns.name = 'OmniPath'
            AND ct.name = jsonb_array_elements(additional_identifiers)->>'type'
        )
        ON CONFLICT (namespace_id, name) DO NOTHING
        RETURNING id
    ),
    -- Extract interaction_type terms
    interaction_type_terms AS (
        INSERT INTO cv_term (namespace_id, name)
        SELECT DISTINCT
            v_omnipath_ns_id,
            interaction_type
        FROM silver_interactions
        WHERE interaction_type IS NOT NULL
        AND NOT EXISTS (
            SELECT 1 FROM cv_term ct
            JOIN cv_namespace ns ON ct.namespace_id = ns.id
            WHERE ns.name = 'OmniPath' AND ct.name = interaction_type
        )
        ON CONFLICT (namespace_id, name) DO NOTHING
        RETURNING id
    ),
    -- Extract direction terms
    direction_terms AS (
        INSERT INTO cv_term (namespace_id, name)
        SELECT DISTINCT
            v_omnipath_ns_id,
            direction
        FROM silver_interactions
        WHERE direction IS NOT NULL
        AND NOT EXISTS (
            SELECT 1 FROM cv_term ct
            JOIN cv_namespace ns ON ct.namespace_id = ns.id
            WHERE ns.name = 'OmniPath' AND ct.name = direction
        )
        ON CONFLICT (namespace_id, name) DO NOTHING
        RETURNING id
    ),
    -- Extract sign terms
    sign_terms AS (
        INSERT INTO cv_term (namespace_id, name)
        SELECT DISTINCT
            v_omnipath_ns_id,
            sign
        FROM silver_interactions
        WHERE sign IS NOT NULL
        AND NOT EXISTS (
            SELECT 1 FROM cv_term ct
            JOIN cv_namespace ns ON ct.namespace_id = ns.id
            WHERE ns.name = 'OmniPath' AND ct.name = sign
        )
        ON CONFLICT (namespace_id, name) DO NOTHING
        RETURNING id
    ),
    -- Extract causal_mechanism terms
    causal_mechanism_terms AS (
        INSERT INTO cv_term (namespace_id, name)
        SELECT DISTINCT
            v_omnipath_ns_id,
            causal_mechanism
        FROM silver_interactions
        WHERE causal_mechanism IS NOT NULL
        AND NOT EXISTS (
            SELECT 1 FROM cv_term ct
            JOIN cv_namespace ns ON ct.namespace_id = ns.id
            WHERE ns.name = 'OmniPath' AND ct.name = causal_mechanism
        )
        ON CONFLICT (namespace_id, name) DO NOTHING
        RETURNING id
    ),
    -- Extract reference_type terms
    reference_type_terms AS (
        INSERT INTO cv_term (namespace_id, name)
        SELECT DISTINCT
            v_omnipath_ns_id,
            reference_type
        FROM silver_interactions
        WHERE reference_type IS NOT NULL
        AND NOT EXISTS (
            SELECT 1 FROM cv_term ct
            JOIN cv_namespace ns ON ct.namespace_id = ns.id
            WHERE ns.name = 'OmniPath' AND ct.name = reference_type
        )
        ON CONFLICT (namespace_id, name) DO NOTHING
        RETURNING id
    ),
    -- Extract detection_method terms
    detection_method_terms AS (
        INSERT INTO cv_term (namespace_id, name)
        SELECT DISTINCT
            v_omnipath_ns_id,
            detection_method
        FROM silver_interactions
        WHERE detection_method IS NOT NULL
        AND NOT EXISTS (
            SELECT 1 FROM cv_term ct
            JOIN cv_namespace ns ON ct.namespace_id = ns.id
            WHERE ns.name = 'OmniPath' AND ct.name = detection_method
        )
        ON CONFLICT (namespace_id, name) DO NOTHING
        RETURNING id
    ),
    -- Extract membership role terms from complex_members JSONB
    membership_role_terms AS (
        INSERT INTO cv_term (namespace_id, name)
        SELECT DISTINCT
            v_omnipath_ns_id,
            jsonb_array_elements(complex_members)->>'role'
        FROM silver_entities
        WHERE complex_members IS NOT NULL
        AND jsonb_array_elements(complex_members)->>'role' IS NOT NULL
        AND NOT EXISTS (
            SELECT 1 FROM cv_term ct
            JOIN cv_namespace ns ON ct.namespace_id = ns.id
            WHERE ns.name = 'OmniPath'
            AND ct.name = jsonb_array_elements(complex_members)->>'role'
        )
        ON CONFLICT (namespace_id, name) DO NOTHING
        RETURNING id
    ),
    -- Extract annotation terms from entity annotations JSONB
    entity_annotation_terms AS (
        INSERT INTO cv_term (namespace_id, name)
        SELECT DISTINCT
            v_omnipath_ns_id,
            jsonb_array_elements(annotations)->>'term'
        FROM silver_entities
        WHERE annotations IS NOT NULL
        AND jsonb_array_elements(annotations)->>'term' IS NOT NULL
        AND NOT EXISTS (
            SELECT 1 FROM cv_term ct
            JOIN cv_namespace ns ON ct.namespace_id = ns.id
            WHERE ns.name = 'OmniPath'
            AND ct.name = jsonb_array_elements(annotations)->>'term'
        )
        ON CONFLICT (namespace_id, name) DO NOTHING
        RETURNING id
    ),
    -- Extract annotation terms from interaction annotations JSONB
    interaction_annotation_terms AS (
        INSERT INTO cv_term (namespace_id, name)
        SELECT DISTINCT
            v_omnipath_ns_id,
            jsonb_array_elements(interaction_annotations)->>'term'
        FROM silver_interactions
        WHERE interaction_annotations IS NOT NULL
        AND jsonb_array_elements(interaction_annotations)->>'term' IS NOT NULL
        AND NOT EXISTS (
            SELECT 1 FROM cv_term ct
            JOIN cv_namespace ns ON ct.namespace_id = ns.id
            WHERE ns.name = 'OmniPath'
            AND ct.name = jsonb_array_elements(interaction_annotations)->>'term'
        )
        ON CONFLICT (namespace_id, name) DO NOTHING
        RETURNING id
    ),
    -- Extract annotation terms from entity context JSONB
    entity_context_terms AS (
        INSERT INTO cv_term (namespace_id, name)
        SELECT DISTINCT
            v_omnipath_ns_id,
            term
        FROM silver_interactions,
        LATERAL (
            SELECT jsonb_array_elements(entity_a_context)->>'term' as term
            UNION
            SELECT jsonb_array_elements(entity_b_context)->>'term' as term
        ) contexts
        WHERE term IS NOT NULL
        AND NOT EXISTS (
            SELECT 1 FROM cv_term ct
            JOIN cv_namespace ns ON ct.namespace_id = ns.id
            WHERE ns.name = 'OmniPath' AND ct.name = term
        )
        ON CONFLICT (namespace_id, name) DO NOTHING
        RETURNING id
    )
    SELECT
        (SELECT COUNT(*) FROM entity_type_terms) +
        (SELECT COUNT(*) FROM identifier_type_terms) +
        (SELECT COUNT(*) FROM additional_identifier_terms) +
        (SELECT COUNT(*) FROM interaction_type_terms) +
        (SELECT COUNT(*) FROM direction_terms) +
        (SELECT COUNT(*) FROM sign_terms) +
        (SELECT COUNT(*) FROM causal_mechanism_terms) +
        (SELECT COUNT(*) FROM reference_type_terms) +
        (SELECT COUNT(*) FROM detection_method_terms) +
        (SELECT COUNT(*) FROM membership_role_terms) +
        (SELECT COUNT(*) FROM entity_annotation_terms) +
        (SELECT COUNT(*) FROM interaction_annotation_terms) +
        (SELECT COUNT(*) FROM entity_context_terms)
    INTO v_extracted;

    RETURN QUERY SELECT v_extracted;
END;
$$ LANGUAGE plpgsql;

-- Execute the extraction
DO $$
DECLARE
    v_count INT;
BEGIN
    SELECT terms_extracted INTO v_count FROM extract_cv_terms_from_silver();
    RAISE NOTICE 'Extracted % additional CV terms from silver tables', v_count;
END;
$$;
