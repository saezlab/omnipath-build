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
BEGIN
    -- ✨ STEP 1: Cache all CV terms we'll need in a temp table
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

    -- ✨ STEP 2: Now process entities using JOINs (fast!)
    WITH entity_deduplication AS (
        SELECT
            entity_type,
            CASE
                WHEN entity_type = 'protein' THEN
                    COALESCE(
                        (additional_identifiers->>'uniprot'),
                        identifier
                    )
                WHEN entity_type = 'compound' THEN
                    COALESCE(
                        compound_inchi,
                        (additional_identifiers->>'chebi'),
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
            reaction_smiles
        FROM silver_entities
        WHERE is_valid = TRUE
        AND NOT EXISTS (
            SELECT 1 FROM entity_identifier ei
            WHERE ei.identifier = silver_entities.identifier
        )
    ),
    
    entity_upserts AS (
        INSERT INTO entity (cv_term_id, created_at)
        SELECT DISTINCT ON (ed.canonical_key)
            tcc.term_id,  -- ✨ FAST: Simple JOIN lookup!
            NOW()
        FROM entity_deduplication ed
        JOIN temp_cv_cache tcc 
            ON tcc.namespace = 'entity_type' 
            AND tcc.term_name = ed.entity_type
        ON CONFLICT DO NOTHING
        RETURNING id
    ),
    
    entity_mapping AS (
        SELECT DISTINCT ON (ed.canonical_key)
            ed.canonical_key,
            COALESCE(
                (SELECT e.id
                 FROM entity e
                 JOIN entity_identifier ei ON ei.entity_id = e.id
                 WHERE ei.identifier = ed.canonical_key
                 LIMIT 1),
                eu.id
            ) as entity_id,
            ed.*
        FROM entity_deduplication ed
        LEFT JOIN entity_upserts eu ON TRUE
    ),
    
    provenance_records AS (
        INSERT INTO provenance (source_id, created_at)
        SELECT DISTINCT
            (SELECT id FROM source LIMIT 1),  -- Default source, adjust as needed
            NOW()
        FROM entity_mapping em
        ON CONFLICT DO NOTHING
        RETURNING id, source_id
    ),
    
    identifier_upserts AS (
        INSERT INTO entity_identifier (
            entity_id,
            cv_term_id,
            identifier,
            provenance_id,
            created_at
        )
        SELECT 
            em.entity_id,
            tcc.term_id,  -- ✨ FAST: Simple JOIN lookup!
            em.identifier,
            pr.id,
            NOW()
        FROM entity_mapping em
        JOIN temp_cv_cache tcc
            ON tcc.namespace = 'identifier_type'
            AND tcc.term_name = em.identifier_type
        CROSS JOIN provenance_records pr
        ON CONFLICT (entity_id, identifier, provenance_id)
        DO NOTHING
        RETURNING entity_id
    ),
    
    protein_upserts AS (
        INSERT INTO protein (entity_id, name, class, sequence)
        SELECT 
            em.entity_id,
            em.name,
            em.protein_class,
            em.protein_sequence
        FROM entity_mapping em
        WHERE em.entity_type = 'protein'
        ON CONFLICT (entity_id) 
        DO UPDATE SET
            name = COALESCE(EXCLUDED.name, protein.name),
            class = COALESCE(EXCLUDED.class, protein.class),
            sequence = COALESCE(EXCLUDED.sequence, protein.sequence)
        RETURNING entity_id
    ),
    
    compound_upserts AS (
        INSERT INTO compound (
            entity_id, formula, molecular_weight, exact_mass,
            tpsa, logp, hbd, hba, rotatable_bonds, aromatic_rings, heavy_atoms
        )
        SELECT 
            em.entity_id,
            em.compound_formula,
            em.molecular_weight,
            em.exact_mass,
            em.tpsa,
            em.logp,
            em.hbd,
            em.hba,
            em.rotatable_bonds,
            em.aromatic_rings,
            em.heavy_atoms
        FROM entity_mapping em
        WHERE em.entity_type = 'compound'
        ON CONFLICT (entity_id)
        DO UPDATE SET
            formula = COALESCE(EXCLUDED.formula, compound.formula),
            molecular_weight = COALESCE(EXCLUDED.molecular_weight, compound.molecular_weight),
            exact_mass = COALESCE(EXCLUDED.exact_mass, compound.exact_mass),
            tpsa = COALESCE(EXCLUDED.tpsa, compound.tpsa),
            logp = COALESCE(EXCLUDED.logp, compound.logp),
            hbd = COALESCE(EXCLUDED.hbd, compound.hbd),
            hba = COALESCE(EXCLUDED.hba, compound.hba),
            rotatable_bonds = COALESCE(EXCLUDED.rotatable_bonds, compound.rotatable_bonds),
            aromatic_rings = COALESCE(EXCLUDED.aromatic_rings, compound.aromatic_rings),
            heavy_atoms = COALESCE(EXCLUDED.heavy_atoms, compound.heavy_atoms)
        RETURNING entity_id
    ),
    
    reaction_upserts AS (
        INSERT INTO reaction (
            entity_id, equation, directionality, pathway, ec_number, smiles
        )
        SELECT 
            em.entity_id,
            em.reaction_equation,
            em.reaction_directionality,
            em.reaction_pathway,
            em.reaction_ec_number,
            em.reaction_smiles
        FROM entity_mapping em
        WHERE em.entity_type = 'reaction'
        ON CONFLICT (entity_id)
        DO UPDATE SET
            equation = COALESCE(EXCLUDED.equation, reaction.equation),
            directionality = COALESCE(EXCLUDED.directionality, reaction.directionality),
            pathway = COALESCE(EXCLUDED.pathway, reaction.pathway),
            ec_number = COALESCE(EXCLUDED.ec_number, reaction.ec_number),
            smiles = COALESCE(EXCLUDED.smiles, reaction.smiles)
        RETURNING entity_id
    )

    SELECT
        COUNT(DISTINCT canonical_key)::INT,
        COUNT(DISTINCT CASE WHEN entity_id NOT IN (
            SELECT id FROM entity WHERE created_at < NOW() - INTERVAL '1 second'
        ) THEN entity_id END)::INT,
        COUNT(DISTINCT CASE WHEN entity_id IN (
            SELECT id FROM entity WHERE created_at < NOW() - INTERVAL '1 second'
        ) THEN entity_id END)::INT
    INTO v_processed, v_inserted, v_updated
    FROM entity_mapping;
    
    RETURN QUERY SELECT v_processed, v_inserted, v_updated;
END;
$$ LANGUAGE plpgsql;
