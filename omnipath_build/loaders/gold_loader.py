#!/usr/bin/env python3
"""Refactored Gold Loader for OmniPath 2.0.

Creates final deduplicated and enriched tables from PostgreSQL silver layer data.
"""

import re
import sys
from typing import Any
from pathlib import Path

# Add parent directory to path for utils import
sys.path.append(str(Path(__file__).parent.parent))

# Import utilities
from utils import BaseLoader, GoldLoaderError, log_execution_time
from utils.constants import get_database_path
from utils.sql_adapter import SQLAdapter, SQLExecutionManager

__all__ = [
    'GoldLoader',
]


class GoldLoader(BaseLoader):
    """Refactored Gold Loader that creates final tables from silver layer data.

    Inherits from BaseLoader for common functionality.
    """

    def __init__(self, database_name: str, db_connector: Any) -> None:  # noqa: ANN401
        """Initialize GoldLoader with database name and connection.

        Args:
            database_name: Name of the database (e.g., 'omnipath')
            db_connector: Database connector instance
        """
        self.database_name = database_name
        super().__init__(db_connector=db_connector)

    def _initialize(self) -> None:
        """Initialize gold-specific attributes."""
        # Path to SQL transforms using database name
        self.transforms_dir = get_database_path(self.database_name) / 'gold'

        if not self.transforms_dir.exists():
            raise GoldLoaderError(
                f'Gold transforms directory not found: {self.transforms_dir}'
            )

        # Initialize SQL adapter and execution manager
        self.sql_adapter = SQLAdapter()
        self.execution_manager = SQLExecutionManager(
            self.sql_adapter, self.transforms_dir
        )

        # Create PostgreSQL schemas
        self._create_postgres_schemas()

        # Initialize execution order from file naming convention

        self.logger.info(
            f'Gold loader initialized, transforms dir: {self.transforms_dir}'
        )

    def _get_ordered_sql_files(self) -> list[str]:
        """Get SQL files ordered by numeric prefix in filename."""
        if not self.transforms_dir.exists():
            raise GoldLoaderError(
                f'Transforms directory not found: {self.transforms_dir}'
            )

        sql_files = list(self.transforms_dir.glob('*.sql'))

        # Separate files with numeric prefixes from those without
        prefixed_files = []
        unprefixed_files = []

        for sql_file in sql_files:
            filename = sql_file.name
            # Match files with numeric prefix like "1_", "10_", etc.
            match = re.match(r'^(\d+)_', filename)
            if match:
                prefix = int(match.group(1))
                prefixed_files.append((prefix, filename))
            else:
                unprefixed_files.append(filename)

        # Sort prefixed files by their numeric prefix
        prefixed_files.sort(key=lambda x: x[0])

        # Sort unprefixed files alphabetically
        unprefixed_files.sort()

        # Combine: prefixed files first, then unprefixed
        ordered_filenames = [
            filename for _, filename in prefixed_files
        ] + unprefixed_files

        self.logger.debug(f'Ordered SQL files: {ordered_filenames}')
        return ordered_filenames

    def _create_postgres_schemas(self) -> None:
        """Create required schemas in PostgreSQL."""
        try:
            self.create_schemas(['gold', 'stage'])
            self.logger.info('Created PostgreSQL gold and stage schemas')
        except Exception as e:
            self.logger.error(f'Failed to create PostgreSQL schemas: {e}')
            raise GoldLoaderError(f'Schema creation failed: {e}') from e

    def load(self, step: int | None = None) -> dict[str, Any]:
        """Execute gold layer transformation.

        Args:
            step: Specific step to execute (None = all steps)

        Returns:
            Execution statistics
        """
        if step is not None:
            return self.execute_step(step)
        else:
            return self.execute_all()

    @log_execution_time()
    def execute_all(self) -> dict[str, Any]:
        """Execute all SQL files in numeric order based on filename prefixes."""
        self.logger.info('Starting gold layer transformation...')

        # Get ordered list of SQL files
        ordered_files = self._get_ordered_sql_files()

        for i, filename in enumerate(ordered_files):
            self.logger.info(f'\n=== STEP {i + 1}/{len(ordered_files)} ===')
            self.logger.info(f'Executing file: {filename}')

            try:
                self.execution_manager.execute_sql_file(filename, self.conn)
            except Exception as e:
                self.logger.error(f'Failed to execute {filename}: {e}')
                raise GoldLoaderError(f'SQL execution failed: {e}') from e

        # Get execution summary
        summary = self.execution_manager.get_execution_summary()

        self.logger.info('\nGold layer transformation completed')
        self.logger.info(
            f'Files executed: {summary["successful"]}/{summary["total_files"]}'
        )
        self.logger.info(f'Total time: {summary["total_time"]:.2f}s')
        if summary['total_rows']:
            self.logger.info(
                f'Total rows processed: {self.format_row_count(summary["total_rows"])}'
            )

        return summary

    def execute_step(self, step: int) -> dict[str, Any]:
        """Execute a specific SQL file by step number (1-based)."""
        ordered_files = self._get_ordered_sql_files()

        if step < 1 or step > len(ordered_files):
            raise ValueError(
                f'Invalid step {step}. Must be 1-{len(ordered_files)}'
            )

        filename = ordered_files[step - 1]
        self.logger.info(f'Executing step {step}: {filename}')

        try:
            self.execution_manager.execute_sql_file(filename, self.conn)
        except Exception as e:
            self.logger.error(f'Failed to execute {filename}: {e}')
            raise GoldLoaderError(f'SQL execution failed: {e}') from e

        return self.execution_manager.get_execution_summary()

    def execute_sql_file(self, filename: str) -> dict[str, Any]:
        """Execute a single SQL file."""
        try:
            return self.execution_manager.execute_sql_file(filename, self.conn)
        except Exception as e:
            self.logger.error(f'Failed to execute {filename}: {e}')
            raise GoldLoaderError(f'SQL execution failed: {e}') from e

    def list_sql_files(self) -> list[str]:
        """List all SQL files in the transforms directory."""
        return self._get_ordered_sql_files()

    def get_execution_plan(self) -> dict[str, str]:
        """Get the execution plan showing file order."""
        ordered_files = self._get_ordered_sql_files()
        return {
            f'Step {i + 1}': filename
            for i, filename in enumerate(ordered_files)
        }

    def validate_sql_files(self) -> dict[str, bool]:
        """Validate that all SQL files exist and have basic structure."""
        results = {}

        for filename in self.list_sql_files():
            filepath = self.transforms_dir / filename

            if not filepath.exists():
                results[filename] = False
                self.logger.warning(f'SQL file not found: {filepath}')
                continue

            try:
                sql_content = self.sql_adapter.adapt_sql_file(filepath)
                is_valid = self.sql_adapter.validate_sql_structure(sql_content)
                results[filename] = is_valid

                if not is_valid:
                    self.logger.warning(f'SQL validation failed for {filename}')
                else:
                    self.logger.debug(f'SQL validation passed for {filename}')

            except (OSError, RuntimeError) as e:
                results[filename] = False
                self.logger.error(f'Error validating {filename}: {e}')

        return results

    def get_table_stats(self) -> dict[str, int]:
        """Get statistics for all gold tables."""
        stats = {}

        try:
            tables_result = self.execute_sql("""
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = 'gold'
                ORDER BY table_name
            """).fetchall()

            for row in tables_result:
                table_name = row[0]
                try:
                    count_result = self.execute_sql(
                        f'SELECT COUNT(*) FROM pg.gold.{table_name}'
                    ).fetchone()
                    stats[table_name] = count_result[0] if count_result else 0
                except (OSError, RuntimeError) as e:
                    self.logger.warning(
                        f'Could not get count for {table_name}: {e}'
                    )
                    stats[table_name] = 0

        except (OSError, RuntimeError) as e:
            self.logger.error(f'Failed to get table statistics: {e}')

        return stats

    def validate_gold_data(self) -> None:
        """Validate gold tables and show statistics."""
        self.logger.info('Validating gold layer data...')

        # Get table statistics
        stats = self.get_table_stats()

        if not stats:
            self.logger.warning('No gold tables found')
            return

        for table_name, row_count in stats.items():
            self.logger.info(f'\n{table_name.upper()} table:')
            self.logger.info(
                f'  Total rows: {self.format_row_count(row_count)}'
            )

            # Show sample data
            try:
                sample = self.execute_sql(
                    f'SELECT * FROM pg.gold.{table_name} LIMIT 2'
                ).fetchall()

                if sample:
                    self.logger.info('  Sample rows:')
                    cols = [desc[0] for desc in self.conn.description]
                    for row in sample:
                        self.logger.info(
                            f'    {dict(zip(cols, row, strict=False))}'
                        )

            except (OSError, RuntimeError) as e:
                self.logger.error(f'  Failed to get sample data: {e}')

    def get_dependency_info(self) -> dict[str, Any]:
        """Get information about SQL file execution order."""
        ordered_files = self._get_ordered_sql_files()

        info = {
            'total_files': len(ordered_files),
            'execution_order': self.get_execution_plan(),
            'file_validation': self.validate_sql_files(),
        }

        return info
