"""Constants and configuration values for OmniPath 2.0 pipeline.

Centralizes magic numbers, patterns, and repeated values.
"""

from pathlib import Path

__all__ = [
    'LoaderConstants',
    'SQLPatterns',
    'get_database_path',
]


def get_database_path(database_name: str) -> Path:
    """Get the path to a database directory relative to the omnipath_build package.

    Args:
        database_name: Name of the database

    Returns:
        Path to the database directory
    """
    return Path(__file__).parent.parent / 'databases' / database_name


class LoaderConstants:
    """Constants used across all loaders."""

    # Batch and sample sizes
    DEFAULT_BATCH_SIZE = 100_000
    BRONZE_SAMPLE_SIZE = 100  # Rows to write to PostgreSQL bronze

    # Memory and performance limits
    DEFAULT_MEMORY_LIMIT = '4GB'
    DEFAULT_MAX_MEMORY = '4GB'
    DEFAULT_THREAD_COUNT = 4

    # Progress reporting
    PROGRESS_LOG_INTERVAL = 10_000  # Log progress every N rows

    # Null value handling
    NULL_VALUES = {
        '',
        'NULL',
        'null',
        'None',
        'none',
        'N/A',
        'n/a',
        'NA',
        'na',
        '-',
    }


class SQLPatterns:
    """SQL schema and pattern mappings."""

    # Schema prefixes for PostgreSQL
    SCHEMA_PREFIXES = {
        'silver': 'pg.silver',
        'gold': 'pg.gold',
        'metadata': 'pg.metadata',
        'bronze': 'pg.bronze',
        'stage': 'pg.stage',
    }

    # Table name patterns to replace in SQL
    TABLE_PATTERNS = [
        ('FROM silver.', 'FROM pg.silver.'),
        ('JOIN silver.', 'JOIN pg.silver.'),
        ('LEFT JOIN silver.', 'LEFT JOIN pg.silver.'),
        ('RIGHT JOIN silver.', 'RIGHT JOIN pg.silver.'),
        ('INNER JOIN silver.', 'INNER JOIN pg.silver.'),
        ('OUTER JOIN silver.', 'OUTER JOIN pg.silver.'),
        ('FROM gold.', 'FROM pg.gold.'),
        ('JOIN gold.', 'JOIN pg.gold.'),
        ('LEFT JOIN gold.', 'LEFT JOIN pg.gold.'),
        ('RIGHT JOIN gold.', 'RIGHT JOIN pg.gold.'),
        ('INNER JOIN gold.', 'INNER JOIN pg.gold.'),
        ('OUTER JOIN gold.', 'OUTER JOIN pg.gold.'),
        ('CREATE TABLE gold.', 'CREATE TABLE pg.gold.'),
        ('CREATE OR REPLACE TABLE gold.', 'CREATE OR REPLACE TABLE pg.gold.'),
        ('DROP TABLE IF EXISTS gold.', 'DROP TABLE IF EXISTS pg.gold.'),
        ('INSERT INTO gold.', 'INSERT INTO pg.gold.'),
        ('UPDATE gold.', 'UPDATE pg.gold.'),
        ('FROM metadata.', 'FROM pg.metadata.'),
        ('JOIN metadata.', 'JOIN pg.metadata.'),
        ('LEFT JOIN metadata.', 'LEFT JOIN pg.metadata.'),
        ('RIGHT JOIN metadata.', 'RIGHT JOIN pg.metadata.'),
        ('INNER JOIN metadata.', 'INNER JOIN pg.metadata.'),
        ('OUTER JOIN metadata.', 'OUTER JOIN pg.metadata.'),
    ]

    # Reserved column names that need special handling
    RESERVED_COLUMNS = {
        'references',  # PostgreSQL reserved word
        'user',
        'order',
        'group',
    }
