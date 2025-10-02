CREATE OR REPLACE FUNCTION process_entities_to_gold()
RETURNS TABLE(
    entities_processed INT,
    entities_inserted INT,
    entities_updated INT
) AS $$
DECLARE
    v_processed INT := 0;
    v_inserted INT := 0;
    v_updated INT := 0;
    v_count INT := 0;
BEGIN
    -- ✨ STEP 1: Cache all CV terms we'll need in a temp table
    DROP TABLE IF EXISTS temp_cv_cache;
    CREATE TEMP TABLE temp_cv_cache (
        namespace VARCHAR,
        term_name VARCHAR,
        term_id INT,
        PRIMARY KEY (namespace, term_name)
    );

    INSERT INTO temp_cv_cache (namespace, term_name, term_id)
    SELECT
        ns.name,
        t.name,
        t.id
    FROM gold.cv_term t
    JOIN gold.cv_namespace ns ON t.namespace_id = ns.id;

    GET DIAGNOSTICS v_count = ROW_COUNT;
    RAISE NOTICE '✓ Cached % CV terms', v_count;

    -- ✨ STEP 2: Extract and deduplicate entities from silver
    DROP TABLE IF EXISTS temp_valid_entities;
    CREATE TEMP TABLE temp_valid_entities AS
    SELECT
        entity_type,
        CASE
            WHEN entity_type = 'protein' THEN
                COALESCE(
                    (additional_identifiers::jsonb->>'uniprot'),
                    identifier
                )
            WHEN entity_type = 'compound' THEN
                COALESCE(
                    compound_inchi,
                    (additional_identifiers::jsonb->>'chebi'),
                    identifier
                )
            WHEN entity_type = 'reaction' THEN
                COALESCE(
                    reaction_ec_number,
                    identifier
                )
            ELSE identifier
        END as canonical_key,
        identifier,
        identifier_type,
        additional_identifiers,
        name,
        name_variants,
        protein_sequence,
        protein_class,
        compound_formula,
        compound_smiles,
        compound_inchi,
        molecular_weight,
        exact_mass,
        tpsa,
        logp,
        hbd,
        hba,
        rotatable_bonds,
        aromatic_rings,
        heavy_atoms,
        reaction_equation,
        reaction_directionality,
        reaction_pathway,
        reaction_ec_number,
        reaction_smiles,
        source_database
    FROM silver.silver_entities se
    WHERE is_valid = TRUE
      AND NOT EXISTS (
          SELECT 1 FROM gold.entity_identifier ei
          WHERE ei.identifier = se.identifier
      );

    DELETE FROM temp_valid_entities
    WHERE canonical_key IS NULL OR btrim(canonical_key) = '';

    GET DIAGNOSTICS v_count = ROW_COUNT;
    RAISE NOTICE '✓ Found % valid entities from silver (after removing % null canonical keys)',
        (SELECT COUNT(*) FROM temp_valid_entities), v_count;

    -- ✨ STEP 3: Prepare entities to insert (deduplicate by canonical_key)
    DROP TABLE IF EXISTS temp_entities_to_insert;
    CREATE TEMP TABLE temp_entities_to_insert AS
    SELECT DISTINCT ON (ve.canonical_key)
        ve.canonical_key,
        ve.entity_type,
        tcc.term_id as cv_term_id
    FROM temp_valid_entities ve
    JOIN LATERAL (
        SELECT term_id
        FROM temp_cv_cache tcc
        WHERE tcc.term_name = ve.entity_type
          AND tcc.namespace IN ('entity_type', 'OmniPath')
        ORDER BY CASE WHEN tcc.namespace = 'entity_type' THEN 0 ELSE 1 END
        LIMIT 1
    ) tcc ON TRUE
    WHERE NOT EXISTS (
        SELECT 1 FROM gold.entity_identifier ei
        WHERE ei.identifier = ve.canonical_key
    )
    ORDER BY ve.canonical_key;

    GET DIAGNOSTICS v_count = ROW_COUNT;
    RAISE NOTICE '✓ Prepared % unique entities to insert', v_count;

    -- ✨ STEP 4: Insert new entities with row tracking
    -- Add row numbers within each entity type to preserve mapping
    ALTER TABLE temp_entities_to_insert ADD COLUMN rn INT;
    UPDATE temp_entities_to_insert SET rn = t.rn
    FROM (
        SELECT canonical_key, ROW_NUMBER() OVER (PARTITION BY cv_term_id ORDER BY canonical_key) as rn
        FROM temp_entities_to_insert
    ) t
    WHERE temp_entities_to_insert.canonical_key = t.canonical_key;

    DROP TABLE IF EXISTS temp_inserted_entity_ids;
    CREATE TEMP TABLE temp_inserted_entity_ids AS
    WITH inserts AS (
        INSERT INTO gold.entity (cv_term_id, created_at)
        SELECT cv_term_id, NOW()
        FROM temp_entities_to_insert
        ORDER BY cv_term_id, rn  -- Preserve order within each type
        ON CONFLICT DO NOTHING
        RETURNING id, cv_term_id, created_at
    )
    SELECT
        id as entity_id,
        cv_term_id,
        created_at,
        ROW_NUMBER() OVER (PARTITION BY cv_term_id ORDER BY id) as rn
    FROM inserts;

    GET DIAGNOSTICS v_count = ROW_COUNT;
    v_inserted := v_count;
    RAISE NOTICE '✓ Inserted % new entities into gold.entity', v_count;

    -- Map entity IDs back to canonical keys using row numbers
    DROP TABLE IF EXISTS temp_inserted_entities;
    CREATE TEMP TABLE temp_inserted_entities AS
    SELECT
        ie.entity_id,
        eti.canonical_key,
        eti.entity_type,
        ie.created_at
    FROM temp_inserted_entity_ids ie
    JOIN temp_entities_to_insert eti
        ON eti.cv_term_id = ie.cv_term_id
        AND eti.rn = ie.rn
    WHERE ie.created_at >= NOW() - INTERVAL '5 seconds';

    GET DIAGNOSTICS v_count = ROW_COUNT;
    RAISE NOTICE '✓ Mapped % inserted entities to their canonical keys', v_count;

    -- ✨ STEP 5: Ensure OmniPath source and provenance exist first
    INSERT INTO gold.source (name)
    VALUES ('OmniPath')
    ON CONFLICT (name) DO NOTHING;

    INSERT INTO gold.provenance (source_id, created_at)
    SELECT gs.id, NOW()
    FROM gold.source gs
    WHERE gs.name = 'OmniPath'
    AND NOT EXISTS (
        SELECT 1 FROM gold.provenance pr WHERE pr.source_id = gs.id
    );

    -- Now create canonical identifiers for new entities
    -- OPTIMIZATION: Since these are newly inserted entities, no duplicates possible - just insert directly
    WITH default_prov AS (
        SELECT pr.id as provenance_id
        FROM gold.provenance pr
        JOIN gold.source gs ON gs.id = pr.source_id
        WHERE gs.name = 'OmniPath'
        LIMIT 1
    )
    INSERT INTO gold.entity_identifier (
        entity_id,
        cv_term_id,
        identifier,
        provenance_id,
        created_at
    )
    SELECT
        ie.entity_id,
        COALESCE(
            CASE
                WHEN ie.entity_type = 'protein' THEN
                    (SELECT term_id FROM temp_cv_cache WHERE term_name = 'uniprot' LIMIT 1)
                WHEN ie.entity_type = 'compound' THEN
                    (SELECT term_id FROM temp_cv_cache WHERE term_name = 'inchi' LIMIT 1)
                WHEN ie.entity_type = 'reaction' THEN
                    (SELECT term_id FROM temp_cv_cache WHERE term_name = 'ec-code' LIMIT 1)
                ELSE
                    (SELECT term_id FROM temp_cv_cache WHERE term_name = ie.entity_type AND namespace = 'OmniPath' LIMIT 1)
            END,
            6075  -- Fallback to compound cv_term
        ) as cv_term_id,
        ie.canonical_key,
        dp.provenance_id,
        NOW()
    FROM temp_inserted_entities ie
    CROSS JOIN default_prov dp;

    GET DIAGNOSTICS v_count = ROW_COUNT;
    RAISE NOTICE '✓ Created % canonical identifiers', v_count;

    -- ✨ STEP 6: Map all entities (existing + new) to their IDs
    DROP TABLE IF EXISTS temp_entity_mapping;
    CREATE TEMP TABLE temp_entity_mapping AS
    SELECT DISTINCT ON (ve.canonical_key)
        e.id as entity_id,
        ve.*
    FROM temp_valid_entities ve
    JOIN gold.entity_identifier ei ON ei.identifier = ve.canonical_key
    JOIN gold.entity e ON e.id = ei.entity_id
    ORDER BY ve.canonical_key, ve.identifier, ve.source_database;

    GET DIAGNOSTICS v_count = ROW_COUNT;
    v_processed := v_count;
    RAISE NOTICE '✓ Mapped % entities to IDs', v_count;

    -- ✨ STEP 7: Select primary data for each entity
    DROP TABLE IF EXISTS temp_entity_primary;
    CREATE TEMP TABLE temp_entity_primary AS
    SELECT DISTINCT ON (entity_id)
        *
    FROM temp_entity_mapping
    ORDER BY entity_id, identifier_type NULLS LAST, identifier NULLS LAST;

    GET DIAGNOSTICS v_count = ROW_COUNT;
    RAISE NOTICE '✓ Selected primary data for % entities', v_count;

    -- ✨ STEP 8: Upsert sources and provenance
    INSERT INTO gold.source (name)
    SELECT DISTINCT COALESCE(source_database, 'OmniPath')
    FROM temp_entity_mapping
    ON CONFLICT (name) DO NOTHING;

    GET DIAGNOSTICS v_count = ROW_COUNT;
    RAISE NOTICE '✓ Upserted % sources', v_count;

    INSERT INTO gold.provenance (source_id, created_at)
    SELECT DISTINCT gs.id, NOW()
    FROM (SELECT DISTINCT COALESCE(source_database, 'OmniPath') as src FROM temp_entity_mapping) t
    JOIN gold.source gs ON gs.name = t.src
    WHERE NOT EXISTS (
        SELECT 1 FROM gold.provenance pr WHERE pr.source_id = gs.id
    );

    GET DIAGNOSTICS v_count = ROW_COUNT;
    RAISE NOTICE '✓ Created % provenance records', v_count;

    -- Create provenance lookup
    DROP TABLE IF EXISTS temp_provenance_lookup;
    CREATE TEMP TABLE temp_provenance_lookup AS
    SELECT DISTINCT
        gs.name AS source_name,
        pr.id AS provenance_id
    FROM gold.provenance pr
    JOIN gold.source gs ON gs.id = pr.source_id
    WHERE gs.name IN (SELECT DISTINCT COALESCE(source_database, 'OmniPath') FROM temp_entity_mapping);

    -- ✨ STEP 9: Insert additional identifiers
    -- OPTIMIZATION: Create temp table of existing identifiers for this batch to avoid N*M NOT EXISTS
    CREATE TEMP TABLE temp_existing_identifiers AS
    SELECT DISTINCT entity_id, identifier, provenance_id
    FROM gold.entity_identifier
    WHERE entity_id IN (SELECT entity_id FROM temp_entity_mapping);

    CREATE INDEX idx_temp_existing ON temp_existing_identifiers(entity_id, provenance_id);

    WITH identifier_inserts AS (
        INSERT INTO gold.entity_identifier (
            entity_id,
            cv_term_id,
            identifier,
            provenance_id,
            created_at
        )
        SELECT DISTINCT
            em.entity_id,
            tcc.term_id,
            em.identifier,
            pl.provenance_id,
            NOW()
        FROM temp_entity_mapping em
        JOIN LATERAL (
            SELECT term_id
            FROM temp_cv_cache tcc
            WHERE tcc.term_name = em.identifier_type
              AND tcc.namespace IN ('identifier_type', 'OmniPath')
            ORDER BY CASE WHEN tcc.namespace = 'identifier_type' THEN 0 ELSE 1 END
            LIMIT 1
        ) tcc ON TRUE
        JOIN temp_provenance_lookup pl ON pl.source_name = COALESCE(em.source_database, 'OmniPath')
        LEFT JOIN temp_existing_identifiers tei
            ON tei.entity_id = em.entity_id
            AND tei.identifier = em.identifier
            AND tei.provenance_id = pl.provenance_id
        WHERE em.identifier_type IS NOT NULL
          AND em.identifier IS NOT NULL
          AND tei.entity_id IS NULL  -- Not in existing identifiers
        RETURNING entity_id
    )
    SELECT COUNT(*) INTO v_count FROM identifier_inserts;

    DROP TABLE temp_existing_identifiers;

    RAISE NOTICE '✓ Inserted % additional identifiers', v_count;

    -- ✨ STEP 10: Upsert proteins
    WITH protein_inserts AS (
        INSERT INTO gold.protein (entity_id, name, class, sequence)
        SELECT
            entity_id,
            name,
            protein_class,
            protein_sequence
        FROM temp_entity_primary
        WHERE entity_type = 'protein'
        ON CONFLICT (entity_id)
        DO UPDATE SET
            name = COALESCE(EXCLUDED.name, gold.protein.name),
            class = COALESCE(EXCLUDED.class, gold.protein.class),
            sequence = COALESCE(EXCLUDED.sequence, gold.protein.sequence)
        RETURNING entity_id
    )
    SELECT COUNT(*) INTO v_count FROM protein_inserts;

    RAISE NOTICE '✓ Upserted % proteins', v_count;

    -- ✨ STEP 11: Upsert compounds
    WITH compound_inserts AS (
        INSERT INTO gold.compound (
            entity_id, formula, molecular_weight, exact_mass,
            tpsa, logp, hbd, hba, rotatable_bonds, aromatic_rings, heavy_atoms
        )
        SELECT
            entity_id,
            compound_formula,
            molecular_weight,
            exact_mass,
            tpsa,
            logp,
            hbd,
            hba,
            rotatable_bonds,
            aromatic_rings,
            heavy_atoms
        FROM temp_entity_primary
        WHERE entity_type = 'compound'
        ON CONFLICT (entity_id)
        DO UPDATE SET
            formula = COALESCE(EXCLUDED.formula, gold.compound.formula),
            molecular_weight = COALESCE(EXCLUDED.molecular_weight, gold.compound.molecular_weight),
            exact_mass = COALESCE(EXCLUDED.exact_mass, gold.compound.exact_mass),
            tpsa = COALESCE(EXCLUDED.tpsa, gold.compound.tpsa),
            logp = COALESCE(EXCLUDED.logp, gold.compound.logp),
            hbd = COALESCE(EXCLUDED.hbd, gold.compound.hbd),
            hba = COALESCE(EXCLUDED.hba, gold.compound.hba),
            rotatable_bonds = COALESCE(EXCLUDED.rotatable_bonds, gold.compound.rotatable_bonds),
            aromatic_rings = COALESCE(EXCLUDED.aromatic_rings, gold.compound.aromatic_rings),
            heavy_atoms = COALESCE(EXCLUDED.heavy_atoms, gold.compound.heavy_atoms)
        RETURNING entity_id
    )
    SELECT COUNT(*) INTO v_count FROM compound_inserts;

    RAISE NOTICE '✓ Upserted % compounds', v_count;

    -- ✨ STEP 12: Upsert reactions
    WITH reaction_inserts AS (
        INSERT INTO gold.reaction_detail (
            entity_id, equation, directionality, pathway, ec_number, smiles
        )
        SELECT
            entity_id,
            reaction_equation,
            reaction_directionality,
            reaction_pathway,
            reaction_ec_number,
            reaction_smiles
        FROM temp_entity_primary
        WHERE entity_type = 'reaction'
        ON CONFLICT (entity_id)
        DO UPDATE SET
            equation = COALESCE(EXCLUDED.equation, gold.reaction_detail.equation),
            directionality = COALESCE(EXCLUDED.directionality, gold.reaction_detail.directionality),
            pathway = COALESCE(EXCLUDED.pathway, gold.reaction_detail.pathway),
            ec_number = COALESCE(EXCLUDED.ec_number, gold.reaction_detail.ec_number),
            smiles = COALESCE(EXCLUDED.smiles, gold.reaction_detail.smiles)
        RETURNING entity_id
    )
    SELECT COUNT(*) INTO v_count FROM reaction_inserts;

    RAISE NOTICE '✓ Upserted % reactions', v_count;

    RETURN QUERY SELECT v_processed, v_inserted, 0;
END;
$$ LANGUAGE plpgsql;

-- Execute the function
DO $$
DECLARE
    v_processed INT;
    v_inserted INT;
    v_updated INT;
BEGIN
    SELECT * INTO v_processed, v_inserted, v_updated FROM process_entities_to_gold();
    RAISE NOTICE 'Processed % entities: % inserted, % updated', v_processed, v_inserted, v_updated;
END;
$$;
