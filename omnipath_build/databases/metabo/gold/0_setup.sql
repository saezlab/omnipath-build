-- ============================================================================
-- GOLD LAYER SCHEMA - With Minimal Essential Indexes
-- ============================================================================

-- CONTROLLED VOCABULARY TABLES
CREATE TABLE cv_namespace (
    id              SERIAL PRIMARY KEY,  -- ✅ PK auto-indexed
    name            VARCHAR(255) NOT NULL UNIQUE  -- ✅ UNIQUE auto-indexed (for lookups)
);

CREATE TABLE cv_term (
    id              SERIAL PRIMARY KEY,  -- ✅ PK auto-indexed
    namespace_id    INT NOT NULL REFERENCES cv_namespace(id) ON DELETE CASCADE,
    accession       VARCHAR(100),
    name            VARCHAR(255) NOT NULL,
    description     TEXT,
    is_obsolete     BOOLEAN DEFAULT FALSE,
    replaces        INT REFERENCES cv_term(id),
    replaced_by     INT REFERENCES cv_term(id),
    CONSTRAINT cv_term_unique_in_namespace UNIQUE (namespace_id, name)  -- ✅ Needed for get_or_create
);

-- ✅ CRITICAL for temp_cv_cache JOINs
CREATE INDEX idx_cv_term_namespace_name ON cv_term(namespace_id, name);

-- SOURCE & PROVENANCE TABLES
CREATE TABLE source (
    id              SERIAL PRIMARY KEY,
    name            VARCHAR(255) NOT NULL UNIQUE,  -- ✅ Needed for lookups
    url             VARCHAR(500),
    description     TEXT,
    created_at      TIMESTAMP DEFAULT NOW()
);

CREATE TABLE reference (
    id              BIGSERIAL PRIMARY KEY,
    type_id         INT REFERENCES cv_term(id),
    value           TEXT NOT NULL UNIQUE,  -- ✅ Needed for ON CONFLICT
    citation        TEXT,
    year            INT,
    journal         TEXT,
    title           TEXT
);

CREATE TABLE provenance (
    id                  BIGSERIAL PRIMARY KEY,
    source_id           INT NOT NULL REFERENCES source(id),
    primary_source_id   INT REFERENCES source(id),
    reference_id        BIGINT REFERENCES reference(id),
    created_at          TIMESTAMP DEFAULT NOW()
);

-- ✅ CRITICAL for finding existing provenance records
CREATE INDEX idx_provenance_source_ref ON provenance(source_id, reference_id);

-- ENTITY TABLES
CREATE TABLE entity (
    id              BIGSERIAL PRIMARY KEY,
    cv_term_id      INT NOT NULL REFERENCES cv_term(id),
    created_at      TIMESTAMP DEFAULT NOW()
);

CREATE TABLE entity_identifier (
    id              BIGSERIAL PRIMARY KEY,
    entity_id       BIGINT NOT NULL REFERENCES entity(id) ON DELETE CASCADE,
    cv_term_id      INT NOT NULL REFERENCES cv_term(id),
    identifier      VARCHAR(255) NOT NULL,
    provenance_id   BIGINT NOT NULL REFERENCES provenance(id),
    created_at      TIMESTAMP DEFAULT NOW(),
    CONSTRAINT entity_identifier_unique UNIQUE (entity_id, identifier, provenance_id)  -- ✅ For ON CONFLICT
);

-- ✅ CRITICAL for deduplication lookups
CREATE INDEX idx_entity_identifier_lookup ON entity_identifier(identifier);
-- ✅ CRITICAL for finding entity identifiers by entity
CREATE INDEX idx_entity_identifier_entity ON entity_identifier(entity_id);

-- ENTITY TYPE-SPECIFIC TABLES
CREATE TABLE protein (
    entity_id       BIGINT PRIMARY KEY REFERENCES entity(id) ON DELETE CASCADE,
    name            TEXT,
    class           VARCHAR(255),
    sequence        TEXT
);

