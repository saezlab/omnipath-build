-- High-impact indexes for metabo/gold database
-- Following 80/20 rule for maximum query performance

-- 1. CRITICAL: RDKit structure search index
-- Enables fast substructure and similarity searches
CREATE INDEX IF NOT EXISTS idx_canonical_mol_gist ON gold.canonical_structures USING gist(mol);

-- 2. Chemical identifier lookups
-- InChIKey is the most common lookup identifier across databases
CREATE INDEX IF NOT EXISTS idx_canonical_inchikey ON gold.canonical_structures(inchikey);

-- 3. Drug-likeness filtering
-- Molecular weight is the most frequently filtered molecular property
CREATE INDEX IF NOT EXISTS idx_canonical_mw ON gold.canonical_structures(molecular_weight);

-- 4. Compound type queries
-- Partial index for drug compounds (much faster than scanning all rows)
CREATE INDEX IF NOT EXISTS idx_compounds_drugs ON gold.compounds(compound_id) WHERE is_drug = TRUE;

-- Foreign key performance for joins between compounds and canonical_structures
CREATE INDEX IF NOT EXISTS idx_compounds_canonical_id ON gold.compounds(canonical_id);

-- 5. External ID lookups
-- Critical for integrating with external databases and identifier mapping
CREATE INDEX IF NOT EXISTS idx_identifiers_type_value ON gold.compound_identifiers(identifier_type, identifier_value);

-- 1. Trigram index for fast prefix searches on identifier values       
-- This enables fast ILIKE 'prefix%' queries used in autocomplete
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE INDEX IF NOT EXISTS idx_compound_identifiers_value_trgm ON gold.compound_identifiers USING gin(identifier_value gin_trgm_ops);

-- 2. Composite index for faster joins between identifiers and compounds
-- This speeds up queries that join on compound_id and need canonical_id
CREATE INDEX IF NOT EXISTS idx_compound_identifiers_compound_canonical ON gold.compound_identifiers(compound_id, identifier_type);

-- Create index for fast similarity searches
CREATE INDEX IF NOT EXISTS idx_canonical_morgan_fp ON gold.canonical_structures USING gist(morgan_fp);
