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
    'cv_namespace',
    'cv_term',
    'entity_evidence',
    'entity_identifiers',
    'evidence_reference',
    'interaction_evidence',
    'membership',
    'references',
    'source',
]

# Tables with complex types that need JSON conversion
TABLES_WITH_COMPLEX_TYPES = {
    'entity_evidence': ['annotations'],
    'interaction_evidence': ['interaction_annotations'],
}

# Columns to exclude from loading
# TODO dont even write these to parquet in the first place
COLUMNS_TO_EXCLUDE = {
    'interaction_evidence': ['references'],
}

# Columns that need special conversion for RDKit types
# Format: table_name -> {column_name: conversion_function}
RDKIT_CONVERSIONS = {
    'compound': {
        'molfile': 'mol_from_ctab',  # Convert molfile text to mol type
        'morgan_fp': 'bfp_from_binary_text',  # Convert binary to bfp type
    }
}

# Primary keys to add after table creation
# Format: (table_name, primary_key_column)
PRIMARY_KEY_CONSTRAINTS = [
    ('cv_namespace', 'id'),
    ('cv_term', 'id'),
    ('source', 'id'),
    ('references', 'id'),
    ('entity_evidence', 'id'),
    ('membership', 'id'),
    ('interaction_evidence', 'id'),
    ('evidence_reference', 'id'),
    ('compound', 'id'),
]

# Foreign key constraints to add after table creation
# Format: (table_name, column_name, referenced_table, referenced_column)
FOREIGN_KEY_CONSTRAINTS = [
    # cv_term table
    ('cv_term', 'namespace_id', 'cv_namespace', 'id'),
    ('cv_term', 'replaces_id', 'cv_term', 'id'),
    ('cv_term', 'replaced_by_id', 'cv_term', 'id'),

    # entity_identifiers table
    ('entity_identifiers', 'type_id', 'cv_term', 'id'),

    # entity_evidence table
    ('entity_evidence', 'source_id', 'source', 'id'),
    ('entity_evidence', 'entity_type_id', 'cv_term', 'id'),

    # membership table
    ('membership', 'role_id', 'cv_term', 'id'),
    ('membership', 'source_id', 'source', 'id'),

    # interaction_evidence table
    ('interaction_evidence', 'interaction_type_id', 'cv_term', 'id'),
    ('interaction_evidence', 'detection_method_id', 'cv_term', 'id'),

    # evidence_reference table
    ('evidence_reference', 'reference_id', 'references', 'id'),
    ('evidence_reference', 'entity_evidence_id', 'entity_evidence', 'id'),
    ('evidence_reference', 'interaction_evidence_id', 'interaction_evidence', 'id'),
    ('evidence_reference', 'membership_id', 'membership', 'id'),
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

        # Process each table
        for table_name in TABLES_TO_LOAD:
            parquet_file = output_dir / f'{table_name}.parquet'

            if not parquet_file.exists():
                logger.warning(f'Skipping {table_name}: file not found at {parquet_file}')
                continue

            logger.info(f'Processing table: {table_name}')

            # Drop table if requested
            if drop_existing:
                logger.info(f'  Dropping existing table {schema}.{table_name} if it exists...')
                con.execute(f"DROP TABLE IF EXISTS pg.{schema}.{table_name}")

            # Load parquet into a temporary view
            logger.info(f'  Reading parquet file: {parquet_file}')

            # Get all columns from the parquet file
            schema_info = con.execute(f"DESCRIBE SELECT * FROM read_parquet('{parquet_file}')").fetchall()
            all_columns = [row[0] for row in schema_info]

            # Determine which columns to exclude
            columns_to_exclude = COLUMNS_TO_EXCLUDE.get(table_name, [])
            if columns_to_exclude:
                logger.info(f'  Excluding columns: {", ".join(columns_to_exclude)}')

            # Check if table has complex types that need conversion
            complex_columns = TABLES_WITH_COMPLEX_TYPES.get(table_name, [])
            if complex_columns:
                logger.info(f'  Converting complex columns to JSON: {", ".join(complex_columns)}')

            # Check if table has RDKit columns (will be converted later in PostgreSQL)
            rdkit_columns = RDKIT_CONVERSIONS.get(table_name, {})
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
                con.execute(f"CREATE OR REPLACE VIEW temp_{table_name} AS SELECT {select_clause} FROM read_parquet('{parquet_file}')")
            else:
                con.execute(f"CREATE OR REPLACE VIEW temp_{table_name} AS SELECT * FROM read_parquet('{parquet_file}')")

            # Get row count
            row_count = con.execute(f"SELECT COUNT(*) FROM temp_{table_name}").fetchone()[0]
            logger.info(f'  Found {row_count:,} rows')

            # Create table in PostgreSQL from parquet data
            logger.info(f'  Writing to PostgreSQL table: {schema}.{table_name}')
            con.execute(f"CREATE TABLE IF NOT EXISTS pg.{schema}.{table_name} AS SELECT * FROM temp_{table_name}")

            logger.info(f'  ✓ Successfully loaded {table_name}')

        logger.info('All tables loaded successfully!')

        # Apply RDKit type conversions to compound table
        apply_rdkit_conversions(postgres_uri, schema)

        # Add primary key constraints first (required for foreign keys)
        add_primary_keys(postgres_uri, schema)

        # Add foreign key constraints using native PostgreSQL connection
        add_foreign_keys(postgres_uri, schema)

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
