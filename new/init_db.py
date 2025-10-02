"""
PostgreSQL Database Initialization Script
Sets up all tables, indexes, and views in PostgreSQL
"""

import psycopg2
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT
from typing import Optional

__all__ = [
    'create_annotation_tables',
    'create_core_tables',
    'create_entity_tables',
    'create_functions',
    'create_indexes',
    'create_interaction_tables',
    'create_views',
    'drop_all_tables',
    'initialize_database',
    'print_database_stats',
]


def initialize_database(
    host: str = "localhost",
    port: int = 5432,
    database: str = "biodata",
    user: str = "postgres",
    password: str = "postgres",
    drop_existing: bool = False
):
    """
    Initialize PostgreSQL database with all required tables and indexes
    
    Args:
        host: PostgreSQL host
        port: PostgreSQL port
        database: Database name
        user: Database user
        password: Database password
        drop_existing: If True, drop existing tables before creating new ones
    """
    # Connect to PostgreSQL
    conn = psycopg2.connect(
        host=host,
        port=port,
        database=database,
        user=user,
        password=password
    )
    conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    cur = conn.cursor()
    
    try:
        if drop_existing:
            print("Dropping existing tables...")
            drop_all_tables(cur)
        
        print("Creating core tables...")
        create_core_tables(cur)
        
        print("Creating entity tables...")
        create_entity_tables(cur)
        
        print("Creating interaction tables...")
        create_interaction_tables(cur)
        
        print("Creating annotation tables...")
        create_annotation_tables(cur)
        
        print("Creating indexes...")
        create_indexes(cur)
        
        print("Creating utility views...")
        create_views(cur)
        
        print("Creating helper functions...")
        create_functions(cur)
        
        print(f"✓ Database '{database}' initialized successfully!")
        print_database_stats(host, port, database, user, password)
        
    finally:
        cur.close()
        conn.close()


def drop_all_tables(cur):
    """Drop all tables in reverse dependency order"""
    tables = [
        'annotation',
        'entity_annotation_record',
        'annotation_record',
        'interaction_evidence',
        'interaction',
        'membership',
        'entity_identifier',
        'protein',
        'compound',
        'reaction',
        'entity',
        'provenance',
        'reference',
        'source',
        'cv_term',
        'cv_namespace'
    ]
    
    for table in tables:
        cur.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
        print(f"  Dropped {table}")


