"""SQL adaptation utilities for PostgreSQL integration.

Provides a cleaner alternative to string manipulation for SQL schema adaptation.
"""

import re
from typing import Any
import logging
from pathlib import Path

from .constants import SQLPatterns

__all__ = [
    'SQLAdapter',
    'SQLExecutionManager',
]

logger = logging.getLogger(__name__)


class SQLAdapter:
    """Adapts SQL queries for PostgreSQL by adding schema prefixes and handling naming conventions."""

    def __init__(self, schema_mappings: dict[str, str] | None = None) -> None:
        """Initialize SQL adapter.

        Args:
            schema_mappings: Custom schema mappings (defaults to PostgreSQL patterns)
        """
        self.schema_mappings = schema_mappings or SQLPatterns.SCHEMA_PREFIXES

        # Build replacement patterns
        self.replacement_patterns = self._build_replacement_patterns()

    def _build_replacement_patterns(self) -> list[tuple]:
        """Build list of (pattern, replacement) tuples for SQL adaptation."""
        patterns = []

        # Schema references in different SQL contexts
        for schema, pg_schema in self.schema_mappings.items():
            # FROM/JOIN clauses
            patterns.extend(
                [
                    (f'FROM {schema}.', f'FROM {pg_schema}.'),
                    (f'JOIN {schema}.', f'JOIN {pg_schema}.'),
                    (f'LEFT JOIN {schema}.', f'LEFT JOIN {pg_schema}.'),
                    (f'RIGHT JOIN {schema}.', f'RIGHT JOIN {pg_schema}.'),
                    (f'INNER JOIN {schema}.', f'INNER JOIN {pg_schema}.'),
                    (f'OUTER JOIN {schema}.', f'OUTER JOIN {pg_schema}.'),
                    (f'FULL JOIN {schema}.', f'FULL JOIN {pg_schema}.'),
                    (f'CROSS JOIN {schema}.', f'CROSS JOIN {pg_schema}.'),
                ]
            )

            # DDL statements
            patterns.extend(
                [
                    (f'CREATE TABLE {schema}.', f'CREATE TABLE {pg_schema}.'),
                    (
                        f'CREATE OR REPLACE TABLE {schema}.',
                        f'CREATE OR REPLACE TABLE {pg_schema}.',
                    ),
                    (
                        f'DROP TABLE IF EXISTS {schema}.',
                        f'DROP TABLE IF EXISTS {pg_schema}.',
                    ),
                    (f'DROP TABLE {schema}.', f'DROP TABLE {pg_schema}.'),
                    (f'ALTER TABLE {schema}.', f'ALTER TABLE {pg_schema}.'),
                    (
                        f'TRUNCATE TABLE {schema}.',
                        f'TRUNCATE TABLE {pg_schema}.',
                    ),
                ]
            )

            # DML statements
            patterns.extend(
                [
                    (f'INSERT INTO {schema}.', f'INSERT INTO {pg_schema}.'),
                    (f'UPDATE {schema}.', f'UPDATE {pg_schema}.'),
                    (f'DELETE FROM {schema}.', f'DELETE FROM {pg_schema}.'),
                ]
            )

            # Other contexts
            patterns.extend(
                [
                    (
                        f'EXISTS (SELECT * FROM {schema}.',
                        f'EXISTS (SELECT * FROM {pg_schema}.',
                    ),
                    (f'SELECT * FROM {schema}.', f'SELECT * FROM {pg_schema}.'),
                ]
            )

        return patterns

    def adapt_sql(self, sql: str) -> str:
        """Adapt SQL for PostgreSQL by replacing schema references.

        Args:
            sql: Original SQL query

        Returns:
            Adapted SQL with proper schema prefixes
        """
        adapted_sql = sql

        # Apply all replacement patterns
        for pattern, replacement in self.replacement_patterns:
            adapted_sql = adapted_sql.replace(pattern, replacement)

        # Log if changes were made
        if adapted_sql != sql:
            logger.debug('SQL adapted with schema prefixes')

        return adapted_sql

    def adapt_sql_file(self, file_path: Path) -> str:
        """Read and adapt SQL from a file.

        Args:
            file_path: Path to SQL file

        Returns:
            Adapted SQL content
        """
        if not file_path.exists():
            raise FileNotFoundError(f'SQL file not found: {file_path}')

        with open(file_path, encoding='utf-8') as f:
            sql = f.read()

        return self.adapt_sql(sql)

    def validate_sql_structure(self, sql: str) -> bool:
        """Basic validation of SQL structure.

        Args:
            sql: SQL query to validate

        Returns:
            True if basic structure looks valid
        """
        sql_upper = sql.upper().strip()

        # Check for common SQL keywords
        valid_starts = [
            'SELECT',
            'INSERT',
            'UPDATE',
            'DELETE',
            'CREATE',
            'DROP',
            'ALTER',
            'TRUNCATE',
            'WITH',
            'MERGE',
        ]

        if not any(sql_upper.startswith(keyword) for keyword in valid_starts):
            logger.warning('SQL does not start with a recognized keyword')
            return False

        # Check for balanced parentheses
        if sql.count('(') != sql.count(')'):
            logger.warning('Unbalanced parentheses in SQL')
            return False

        # Check for potential SQL injection patterns (basic)
        suspicious_patterns = [
            r';\s*drop\s+table',
            r';\s*delete\s+from',
            r'union\s+select.*from',
            r'insert\s+into.*values',
        ]

        for pattern in suspicious_patterns:
            if re.search(pattern, sql_upper):
                logger.warning(
                    f'Potentially suspicious SQL pattern detected: {pattern}'
                )
                return False

        return True

    def get_referenced_schemas(self, sql: str) -> list[str]:
        """Extract schema names referenced in the SQL.

        Args:
            sql: SQL query

        Returns:
            List of schema names found
        """
        schemas = set()

        # Pattern to match schema.table references
        pattern = r'\b(\w+)\.\w+'
        matches = re.findall(pattern, sql)

        for match in matches:
            if match in self.schema_mappings:
                schemas.add(match)

        return sorted(schemas)

    def add_schema_mapping(self, schema: str, pg_schema: str) -> None:
        """Add a custom schema mapping.

        Args:
            schema: Original schema name
            pg_schema: PostgreSQL schema name with prefix
        """
        self.schema_mappings[schema] = pg_schema
        # Rebuild patterns with new mapping
        self.replacement_patterns = self._build_replacement_patterns()
        logger.debug(f'Added schema mapping: {schema} -> {pg_schema}')


