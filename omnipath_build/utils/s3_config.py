"""S3 configuration utilities for OmniPath 2.0 pipeline.

Provides S3 configuration for DuckDB and path management utilities.
"""

import os

import duckdb

__all__ = [
    'S3Config',
    'configure_duckdb_s3',
    'get_latest_s3_parquet',
    'get_s3_bronze_path',
    'get_s3_gold_path',
    'get_s3_silver_path',
    'list_s3_parquet_files',
]


class S3Config:
    """S3 configuration from environment variables."""

    def __init__(self) -> None:
        """Initialize S3 configuration from environment."""
        self.endpoint = os.getenv('S3_ENDPOINT', 'http://127.0.0.1:9000')
        self.access_key = os.getenv('S3_ACCESS_KEY', 'minioadmin')
        self.secret_key = os.getenv('S3_SECRET_KEY', 'minioadmin')
        self.bucket = os.getenv('DATA_BUCKET', 'database-builder')
        self.use_ssl = self.endpoint.startswith('https://')

    @property
    def endpoint_without_protocol(self) -> str:
        """Get endpoint without http:// or https:// prefix."""
        return self.endpoint.replace('https://', '').replace('http://', '')

    def configure_duckdb(self, conn: duckdb.DuckDBPyConnection) -> None:
        """Configure DuckDB connection for S3 access."""
        configure_duckdb_s3(conn, self)


def configure_duckdb_s3(
    conn: duckdb.DuckDBPyConnection, config: S3Config | None = None
) -> None:
    """Configure DuckDB connection for S3 access.

    Args:
        conn: DuckDB connection to configure
        config: S3 configuration (creates default if None)
    """
    if config is None:
        config = S3Config()

    # Load httpfs extension for S3 support
    conn.execute('INSTALL httpfs')
    conn.execute('LOAD httpfs')

    # Configure S3 settings
    conn.execute(f"SET s3_endpoint='{config.endpoint_without_protocol}'")
    conn.execute(f"SET s3_access_key_id='{config.access_key}'")
    conn.execute(f"SET s3_secret_access_key='{config.secret_key}'")
    conn.execute(f'SET s3_use_ssl={str(config.use_ssl).lower()}')
    conn.execute("SET s3_url_style='path'")


def get_s3_bronze_path(
    resource_id: str, dataset_name: str, timestamp: str
) -> str:
    """Get S3 path for bronze parquet file.

    Args:
        resource_id: Resource identifier
        dataset_name: Dataset name
        timestamp: Timestamp string for filename

    Returns:
        S3 path for bronze parquet file
    """
    config = S3Config()
    return f's3://{config.bucket}/bronze/{resource_id}/{dataset_name}/{timestamp}.parquet'


def get_s3_silver_path(
    database_name: str, table_name: str, source_database: str
) -> str:
    """Get S3 path for silver parquet file.

    Args:
        database_name: Database name
        table_name: Table name
        source_database: Source database identifier

    Returns:
        S3 path for silver parquet file
    """
    config = S3Config()
    return f's3://{config.bucket}/silver/{database_name}/data/{table_name}/{source_database}.parquet'


def get_s3_gold_path(database_name: str, table_name: str) -> str:
    """Get S3 path for gold parquet file.

    Args:
        database_name: Database name
        table_name: Table name

    Returns:
        S3 path for gold parquet file
    """
    config = S3Config()
    return (
        f's3://{config.bucket}/gold/{database_name}/data/{table_name}.parquet'
    )


def list_s3_parquet_files(
    conn: duckdb.DuckDBPyConnection, s3_prefix: str
) -> list[str]:
    """List parquet files in S3 with given prefix.

    Args:
        conn: DuckDB connection configured for S3
        s3_prefix: S3 prefix to search (e.g., 's3://bucket/bronze/resource/')

    Returns:
        List of S3 paths to parquet files
    """
    # Use glob_s3 if available, otherwise fall back to listing approach
    try:
        # Try to use glob pattern to find parquet files
        result = conn.execute(f"""
            SELECT DISTINCT file_path
            FROM glob_s3('{s3_prefix}*.parquet')
            ORDER BY file_path DESC
        """).fetchall()
        return [row[0] for row in result]
    except (RuntimeError, ValueError, TypeError):
        # Fallback: try to list files in directory structure
        try:
            result = conn.execute(f"""
                SELECT DISTINCT file_path
                FROM glob_s3('{s3_prefix}**/*.parquet')
                ORDER BY file_path DESC
            """).fetchall()
            return [row[0] for row in result]
        except (RuntimeError, ValueError, TypeError):
            # If S3 listing fails, return empty list
            return []


def get_latest_s3_parquet(
    conn: duckdb.DuckDBPyConnection, resource_id: str, dataset_name: str
) -> str | None:
    """Get the latest bronze parquet file from S3.

    Args:
        conn: DuckDB connection configured for S3
        resource_id: Resource identifier
        dataset_name: Dataset name

    Returns:
        S3 path to latest parquet file or None if not found
    """
    config = S3Config()
    s3_prefix = f's3://{config.bucket}/bronze/{resource_id}/{dataset_name}/'

    files = list_s3_parquet_files(conn, s3_prefix)
    return files[0] if files else None
