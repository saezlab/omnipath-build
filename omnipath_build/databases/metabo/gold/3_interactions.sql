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
    FROM gold.cv_term t
    JOIN gold.cv_namespace ns ON t.namespace_id = ns.id
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
        FROM silver.silver_interactions si
        JOIN gold.entity_identifier eia ON eia.identifier = si.entity_a_identifier
        JOIN gold.entity ea ON ea.id = eia.entity_id
        JOIN gold.entity_identifier eib ON eib.identifier = si.entity_b_identifier
        JOIN gold.entity eb ON eb.id = eib.entity_id
        WHERE si.is_valid = TRUE
        AND NOT EXISTS (
            SELECT 1 FROM gold.interaction_evidence ie
            WHERE ie.sentence = si.sentence
        )
    ),
    
    requested_sources AS (
        SELECT DISTINCT COALESCE(ist.source_name, 'OmniPath') AS source_name
        FROM interaction_staging ist
    ),

    source_upserts AS (
        INSERT INTO gold.source (name)
        SELECT source_name
        FROM requested_sources
        WHERE source_name IS NOT NULL
        ON CONFLICT (name) DO NOTHING
        RETURNING id, name
    ),
    
    interaction_upserts AS (
        INSERT INTO gold.interaction (entity_a_id, entity_b_id)
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
                (SELECT i.id FROM gold.interaction i 
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
        INSERT INTO gold.reference (
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
        JOIN LATERAL (
            SELECT term_id
            FROM temp_cv_cache tcc
            WHERE tcc.term_name = im.reference_type
              AND tcc.namespace IN ('reference_type', 'OmniPath')
            ORDER BY CASE WHEN tcc.namespace = 'reference_type' THEN 0 ELSE 1 END
            LIMIT 1
        ) tcc ON TRUE
        WHERE im.reference_value IS NOT NULL
        ON CONFLICT (value) DO NOTHING
        RETURNING id, value
    ),

    reference_lookup AS (
        SELECT DISTINCT
            im.reference_value,
            gr.id AS reference_id
        FROM interaction_mapping im
        LEFT JOIN gold.reference gr ON gr.value = im.reference_value
    ),
    
    provenance_upserts AS (
        INSERT INTO gold.provenance (
            source_id,
            primary_source_id,
            reference_id,
            created_at
        )
        SELECT DISTINCT
            gs.id,
            CASE WHEN im.is_primary_source THEN gs.id END,
            rl.reference_id,
            NOW()
        FROM interaction_mapping im
        JOIN gold.source gs ON gs.name = COALESCE(im.source_name, 'OmniPath')
        LEFT JOIN reference_lookup rl
            ON rl.reference_value IS NOT DISTINCT FROM im.reference_value
        WHERE NOT EXISTS (
            SELECT 1
            FROM gold.provenance pr
            WHERE pr.source_id = gs.id
              AND pr.reference_id IS NOT DISTINCT FROM rl.reference_id
        )
        RETURNING id, source_id, reference_id
    ),
    
    provenance_lookup AS (
        SELECT DISTINCT
            COALESCE(im.source_name, 'OmniPath') AS source_name,
            im.reference_value,
            pr.id AS provenance_id
        FROM interaction_mapping im
        JOIN gold.source gs ON gs.name = COALESCE(im.source_name, 'OmniPath')
        JOIN gold.provenance pr ON pr.source_id = gs.id
        LEFT JOIN reference_lookup rl
            ON rl.reference_id IS NOT DISTINCT FROM pr.reference_id
        WHERE rl.reference_value IS NOT DISTINCT FROM im.reference_value
    ),

    annotation_record_upserts AS (
        INSERT INTO gold.annotation_record (provenance_id, created_at)
        SELECT DISTINCT pl.provenance_id, NOW()
        FROM provenance_lookup pl
        WHERE NOT EXISTS (
            SELECT 1
            FROM gold.annotation_record ar
            WHERE ar.provenance_id = pl.provenance_id
        )
        RETURNING id, provenance_id
    ),

    annotation_record_lookup AS (
        SELECT DISTINCT
            ar.provenance_id,
            ar.id AS annotation_record_id
        FROM gold.annotation_record ar
        WHERE ar.provenance_id IN (SELECT provenance_id FROM provenance_lookup)
    ),
    
    evidence_inserts AS (
        INSERT INTO gold.interaction_evidence (
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
            pl.provenance_id,
            ar.annotation_record_id,
            tcc_type.term_id,          -- ✨ FAST JOIN!
            tcc_dir.term_id,            -- ✨ FAST JOIN!
            tcc_sign.term_id,           -- ✨ FAST JOIN!
            tcc_mech.term_id,           -- ✨ FAST JOIN!
            im.sentence,
            im.is_directed,
            NOW()
        FROM interaction_mapping im
        JOIN LATERAL (
            SELECT term_id
            FROM temp_cv_cache tcc
            WHERE tcc.term_name = im.interaction_type
              AND tcc.namespace IN ('interaction_type', 'OmniPath')
            ORDER BY CASE WHEN tcc.namespace = 'interaction_type' THEN 0 ELSE 1 END
            LIMIT 1
        ) tcc_type ON TRUE
        LEFT JOIN LATERAL (
            SELECT term_id
            FROM temp_cv_cache tcc
            WHERE tcc.term_name = im.direction
              AND tcc.namespace IN ('direction', 'OmniPath')
            ORDER BY CASE WHEN tcc.namespace = 'direction' THEN 0 ELSE 1 END
            LIMIT 1
        ) tcc_dir ON TRUE
        LEFT JOIN LATERAL (
            SELECT term_id
            FROM temp_cv_cache tcc
            WHERE tcc.term_name = im.sign
              AND tcc.namespace IN ('sign', 'OmniPath')
            ORDER BY CASE WHEN tcc.namespace = 'sign' THEN 0 ELSE 1 END
            LIMIT 1
        ) tcc_sign ON TRUE
        LEFT JOIN LATERAL (
            SELECT term_id
            FROM temp_cv_cache tcc
            WHERE tcc.term_name = im.causal_mechanism
              AND tcc.namespace IN ('causal_mechanism', 'OmniPath')
            ORDER BY CASE WHEN tcc.namespace = 'causal_mechanism' THEN 0 ELSE 1 END
            LIMIT 1
        ) tcc_mech ON TRUE
        JOIN provenance_lookup pl
            ON pl.source_name = COALESCE(im.source_name, 'OmniPath')
           AND pl.reference_value IS NOT DISTINCT FROM im.reference_value
        JOIN annotation_record_lookup ar ON ar.provenance_id = pl.provenance_id
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

-- Execute the function
DO $$
DECLARE
    v_processed INT;
    v_inserted INT;
    v_evidence INT;
BEGIN
    SELECT * INTO v_processed, v_inserted, v_evidence FROM process_interactions_to_gold();
    RAISE NOTICE 'Processed % interactions: % inserted, % evidence records added', v_processed, v_inserted, v_evidence;
END;
$$;