class SQLExecutionManager:
    """Manages SQL file execution with dependency tracking and error handling."""

    def __init__(self, sql_adapter: SQLAdapter, base_dir: Path) -> None:
        """Initialize execution manager.

        Args:
            sql_adapter: SQLAdapter instance
            base_dir: Base directory containing SQL files
        """
        self.sql_adapter = sql_adapter
        self.base_dir = base_dir
        self.execution_stats = {}

    def execute_sql_file(
        self,
        filename: str,
        connection: Any,  # noqa: ANN401
    ) -> dict[str, Any]:
        """Execute a single SQL file with timing and error handling.

        Args:
            filename: SQL file name
            connection: Database connection

        Returns:
            Execution statistics
        """
        import time

        filepath = self.base_dir / filename

        if not filepath.exists():
            raise FileNotFoundError(f'SQL file not found: {filepath}')

        logger.info(f'Executing: {filename}')

        start_time = time.time()
        try:
            # Read and adapt SQL
            adapted_sql = self.sql_adapter.adapt_sql_file(filepath)

            # Validate SQL structure
            if not self.sql_adapter.validate_sql_structure(adapted_sql):
                logger.warning(
                    f'SQL structure validation failed for {filename}'
                )

            # Execute SQL
            connection.execute(adapted_sql)
            elapsed = time.time() - start_time

            # Collect statistics
            stats = {
                'filename': filename,
                'elapsed_time': elapsed,
                'status': 'success',
                'error': None,
                'row_count': None,
            }

            # Try to get row count for created table
            try:
                table_name = self._extract_table_name(filename)
                if table_name:
                    count_result = connection.execute(
                        f'SELECT COUNT(*) FROM pg.gold.{table_name}'
                    ).fetchone()
                    stats['row_count'] = count_result[0] if count_result else 0
            except (OSError, RuntimeError) as e:
                logger.debug(f'Could not get row count for {filename}: {e}')

            # Log success
            if stats['row_count'] is not None:
                logger.info(
                    f'✓ {filename} completed in {elapsed:.2f}s - {stats["row_count"]:,} rows'
                )
            else:
                logger.info(f'✓ {filename} completed in {elapsed:.2f}s')

            self.execution_stats[filename] = stats
            return stats

        except Exception as e:
            elapsed = time.time() - start_time
            logger.error(f'Failed to execute {filename}: {e}')

            stats = {
                'filename': filename,
                'elapsed_time': elapsed,
                'status': 'error',
                'error': str(e),
                'row_count': None,
            }

            self.execution_stats[filename] = stats
            raise

    def _extract_table_name(self, filename: str) -> str | None:
        """Extract table name from SQL filename."""
        base_name = filename.replace('.sql', '')

        # Special case for populate scripts
        if '_populate' in base_name:
            return base_name.replace('_populate', '')

        return base_name

    def get_execution_summary(self) -> dict[str, any]:
        """Get summary of all executed SQL files."""
        total_files = len(self.execution_stats)
        successful = sum(
            1 for s in self.execution_stats.values() if s['status'] == 'success'
        )
        total_time = sum(
            s['elapsed_time'] for s in self.execution_stats.values()
        )
        total_rows = sum(
            s['row_count']
            for s in self.execution_stats.values()
            if s['row_count']
        )

        return {
            'total_files': total_files,
            'successful': successful,
            'failed': total_files - successful,
            'total_time': total_time,
            'total_rows': total_rows,
            'stats': self.execution_stats,
        }