CREATE TABLE compound (
    entity_id           BIGINT PRIMARY KEY REFERENCES entity(id) ON DELETE CASCADE,
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

CREATE TABLE reaction (
    entity_id           BIGINT PRIMARY KEY REFERENCES entity(id) ON DELETE CASCADE,
    equation            TEXT,
    directionality      VARCHAR(50),
    pathway             VARCHAR(255),
    ec_number           VARCHAR(50),
    smiles              TEXT
);

-- INTERACTION TABLES
CREATE TABLE interaction (
    id              BIGSERIAL PRIMARY KEY,
    entity_a_id     BIGINT NOT NULL REFERENCES entity(id) ON DELETE CASCADE,
    entity_b_id     BIGINT NOT NULL REFERENCES entity(id) ON DELETE CASCADE,
    CONSTRAINT interaction_unique UNIQUE (entity_a_id, entity_b_id),  -- ✅ For ON CONFLICT
    CONSTRAINT interaction_ordered CHECK (entity_a_id <= entity_b_id)
);

-- ✅ CRITICAL for finding interactions by entity pair
CREATE INDEX idx_interaction_entities ON interaction(entity_a_id, entity_b_id);

CREATE TABLE membership (
    id                      BIGSERIAL PRIMARY KEY,
    parent_id               BIGINT NOT NULL REFERENCES entity(id) ON DELETE CASCADE,
    member_id               BIGINT NOT NULL REFERENCES entity(id) ON DELETE CASCADE,
    stoichiometry           FLOAT,
    role_id                 INT REFERENCES cv_term(id),
    annotation_record_id    BIGINT,
    provenance_id           BIGINT NOT NULL REFERENCES provenance(id)
);

-- ✅ CRITICAL for finding complex members
CREATE INDEX idx_membership_parent ON membership(parent_id);
CREATE INDEX idx_membership_member ON membership(member_id);

-- ANNOTATION TABLES
CREATE TABLE annotation_record (
    id              BIGSERIAL PRIMARY KEY,
    provenance_id   BIGINT NOT NULL REFERENCES provenance(id),
    created_at      TIMESTAMP DEFAULT NOW(),
    note            TEXT
);

CREATE TABLE annotation (
    id              BIGSERIAL PRIMARY KEY,
    record_id       BIGINT NOT NULL REFERENCES annotation_record(id) ON DELETE CASCADE,
    term_id         INT REFERENCES cv_term(id),
    value_term_id   INT REFERENCES cv_term(id),
    value_text      TEXT,
    value_num       FLOAT,
    units           VARCHAR(100),
    created_at      TIMESTAMP DEFAULT NOW()
);

-- ✅ CRITICAL for finding annotations by record
CREATE INDEX idx_annotation_record ON annotation(record_id);

CREATE TABLE entity_annotation_record (
    entity_id               BIGINT NOT NULL REFERENCES entity(id) ON DELETE CASCADE,
    annotation_record_id    BIGINT NOT NULL REFERENCES annotation_record(id) ON DELETE CASCADE,
    role                    VARCHAR(100),
    PRIMARY KEY (entity_id, annotation_record_id)
);

CREATE TABLE interaction_evidence (
    id                              BIGSERIAL PRIMARY KEY,
    interaction_id                  BIGINT NOT NULL REFERENCES interaction(id) ON DELETE CASCADE,
    provenance_id                   BIGINT NOT NULL REFERENCES provenance(id),
    annotation_record_id            BIGINT REFERENCES annotation_record(id),
    entity_a_annotation_record_id   BIGINT REFERENCES annotation_record(id),
    entity_b_annotation_record_id   BIGINT REFERENCES annotation_record(id),
    type_id                         INT REFERENCES cv_term(id),
    direction_id                    INT REFERENCES cv_term(id),
    sign_id                         INT REFERENCES cv_term(id),
    causal_mechanism_id             INT REFERENCES cv_term(id),
    causal_statement_id             INT REFERENCES cv_term(id),
    sentence                        TEXT,
    is_directed                     BOOLEAN,
    created_at                      TIMESTAMP DEFAULT NOW()
);

-- ✅ CRITICAL for finding evidence by interaction
CREATE INDEX idx_interaction_evidence_interaction ON interaction_evidence(interaction_id);
-- ✅ CRITICAL for checking if evidence exists (NOT EXISTS in function)
CREATE INDEX idx_interaction_evidence_provenance_sentence ON interaction_evidence(provenance_id, sentence);

-- ============================================================================
-- CONSTRAINTS & FOREIGN KEY INDEXES
-- ============================================================================

-- Add foreign key indexes for JOINs (minimal set)
CREATE INDEX idx_entity_type ON entity(cv_term_id);
CREATE INDEX idx_entity_identifier_type ON entity_identifier(cv_term_id);
CREATE INDEX idx_provenance_source ON provenance(source_id);
CREATE INDEX idx_annotation_term ON annotation(term_id) WHERE term_id IS NOT NULL;
