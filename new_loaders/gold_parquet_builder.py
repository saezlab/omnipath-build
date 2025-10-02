#!/usr/bin/env python3
"""Build relational gold Parquet files from silver data using DuckDB."""

import json
import logging
from pathlib import Path
from typing import Optional
import duckdb

__all__ = [
    'GoldParquetBuilder',
]

logger = logging.getLogger(__name__)


class GoldParquetBuilder:
    """Builds relational gold Parquet files from silver data using DuckDB.

    This class creates a full relational structure in DuckDB that mirrors the
    PostgreSQL gold schema, then exports each table to a separate Parquet file.
    """

    TABLES: tuple[str, ...] = (
        'cv_namespace',
        'cv_term',
        'source',
        'reference',
        'provenance',
        'entity',
        'entity_identifier',
        'compound',
        'protein',
        'reaction_detail',
        'interaction',
        'membership',
        'annotation_record',
        'annotation',
        'entity_annotation_record',
        'interaction_evidence',
    )

    SEQUENCE_TABLE_COLUMN_MAP: dict[str, tuple[str, str]] = {
        'seq_cv_namespace': ('cv_namespace', 'id'),
        'seq_cv_term': ('cv_term', 'id'),
        'seq_source': ('source', 'id'),
        'seq_reference': ('reference', 'id'),
        'seq_provenance': ('provenance', 'id'),
        'seq_entity': ('entity', 'id'),
        'seq_entity_identifier': ('entity_identifier', 'id'),
        'seq_interaction': ('interaction', 'id'),
        'seq_annotation_record': ('annotation_record', 'id'),
        'seq_annotation': ('annotation', 'id'),
        'seq_interaction_evidence': ('interaction_evidence', 'id'),
    }

    def __init__(self, source_name: str, output_dir: Path):
        """Initialize the builder with source name and output directory.

        Args:
            source_name: Name of the data source (e.g., 'hmdb', 'psimi')
            output_dir: Directory where Parquet files will be written
        """
        self.source_name = source_name
        self.output_dir = output_dir
        self.conn = duckdb.connect(':memory:')

        # Create gold schema tables in DuckDB
        self._create_gold_schema()
        self._load_existing_data()

        logger.info(f"GoldParquetBuilder initialized for source: {source_name}")

    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit - close DuckDB connection."""
        if self.conn:
            self.conn.close()

    def _create_gold_schema(self) -> None:
        """Create gold tables in DuckDB matching PostgreSQL schema.

        Note: DuckDB doesn't support all PostgreSQL features (like SERIAL),
        so we use explicit sequences and manually generate IDs.
        """
        logger.info("Creating gold schema in DuckDB...")

        # Create sequences for ID generation
        self.conn.execute("CREATE SEQUENCE seq_cv_namespace START 1")
        self.conn.execute("CREATE SEQUENCE seq_cv_term START 1")
        self.conn.execute("CREATE SEQUENCE seq_source START 1")
        self.conn.execute("CREATE SEQUENCE seq_reference START 1")
        self.conn.execute("CREATE SEQUENCE seq_provenance START 1")
        self.conn.execute("CREATE SEQUENCE seq_entity START 1")
        self.conn.execute("CREATE SEQUENCE seq_entity_identifier START 1")
        self.conn.execute("CREATE SEQUENCE seq_interaction START 1")
        self.conn.execute("CREATE SEQUENCE seq_annotation_record START 1")
        self.conn.execute("CREATE SEQUENCE seq_annotation START 1")
        self.conn.execute("CREATE SEQUENCE seq_interaction_evidence START 1")

        # Create tables (without FK constraints for simplicity, we enforce at insert time)

        # 1. Controlled Vocabulary Tables
        self.conn.execute("""
            CREATE TABLE cv_namespace (
                id INTEGER PRIMARY KEY,
                name VARCHAR(255) NOT NULL UNIQUE
            )
        """)

        self.conn.execute("""
            CREATE TABLE cv_term (
                id INTEGER PRIMARY KEY,
                namespace_id INTEGER NOT NULL,
                accession VARCHAR(100),
                name VARCHAR(255) NOT NULL,
                description TEXT,
                is_obsolete BOOLEAN DEFAULT FALSE,
                replaces INTEGER,
                replaced_by INTEGER
            )
        """)

        # 2. Source & Provenance Tables
        self.conn.execute("""
            CREATE TABLE source (
                id INTEGER PRIMARY KEY,
                name VARCHAR(255) NOT NULL UNIQUE,
                url VARCHAR(500),
                description TEXT,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)

        self.conn.execute("""
            CREATE TABLE reference (
                id BIGINT PRIMARY KEY,
                type_id INTEGER,
                value TEXT NOT NULL UNIQUE,
                citation TEXT,
                year INTEGER,
                journal TEXT,
                title TEXT
            )
        """)

        self.conn.execute("""
            CREATE TABLE provenance (
                id BIGINT PRIMARY KEY,
                source_id INTEGER NOT NULL,
                primary_source_id INTEGER,
                reference_id BIGINT,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)

        # 3. Entity Tables (Core)
        self.conn.execute("""
            CREATE TABLE entity (
                id BIGINT PRIMARY KEY,
                cv_term_id INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)

        self.conn.execute("""
            CREATE TABLE entity_identifier (
                id BIGINT PRIMARY KEY,
                entity_id BIGINT NOT NULL,
                cv_term_id INTEGER NOT NULL,
                identifier TEXT NOT NULL,
                provenance_id BIGINT NOT NULL,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)

        # Type-specific tables
        self.conn.execute("""
            CREATE TABLE compound (
                entity_id BIGINT PRIMARY KEY,
                formula VARCHAR(255),
                molecular_weight DOUBLE,
                exact_mass DOUBLE,
                tpsa DOUBLE,
                logp DOUBLE,
                hbd INTEGER,
                hba INTEGER,
                rotatable_bonds INTEGER,
                aromatic_rings INTEGER,
                heavy_atoms INTEGER
            )
        """)

        self.conn.execute("""
            CREATE TABLE protein (
                entity_id BIGINT PRIMARY KEY,
                name VARCHAR(500),
                class VARCHAR(255),
                sequence TEXT
            )
        """)

        self.conn.execute("""
            CREATE TABLE reaction_detail (
                entity_id BIGINT PRIMARY KEY,
                equation TEXT,
                ec_number VARCHAR(100),
                reversible BOOLEAN
            )
        """)

        # 4. Interaction Tables
        self.conn.execute("""
            CREATE TABLE interaction (
                id BIGINT PRIMARY KEY,
                entity_a_id BIGINT NOT NULL,
                entity_b_id BIGINT NOT NULL,
                cv_term_id INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)

        self.conn.execute("""
            CREATE TABLE membership (
                complex_entity_id BIGINT NOT NULL,
                member_entity_id BIGINT NOT NULL,
                stoichiometry INTEGER,
                PRIMARY KEY (complex_entity_id, member_entity_id)
            )
        """)

        self.conn.execute("""
            CREATE TABLE interaction_evidence (
                id BIGINT PRIMARY KEY,
                interaction_id BIGINT NOT NULL,
                cv_term_id INTEGER NOT NULL,
                provenance_id BIGINT NOT NULL,
                confidence_score DOUBLE,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)

        # 5. Annotation Tables
        self.conn.execute("""
            CREATE TABLE annotation_record (
                id BIGINT PRIMARY KEY,
                provenance_id BIGINT NOT NULL,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)

        self.conn.execute("""
            CREATE TABLE annotation (
                id BIGINT PRIMARY KEY,
                annotation_record_id BIGINT NOT NULL,
                cv_term_id INTEGER NOT NULL,
                term_value INTEGER,
                text_value TEXT,
                numeric_value DOUBLE
            )
        """)

        self.conn.execute("""
            CREATE TABLE entity_annotation_record (
                entity_id BIGINT NOT NULL,
                annotation_record_id BIGINT NOT NULL,
                PRIMARY KEY (entity_id, annotation_record_id)
            )
        """)

        logger.info("✓ Gold schema created successfully")

    def _load_existing_data(self) -> None:
        """Load existing gold Parquet data so tables are shared across sources."""
        if not self.output_dir.exists():
            return

        for table in self.TABLES:
            parquet_path = self.output_dir / f"{table}.parquet"
            if not parquet_path.exists():
                continue

            logger.info(f"Loading existing data for {table} from {parquet_path}")
            self.conn.execute(
                f"INSERT INTO {table} SELECT * FROM read_parquet(?)",
                [str(parquet_path)]
            )

        self._reset_sequences_from_data()

    def _reset_sequences_from_data(self) -> None:
        """Reset DuckDB sequences to avoid ID collisions after loading data."""
        for sequence_name, (table_name, column_name) in self.SEQUENCE_TABLE_COLUMN_MAP.items():
            max_id = self.conn.execute(
                f"SELECT COALESCE(MAX({column_name}), 0) FROM {table_name}"
            ).fetchone()[0]

            # DuckDB sequences restart at the provided value, so add 1 for the next id
            next_value = max_id + 1
            self.conn.execute(
                f"ALTER SEQUENCE {sequence_name} RESTART WITH {next_value}"
            )

    # ========================================================================
    # Helper methods for building data
    # ========================================================================

    def _ensure_namespace(self, namespace_name: str) -> int:
        """Get or create namespace, return namespace_id."""
        result = self.conn.execute(
            "SELECT id FROM cv_namespace WHERE name = ?",
            [namespace_name]
        ).fetchone()

        if result:
            return result[0]
        else:
            namespace_id = self.conn.execute("SELECT nextval('seq_cv_namespace')").fetchone()[0]
            self.conn.execute(
                "INSERT INTO cv_namespace (id, name) VALUES (?, ?)",
                [namespace_id, namespace_name]
            )
            logger.debug(f"Created namespace: {namespace_name} (id={namespace_id})")
            return namespace_id

    def _ensure_source(self, source_name: str, url: str = None, description: str = None) -> int:
        """Get or create source, return source_id."""
        result = self.conn.execute(
            "SELECT id FROM source WHERE name = ?",
            [source_name]
        ).fetchone()

        if result:
            return result[0]
        else:
            source_id = self.conn.execute("SELECT nextval('seq_source')").fetchone()[0]
            self.conn.execute(
                "INSERT INTO source (id, name, url, description, created_at) VALUES (?, ?, ?, ?, NOW())",
                [source_id, source_name, url, description]
            )
            logger.debug(f"Created source: {source_name} (id={source_id})")
            return source_id

    def _ensure_provenance(self, source_id: int, reference_id: int = None,
                          primary_source_id: int = None) -> int:
        """Get or create provenance, return provenance_id."""
        # Check if exists
        result = self.conn.execute(
            """SELECT id FROM provenance
               WHERE source_id = ?
               AND (reference_id IS NULL AND ? IS NULL OR reference_id = ?)
               AND (primary_source_id IS NULL AND ? IS NULL OR primary_source_id = ?)""",
            [source_id, reference_id, reference_id, primary_source_id, primary_source_id]
        ).fetchone()

        if result:
            return result[0]
        else:
            provenance_id = self.conn.execute("SELECT nextval('seq_provenance')").fetchone()[0]
            self.conn.execute(
                """INSERT INTO provenance (id, source_id, primary_source_id, reference_id, created_at)
                   VALUES (?, ?, ?, ?, NOW())""",
                [provenance_id, source_id, primary_source_id, reference_id]
            )
            logger.debug(f"Created provenance (id={provenance_id}, source_id={source_id})")
            return provenance_id

    def _get_or_create_cv_term(self, namespace_name: str, term_name: str,
                               accession: str = None, description: str = None) -> int:
        """Get or create CV term, return cv_term_id."""
        namespace_id = self._ensure_namespace(namespace_name)

        result = self.conn.execute(
            "SELECT id FROM cv_term WHERE namespace_id = ? AND name = ?",
            [namespace_id, term_name]
        ).fetchone()

        if result:
            return result[0]
        else:
            cv_term_id = self.conn.execute("SELECT nextval('seq_cv_term')").fetchone()[0]
            self.conn.execute("""
                INSERT INTO cv_term (id, namespace_id, accession, name, description, is_obsolete)
                VALUES (?, ?, ?, ?, ?, FALSE)
            """, [cv_term_id, namespace_id, accession, term_name, description])
            logger.debug(f"Created CV term: {namespace_name}:{term_name} (id={cv_term_id})")
            return cv_term_id

    def _find_entity_by_identifier(self, identifier: str) -> Optional[int]:
        """Find entity_id by identifier (canonical lookup)."""
        result = self.conn.execute("""
            SELECT entity_id
            FROM entity_identifier
            WHERE identifier = ?
            LIMIT 1
        """, [identifier]).fetchone()
        return result[0] if result else None

    def _create_entity(self, cv_term_id: int) -> int:
        """Create entity and return entity_id."""
        entity_id = self.conn.execute("SELECT nextval('seq_entity')").fetchone()[0]
        self.conn.execute("""
            INSERT INTO entity (id, cv_term_id, created_at)
            VALUES (?, ?, NOW())
        """, [entity_id, cv_term_id])
        logger.debug(f"Created entity (id={entity_id}, cv_term_id={cv_term_id})")
        return entity_id

    def _add_identifier(self, entity_id: int, cv_term_id: int,
                       identifier: str, provenance_id: int) -> None:
        """Add identifier to entity (skip if exists)."""
        # Check if exists (to avoid duplicates)
        result = self.conn.execute("""
            SELECT 1 FROM entity_identifier
            WHERE entity_id = ? AND identifier = ? AND provenance_id = ?
        """, [entity_id, identifier, provenance_id]).fetchone()

        if result is None:
            identifier_id = self.conn.execute("SELECT nextval('seq_entity_identifier')").fetchone()[0]
            self.conn.execute("""
                INSERT INTO entity_identifier
                (id, entity_id, cv_term_id, identifier, provenance_id, created_at)
                VALUES (?, ?, ?, ?, ?, NOW())
            """, [identifier_id, entity_id, cv_term_id, identifier, provenance_id])
            logger.debug(f"Added identifier: {identifier} to entity {entity_id}")

    def _upsert_compound(self, entity_id: int, row: dict) -> None:
        """Insert or update compound-specific data."""
        # Check if exists
        result = self.conn.execute("SELECT 1 FROM compound WHERE entity_id = ?", [entity_id]).fetchone()

        if result is None:
            # Insert
            self.conn.execute("""
                INSERT INTO compound (
                    entity_id, formula, molecular_weight, exact_mass,
                    tpsa, logp, hbd, hba, rotatable_bonds, aromatic_rings, heavy_atoms
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, [
                entity_id,
                row.get('compound_formula'),
                row.get('molecular_weight'),
                row.get('exact_mass'),
                row.get('tpsa'),
                row.get('logp'),
                row.get('hbd'),
                row.get('hba'),
                row.get('rotatable_bonds'),
                row.get('aromatic_rings'),
                row.get('heavy_atoms')
            ])
            logger.debug(f"Inserted compound data for entity {entity_id}")
        else:
            # Update with COALESCE logic (keep existing if new is NULL)
            self.conn.execute("""
                UPDATE compound SET
                    formula = COALESCE(?, formula),
                    molecular_weight = COALESCE(?, molecular_weight),
                    exact_mass = COALESCE(?, exact_mass),
                    tpsa = COALESCE(?, tpsa),
                    logp = COALESCE(?, logp),
                    hbd = COALESCE(?, hbd),
                    hba = COALESCE(?, hba),
                    rotatable_bonds = COALESCE(?, rotatable_bonds),
                    aromatic_rings = COALESCE(?, aromatic_rings),
                    heavy_atoms = COALESCE(?, heavy_atoms)
                WHERE entity_id = ?
            """, [
                row.get('compound_formula'),
                row.get('molecular_weight'),
                row.get('exact_mass'),
                row.get('tpsa'),
                row.get('logp'),
                row.get('hbd'),
                row.get('hba'),
                row.get('rotatable_bonds'),
                row.get('aromatic_rings'),
                row.get('heavy_atoms'),
                entity_id
            ])
            logger.debug(f"Updated compound data for entity {entity_id}")

    # ========================================================================
    # Main methods
    # ========================================================================

    def build_cv_terms(self, silver_parquet_path: Path) -> dict:
        """Build CV terms from silver Parquet into DuckDB gold tables.

        Args:
            silver_parquet_path: Path to silver CV terms Parquet file

        Returns:
            Dict with stats: {'created': N, 'total': M}
        """
        logger.info(f"Building CV terms from {silver_parquet_path.name}")

        # Step 1: Read silver data
        df = self.conn.execute(f"""
            SELECT
                namespace,
                term_accession,
                term_name,
                term_definition
            FROM '{silver_parquet_path}'
        """).fetchdf()

        logger.info(f"Read {len(df)} CV terms from silver")

        # Step 2: Process all terms
        created = 0

        for _, row in df.iterrows():
            # This will create both namespace and term if needed
            cv_term_id = self._get_or_create_cv_term(
                namespace_name=row['namespace'],
                term_name=row['term_name'],
                accession=row['term_accession'],
                description=row['term_definition']
            )
            created += 1

        logger.info(f"✓ CV terms: {created} processed")
        return {'created': created, 'total': len(df)}

    def build_entities(self, silver_parquet_path: Path) -> dict:
        """Build entities from silver Parquet into DuckDB gold tables.

        Args:
            silver_parquet_path: Path to silver entities Parquet file

        Returns:
            Dict with stats
        """
        logger.info(f"Building entities from {silver_parquet_path.name}")

        # Step 1: Determine available columns
        result = self.conn.execute(f"SELECT * FROM '{silver_parquet_path}' LIMIT 0")
        available_columns = {desc[0] for desc in result.description}

        # Track counts so we can report how much this source contributes
        entity_count_before = self.conn.execute("SELECT COUNT(*) FROM entity").fetchone()[0]
        identifier_count_before = self.conn.execute("SELECT COUNT(*) FROM entity_identifier").fetchone()[0]
        compound_count_before = self.conn.execute("SELECT COUNT(*) FROM compound").fetchone()[0]

        # Core columns (required)
        core_columns = ['entity_type', 'identifier', 'identifier_type', 'additional_identifiers']

        # Optional compound columns
        optional_columns = [
            'compound_formula', 'compound_smiles', 'compound_inchi',
            'molecular_weight', 'exact_mass', 'tpsa', 'logp',
            'hbd', 'hba', 'rotatable_bonds', 'aromatic_rings', 'heavy_atoms',
            'source_database'
        ]

        # Build SELECT clause
        select_parts = []
        for col in core_columns:
            if col in available_columns:
                select_parts.append(col)
            else:
                select_parts.append(f"NULL AS {col}")

        for col in optional_columns:
            if col in available_columns:
                select_parts.append(col)
            else:
                select_parts.append(f"NULL AS {col}")

        # Add canonical_id calculation
        if 'compound_inchi' in available_columns:
            canonical_expr = """
                CASE
                    WHEN compound_inchi IS NOT NULL AND TRIM(compound_inchi) != ''
                        THEN compound_inchi
                    ELSE identifier
                END as canonical_id
            """
        else:
            canonical_expr = "identifier as canonical_id"

        select_parts.append(canonical_expr)

        select_clause = ',\n                '.join(select_parts)

        # Step 2: Create temp view of silver data (stay in DuckDB, don't fetch to pandas!)
        where_clause = "WHERE is_valid = TRUE" if 'is_valid' in available_columns else ""

        temp_table = f"temp_silver_{self.source_name}"
        self.conn.execute(f"""
            CREATE OR REPLACE TEMP VIEW {temp_table} AS
            SELECT
                {select_clause}
            FROM '{silver_parquet_path}'
            {where_clause}
        """)

        total_count = self.conn.execute(f"SELECT COUNT(*) FROM {temp_table}").fetchone()[0]
        logger.info(f"Processing {total_count:,} entities from silver")

        # Step 3: Setup source and provenance
        source_id = self._ensure_source(self.source_name)
        provenance_id = self._ensure_provenance(source_id)

        # Step 4: Get CV terms we'll need
        compound_cv_term_id = self._get_or_create_cv_term('entity_type', 'compound')

        # Step 5: Deduplicate by canonical_id (keep first occurrence) - pure SQL!
        logger.info("  Deduplicating entities by canonical identifier...")
        self.conn.execute(f"""
            CREATE TEMP TABLE dedup_candidates AS
            SELECT DISTINCT ON (canonical_id) *
            FROM {temp_table}
            ORDER BY canonical_id
        """)

        candidate_count = self.conn.execute(
            "SELECT COUNT(*) FROM dedup_candidates"
        ).fetchone()[0]

        self.conn.execute(f"""
            CREATE TEMP TABLE unique_entities AS
            SELECT
                nextval('seq_entity') AS entity_id,
                candidate.*
            FROM dedup_candidates AS candidate
            WHERE candidate.canonical_id IS NULL
                OR NOT EXISTS (
                    SELECT 1
                    FROM entity_identifier existing
                    WHERE existing.identifier = candidate.canonical_id
                )
        """)

        unique_count = self.conn.execute("SELECT COUNT(*) FROM unique_entities").fetchone()[0]
        skipped_existing = candidate_count - unique_count
        logger.info(
            "  %s new unique entities (from %s total; skipped %s already in gold)",
            f"{unique_count:,}",
            f"{total_count:,}",
            f"{max(skipped_existing, 0):,}"
        )

        # Bulk insert entities
        logger.info("  Creating entities...")
        self.conn.execute(f"""
            INSERT INTO entity (id, cv_term_id, created_at)
            SELECT
                entity_id,
                {compound_cv_term_id} as cv_term_id,
                NOW() as created_at
            FROM unique_entities
        """)

        entities_after = self.conn.execute("SELECT COUNT(*) FROM entity").fetchone()[0]
        entities_created = entities_after - entity_count_before
        logger.info(f"  ✓ Created {entities_created:,} entities")

        # Map canonical identifiers directly to their generated entity IDs
        self.conn.execute("""
            CREATE TEMP TABLE canonical_mapping AS
            SELECT *
            FROM unique_entities
        """)

        # Pre-create all identifier type CV terms
        logger.info("  Creating identifier type CV terms...")
        id_types = self.conn.execute("""
            SELECT DISTINCT identifier_type FROM unique_entities
            WHERE identifier_type IS NOT NULL
            UNION
            SELECT 'inchi' WHERE EXISTS (
                SELECT 1 FROM unique_entities
                WHERE compound_inchi IS NOT NULL AND TRIM(compound_inchi) != ''
            )
        """).fetchall()

        identifier_types = {}
        for (id_type,) in id_types:
            if id_type:
                identifier_types[id_type] = self._get_or_create_cv_term('identifier_type', id_type)

        # Determine default identifier type (for non-InChI cases)
        default_id_type = self.conn.execute("""
            SELECT identifier_type FROM unique_entities
            WHERE identifier_type IS NOT NULL
            LIMIT 1
        """).fetchone()
        default_cv_term_id = identifier_types.get(default_id_type[0]) if default_id_type else None

        # Bulk insert canonical identifiers - pre-calculate IDs to avoid nextval() overhead
        logger.info("  Creating canonical identifiers...")
        inchi_cv_term_id = identifier_types.get('inchi')
        next_identifier_id = self.conn.execute("SELECT nextval('seq_entity_identifier')").fetchone()[0]

        # Build cv_term_id expression based on available types
        if inchi_cv_term_id and default_cv_term_id:
            cv_term_expr = f"""CASE
                    WHEN compound_inchi IS NOT NULL AND TRIM(compound_inchi) != ''
                    THEN {inchi_cv_term_id}
                    ELSE {default_cv_term_id}
                END"""
        elif inchi_cv_term_id:
            cv_term_expr = str(inchi_cv_term_id)
        elif default_cv_term_id:
            cv_term_expr = str(default_cv_term_id)
        else:
            cv_term_expr = "NULL"

        self.conn.execute(f"""
            INSERT INTO entity_identifier (id, entity_id, cv_term_id, identifier, provenance_id, created_at)
            SELECT
                {next_identifier_id} + ROW_NUMBER() OVER () - 1 as id,
                entity_id,
                {cv_term_expr} as cv_term_id,
                canonical_id as identifier,
                {provenance_id} as provenance_id,
                NOW() as created_at
            FROM canonical_mapping
            WHERE canonical_id IS NOT NULL
        """)

        identifiers_after = self.conn.execute(
            "SELECT COUNT(*) FROM entity_identifier"
        ).fetchone()[0]
        canonical_id_count = identifiers_after - identifier_count_before
        logger.info(f"  ✓ Created {canonical_id_count:,} canonical identifiers")

        # Bulk insert compound data - NO JOIN, data is already in canonical_mapping!
        logger.info("  Creating compound data...")
        self.conn.execute(f"""
            INSERT INTO compound (
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
            FROM canonical_mapping
        """)

        compounds_after = self.conn.execute("SELECT COUNT(*) FROM compound").fetchone()[0]
        compound_count = compounds_after - compound_count_before
        logger.info(f"  ✓ Created {compound_count:,} compound records")

        # TODO: Handle additional_identifiers (JSON parsing is complex in SQL, skip for now or handle in separate pass)
        logger.info("  Note: additional_identifiers not yet implemented in bulk mode")

        # Cleanup temp tables and views
        self.conn.execute(f"DROP VIEW IF EXISTS {temp_table}")
        self.conn.execute("DROP TABLE IF EXISTS dedup_candidates")
        self.conn.execute("DROP TABLE IF EXISTS unique_entities")
        self.conn.execute("DROP TABLE IF EXISTS canonical_mapping")

        logger.info(f"✓ Bulk processing complete: {entities_created:,} entities created")

        return {
            'entities_created': entities_created,
            'identifiers_added': canonical_id_count,
            'total': total_count
        }

    def export_all_tables(self) -> dict[str, Path]:
        """Export all gold tables to Parquet files.

        Returns:
            Dict mapping table_name -> parquet_path
        """
        logger.info(f"Exporting gold tables to {self.output_dir}")
        self.output_dir.mkdir(parents=True, exist_ok=True)
        exported = {}

        for table in self.TABLES:
            # Check if table has data
            count = self.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            if count == 0:
                logger.debug(f"Skipping empty table: {table}")
                continue

            parquet_path = self.output_dir / f"{table}.parquet"
            self.conn.execute(f"COPY {table} TO '{parquet_path}' (FORMAT PARQUET)")
            exported[table] = parquet_path
            logger.info(f"✓ Exported {count:,} rows to {parquet_path.name}")

        return exported
