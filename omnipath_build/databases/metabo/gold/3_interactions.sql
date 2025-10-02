CREATE OR REPLACE FUNCTION process_interactions_to_gold()
RETURNS TABLE(
    interactions_processed INT,
    interactions_inserted INT,
    evidence_added INT
) AS $$
DECLARE
    v_processed INT := 0;
    v_inserted INT := 0;
    v_evidence INT := 0;
BEGIN
    -- ✨ Cache CV terms
    CREATE TEMP TABLE IF NOT EXISTS temp_cv_cache (
        namespace VARCHAR,
        term_name VARCHAR,
        term_id INT,
        PRIMARY KEY (namespace, term_name)
    ) ON COMMIT DROP;
    
    -- Pre-populate all CV terms we'll need
    INSERT INTO temp_cv_cache (namespace, term_name, term_id)
    SELECT
        ns.name,
        t.name,
        t.id
    FROM cv_term t
    JOIN cv_namespace ns ON t.namespace_id = ns.id
    ON CONFLICT DO NOTHING;

    -- ✨ Now process with fast JOINs
    WITH interaction_staging AS (
        SELECT
            ea.id as entity_a_id,
            eb.id as entity_b_id,
            si.interaction_type,
            si.is_directed,
            si.direction,
            si.sign,
            si.causal_mechanism,
            si.sentence,
            si.source_name,
            si.is_primary_source,
            si.reference_type,
            si.reference_value,
            si.citation,
            si.title,
            si.journal,
            si.year
        FROM silver_interactions si
        JOIN entity_identifier eia ON eia.identifier = si.entity_a_identifier
        JOIN entity ea ON ea.id = eia.entity_id
        JOIN entity_identifier eib ON eib.identifier = si.entity_b_identifier
        JOIN entity eb ON eb.id = eib.entity_id
        WHERE si.is_valid = TRUE
        AND NOT EXISTS (
            SELECT 1 FROM interaction_evidence ie
            WHERE ie.sentence = si.sentence
        )
    ),
    
    interaction_upserts AS (
        INSERT INTO interaction (entity_a_id, entity_b_id)
        SELECT DISTINCT
            LEAST(entity_a_id, entity_b_id),
            GREATEST(entity_a_id, entity_b_id)
        FROM interaction_staging
        ON CONFLICT (entity_a_id, entity_b_id) DO NOTHING
        RETURNING id, entity_a_id, entity_b_id
    ),
    
    interaction_mapping AS (
        SELECT 
            ist.*,
            COALESCE(
                (SELECT i.id FROM interaction i 
                 WHERE i.entity_a_id = LEAST(ist.entity_a_id, ist.entity_b_id)
                 AND i.entity_b_id = GREATEST(ist.entity_a_id, ist.entity_b_id)
                 LIMIT 1),
                iu.id
            ) as interaction_id
        FROM interaction_staging ist
        LEFT JOIN interaction_upserts iu 
            ON iu.entity_a_id = LEAST(ist.entity_a_id, ist.entity_b_id)
            AND iu.entity_b_id = GREATEST(ist.entity_a_id, ist.entity_b_id)
    ),
    
    reference_upserts AS (
        INSERT INTO reference (
            type_id, 
            value, 
            citation, 
            year, 
            journal, 
            title
        )
        SELECT DISTINCT
            tcc.term_id,  -- ✨ FAST JOIN!
            im.reference_value,
            im.citation,
            im.year,
            im.journal,
            im.title
        FROM interaction_mapping im
        JOIN temp_cv_cache tcc 
            ON tcc.namespace = 'reference_type' 
            AND tcc.term_name = im.reference_type
        WHERE im.reference_value IS NOT NULL
        ON CONFLICT (value) DO NOTHING
        RETURNING id, value
    ),
    
    provenance_upserts AS (
        INSERT INTO provenance (
            source_id,
            primary_source_id,
            reference_id,
            created_at
        )
        SELECT DISTINCT
            (SELECT id FROM source WHERE name = im.source_name),
            CASE WHEN im.is_primary_source THEN 
                (SELECT id FROM source WHERE name = im.source_name)
            END,
            COALESCE(
                (SELECT id FROM reference WHERE value = im.reference_value),
                ru.id
            ),
            NOW()
        FROM interaction_mapping im
        LEFT JOIN reference_upserts ru ON ru.value = im.reference_value
        ON CONFLICT DO NOTHING
        RETURNING id, source_id, reference_id
    ),
    
    annotation_record_upserts AS (
        INSERT INTO annotation_record (provenance_id, created_at)
        SELECT DISTINCT pu.id, NOW()
        FROM provenance_upserts pu
        RETURNING id, provenance_id
    ),
    
    evidence_inserts AS (
        INSERT INTO interaction_evidence (
            interaction_id,
            provenance_id,
            annotation_record_id,
            type_id,
            direction_id,
            sign_id,
            causal_mechanism_id,
            sentence,
            is_directed,
            created_at
        )
        SELECT 
            im.interaction_id,
            pu.id,
            ar.id,
            tcc_type.term_id,          -- ✨ FAST JOIN!
            tcc_dir.term_id,            -- ✨ FAST JOIN!
            tcc_sign.term_id,           -- ✨ FAST JOIN!
            tcc_mech.term_id,           -- ✨ FAST JOIN!
            im.sentence,
            im.is_directed,
            NOW()
        FROM interaction_mapping im
        JOIN temp_cv_cache tcc_type 
            ON tcc_type.namespace = 'interaction_type' 
            AND tcc_type.term_name = im.interaction_type
        LEFT JOIN temp_cv_cache tcc_dir 
            ON tcc_dir.namespace = 'direction' 
            AND tcc_dir.term_name = im.direction
        LEFT JOIN temp_cv_cache tcc_sign 
            ON tcc_sign.namespace = 'sign' 
            AND tcc_sign.term_name = im.sign
        LEFT JOIN temp_cv_cache tcc_mech 
            ON tcc_mech.namespace = 'causal_mechanism' 
            AND tcc_mech.term_name = im.causal_mechanism
        JOIN provenance_upserts pu ON pu.source_id = (
            SELECT id FROM source WHERE name = im.source_name
        )
        AND (pu.reference_id = (SELECT id FROM reference WHERE value = im.reference_value)
             OR (pu.reference_id IS NULL AND im.reference_value IS NULL))
        JOIN annotation_record_upserts ar ON ar.provenance_id = pu.id
        ON CONFLICT DO NOTHING
        RETURNING id
    )

    SELECT
        (SELECT COUNT(*) FROM interaction_mapping)::INT,
        (SELECT COUNT(*) FROM interaction_upserts)::INT,
        (SELECT COUNT(*) FROM evidence_inserts)::INT
    INTO v_processed, v_inserted, v_evidence;
    
    RETURN QUERY SELECT v_processed, v_inserted, v_evidence;
END;
$$ LANGUAGE plpgsql;
