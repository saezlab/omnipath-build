-- ============================================================================
-- GOLD LAYER SCHEMA - With Minimal Essential Indexes
-- ============================================================================

-- Gold schema is created by the loader; ensure tables exist without destructive drops

-- ============================================================================
-- CONTROLLED VOCABULARY TABLES
-- ============================================================================

CREATE TABLE IF NOT EXISTS gold.cv_namespace (
    id              SERIAL PRIMARY KEY,  -- ✅ PK auto-indexed
    name            VARCHAR(255) NOT NULL UNIQUE  -- ✅ UNIQUE auto-indexed (for lookups)
);

CREATE TABLE IF NOT EXISTS gold.cv_term (
    id              SERIAL PRIMARY KEY,  -- ✅ PK auto-indexed
    namespace_id    INT NOT NULL REFERENCES gold.cv_namespace(id) ON DELETE CASCADE,
    accession       VARCHAR(100),
    name            VARCHAR(255) NOT NULL,
    description     TEXT,
    is_obsolete     BOOLEAN DEFAULT FALSE,
    replaces        INT REFERENCES gold.cv_term(id),
    replaced_by     INT REFERENCES gold.cv_term(id),
    CONSTRAINT cv_term_unique_in_namespace UNIQUE (namespace_id, name)  -- ✅ Needed for get_or_create
);

-- ✅ CRITICAL for temp_cv_cache JOINs
CREATE INDEX IF NOT EXISTS idx_cv_term_namespace_name ON gold.cv_term(namespace_id, name);

-- ============================================================================
-- SOURCE & PROVENANCE TABLES
-- ============================================================================

CREATE TABLE IF NOT EXISTS gold.source (
    id              SERIAL PRIMARY KEY,
    name            VARCHAR(255) NOT NULL UNIQUE,  -- ✅ Needed for lookups
    url             VARCHAR(500),
    description     TEXT,
    created_at      TIMESTAMP DEFAULT NOW()
);

-- Ensure a default OmniPath source exists for fallbacks
INSERT INTO gold.source (name, url, description)
VALUES ('OmniPath', 'https://omnipathdb.org', 'Default fallback source for harmonized records')
ON CONFLICT (name) DO NOTHING;

CREATE TABLE IF NOT EXISTS gold.reference (
    id              BIGSERIAL PRIMARY KEY,
    type_id         INT REFERENCES gold.cv_term(id),
    value           TEXT NOT NULL UNIQUE,  -- ✅ Needed for ON CONFLICT
    citation        TEXT,
    year            INT,
    journal         TEXT,
    title           TEXT
);

CREATE TABLE IF NOT EXISTS gold.provenance (
    id                  BIGSERIAL PRIMARY KEY,
    source_id           INT NOT NULL REFERENCES gold.source(id),
    primary_source_id   INT REFERENCES gold.source(id),
    reference_id        BIGINT REFERENCES gold.reference(id),
    created_at          TIMESTAMP DEFAULT NOW()
);

-- ✅ CRITICAL for finding existing provenance records
CREATE INDEX IF NOT EXISTS idx_provenance_source_ref ON gold.provenance(source_id, reference_id);

-- ============================================================================
-- ENTITY TABLES
-- ============================================================================

