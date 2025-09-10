"""Database connection utilities for OmniPath 2.0 pipeline.

Provides a unified interface for DuckDB with PostgreSQL extension.
"""

from typing import Any
import logging
from contextlib import contextmanager
from collections.abc import Iterator

import duckdb

__all__ = [
    'ConnectionError',
    'PostgresDuckDBConnector',
]

logger = logging.getLogger(__name__)


class PostgresDuckDBConnector:
    """Manages DuckDB connection with PostgreSQL extension for direct read/write operations.

    This class provides a unified interface for all loaders to interact with both
    DuckDB (for compute) and PostgreSQL (for storage).
    """

    def __init__(
        self,
        pg_config: dict[str, str] | None = None,
        duck_config: dict[str, Any] | None = None,
        duck_path: str = ':memory:',
    ) -> None:
        """Initialize the connector with PostgreSQL and DuckDB configurations.

        Args:
            pg_config: PostgreSQL connection configuration with keys:
                       host, port, database, user, password
                       If None, only DuckDB will be available
            duck_config: Optional DuckDB configuration (memory limits, threads, etc.)
            duck_path: Path to DuckDB database file (default: in-memory)
        """
        self.pg_config = pg_config
        self.duck_path = duck_path
        self.duck_config = duck_config or {}
        self.pg_attached = False

        # Create DuckDB connection
        self.conn = self._create_duckdb_connection()

        # Setup PostgreSQL extension and attach if config provided
        if self.pg_config:
            self._setup_postgres_extension()
            self._attach_postgres()
            self.pg_attached = True
            logger.info(
                f'Initialized PostgresDuckDBConnector with PostgreSQL (DuckDB: {duck_path})'
            )
        else:
            logger.info(
                f'Initialized DuckDB-only connector (DuckDB: {duck_path})'
            )

    def _create_duckdb_connection(self) -> duckdb.DuckDBPyConnection:
        """Create and configure DuckDB connection."""
        try:
            if self.duck_config:
                conn = duckdb.connect(self.duck_path, config=self.duck_config)
                logger.debug(
                    f'Created DuckDB connection with config: {self.duck_config}'
                )
            else:
                conn = duckdb.connect(self.duck_path)
                logger.debug('Created DuckDB connection with default config')

            return conn

        except duckdb.Error as e:
            logger.error(f'Failed to create DuckDB connection: {e}')
            raise ConnectionError(f'DuckDB connection failed: {e}') from e

    def _setup_postgres_extension(self) -> None:
        """Install and load PostgreSQL extension in DuckDB."""
        try:
            self.conn.execute('INSTALL postgres')
            self.conn.execute('LOAD postgres')
            logger.debug('PostgreSQL extension loaded successfully')

        except duckdb.Error as e:
            logger.error(f'Failed to setup PostgreSQL extension: {e}')
            raise ConnectionError(
                f'PostgreSQL extension setup failed: {e}'
            ) from e

    def _build_connection_string(self) -> str:
        """Build PostgreSQL connection string from configuration."""
        required_keys = ['host', 'port', 'database', 'user']

        # Validate required keys
        missing_keys = [k for k in required_keys if k not in self.pg_config]
        if missing_keys:
            raise ValueError(
                f'Missing required PostgreSQL config keys: {missing_keys}'
            )

        # Build base connection string
        conn_str = (
            f'dbname={self.pg_config["database"]} '
            f'user={self.pg_config["user"]} '
            f'host={self.pg_config["host"]} '
            f'port={self.pg_config["port"]}'
        )

        # Add password if provided
        if self.pg_config.get('password'):
            conn_str += f' password={self.pg_config["password"]}'

        return conn_str

    def _attach_postgres(self) -> None:
        """Attach PostgreSQL database to DuckDB."""
        try:
            conn_str = self._build_connection_string()
            self.conn.execute(f"ATTACH '{conn_str}' AS pg (TYPE postgres)")
            logger.info('Successfully attached PostgreSQL database')

        except duckdb.Error as e:
            logger.error(f'Failed to attach PostgreSQL database: {e}')
            raise ConnectionError(f'PostgreSQL attachment failed: {e}') from e

    def execute(
        self, sql: str, parameters: list | None = None
    ) -> duckdb.DuckDBPyRelation:
        """Execute SQL query with optional parameters.

        Args:
            sql: SQL query to execute
            parameters: Optional list of parameters for parameterized queries

        Returns:
            DuckDB relation object with query results
        """
        try:
            if parameters:
                return self.conn.execute(sql, parameters)
            return self.conn.execute(sql)

        except duckdb.Error as e:
            logger.error(f'SQL execution failed: {e}')
            logger.debug(f'Failed SQL: {sql}')
            raise

    def create_schema_if_not_exists(
        self, schema_name: str, database: str = 'pg'
    ) -> None:
        """Create schema if it doesn't exist.

        Args:
            schema_name: Name of the schema to create
            database: Database prefix (default: 'pg' for PostgreSQL)
        """
        try:
            self.execute(
                f'CREATE SCHEMA IF NOT EXISTS {database}.{schema_name}'
            )
            logger.debug(f'Ensured schema exists: {database}.{schema_name}')

        except duckdb.Error as e:
            logger.error(
                f'Failed to create schema {database}.{schema_name}: {e}'
            )
            raise

    @contextmanager
    def temporary_table(self, table_name: str) -> Iterator[str]:
        """Context manager for temporary tables that are automatically cleaned up.

        Args:
            table_name: Name of the temporary table

        Yields:
            str: The table name for use in queries
        """
        try:
            yield table_name
        finally:
            try:
                self.execute(f'DROP TABLE IF EXISTS {table_name}')
                logger.debug(f'Cleaned up temporary table: {table_name}')
            except duckdb.Error as e:
                logger.warning(
                    f'Failed to drop temporary table {table_name}: {e}'
                )

    def table_exists(self, table_name: str, schema: str = None) -> bool:
        """Check if a table exists.

        Args:
            table_name: Name of the table
            schema: Optional schema name

        Returns:
            bool: True if table exists, False otherwise
        """
        try:
            if schema:
                query = """
                    SELECT COUNT(*)
                    FROM information_schema.tables
                    WHERE table_schema = ? AND table_name = ?
                """
                result = self.execute(query, [schema, table_name]).fetchone()
            else:
                query = """
                    SELECT COUNT(*)
                    FROM information_schema.tables
                    WHERE table_name = ?
                """
                result = self.execute(query, [table_name]).fetchone()

            return result[0] > 0 if result else False

        except duckdb.Error as e:
            logger.error(f'Failed to check table existence: {e}')
            return False

    def get_row_count(self, table_name: str, schema: str = None) -> int:
        """Get row count for a table.

        Args:
            table_name: Name of the table
            schema: Optional schema name

        Returns:
            int: Number of rows in the table
        """
        try:
            full_table_name = f'{schema}.{table_name}' if schema else table_name
            result = self.execute(
                f'SELECT COUNT(*) FROM {full_table_name}'
            ).fetchone()
            return result[0] if result else 0

        except duckdb.Error as e:
            logger.error(f'Failed to get row count for {full_table_name}: {e}')
            return 0

    def close(self) -> None:
        """Close the database connection."""
        try:
            self.conn.close()
            logger.debug('Database connection closed')

        except duckdb.Error as e:
            logger.warning(f'Error closing database connection: {e}')


class ConnectionError(Exception):
    """Custom exception for database connection errors."""

    pass