def create_core_tables(cur):
    """Create core reference tables"""
    
    # CV Namespace
    cur.execute("""
        CREATE TABLE cv_namespace (
            id SERIAL PRIMARY KEY,
            name VARCHAR(255) UNIQUE NOT NULL,
            uri TEXT,
            description TEXT
        )
    """)
    
    # CV Term
    cur.execute("""
        CREATE TABLE cv_term (
            id SERIAL PRIMARY KEY,
            namespace_id INTEGER NOT NULL REFERENCES cv_namespace(id),
            accession VARCHAR(255) NOT NULL,
            name VARCHAR(255) NOT NULL,
            description TEXT,
            is_obsolete BOOLEAN DEFAULT false,
            replaces INTEGER REFERENCES cv_term(id),
            replaced_by INTEGER REFERENCES cv_term(id),
            UNIQUE (namespace_id, accession)
        )
    """)
    
    # Source
    cur.execute("""
        CREATE TABLE source (
            id SERIAL PRIMARY KEY,
            name VARCHAR(255) UNIQUE NOT NULL,
            url VARCHAR(500),
            description TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # Reference
    cur.execute("""
        CREATE TABLE reference (
            id BIGSERIAL PRIMARY KEY,
            type_id INTEGER NOT NULL REFERENCES cv_term(id),
            value TEXT NOT NULL,
            citation TEXT,
            year INTEGER,
            journal TEXT,
            title TEXT,
            UNIQUE (type_id, value)
        )
    """)
    
    # Provenance
    cur.execute("""
        CREATE TABLE provenance (
            id BIGSERIAL PRIMARY KEY,
            source_id INTEGER NOT NULL REFERENCES source(id),
            primary_source_id INTEGER REFERENCES source(id),
            reference_id BIGINT REFERENCES reference(id),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)


def create_entity_tables(cur):
    """Create entity-related tables"""
    
    # Entity
    cur.execute("""
        CREATE TABLE entity (
            id BIGSERIAL PRIMARY KEY,
            cv_term_id INTEGER NOT NULL REFERENCES cv_term(id),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # Entity Identifier
    cur.execute("""
        CREATE TABLE entity_identifier (
            id BIGSERIAL PRIMARY KEY,
            entity_id BIGINT NOT NULL REFERENCES entity(id) ON DELETE CASCADE,
            cv_term_id INTEGER NOT NULL REFERENCES cv_term(id),
            identifier VARCHAR(500) NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            provenance_id BIGINT NOT NULL REFERENCES provenance(id),
            UNIQUE (cv_term_id, identifier)
        )
    """)
    
    # Protein
    cur.execute("""
        CREATE TABLE protein (
            entity_id BIGINT PRIMARY KEY REFERENCES entity(id) ON DELETE CASCADE,
            name TEXT,
            class VARCHAR(255),
            sequence TEXT
        )
    """)
    
    # Compound
    cur.execute("""
        CREATE TABLE compound (
            entity_id BIGINT PRIMARY KEY REFERENCES entity(id) ON DELETE CASCADE,
            formula VARCHAR(500),
            molecular_weight DOUBLE PRECISION,
            exact_mass DOUBLE PRECISION,
            tpsa DOUBLE PRECISION,
            logp DOUBLE PRECISION,
            hbd INTEGER,
            hba INTEGER,
            rotatable_bonds INTEGER,
            aromatic_rings INTEGER,
            heavy_atoms INTEGER
        )
    """)
    
    # Reaction
    cur.execute("""
        CREATE TABLE reaction (
            entity_id BIGINT PRIMARY KEY REFERENCES entity(id) ON DELETE CASCADE,
            equation TEXT,
            directionality VARCHAR(50),
            pathway VARCHAR(500),
            ec_number VARCHAR(50),
            smiles TEXT
        )
    """)
    
    # Membership (for complexes, pathways, etc.)
    cur.execute("""
        CREATE TABLE membership (
            id BIGSERIAL PRIMARY KEY,
            parent_id BIGINT NOT NULL REFERENCES entity(id) ON DELETE CASCADE,
            member_id BIGINT NOT NULL REFERENCES entity(id) ON DELETE CASCADE,
            stoichiometry DOUBLE PRECISION,
            role_id INTEGER REFERENCES cv_term(id),
            annotation_record_id BIGINT,
            provenance_id BIGINT NOT NULL REFERENCES provenance(id),
            UNIQUE (parent_id, member_id, role_id)
        )
    """)


def create_interaction_tables(cur):
    """Create interaction-related tables"""
    
    # Interaction
    cur.execute("""
        CREATE TABLE interaction (
            id BIGSERIAL PRIMARY KEY,
            entity_a_id BIGINT NOT NULL REFERENCES entity(id) ON DELETE CASCADE,
            entity_b_id BIGINT NOT NULL REFERENCES entity(id) ON DELETE CASCADE,
            CHECK (entity_a_id != entity_b_id)
        )
    """)
    
    # Interaction Evidence
    cur.execute("""
        CREATE TABLE interaction_evidence (
            id BIGSERIAL PRIMARY KEY,
            interaction_id BIGINT NOT NULL REFERENCES interaction(id) ON DELETE CASCADE,
            provenance_id BIGINT NOT NULL REFERENCES provenance(id),
            annotation_record_id BIGINT,
            entity_a_annotation_record_id BIGINT,
            entity_b_annotation_record_id BIGINT,
            type_id INTEGER NOT NULL REFERENCES cv_term(id),
            direction_id INTEGER REFERENCES cv_term(id),
            sign_id INTEGER REFERENCES cv_term(id),
            causal_mechanism_id INTEGER REFERENCES cv_term(id),
            causal_statement_id INTEGER REFERENCES cv_term(id),
            sentence TEXT,
            is_directed BOOLEAN DEFAULT false,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)


def create_annotation_tables(cur):
    """Create annotation-related tables"""
    
    # Annotation Record
    cur.execute("""
        CREATE TABLE annotation_record (
            id BIGSERIAL PRIMARY KEY,
            provenance_id BIGINT NOT NULL REFERENCES provenance(id),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            note TEXT
        )
    """)
    
    # Annotation
    cur.execute("""
        CREATE TABLE annotation (
            id BIGSERIAL PRIMARY KEY,
            record_id BIGINT NOT NULL REFERENCES annotation_record(id) ON DELETE CASCADE,
            term_id INTEGER NOT NULL REFERENCES cv_term(id),
            value_term_id INTEGER REFERENCES cv_term(id),
            value_text TEXT,
            value_num DOUBLE PRECISION,
            units VARCHAR(100),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # Entity Annotation Record (junction table)
    cur.execute("""
        CREATE TABLE entity_annotation_record (
            entity_id BIGINT NOT NULL REFERENCES entity(id) ON DELETE CASCADE,
            annotation_record_id BIGINT NOT NULL REFERENCES annotation_record(id) ON DELETE CASCADE,
            role VARCHAR(100),
            PRIMARY KEY (entity_id, annotation_record_id)
        )
    """)
    
    # Add foreign key constraints for interaction_evidence annotations
    cur.execute("""
        ALTER TABLE interaction_evidence
        ADD CONSTRAINT fk_annotation_record
        FOREIGN KEY (annotation_record_id) REFERENCES annotation_record(id)
    """)
    
    cur.execute("""
        ALTER TABLE interaction_evidence
        ADD CONSTRAINT fk_entity_a_annotation_record
        FOREIGN KEY (entity_a_annotation_record_id) REFERENCES annotation_record(id)
    """)
    
    cur.execute("""
        ALTER TABLE interaction_evidence
        ADD CONSTRAINT fk_entity_b_annotation_record
        FOREIGN KEY (entity_b_annotation_record_id) REFERENCES annotation_record(id)
    """)
    
    cur.execute("""
        ALTER TABLE membership
        ADD CONSTRAINT fk_membership_annotation_record
        FOREIGN KEY (annotation_record_id) REFERENCES annotation_record(id)
    """)


def create_indexes(cur):
    """Create indexes for performance optimization"""
    
    print("  Creating CV term indexes...")
    cur.execute("CREATE INDEX idx_cv_term_namespace ON cv_term(namespace_id)")
    cur.execute("CREATE INDEX idx_cv_term_name ON cv_term(name)")
    cur.execute("CREATE INDEX idx_cv_term_accession ON cv_term(accession)")
    
    print("  Creating entity indexes...")
    cur.execute("CREATE INDEX idx_entity_type ON entity(cv_term_id)")
    cur.execute("CREATE INDEX idx_entity_created ON entity(created_at)")
    
    print("  Creating entity identifier indexes...")
    cur.execute("CREATE INDEX idx_entity_identifier_entity ON entity_identifier(entity_id)")
    cur.execute("CREATE INDEX idx_entity_identifier_type ON entity_identifier(cv_term_id)")
    cur.execute("CREATE INDEX idx_entity_identifier_value ON entity_identifier(identifier)")
    cur.execute("CREATE INDEX idx_entity_identifier_lookup ON entity_identifier(cv_term_id, identifier)")
    
    print("  Creating interaction indexes...")
    cur.execute("CREATE INDEX idx_interaction_entity_a ON interaction(entity_a_id)")
    cur.execute("CREATE INDEX idx_interaction_entity_b ON interaction(entity_b_id)")
    cur.execute("CREATE INDEX idx_interaction_both ON interaction(entity_a_id, entity_b_id)")
    
    print("  Creating interaction evidence indexes...")
    cur.execute("CREATE INDEX idx_interaction_evidence_interaction ON interaction_evidence(interaction_id)")
    cur.execute("CREATE INDEX idx_interaction_evidence_type ON interaction_evidence(type_id)")
    cur.execute("CREATE INDEX idx_interaction_evidence_provenance ON interaction_evidence(provenance_id)")
    
    print("  Creating provenance indexes...")
    cur.execute("CREATE INDEX idx_provenance_source ON provenance(source_id)")
    cur.execute("CREATE INDEX idx_provenance_primary_source ON provenance(primary_source_id)")
    cur.execute("CREATE INDEX idx_provenance_reference ON provenance(reference_id)")
    
    print("  Creating reference indexes...")
    cur.execute("CREATE INDEX idx_reference_type_value ON reference(type_id, value)")
    
    print("  Creating annotation indexes...")
    cur.execute("CREATE INDEX idx_annotation_record ON annotation(record_id)")
    cur.execute("CREATE INDEX idx_annotation_term ON annotation(term_id)")
    cur.execute("CREATE INDEX idx_annotation_value_term ON annotation(value_term_id)")
    
    print("  Creating membership indexes...")
    cur.execute("CREATE INDEX idx_membership_parent ON membership(parent_id)")
    cur.execute("CREATE INDEX idx_membership_member ON membership(member_id)")


def create_views(cur):
    """Create utility views for common queries"""
    
    # Entity with primary identifier
    cur.execute("""
        CREATE VIEW entity_primary_identifier AS
        SELECT DISTINCT ON (e.id)
            e.id AS entity_id,
            e.cv_term_id AS entity_type_id,
            et.name AS entity_type,
            ei.identifier AS primary_identifier,
            eit.name AS identifier_type,
            e.created_at
        FROM entity e
        LEFT JOIN cv_term et ON e.cv_term_id = et.id
        LEFT JOIN entity_identifier ei ON e.id = ei.entity_id
        LEFT JOIN cv_term eit ON ei.cv_term_id = eit.id
        ORDER BY e.id, ei.created_at
    """)
    
    # Entity with all identifiers
    cur.execute("""
        CREATE VIEW entity_all_identifiers AS
        SELECT 
            e.id AS entity_id,
            et.name AS entity_type,
            eit.name AS identifier_type,
            ei.identifier,
            ei.created_at,
            s.name AS source
        FROM entity e
        JOIN entity_identifier ei ON e.id = ei.entity_id
        JOIN cv_term et ON e.cv_term_id = et.id
        JOIN cv_term eit ON ei.cv_term_id = eit.id
        JOIN provenance p ON ei.provenance_id = p.id
        JOIN source s ON p.source_id = s.id
    """)
    
    # Protein entities with details
    cur.execute("""
        CREATE VIEW protein_view AS
        SELECT 
            e.id AS entity_id,
            p.name,
            p.class,
            p.sequence,
            epi.primary_identifier,
            epi.identifier_type
        FROM entity e
        JOIN protein p ON e.id = p.entity_id
        LEFT JOIN entity_primary_identifier epi ON e.id = epi.entity_id
    """)
    
    # Compound entities with details
    cur.execute("""
        CREATE VIEW compound_view AS
        SELECT 
            e.id AS entity_id,
            c.formula,
            c.molecular_weight,
            c.exact_mass,
            c.tpsa,
            c.logp,
            c.hbd,
            c.hba,
            epi.primary_identifier,
            epi.identifier_type
        FROM entity e
        JOIN compound c ON e.id = c.entity_id
        LEFT JOIN entity_primary_identifier epi ON e.id = epi.entity_id
    """)
    
    # Interactions with entity details
    cur.execute("""
        CREATE VIEW interaction_detail AS
        SELECT 
            i.id AS interaction_id,
            i.entity_a_id,
            ea.primary_identifier AS entity_a_identifier,
            ea.identifier_type AS entity_a_id_type,
            i.entity_b_id,
            eb.primary_identifier AS entity_b_identifier,
            eb.identifier_type AS entity_b_id_type,
            COUNT(DISTINCT ie.id) AS evidence_count
        FROM interaction i
        LEFT JOIN entity_primary_identifier ea ON i.entity_a_id = ea.entity_id
        LEFT JOIN entity_primary_identifier eb ON i.entity_b_id = eb.entity_id
        LEFT JOIN interaction_evidence ie ON i.id = ie.interaction_id
        GROUP BY i.id, i.entity_a_id, ea.primary_identifier, ea.identifier_type,
                 i.entity_b_id, eb.primary_identifier, eb.identifier_type
    """)
    
    # Interaction evidence with full details
    cur.execute("""
        CREATE VIEW interaction_evidence_detail AS
        SELECT 
            ie.id AS evidence_id,
            ie.interaction_id,
            iet.name AS interaction_type,
            ie.is_directed,
            dt.name AS direction,
            st.name AS sign,
            ie.sentence,
            s.name AS source,
            ps.name AS primary_source,
            r.value AS reference_value,
            rt.name AS reference_type,
            ie.created_at
        FROM interaction_evidence ie
        JOIN cv_term iet ON ie.type_id = iet.id
        LEFT JOIN cv_term dt ON ie.direction_id = dt.id
        LEFT JOIN cv_term st ON ie.sign_id = st.id
        JOIN provenance p ON ie.provenance_id = p.id
        JOIN source s ON p.source_id = s.id
        LEFT JOIN source ps ON p.primary_source_id = ps.id
        LEFT JOIN reference r ON p.reference_id = r.id
        LEFT JOIN cv_term rt ON r.type_id = rt.id
    """)
    
    # Entity annotations view
    cur.execute("""
        CREATE VIEW entity_annotations AS
        SELECT 
            ear.entity_id,
            ear.role,
            a.id AS annotation_id,
            t.name AS annotation_term,
            tn.name AS term_namespace,
            a.value_text,
            a.value_num,
            a.units,
            vt.name AS value_term,
            s.name AS source
        FROM entity_annotation_record ear
        JOIN annotation_record ar ON ear.annotation_record_id = ar.id
        JOIN annotation a ON ar.id = a.record_id
        JOIN cv_term t ON a.term_id = t.id
        JOIN cv_namespace tn ON t.namespace_id = tn.id
        LEFT JOIN cv_term vt ON a.value_term_id = vt.id
        JOIN provenance p ON ar.provenance_id = p.id
        JOIN source s ON p.source_id = s.id
    """)


def create_functions(cur):
    """Create helper PostgreSQL functions"""
    
    # Function to get entity by identifier
    cur.execute("""
        CREATE OR REPLACE FUNCTION get_entity_by_identifier(
            id_type VARCHAR,
            id_value VARCHAR
        ) RETURNS BIGINT AS $$
        DECLARE
            entity_id BIGINT;
        BEGIN
            SELECT e.id INTO entity_id
            FROM entity e
            JOIN entity_identifier ei ON e.id = ei.entity_id
            JOIN cv_term ct ON ei.cv_term_id = ct.id
            WHERE ct.name = id_type AND ei.identifier = id_value
            LIMIT 1;
            
            RETURN entity_id;
        END;
        $$ LANGUAGE plpgsql;
    """)
    
    # Function to check if identifier exists
    cur.execute("""
        CREATE OR REPLACE FUNCTION identifier_exists(
            id_type VARCHAR,
            id_value VARCHAR
        ) RETURNS BOOLEAN AS $$
        BEGIN
            RETURN EXISTS (
                SELECT 1
                FROM entity_identifier ei
                JOIN cv_term ct ON ei.cv_term_id = ct.id
                WHERE ct.name = id_type AND ei.identifier = id_value
            );
        END;
        $$ LANGUAGE plpgsql;
    """)


def print_database_stats(host, port, database, user, password):
    """Print statistics about the database"""
    conn = psycopg2.connect(
        host=host, port=port, database=database, user=user, password=password
    )
    cur = conn.cursor()
    
    print("\n" + "="*60)
    print("DATABASE STATISTICS")
    print("="*60)
    
    tables = [
        'cv_namespace', 'cv_term', 'source', 'reference', 'provenance',
        'entity', 'entity_identifier', 'protein', 'compound', 'reaction',
        'interaction', 'interaction_evidence', 'annotation_record', 'annotation'
    ]
    
    for table in tables:
        try:
            cur.execute(f"SELECT COUNT(*) FROM {table}")
            count = cur.fetchone()[0]
            print(f"{table:30} {count:>10} rows")
        except Exception as e:
            print(f"{table:30} {'ERROR':>10}")
    
    print("="*60 + "\n")
    cur.close()
    conn.close()


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description='Initialize PostgreSQL database')
    parser.add_argument('--host', default='localhost', help='PostgreSQL host')
    parser.add_argument('--port', type=int, default=5432, help='PostgreSQL port')
    parser.add_argument('--database', default='biodata', help='Database name')
    parser.add_argument('--user', default='postgres', help='Database user')
    parser.add_argument('--password', default='postgres', help='Database password')
    parser.add_argument('--drop', action='store_true', help='Drop existing tables')
    
    args = parser.parse_args()
    
    initialize_database(
        host=args.host,
        port=args.port,
        database=args.database,
        user=args.user,
        password=args.password,
        drop_existing=args.drop
    )
