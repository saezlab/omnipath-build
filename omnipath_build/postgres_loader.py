#!/usr/bin/env python3
"""Load OmniPath gold tables from parquet files to PostgreSQL using DuckDB."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import duckdb
import psycopg2

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# Tables to load (excluding entity_identifier_record_to_global.parquet)
TABLES_TO_LOAD = [
    'compound',
    'entity_evidence',
    'entity_identifiers',
    'evidence_reference',
    'interaction_evidence',
    'membership_evidence',
    'references',
    'source',
    # Aggregate tables (renamed without _aggregate suffix)
    'entity_aggregate:entity',
    'interaction_aggregate:interaction',
    'membership_aggregate:membership',
    # Bridge tables
    'entity_to_evidence',
    'interaction_to_evidence',
    'membership_to_evidence',
]

# Tables with complex types that need JSON conversion
# Use the parquet file name as the key
TABLES_WITH_COMPLEX_TYPES = {
    'entity_evidence': ['annotations'],
    'interaction_evidence': ['interaction_annotations'],
    'entity_aggregate': ['annotation_union'],  # parquet file name
}

# Columns to exclude from loading
COLUMNS_TO_EXCLUDE = {}

# Columns that need special conversion for RDKit types
# Format: table_name -> {column_name: conversion_function}
RDKIT_CONVERSIONS = {
    'compound': {
        'molfile': 'mol_from_ctab',  # Convert molfile text to mol type
        'morgan_fp': 'bfp_from_binary_text',  # Convert binary to bfp type
    }
}

MAX_INDEXED_IDENTIFIER_OCTETS = 1000

# Primary keys to add after table creation
# Format: (table_name, primary_key_column)
# Use the PostgreSQL table name (not parquet file name)
PRIMARY_KEY_CONSTRAINTS = [
    ('source', 'id'),
    ('references', 'id'),
    ('entity_evidence', 'id'),
    ('membership_evidence', 'id'),
    ('interaction_evidence', 'id'),
    ('evidence_reference', 'id'),
    ('compound', 'id'),
    # Aggregate tables (using new names without _aggregate suffix)
    ('entity', 'entity_id'),
    ('interaction', 'interaction_id'),
    ('membership', 'membership_id'),
]

# Foreign key constraints to add after table creation
# Format: (table_name, column_name, referenced_table, referenced_column)
# Note: CV terms are now entities, so all type/role/namespace references point to entity table
# Use the PostgreSQL table name (not parquet file name)
FOREIGN_KEY_CONSTRAINTS = [
    # entity_identifiers table
    ('entity_identifiers', 'id_type_id', 'entity', 'entity_id'),
    ('entity_identifiers', 'entity_id', 'entity', 'entity_id'),

    # entity_evidence table
    ('entity_evidence', 'source_id', 'source', 'id'),
    ('entity_evidence', 'entity_type_id', 'entity', 'entity_id'),
    ('entity_evidence', 'entity_id', 'entity', 'entity_id'),

    # membership_evidence table
    ('membership_evidence', 'role_id', 'entity', 'entity_id'),
    ('membership_evidence', 'source_id', 'source', 'id'),
    ('membership_evidence', 'parent_entity_id', 'entity', 'entity_id'),
    ('membership_evidence', 'entity_id', 'entity', 'entity_id'),

    # interaction_evidence table
    ('interaction_evidence', 'entity_id_a', 'entity', 'entity_id'),
    ('interaction_evidence', 'entity_id_b', 'entity', 'entity_id'),
    ('interaction_evidence', 'interaction_type_id', 'entity', 'entity_id'),
    ('interaction_evidence', 'detection_method_id', 'entity', 'entity_id'),
    ('interaction_evidence', 'causal_mechanism_id', 'entity', 'entity_id'),
    ('interaction_evidence', 'causal_statement_id', 'entity', 'entity_id'),
    ('interaction_evidence', 'source_id', 'source', 'id'),

    # evidence_reference table
    ('evidence_reference', 'reference_id', 'references', 'id'),
    ('evidence_reference', 'entity_evidence_id', 'entity_evidence', 'id'),
    ('evidence_reference', 'interaction_evidence_id', 'interaction_evidence', 'id'),
    ('evidence_reference', 'membership_evidence_id', 'membership_evidence', 'id'),

    # compound table
    ('compound', 'entity_id', 'entity', 'entity_id'),

    # entity table (self-referencing for entity_type_id)
    ('entity', 'entity_type_id', 'entity', 'entity_id'),

    # interaction table
    ('interaction', 'a_id', 'entity', 'entity_id'),
    ('interaction', 'b_id', 'entity', 'entity_id'),

    # membership table (note: role_ids is a list, can't have FK on array column)
    ('membership', 'parent_entity_id', 'entity', 'entity_id'),
    ('membership', 'entity_id', 'entity', 'entity_id'),

    # entity_to_evidence bridge table
    ('entity_to_evidence', 'entity_id', 'entity', 'entity_id'),
    ('entity_to_evidence', 'entity_evidence_id', 'entity_evidence', 'id'),
    ('entity_to_evidence', 'source_id', 'source', 'id'),

    # interaction_to_evidence bridge table
    ('interaction_to_evidence', 'interaction_id', 'interaction', 'interaction_id'),
    ('interaction_to_evidence', 'interaction_evidence_id', 'interaction_evidence', 'id'),
    ('interaction_to_evidence', 'source_id', 'source', 'id'),

    # membership_to_evidence bridge table
    ('membership_to_evidence', 'membership_id', 'membership', 'membership_id'),
    ('membership_to_evidence', 'membership_evidence_id', 'membership_evidence', 'id'),
    ('membership_to_evidence', 'source_id', 'source', 'id'),
]

# Indexes to create for query performance
# Format: (table_name, index_name, columns, index_type, where_clause)
# where_clause can be used to create partial indexes
INDEXES = [
    # entity_identifiers - target generated column that excludes oversized identifiers
    (
        'entity_identifiers',
        'idx_entity_identifiers_type_value_small',
        '(id_type_id, id_value_small text_pattern_ops) INCLUDE (entity_id, id_value)',
        'btree',
        'id_value_small IS NOT NULL',
    ),
    (
        'entity_identifiers',
        'idx_entity_identifiers_value_small_pattern',
        '(id_value_small text_pattern_ops) INCLUDE (id_type_id, entity_id, id_value)',
        'btree',
        'id_value_small IS NOT NULL',
    ),
    ('entity_identifiers', 'idx_entity_identifiers_entity_id', '(entity_id)', 'btree', None),

    # interaction - for filtering by participants and retrieving top evidence rows
    (
        'interaction',
        'idx_interaction_a_id_inc',
        '(a_id) INCLUDE (interaction_id, b_id, evidence_count)',
        'btree',
        None,
    ),
    (
        'interaction',
        'idx_interaction_b_id_inc',
        '(b_id) INCLUDE (interaction_id, a_id, evidence_count)',
        'btree',
        None,
    ),
    ('interaction', 'idx_interaction_evidence_desc', '(evidence_count DESC, interaction_id)', 'btree', None),

    # interaction_evidence - for evidence queries
    ('interaction_evidence', 'idx_interaction_evidence_source_id', '(source_id)', 'btree', None),

    # membership - for membership lookups
    ('membership', 'idx_membership_entity_id', '(entity_id)', 'btree', None),
    ('membership', 'idx_membership_parent_entity_id', '(parent_entity_id)', 'btree', None),

    # Bridge tables - for joins and filtering
    ('entity_to_evidence', 'idx_entity_to_evidence_entity_source', '(entity_id, source_id)', 'btree', None),
    ('interaction_to_evidence', 'idx_interaction_to_evidence_int_source', '(interaction_id, source_id)', 'btree', None),
    ('membership_to_evidence', 'idx_membership_to_evidence_mem_source', '(membership_id, source_id)', 'btree', None),

    # Compound table - RDKit structure searches
    ('compound', 'idx_compound_mol_gist', '(mol)', 'gist', None),
    ('compound', 'idx_compound_morgan_fp_gist', '(morgan_fp)', 'gist', None),
]


def apply_rdkit_conversions(
    postgres_uri: str,
    schema: str = 'public',
) -> None:
    """
    Apply RDKit type conversions to compound table columns.

    Replaces:
    - molfile (text) -> mol (RDKit mol type) using mol_from_ctab()
    - morgan_fp (bytea) -> morgan_fp (RDKit bfp type) using bfp_from_binary_text()

    Args:
        postgres_uri: PostgreSQL connection string
        schema: Target schema in PostgreSQL
    """
    logger.info('Applying RDKit type conversions...')

    # Parse the postgres_uri to get connection parameters
    parsed = urlparse(postgres_uri)

    conn = psycopg2.connect(
        host=parsed.hostname,
        port=parsed.port or 5432,
        user=parsed.username,
        password=parsed.password,
        database=parsed.path.lstrip('/'),
    )

    try:
        cur = conn.cursor()

        # Create RDKit extension if it doesn't exist
        logger.info('  Ensuring RDKit extension is created...')
        cur.execute(f"CREATE EXTENSION IF NOT EXISTS rdkit SCHEMA {schema}")
        conn.commit()

        # Check if compound table exists and has columns to convert
        cur.execute(f"""
            SELECT column_name, data_type
            FROM information_schema.columns
            WHERE table_schema = '{schema}'
            AND table_name = 'compound'
            AND column_name IN ('molfile', 'morgan_fp', 'mol')
            ORDER BY column_name
        """)
        columns = {row[0]: row[1] for row in cur.fetchall()}

        if not columns:
            logger.info('  Compound table not found or no RDKit columns present')
            return

        # Check if already converted
        if 'mol' in columns and 'molfile' not in columns:
            logger.info('  RDKit conversions already applied')
            return

        # Convert molfile -> mol
        if 'molfile' in columns:
            logger.info('  Converting molfile -> mol using mol_from_ctab()')

            # Add new mol column
            cur.execute(f"ALTER TABLE {schema}.compound ADD COLUMN mol mol")

            # Populate mol column
            cur.execute(f"""
                UPDATE {schema}.compound
                SET mol = mol_from_ctab(molfile::cstring)
            """)

            # Drop old molfile column
            cur.execute(f"ALTER TABLE {schema}.compound DROP COLUMN molfile")
            conn.commit()
            logger.info('  ✓ Replaced molfile with mol type')

        # Convert morgan_fp bytea -> morgan_fp bfp
        if 'morgan_fp' in columns and columns['morgan_fp'] == 'bytea':
            logger.info('  Converting morgan_fp (bytea) -> morgan_fp (bfp) using bfp_from_binary_text()')

            # Rename old column
            cur.execute(f"ALTER TABLE {schema}.compound RENAME COLUMN morgan_fp TO morgan_fp_old")

            # Add new morgan_fp column with bfp type
            cur.execute(f"ALTER TABLE {schema}.compound ADD COLUMN morgan_fp bfp")

            # Populate new column
            cur.execute(f"""
                UPDATE {schema}.compound
                SET morgan_fp = bfp_from_binary_text(morgan_fp_old)
            """)

            # Drop old column
            cur.execute(f"ALTER TABLE {schema}.compound DROP COLUMN morgan_fp_old")
            conn.commit()
            logger.info('  ✓ Replaced morgan_fp with bfp type')

        # Ensure RDKit-specific indexes can be created later
        logger.info('  Ensuring rdkit extension is available for molecular indexes...')
        cur.execute("CREATE EXTENSION IF NOT EXISTS rdkit")
        conn.commit()

        logger.info('✓ RDKit type conversions completed')

    except Exception as exc:
        logger.error(f'Failed to apply RDKit conversions: {exc}')
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


def add_primary_keys(
    postgres_uri: str,
    schema: str = 'public',
) -> None:
    """
    Add primary key constraints to the PostgreSQL tables using native PostgreSQL connection.

    Args:
        postgres_uri: PostgreSQL connection string
        schema: Target schema in PostgreSQL
    """
    logger.info('Adding primary key constraints...')

    # Parse the postgres_uri to get connection parameters
    # Format: postgresql://user:pass@host:port/dbname
    parsed = urlparse(postgres_uri)

    conn = psycopg2.connect(
        host=parsed.hostname,
        port=parsed.port or 5432,
        user=parsed.username,
        password=parsed.password,
        database=parsed.path.lstrip('/'),
    )

    try:
        cur = conn.cursor()

        for table_name, pk_column in PRIMARY_KEY_CONSTRAINTS:
            constraint_name = f'pk_{table_name}'

            # Check if primary key already exists
            check_sql = f"""
                SELECT constraint_name
                FROM information_schema.table_constraints
                WHERE table_schema = '{schema}'
                AND table_name = '{table_name}'
                AND constraint_type = 'PRIMARY KEY'
            """
            cur.execute(check_sql)
            existing_pk = cur.fetchone()

            if existing_pk:
                logger.info(f'  Skipping PK: {table_name}.{pk_column} (already exists)')
                continue

            sql = f"""
                ALTER TABLE {schema}.{table_name}
                ADD CONSTRAINT {constraint_name}
                PRIMARY KEY ({pk_column})
            """
            try:
                logger.info(f'  Adding PK: {table_name}.{pk_column}')
                cur.execute(sql)
                conn.commit()
            except Exception as exc:
                logger.error(f'  Failed to add PK {constraint_name}: {exc}')
                conn.rollback()
                raise

        logger.info('✓ All primary key constraints added successfully')

    finally:
        cur.close()
        conn.close()


def add_foreign_keys(
    postgres_uri: str,
    schema: str = 'public',
) -> None:
    """
    Add foreign key constraints to the PostgreSQL tables using native PostgreSQL connection.

    Args:
        postgres_uri: PostgreSQL connection string
        schema: Target schema in PostgreSQL
    """
    logger.info('Adding foreign key constraints...')

    # Parse the postgres_uri to get connection parameters
    # Format: postgresql://user:pass@host:port/dbname
    parsed = urlparse(postgres_uri)

    conn = psycopg2.connect(
        host=parsed.hostname,
        port=parsed.port or 5432,
        user=parsed.username,
        password=parsed.password,
        database=parsed.path.lstrip('/'),
    )

    try:
        cur = conn.cursor()

        for table_name, column_name, ref_table, ref_column in FOREIGN_KEY_CONSTRAINTS:
            constraint_name = f'fk_{table_name}_{column_name}'

            # Check if foreign key already exists
            check_sql = f"""
                SELECT constraint_name
                FROM information_schema.table_constraints
                WHERE table_schema = '{schema}'
                AND table_name = '{table_name}'
                AND constraint_name = '{constraint_name}'
                AND constraint_type = 'FOREIGN KEY'
            """
            cur.execute(check_sql)
            existing_fk = cur.fetchone()

            if existing_fk:
                logger.info(f'  Skipping FK: {table_name}.{column_name} -> {ref_table}.{ref_column} (already exists)')
                continue

            sql = f"""
                ALTER TABLE {schema}.{table_name}
                ADD CONSTRAINT {constraint_name}
                FOREIGN KEY ({column_name})
                REFERENCES {schema}.{ref_table}({ref_column})
                ON DELETE RESTRICT
            """
            try:
                logger.info(f'  Adding FK: {table_name}.{column_name} -> {ref_table}.{ref_column}')
                cur.execute(sql)
                conn.commit()
            except Exception as exc:
                logger.error(f'  Failed to add FK {constraint_name}: {exc}')
                conn.rollback()
                raise

        logger.info('✓ All foreign key constraints added successfully')

    finally:
        cur.close()
        conn.close()


def ensure_entity_identifier_helpers(
    postgres_uri: str,
    schema: str = 'public',
) -> None:
    """
    Ensure helper structures on entity_identifiers for performant indexing.

    Adds a generated column that nulls out identifiers exceeding the allowed size so that
    indexes can remain compact without sacrificing correctness.
    """
    logger.info('Ensuring helper columns on entity_identifiers...')

    parsed = urlparse(postgres_uri)

    conn = psycopg2.connect(
        host=parsed.hostname,
        port=parsed.port or 5432,
        user=parsed.username,
        password=parsed.password,
        database=parsed.path.lstrip('/'),
    )

    try:
        cur = conn.cursor()

        cur.execute(
            """
            SELECT 1
            FROM information_schema.columns
            WHERE table_schema = %s
              AND table_name = 'entity_identifiers'
              AND column_name = 'id_value_small'
            """,
            (schema,),
        )
        exists = cur.fetchone() is not None

        if not exists:
            logger.info(
                '  Adding generated column id_value_small (<= %d bytes) ...',
                MAX_INDEXED_IDENTIFIER_OCTETS,
            )
            cur.execute(
                f"""
                ALTER TABLE {schema}.entity_identifiers
                ADD COLUMN id_value_small text
                GENERATED ALWAYS AS (
                    CASE
                        WHEN octet_length(id_value) <= {MAX_INDEXED_IDENTIFIER_OCTETS}
                        THEN id_value
                    END
                ) STORED
                """
            )
            conn.commit()
        else:
            logger.info('  Generated column id_value_small already present')

    except Exception as exc:
        logger.error('Failed to ensure helper column id_value_small: %s', exc)
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


def create_indexes(
    postgres_uri: str,
    schema: str = 'public',
) -> None:
    """
    Create indexes on PostgreSQL tables for query performance.

    Args:
        postgres_uri: PostgreSQL connection string
        schema: Target schema in PostgreSQL
    """
    logger.info('Creating indexes...')

    # Parse the postgres_uri to get connection parameters
    parsed = urlparse(postgres_uri)

    conn = psycopg2.connect(
        host=parsed.hostname,
        port=parsed.port or 5432,
        user=parsed.username,
        password=parsed.password,
        database=parsed.path.lstrip('/'),
    )

    try:
        cur = conn.cursor()

        for table_name, index_name, columns, index_type, where_clause in INDEXES:
            # Check if index already exists
            check_sql = f"""
                SELECT indexname
                FROM pg_indexes
                WHERE schemaname = '{schema}'
                AND tablename = '{table_name}'
                AND indexname = '{index_name}'
            """
            cur.execute(check_sql)
            existing_idx = cur.fetchone()

            if existing_idx:
                logger.info(f'  Skipping index: {index_name} on {table_name} (already exists)')
                continue

            # Build CREATE INDEX statement
            using_clause = f'USING {index_type}' if index_type else ''
            where_clause_sql = f'WHERE {where_clause}' if where_clause else ''
            sql = f"""
                CREATE INDEX {index_name}
                ON {schema}.{table_name}
                {using_clause}
                {columns}
                {where_clause_sql}
            """
            try:
                logger.info(f'  Creating index: {index_name} on {table_name}{columns}' +
                          (f' WHERE {where_clause}' if where_clause else ''))
                cur.execute(sql)
                conn.commit()
            except Exception as exc:
                logger.error(f'  Failed to create index {index_name}: {exc}')
                conn.rollback()
                raise

        logger.info('✓ All indexes created successfully')

    finally:
        cur.close()
        conn.close()


def load_tables_to_postgres(
    output_dir: Path,
    postgres_uri: str,
    schema: str = 'public',
    drop_existing: bool = False,
) -> int:
    """
    Load parquet tables to PostgreSQL using DuckDB.

    Args:
        output_dir: Directory containing the parquet files
        postgres_uri: PostgreSQL connection string (e.g., 'postgresql://user:pass@localhost:5432/dbname')
        schema: Target schema in PostgreSQL (default: public)
        drop_existing: If True, drop existing tables before creating new ones

    Returns:
        Exit code (0 for success, 1 for error)
    """
    if not output_dir.exists():
        logger.error(f'Output directory not found: {output_dir}')
        return 1

    # Connect to DuckDB
    con = duckdb.connect(':memory:')

    try:
        # Install and load required extensions
        logger.info('Installing PostgreSQL extension...')
        con.execute("INSTALL postgres")
        con.execute("LOAD postgres")

        # Attach to PostgreSQL database
        logger.info(f'Connecting to PostgreSQL: {postgres_uri}')
        con.execute(f"ATTACH '{postgres_uri}' AS pg (TYPE POSTGRES)")

        # If drop_existing, drop and recreate the entire schema to avoid FK dependencies
        if drop_existing:
            logger.info(f'Dropping schema {schema} CASCADE...')
            con.execute(f"DROP SCHEMA IF EXISTS pg.{schema} CASCADE")
            logger.info(f'Creating schema {schema}...')
            con.execute(f"CREATE SCHEMA pg.{schema}")

        # Process each table
        for table_spec in TABLES_TO_LOAD:
            # Handle table name mapping (parquet_file:postgres_table)
            if ':' in table_spec:
                parquet_name, postgres_table_name = table_spec.split(':', 1)
            else:
                parquet_name = postgres_table_name = table_spec

            parquet_file = output_dir / f'{parquet_name}.parquet'

            if not parquet_file.exists():
                logger.warning(f'Skipping {parquet_name}: file not found at {parquet_file}')
                continue

            logger.info(f'Processing table: {parquet_name} -> {postgres_table_name}')

            # Load parquet into a temporary view
            logger.info(f'  Reading parquet file: {parquet_file}')

            # Get all columns from the parquet file
            schema_info = con.execute(f"DESCRIBE SELECT * FROM read_parquet('{parquet_file}')").fetchall()
            all_columns = [row[0] for row in schema_info]

            # Determine which columns to exclude (use parquet name for lookups)
            columns_to_exclude = COLUMNS_TO_EXCLUDE.get(parquet_name, [])
            if columns_to_exclude:
                logger.info(f'  Excluding columns: {", ".join(columns_to_exclude)}')

            # Check if table has complex types that need conversion (use parquet name for lookups)
            complex_columns = TABLES_WITH_COMPLEX_TYPES.get(parquet_name, [])
            if complex_columns:
                logger.info(f'  Converting complex columns to JSON: {", ".join(complex_columns)}')

            # Check if table has RDKit columns (will be converted later in PostgreSQL)
            rdkit_columns = RDKIT_CONVERSIONS.get(parquet_name, {})
            if rdkit_columns:
                logger.info(f'  Note: RDKit columns will be converted after loading: {", ".join(rdkit_columns.keys())}')

            # Build SELECT clause
            if columns_to_exclude or complex_columns:
                select_parts = []
                for col in all_columns:
                    if col in columns_to_exclude:
                        continue  # Skip excluded columns
                    elif col in complex_columns:
                        select_parts.append(f"to_json({col}) AS {col}")
                    else:
                        select_parts.append(col)

                select_clause = ", ".join(select_parts)
                con.execute(f"CREATE OR REPLACE VIEW temp_{parquet_name} AS SELECT {select_clause} FROM read_parquet('{parquet_file}')")
            else:
                con.execute(f"CREATE OR REPLACE VIEW temp_{parquet_name} AS SELECT * FROM read_parquet('{parquet_file}')")

            # Get row count
            row_count = con.execute(f"SELECT COUNT(*) FROM temp_{parquet_name}").fetchone()[0]
            logger.info(f'  Found {row_count:,} rows')

            # Create table in PostgreSQL from parquet data (use postgres_table_name)
            logger.info(f'  Writing to PostgreSQL table: {schema}.{postgres_table_name}')
            con.execute(f"CREATE TABLE IF NOT EXISTS pg.{schema}.{postgres_table_name} AS SELECT * FROM temp_{parquet_name}")

            logger.info(f'  ✓ Successfully loaded {postgres_table_name}')

        logger.info('All tables loaded successfully!')

        # Apply RDKit type conversions to compound table
        apply_rdkit_conversions(postgres_uri, schema)

        # Add primary key constraints first (required for foreign keys)
        add_primary_keys(postgres_uri, schema)

        # Add foreign key constraints using native PostgreSQL connection
        add_foreign_keys(postgres_uri, schema)

        # Ensure helper generated columns exist prior to indexing
        ensure_entity_identifier_helpers(postgres_uri, schema)

        # Create indexes for query performance
        create_indexes(postgres_uri, schema)

        return 0

    except Exception as exc:
        logger.error(f'Error loading tables to PostgreSQL: {exc}', exc_info=True)
        return 1

    finally:
        con.close()


def _build_parser() -> argparse.ArgumentParser:
    """Configure the argument parser."""
    parser = argparse.ArgumentParser(
        description='Load OmniPath gold tables from parquet files to PostgreSQL using DuckDB.',
    )
    parser.add_argument(
        '--output-dir',
        type=Path,
        default=Path('databases/omnipath/output'),
        help='Path to output directory containing parquet files (default: databases/omnipath/output)',
    )
    parser.add_argument(
        '--postgres-uri',
        type=str,
        required=True,
        help='PostgreSQL connection string (e.g., postgresql://user:pass@localhost:5432/dbname)',
    )
    parser.add_argument(
        '--schema',
        type=str,
        default='public',
        help='Target schema in PostgreSQL (default: public)',
    )
    parser.add_argument(
        '--drop-existing',
        action='store_true',
        help='Drop existing tables before creating new ones',
    )
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    """Entry point for the PostgreSQL loader CLI."""
    parser = _build_parser()
    args = parser.parse_args(argv)

    # Resolve output directory path
    project_root = Path(__file__).resolve().parent.parent
    output_dir: Path = args.output_dir
    if not output_dir.is_absolute():
        output_dir = project_root / output_dir

    return load_tables_to_postgres(
        output_dir=output_dir,
        postgres_uri=args.postgres_uri,
        schema=args.schema,
        drop_existing=args.drop_existing,
    )


if __name__ == '__main__':
    sys.exit(main())
