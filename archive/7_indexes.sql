-- Create indexes for optimal query performance on compound entities
-- Prioritizes InChIKey lookups, identifier searches, and property filtering

-- ============================================================================
-- 1. CRITICAL: Entity Identifier Lookups
-- ============================================================================

-- Note: Composite index (cv_term_id, identifier) removed due to btree size limits
-- Long identifiers (InChI >2704 bytes) exceed btree v4 maximum
-- Use separate indexes instead

-- Index on identifier type only
CREATE INDEX IF NOT EXISTS idx_entity_identifier_type
ON gold.entity_identifier(cv_term_id);

-- Index for reverse lookups: given entity, find all identifiers
CREATE INDEX IF NOT EXISTS idx_entity_identifier_entity_id
ON gold.entity_identifier(entity_id);

-- Index for provenance tracking
CREATE INDEX IF NOT EXISTS idx_entity_identifier_provenance_id
ON gold.entity_identifier(provenance_id);

-- ============================================================================
-- 2. Entity Type Filtering
-- ============================================================================

-- Index on entity type for filtering by compound/protein/etc.
CREATE INDEX IF NOT EXISTS idx_entity_cv_term_id
ON gold.entity(cv_term_id);

-- ============================================================================
-- 3. Compound Property Searches
-- ============================================================================

-- Foreign key index from compound to entity
CREATE INDEX IF NOT EXISTS idx_compound_entity_id
ON gold.compound(entity_id);

-- Molecular weight range filtering (very common for drug-likeness)
CREATE INDEX IF NOT EXISTS idx_compound_molecular_weight
ON gold.compound(molecular_weight)
WHERE molecular_weight IS NOT NULL;

-- LogP filtering (lipophilicity, drug-likeness)
CREATE INDEX IF NOT EXISTS idx_compound_logp
ON gold.compound(logp)
WHERE logp IS NOT NULL;

-- TPSA filtering (membrane permeability predictor)
CREATE INDEX IF NOT EXISTS idx_compound_tpsa
ON gold.compound(tpsa)
WHERE tpsa IS NOT NULL;

-- ============================================================================
-- 4. Annotation Lookups
-- ============================================================================

-- Index for finding annotations by term
CREATE INDEX IF NOT EXISTS idx_annotation_term_id
ON gold.annotation(term_id);

-- Index for annotation record lookups
CREATE INDEX IF NOT EXISTS idx_annotation_record_id
ON gold.annotation(record_id);

-- Index for entity annotation links
CREATE INDEX IF NOT EXISTS idx_entity_annotation_record_entity_id
ON gold.entity_annotation_record(entity_id);

CREATE INDEX IF NOT EXISTS idx_entity_annotation_record_annotation_record_id
ON gold.entity_annotation_record(annotation_record_id);

-- Index for annotation provenance
CREATE INDEX IF NOT EXISTS idx_annotation_record_provenance_id
ON gold.annotation_record(provenance_id);

-- ============================================================================
-- 5. Provenance Tracking
-- ============================================================================

-- Index for source-based queries
CREATE INDEX IF NOT EXISTS idx_provenance_source_id
ON gold.provenance(source_id);

-- Index for primary source tracking
CREATE INDEX IF NOT EXISTS idx_provenance_primary_source_id
ON gold.provenance(primary_source_id)
WHERE primary_source_id IS NOT NULL;

-- Index for reference lookups
CREATE INDEX IF NOT EXISTS idx_provenance_reference_id
ON gold.provenance(reference_id)
WHERE reference_id IS NOT NULL;

-- ============================================================================
-- 6. Reference Lookups
-- ============================================================================

-- Composite index for reference lookups by type and value
CREATE INDEX IF NOT EXISTS idx_reference_type_value
ON gold.reference(type_id, value);

-- ============================================================================
-- 7. Full-Text Search on Identifiers (Optional but Recommended)
-- ============================================================================

-- GIN index for fast prefix/substring searches on identifier values
-- Enables autocomplete and fuzzy matching
CREATE INDEX IF NOT EXISTS idx_entity_identifier_value_trgm
ON gold.entity_identifier USING gin(identifier gin_trgm_ops);

-- ============================================================================
-- 8. CV Term Lookups
-- ============================================================================

-- Index on CV term accession for fast lookups by accession code
CREATE INDEX IF NOT EXISTS idx_cv_term_accession
ON gold.cv_term(accession);

-- Index on CV term name for human-readable lookups
CREATE INDEX IF NOT EXISTS idx_cv_term_name
ON gold.cv_term(name);

-- Index on namespace for filtering by vocabulary
CREATE INDEX IF NOT EXISTS idx_cv_term_namespace_id
ON gold.cv_term(namespace_id);

-- ============================================================================
-- Statistics
-- ============================================================================

DO $$
BEGIN
    RAISE NOTICE 'Indexes created successfully';
    RAISE NOTICE 'Run ANALYZE to update query planner statistics';
END $$;

-- Update statistics for query planner
ANALYZE gold.entity;
ANALYZE gold.entity_identifier;
ANALYZE gold.compound;
ANALYZE gold.annotation;
ANALYZE gold.annotation_record;
ANALYZE gold.entity_annotation_record;
ANALYZE gold.provenance;
ANALYZE gold.reference;
