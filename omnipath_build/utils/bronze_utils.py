"""Utilities for Bronze layer operations.

Provides common functionality for writing to PostgreSQL bronze schema.
"""

from typing import Any
import logging
from pathlib import Path
from datetime import datetime

import duckdb
import psycopg2

from .constants import LoaderConstants
from .exceptions import BronzeLoaderError

__all__ = [
    'BronzeWriter',
]

logger = logging.getLogger(__name__)


class BronzeWriter:
    """Handles writing data to PostgreSQL bronze schema."""

    def __init__(
        self,
        pg_config: dict[str, str] | None,
        duckdb_conn: duckdb.DuckDBPyConnection,
    ) -> None:
        """Initialize Bronze writer.

        Args:
            pg_config: PostgreSQL connection configuration (None for DuckDB-only mode)
            duckdb_conn: DuckDB connection for data processing
        """
        self.pg_config = pg_config
        self.duckdb_conn = duckdb_conn
        self.pg_enabled = pg_config is not None

        if self.pg_enabled:
            self._setup_bronze_schema()
        else:
            logger.info(
                'PostgreSQL disabled, using DuckDB-only mode for bronze layer'
            )

    def _get_pg_connection(self) -> psycopg2.extensions.connection:
        """Get PostgreSQL connection."""
        if not self.pg_enabled:
            raise RuntimeError('PostgreSQL not available in DuckDB-only mode')
        return psycopg2.connect(**self.pg_config)

    def _setup_bronze_schema(self) -> None:
        """Create bronze schema in PostgreSQL if it doesn't exist."""
        try:
            with self._get_pg_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute('CREATE SCHEMA IF NOT EXISTS bronze')
                    conn.commit()
            logger.debug('PostgreSQL bronze schema ready')
        except psycopg2.Error as e:
            logger.warning(f'Could not setup PostgreSQL bronze schema: {e}')

    def write_to_bronze(
        self,
        resource_id: str,
        dataset_name: str,
        data_source: Path,
        source_type: str = 'csv',
        delimiter: str = '\t',
        has_header: bool = True,
    ) -> int:
        """Write first N rows to PostgreSQL bronze schema.

        Args:
            resource_id: Resource identifier
            dataset_name: Dataset name
            data_source: Path to data file (CSV or Parquet)
            source_type: Type of source file ('csv' or 'parquet')
            delimiter: Delimiter for CSV files
            has_header: Whether CSV has header row

        Returns:
            Number of rows written
        """
        if not self.pg_enabled:
            logger.info(
                f'Skipping PostgreSQL bronze write for {resource_id}/{dataset_name} (DuckDB-only mode)'
            )
            return 0

        table_name = self._sanitize_table_name(resource_id, dataset_name)

        try:
            with self._get_pg_connection() as conn:
                with conn.cursor() as cur:
                    # Drop existing table
                    cur.execute(
                        f'DROP TABLE IF EXISTS bronze.{table_name} CASCADE'
                    )

                    # Get data based on source type
                    if source_type == 'csv':
                        cols, rows = self._read_csv_sample(
                            data_source, delimiter, has_header
                        )
                    elif source_type == 'parquet':
                        cols, rows = self._read_parquet_sample(data_source)
                    else:
                        raise ValueError(
                            f'Unsupported source type: {source_type}'
                        )

                    if not cols or not rows:
                        logger.warning(
                            f'No data found for bronze table {table_name}'
                        )
                        return 0

                    # Create and populate table
                    self._create_bronze_table(cur, table_name, cols)
                    row_count = self._insert_bronze_data(
                        cur, table_name, cols, rows, resource_id, dataset_name
                    )

                    conn.commit()
                    logger.info(
                        f'✅ Wrote {row_count} rows to PostgreSQL bronze.{table_name}'
                    )
                    return row_count

        except Exception as e:
            logger.error(
                f'Failed to write to PostgreSQL bronze.{table_name}: {e}'
            )
            raise BronzeLoaderError(f'Bronze write failed: {e}') from e
        finally:
            # Clean up any temporary tables
            self._cleanup_temp_tables()

    def write_to_bronze_from_s3(
        self,
        resource_id: str,
        dataset_name: str,
        s3_parquet_path: str,
    ) -> int:
        """Write first N rows to PostgreSQL bronze schema from S3 parquet file.

        Args:
            resource_id: Resource identifier
            dataset_name: Dataset name
            s3_parquet_path: S3 path to parquet file

        Returns:
            Number of rows written
        """
        if not self.pg_enabled:
            logger.info(
                f'Skipping PostgreSQL bronze write for {resource_id}/{dataset_name} (DuckDB-only mode)'
            )
            return 0

        table_name = self._sanitize_table_name(resource_id, dataset_name)

        try:
            with self._get_pg_connection() as conn:
                with conn.cursor() as cur:
                    # Drop existing table
                    cur.execute(
                        f'DROP TABLE IF EXISTS bronze.{table_name} CASCADE'
                    )

                    # Get sample data from S3 parquet
                    cols, rows = self._read_s3_parquet_sample(s3_parquet_path)

                    if not cols or not rows:
                        logger.warning(
                            f'No data found in S3 parquet for bronze table {table_name}'
                        )
                        return 0

                    # Create and populate table
                    self._create_bronze_table(cur, table_name, cols)
                    row_count = self._insert_bronze_data(
                        cur, table_name, cols, rows, resource_id, dataset_name
                    )

                    conn.commit()
                    logger.info(
                        f'✅ Wrote {row_count} rows to PostgreSQL bronze.{table_name} from S3'
                    )
                    return row_count

        except Exception as e:
            logger.error(
                f'Failed to write to PostgreSQL bronze.{table_name} from S3: {e}'
            )
            raise BronzeLoaderError(f'Bronze S3 write failed: {e}') from e
        finally:
            # Clean up any temporary tables
            self._cleanup_temp_tables()

    def _sanitize_table_name(self, resource_id: str, dataset_name: str) -> str:
        """Create a valid PostgreSQL table name."""
        combined = f'{resource_id}__{dataset_name}'
        return combined.replace('-', '_').replace(' ', '_').lower()

    def _read_csv_sample(
        self, csv_path: Path, delimiter: str, has_header: bool
    ) -> tuple[list[str], list[tuple[Any, ...]]]:
        """Read sample data from CSV file."""
        temp_table = (
            f'temp_bronze_csv_{datetime.now().strftime("%Y%m%d_%H%M%S")}'
        )

        try:
            # Create temporary table with sample data
            header_param = 'TRUE' if has_header else 'FALSE'

            self.duckdb_conn.execute(f"""
                CREATE TABLE {temp_table} AS
                SELECT * FROM read_csv_auto(
                    '{csv_path}',
                    delim='{delimiter}',
                    header={header_param},
                    normalize_names=TRUE,
                    all_varchar=TRUE,
                    sample_size={LoaderConstants.CSV_SAMPLE_SIZE},
                    ignore_errors=TRUE,
                    null_padding=TRUE
                ) LIMIT {LoaderConstants.BRONZE_SAMPLE_SIZE}
            """)

            # Get columns
            cols = [
                row[0]
                for row in self.duckdb_conn.execute(
                    """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_name = ?
                ORDER BY ordinal_position
            """,
                    [temp_table],
                ).fetchall()
            ]

            # Get data
            rows = self.duckdb_conn.execute(
                f'SELECT * FROM {temp_table}'
            ).fetchall()

            return cols, rows

        finally:
            # Clean up
            try:
                self.duckdb_conn.execute(f'DROP TABLE IF EXISTS {temp_table}')
            except duckdb.Error:
                pass

    def _read_s3_parquet_sample(
        self, s3_parquet_path: str
    ) -> tuple[list[str], list[tuple[Any, ...]]]:
        """Read sample data from S3 parquet file."""
        temp_table = (
            f'temp_bronze_s3_parquet_{datetime.now().strftime("%Y%m%d_%H%M%S")}'
        )

        try:
            # Create temporary table with sample data from S3
            self.duckdb_conn.execute(f"""
                CREATE TABLE {temp_table} AS
                SELECT * FROM read_parquet('{s3_parquet_path}')
                LIMIT {LoaderConstants.BRONZE_SAMPLE_SIZE}
            """)

            # Get columns (excluding metadata columns)
            cols = [
                row[0]
                for row in self.duckdb_conn.execute(
                    """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_name = ?
                  AND column_name NOT LIKE 'metadata_%'
                ORDER BY ordinal_position
            """,
                    [temp_table],
                ).fetchall()
            ]

            # Handle empty column list gracefully
            if not cols:
                logger.warning(
                    f'No data columns found in {s3_parquet_path}, only metadata columns exist'
                )
                return [], []

            # Get data (only non-metadata columns)
            col_list = ', '.join(f'"{col}"' for col in cols)
            rows = self.duckdb_conn.execute(
                f'SELECT {col_list} FROM {temp_table}'
            ).fetchall()

            return cols, rows

        finally:
            # Clean up
            try:
                self.duckdb_conn.execute(f'DROP TABLE IF EXISTS {temp_table}')
            except duckdb.Error:
                pass

    def _read_parquet_sample(
        self, parquet_path: Path
    ) -> tuple[list[str], list[tuple[Any, ...]]]:
        """Read sample data from Parquet file."""
        temp_table = (
            f'temp_bronze_parquet_{datetime.now().strftime("%Y%m%d_%H%M%S")}'
        )

        try:
            # Create temporary table with sample data
            self.duckdb_conn.execute(f"""
                CREATE TABLE {temp_table} AS
                SELECT * FROM read_parquet('{parquet_path}')
                LIMIT {LoaderConstants.BRONZE_SAMPLE_SIZE}
            """)

            # Get columns (excluding metadata columns)
            cols = [
                row[0]
                for row in self.duckdb_conn.execute(
                    """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_name = ?
                  AND column_name NOT LIKE 'metadata_%'
                ORDER BY ordinal_position
            """,
                    [temp_table],
                ).fetchall()
            ]

            # Handle empty column list gracefully
            if not cols:
                logger.warning(
                    f'No data columns found in {parquet_path}, only metadata columns exist'
                )
                return [], []

            # Get data (only non-metadata columns)
            col_list = ', '.join(f'"{col}"' for col in cols)
            rows = self.duckdb_conn.execute(
                f'SELECT {col_list} FROM {temp_table}'
            ).fetchall()

            return cols, rows

        finally:
            # Clean up
            try:
                self.duckdb_conn.execute(f'DROP TABLE IF EXISTS {temp_table}')
            except duckdb.Error:
                pass

    def _create_bronze_table(
        self,
        cursor: psycopg2.extensions.cursor,
        table_name: str,
        columns: list[str],
    ) -> None:
        """Create bronze table with metadata columns."""
        # Sanitize column names
        sanitized_cols = []
        for col in columns:
            if col and col.strip():
                sanitized_col = col.replace('"', '').replace("'", '').strip()
                if sanitized_col:
                    sanitized_cols.append(f'"{sanitized_col}" TEXT')

        if not sanitized_cols:
            raise ValueError('No valid columns found')

        # Add metadata columns
        create_sql = f"""
            CREATE TABLE bronze.{table_name} (
                {', '.join(sanitized_cols)},
                metadata_resource TEXT,
                metadata_dataset TEXT,
                metadata_loaded_at TEXT,
                metadata_row_number INTEGER
            )
        """

        cursor.execute(create_sql)

    def _insert_bronze_data(
        self,
        cursor: psycopg2.extensions.cursor,
        table_name: str,
        columns: list[str],
        rows: list[tuple[Any, ...]],
        resource_id: str,
        dataset_name: str,
    ) -> int:
        """Insert data into bronze table."""
        load_ts = datetime.now().isoformat()
        placeholders = ', '.join(['%s'] * (len(columns) + 4))

        insert_sql = f'INSERT INTO bronze.{table_name} VALUES ({placeholders})'

        insert_data = []
        for i, row in enumerate(rows, 1):
            # Process row values
            processed_row = []
            for val in row:
                if val is None:
                    processed_row.append(None)
                elif isinstance(val, list | tuple | dict):
                    # Convert complex types to JSON strings
                    import json

                    processed_row.append(json.dumps(val, default=str))
                elif val in LoaderConstants.NULL_VALUES:
                    processed_row.append(None)
                elif isinstance(val, str | int | float | bool):
                    # Handle basic types
                    processed_row.append(val)
                else:
                    # Convert other types to string
                    processed_row.append(str(val))

            # Add metadata
            processed_row.extend([resource_id, dataset_name, load_ts, i])
            insert_data.append(processed_row)

        cursor.executemany(insert_sql, insert_data)
        return len(insert_data)

    def _cleanup_temp_tables(self) -> None:
        """Clean up any temporary tables."""
        try:
            # Find and drop temporary tables
            temp_tables = self.duckdb_conn.execute("""
                SELECT table_name
                FROM information_schema.tables
                WHERE table_name LIKE 'temp_bronze_%'
            """).fetchall()

            for (table_name,) in temp_tables:
                try:
                    self.duckdb_conn.execute(
                        f'DROP TABLE IF EXISTS {table_name}'
                    )
                except duckdb.Error:
                    pass
        except duckdb.Error:
            pass
