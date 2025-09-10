-- This query will be executed by DuckDB and written to gold/data/interaction_evidence.parquet
-- Optimized gold_interaction_evidence.sql
-- Maps to Django model: db.models.InteractionEvidence
-- Only selects needed columns to minimize memory usage


WITH entity_lookup AS (
    -- Minimal entity lookup
    SELECT id, canonical_identifier
    FROM read_parquet('gold/data/entity.parquet') AS entity
),
interaction_lookup AS (
    -- Minimal interaction lookup
    SELECT id, entity_a_id, entity_b_id
    FROM read_parquet('gold/data/interaction_canonical.parquet') AS interaction_canonical
),
evidence_with_ids AS (
    -- Join interactions with entities and canonical interactions
    SELECT 
        i.*,
        e1.id as entity_a_id,
        e2.id as entity_b_id,
        ic.id as interaction_id
    FROM read_parquet('silver/data/interactions/*.parquet') AS i
    INNER JOIN entity_lookup e1 ON i.entity_a = e1.canonical_identifier
    INNER JOIN entity_lookup e2 ON i.entity_b = e2.canonical_identifier
    INNER JOIN interaction_lookup ic ON (
        (ic.entity_a_id = LEAST(e1.id, e2.id) AND ic.entity_b_id = GREATEST(e1.id, e2.id))
    )
),
normalized_evidence AS (
    -- Add directionality and sign information
    SELECT
        ei.*,
        -- Track if entities were swapped (for directionality)
        CASE 
            WHEN ei.entity_a <= ei.entity_b THEN 'forward' 
            ELSE 'reverse' 
        END AS direction,
        -- Determine if directed based on causal information
        (ei.causal_mechanism IS NOT NULL OR ei.causal_statement IS NOT NULL) AS is_directed,
        -- Derive sign from causal statement MI terms
        CASE
            WHEN ei.causal_statement IN (
                'MI:2235',  -- up-regulates
                'MI:2236',  -- up-regulates activity
                'MI:2237',  -- up-regulates expression
                'MI:2238',  -- up-regulates process
                'MI:2239'   -- up-regulates quantity
            ) THEN 'positive'
            WHEN ei.causal_statement IN (
                'MI:2240',  -- down-regulates
                'MI:2241',  -- down-regulates activity
                'MI:2242',  -- down-regulates expression
                'MI:2243',  -- down-regulates process
                'MI:2244'   -- down-regulates quantity
            ) THEN 'negative'
            WHEN ei.causal_statement IS NOT NULL THEN 'unknown'
            ELSE NULL
        END AS sign
    FROM evidence_with_ids ei
)
SELECT
    ROW_NUMBER() OVER (ORDER BY ne.source_identifier) AS id,
    ne.interaction_id,
    ds.id AS data_source_id,
    ref.id AS reference_id,
    it.id AS interaction_type_id,
    cm.id AS causal_mechanism_id,
    cs.id AS causal_statement_id,
    ne.evidence_sentence,
    ne.source_identifier,
    ne.is_directed,
    ne.direction,
    ne.sign
FROM normalized_evidence ne
-- Map CV terms (only select id and accession)
LEFT JOIN (SELECT id, accession FROM read_parquet('gold/data/cv_term.parquet')) ds ON ds.accession = ne.data_source
LEFT JOIN (SELECT id, accession FROM read_parquet('gold/data/cv_term.parquet')) it ON it.accession = ne.interaction_type
LEFT JOIN (SELECT id, accession FROM read_parquet('gold/data/cv_term.parquet')) cm ON cm.accession = ne.causal_mechanism
LEFT JOIN (SELECT id, accession FROM read_parquet('gold/data/cv_term.parquet')) cs ON cs.accession = ne.causal_statement
-- Map reference (only select id and pubmed_id)
LEFT JOIN (SELECT id, pubmed_id FROM read_parquet('gold/data/reference.parquet')) ref ON ref.pubmed_id = TRY_CAST(ne.pubmed_id AS BIGINT)
ORDER BY id;