CREATE TABLE IF NOT EXISTS gold.entity (
    id              BIGSERIAL PRIMARY KEY,
    cv_term_id      INT NOT NULL REFERENCES gold.cv_term(id),
    created_at      TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS gold.entity_identifier (
    id              BIGSERIAL PRIMARY KEY,
    entity_id       BIGINT NOT NULL REFERENCES gold.entity(id) ON DELETE CASCADE,
    cv_term_id      INT NOT NULL REFERENCES gold.cv_term(id),
    identifier      TEXT NOT NULL,  -- Changed from VARCHAR(255) to support long identifiers (InChI can be >3000 chars)
    provenance_id   BIGINT NOT NULL REFERENCES gold.provenance(id),
    created_at      TIMESTAMP DEFAULT NOW()
    -- Note: UNIQUE constraint removed due to btree index size limits for long identifiers (InChI >2704 bytes)
    -- Deduplication handled at application level with explicit checks before insert
);

-- ✅ CRITICAL for finding entity identifiers by entity
CREATE INDEX IF NOT EXISTS idx_entity_identifier_entity ON gold.entity_identifier(entity_id);
-- ✅ Composite index for manual deduplication queries
CREATE INDEX IF NOT EXISTS idx_entity_identifier_dedup ON gold.entity_identifier(entity_id, provenance_id);

-- ENTITY TYPE-SPECIFIC TABLES
CREATE TABLE IF NOT EXISTS gold.protein (
    entity_id       BIGINT PRIMARY KEY REFERENCES gold.entity(id) ON DELETE CASCADE,
    name            TEXT,
    class           VARCHAR(255),
    sequence        TEXT
);

CREATE TABLE IF NOT EXISTS gold.compound (
    entity_id           BIGINT PRIMARY KEY REFERENCES gold.entity(id) ON DELETE CASCADE,
    formula             VARCHAR(255),
    molecular_weight    FLOAT,
    exact_mass          FLOAT,
    tpsa                FLOAT,
    logp                FLOAT,
    hbd                 INT,
    hba                 INT,
    rotatable_bonds     INT,
    aromatic_rings      INT,
    heavy_atoms         INT
);

-- Table renamed from `reaction` because the RDKit extension defines a type with that name
CREATE TABLE IF NOT EXISTS gold.reaction_detail (
    entity_id           BIGINT PRIMARY KEY REFERENCES gold.entity(id) ON DELETE CASCADE,
    equation            TEXT,
    directionality      VARCHAR(50),
    pathway             VARCHAR(255),
    ec_number           VARCHAR(50),
    smiles              TEXT
);

-- ============================================================================
-- INTERACTION TABLES
-- ============================================================================

CREATE TABLE IF NOT EXISTS gold.interaction (
    id              BIGSERIAL PRIMARY KEY,
    entity_a_id     BIGINT NOT NULL REFERENCES gold.entity(id) ON DELETE CASCADE,
    entity_b_id     BIGINT NOT NULL REFERENCES gold.entity(id) ON DELETE CASCADE,
    CONSTRAINT interaction_unique UNIQUE (entity_a_id, entity_b_id),  -- ✅ For ON CONFLICT
    CONSTRAINT interaction_ordered CHECK (entity_a_id <= entity_b_id)
);

-- ✅ CRITICAL for finding interactions by entity pair
CREATE INDEX IF NOT EXISTS idx_interaction_entities ON gold.interaction(entity_a_id, entity_b_id);

CREATE TABLE IF NOT EXISTS gold.membership (
    id                      BIGSERIAL PRIMARY KEY,
    parent_id               BIGINT NOT NULL REFERENCES gold.entity(id) ON DELETE CASCADE,
    member_id               BIGINT NOT NULL REFERENCES gold.entity(id) ON DELETE CASCADE,
    stoichiometry           FLOAT,
    role_id                 INT REFERENCES gold.cv_term(id),
    annotation_record_id    BIGINT,
    provenance_id           BIGINT NOT NULL REFERENCES gold.provenance(id)
);

-- ✅ CRITICAL for finding complex members
CREATE INDEX IF NOT EXISTS idx_membership_parent ON gold.membership(parent_id);
CREATE INDEX IF NOT EXISTS idx_membership_member ON gold.membership(member_id);

-- ============================================================================
-- ANNOTATION TABLES
-- ============================================================================

CREATE TABLE IF NOT EXISTS gold.annotation_record (
    id              BIGSERIAL PRIMARY KEY,
    provenance_id   BIGINT NOT NULL REFERENCES gold.provenance(id),
    created_at      TIMESTAMP DEFAULT NOW(),
    note            TEXT
);

CREATE TABLE IF NOT EXISTS gold.annotation (
    id              BIGSERIAL PRIMARY KEY,
    record_id       BIGINT NOT NULL REFERENCES gold.annotation_record(id) ON DELETE CASCADE,
    term_id         INT REFERENCES gold.cv_term(id),
    value_term_id   INT REFERENCES gold.cv_term(id),
    value_text      TEXT,
    value_num       FLOAT,
    units           VARCHAR(100),
    created_at      TIMESTAMP DEFAULT NOW()
);

-- ✅ CRITICAL for finding annotations by record
CREATE INDEX IF NOT EXISTS idx_annotation_record ON gold.annotation(record_id);

CREATE TABLE IF NOT EXISTS gold.entity_annotation_record (
    entity_id               BIGINT NOT NULL REFERENCES gold.entity(id) ON DELETE CASCADE,
    annotation_record_id    BIGINT NOT NULL REFERENCES gold.annotation_record(id) ON DELETE CASCADE,
    role                    VARCHAR(100),
    PRIMARY KEY (entity_id, annotation_record_id)
);

CREATE TABLE IF NOT EXISTS gold.interaction_evidence (
    id                              BIGSERIAL PRIMARY KEY,
    interaction_id                  BIGINT NOT NULL REFERENCES gold.interaction(id) ON DELETE CASCADE,
    provenance_id                   BIGINT NOT NULL REFERENCES gold.provenance(id),
    annotation_record_id            BIGINT REFERENCES gold.annotation_record(id),
    entity_a_annotation_record_id   BIGINT REFERENCES gold.annotation_record(id),
    entity_b_annotation_record_id   BIGINT REFERENCES gold.annotation_record(id),
    type_id                         INT REFERENCES gold.cv_term(id),
    direction_id                    INT REFERENCES gold.cv_term(id),
    sign_id                         INT REFERENCES gold.cv_term(id),
    causal_mechanism_id             INT REFERENCES gold.cv_term(id),
    causal_statement_id             INT REFERENCES gold.cv_term(id),
    sentence                        TEXT,
    is_directed                     BOOLEAN,
    created_at                      TIMESTAMP DEFAULT NOW()
);

-- ✅ CRITICAL for finding evidence by interaction
CREATE INDEX IF NOT EXISTS idx_interaction_evidence_interaction ON gold.interaction_evidence(interaction_id);
-- ✅ CRITICAL for checking if evidence exists (NOT EXISTS in function)
CREATE INDEX IF NOT EXISTS idx_interaction_evidence_provenance_sentence ON gold.interaction_evidence(provenance_id, sentence);

-- ============================================================================
-- CONSTRAINTS & FOREIGN KEY INDEXES
-- ============================================================================

-- Add foreign key indexes for JOINs (minimal set)
CREATE INDEX IF NOT EXISTS idx_entity_type ON gold.entity(cv_term_id);
CREATE INDEX IF NOT EXISTS idx_entity_identifier_type ON gold.entity_identifier(cv_term_id);
CREATE INDEX IF NOT EXISTS idx_provenance_source ON gold.provenance(source_id);
CREATE INDEX IF NOT EXISTS idx_annotation_term ON gold.annotation(term_id) WHERE term_id IS NOT NULL;
