#!/usr/bin/env python3
"""Load OmniPath gold tables from parquet files to PostgreSQL using DuckDB."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Optional

import duckdb

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
