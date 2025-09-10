#!/usr/bin/env python3
"""PostgreSQL Exporter for OmniPath 2.0.

Export Parquet gold layer files to PostgreSQL for downstream compatibility.
"""

import sys
from typing import Any
from pathlib import Path

# Add parent directory to path for utils import
sys.path.append(str(Path(__file__).parent.parent))

# Import utilities
from utils import BaseLoader, log_execution_time
from utils.constants import get_database_path

__all__ = [
    'PostgresExporter',
]


class PostgresExporter(BaseLoader):
    """Export Parquet gold layer files to PostgreSQL.

    This class provides compatibility by exporting the final gold Parquet files
    to PostgreSQL tables for existing downstream systems.
    """

    def __init__(self, database_name: str, db_connector: Any) -> None:  # noqa: ANN401
        """Initialize PostgresExporter with database name and connection.

        Args:
            database_name: Name of the database (e.g., 'omnipath')
            db_connector: Database connector instance with PostgreSQL support
        """
        self.database_name = database_name
        super().__init__(db_connector=db_connector)

    def _initialize(self) -> None:
        """Initialize exporter-specific attributes."""
        # Path to gold parquet files
        self.gold_data_path = (
            get_database_path(self.database_name) / 'gold' / 'data'
        )

        if not self.gold_data_path.exists():
            raise RuntimeError(
                f'Gold data directory not found: {self.gold_data_path}'
            )

        # Create PostgreSQL gold schema
        self._create_postgres_schemas()

        self.logger.info(
            f'PostgreSQL exporter initialized, gold data path: {self.gold_data_path}'
        )

    def _create_postgres_schemas(self) -> None:
        """Create required schemas in PostgreSQL."""
        try:
            self.create_schemas(['gold'])
            self.logger.info('Created PostgreSQL gold schema')
        except (RuntimeError, ConnectionError, OSError) as e:
            self.logger.error(f'Failed to create PostgreSQL schemas: {e}')
            raise RuntimeError(f'Schema creation failed: {e}') from e

    @log_execution_time()
    def export(self, tables: list[str] | None = None) -> dict[str, int]:
        """Export gold Parquet files to PostgreSQL.

        Args:
            tables: Specific tables to export (None = all available tables)

        Returns:
            Dict mapping table names to row counts
        """
        if tables is None:
            tables = self._discover_tables()

        if not tables:
            self.logger.warning('No tables found to export')
            return {}

        self.logger.info(f'Exporting {len(tables)} tables to PostgreSQL')

        results = {}
        for table in tables:
            parquet_file = self.gold_data_path / f'{table}.parquet'

            if not parquet_file.exists():
                self.logger.warning(f'Parquet file not found: {parquet_file}')
                results[table] = 0
                continue

            try:
                row_count = self._export_table(table, parquet_file)
                results[table] = row_count
                self.logger.info(
                    f'✓ Exported {table}: {self.format_row_count(row_count)} rows'
                )
            except (RuntimeError, ConnectionError, OSError, ValueError) as e:
                self.logger.error(f'Failed to export table {table}: {e}')
                results[table] = 0

        total_rows = sum(results.values())
        self.logger.info(
            f'Export completed: {len([r for r in results.values() if r > 0])}/{len(tables)} tables, '
            f'{self.format_row_count(total_rows)} total rows'
        )

        return results

    def _discover_tables(self) -> list[str]:
        """Discover all available Parquet tables in gold data directory."""
        if not self.gold_data_path.exists():
            return []

        parquet_files = list(self.gold_data_path.glob('*.parquet'))
        table_names = [
            pf.stem for pf in parquet_files
        ]  # filename without .parquet
        table_names.sort()

        self.logger.info(
            f'Discovered {len(table_names)} tables: {", ".join(table_names)}'
        )
        return table_names

    def _export_table(self, table_name: str, parquet_file: Path) -> int:
        """Export single Parquet file to PostgreSQL table.

        Args:
            table_name: Name of the table to create
            parquet_file: Path to source Parquet file

        Returns:
            Number of rows exported
        """
        self.logger.info(f'Exporting {parquet_file} -> gold.{table_name}')

        # Drop existing table if it exists
        drop_sql = f'DROP TABLE IF EXISTS gold.{table_name} CASCADE'
        self.execute_sql(drop_sql)

        # Create table from Parquet schema and insert data in one operation
        create_and_insert_sql = f"""
            CREATE TABLE gold.{table_name} AS
            SELECT * FROM read_parquet('{parquet_file}')
        """

        try:
            self.execute_sql(create_and_insert_sql)

            # Get row count
            count_sql = f'SELECT COUNT(*) FROM gold.{table_name}'
            row_count = self.execute_sql(count_sql).fetchone()[0]

            return row_count

        except (RuntimeError, ConnectionError, OSError, ValueError) as e:
            self.logger.error(
                f'Failed to create/populate table {table_name}: {e}'
            )
            raise RuntimeError(f'Table export failed: {e}') from e

    def validate_export(
        self, tables: list[str] | None = None
    ) -> dict[str, dict[str, Any]]:
        """Validate exported tables by comparing with source Parquet files.

        Args:
            tables: Specific tables to validate (None = all tables)

        Returns:
            Dict with validation results for each table
        """
        if tables is None:
            tables = self._discover_tables()

        results = {}

        for table in tables:
            parquet_file = self.gold_data_path / f'{table}.parquet'

            if not parquet_file.exists():
                results[table] = {
                    'status': 'error',
                    'message': 'Parquet file not found',
                }
                continue

            try:
                # Get row counts from both sources
                parquet_count = self.execute_sql(f"""
                    SELECT COUNT(*) FROM read_parquet('{parquet_file}')
                """).fetchone()[0]

                postgres_count = self.execute_sql(f"""
                    SELECT COUNT(*) FROM gold.{table}
                """).fetchone()[0]

                if parquet_count == postgres_count:
                    results[table] = {
                        'status': 'success',
                        'parquet_rows': parquet_count,
                        'postgres_rows': postgres_count,
                    }
                else:
                    results[table] = {
                        'status': 'mismatch',
                        'parquet_rows': parquet_count,
                        'postgres_rows': postgres_count,
                        'message': f'Row count mismatch: {parquet_count} vs {postgres_count}',
                    }

            except (RuntimeError, ConnectionError, OSError, ValueError) as e:
                results[table] = {'status': 'error', 'message': str(e)}

        # Log summary
        successful = len(
            [r for r in results.values() if r['status'] == 'success']
        )
        self.logger.info(
            f'Validation completed: {successful}/{len(results)} tables validated successfully'
        )

        return results

    def show_export_status(self) -> None:
        """Show status of exported tables."""
        self.logger.info(f'Export Status for database: {self.database_name}')
        self.logger.info('=' * 50)

        # Check if PostgreSQL schema exists
        try:
            schema_result = self.execute_sql("""
                SELECT 1 FROM information_schema.schemata
                WHERE schema_name = 'gold'
            """).fetchall()

            if not schema_result:
                self.logger.info('PostgreSQL gold schema: ✗ (not found)')
                return

            self.logger.info('PostgreSQL gold schema: ✓')

            # Get list of tables
            tables_result = self.execute_sql(
                """
                SELECT table_name,
                       (SELECT COUNT(*) FROM gold.{} LIMIT 1) as row_count
                FROM information_schema.tables
                WHERE table_schema = 'gold'
                ORDER BY table_name
            """.format('{}')
            )  # Placeholder for dynamic table name

            # Get tables with individual counts
            table_counts = {}
            tables_result = self.execute_sql("""
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = 'gold'
                ORDER BY table_name
            """).fetchall()

            for (table_name,) in tables_result:
                try:
                    count_result = self.execute_sql(
                        f'SELECT COUNT(*) FROM gold.{table_name}'
                    ).fetchone()
                    table_counts[table_name] = (
                        count_result[0] if count_result else 0
                    )
                except (
                    RuntimeError,
                    ConnectionError,
                    OSError,
                    ValueError,
                ) as e:
                    self.logger.warning(
                        f'Could not get count for {table_name}: {e}'
                    )
                    table_counts[table_name] = 0

            if table_counts:
                total_rows = sum(table_counts.values())
                self.logger.info(
                    f'Exported tables ({len(table_counts)} total):'
                )
                for table_name, row_count in table_counts.items():
                    self.logger.info(
                        f'  {table_name}: {self.format_row_count(row_count)} rows'
                    )
                self.logger.info(
                    f'Total exported rows: {self.format_row_count(total_rows)}'
                )
            else:
                self.logger.info('No exported tables found')

        except (RuntimeError, ConnectionError, OSError, ValueError) as e:
            self.logger.error(f'Failed to get export status: {e}')
