#!/usr/bin/env python3
"""Silver Loader: Bronze → Silver transformations.

This loader handles the transformation of bronze parquet files to silver parquet files
using field mappings defined in resource configuration YAML files.

Usage:
    from omnipath_build import SilverLoader

    with SilverLoader('metabo', 'hmdb') as loader:
        silver_files = loader.load()
"""

import logging
from pathlib import Path
from typing import Any

import yaml
import duckdb

from .utils import PathManager

__all__ = [
    'SilverLoader',
]

logger = logging.getLogger(__name__)


class SilverLoader:
    """Loads and transforms bronze parquet files to silver parquet format."""

    def __init__(
        self,
        database_name: str,
        source_module: str,
        base_path: Path | None = None
    ):
        """Initialize silver loader.

        Args:
            database_name: Name of database (e.g., 'metabo')
            source_module: Source module name (e.g., 'hmdb', 'psimi')
            base_path: Base path to database files (defaults to omnipath_build/databases)
        """
        self.database_name = database_name
        self.source_module = source_module

        # Use PathManager for all paths
        self.path_manager = PathManager(database_name, base_path)
        self.base_path = self.path_manager.db_path

        self.resource_config_path = self.path_manager.resource_config_file(source_module)

        # Path to transformation functions SQL file (in configuration directory now)
        self.transform_sql_path = self.path_manager.configuration_path() / 'transformation_functions.sql'

        # Load resource configuration
        self.config = self._load_config()

        # Load expected silver table schemas
        self.expected_columns = self._load_expected_columns()

        # Initialize DuckDB connection
        self.conn = None

        logger.info(f"Initialized SilverLoader for {source_module} in {database_name}")

    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit - cleanup DuckDB connection."""
        if self.conn:
            self.conn.close()
            self.conn = None

    def _load_config(self) -> dict[str, Any]:
        """Load resource configuration from YAML."""
        if not self.resource_config_path.exists():
            raise FileNotFoundError(f"Config not found: {self.resource_config_path}")

        with open(self.resource_config_path) as f:
            config = yaml.safe_load(f)

        logger.debug(f"Loaded config for {self.source_module}")
        return config

    def _load_expected_columns(self) -> dict[str, list[str]]:
        """Load expected column names for each silver table from schema config."""
        config_path = self.path_manager.silver_tables_config()
        if not config_path.exists():
            logger.warning("Silver tables config not found at %s", config_path)
            return {}

        with open(config_path) as f:
            schema_config = yaml.safe_load(f) or {}

        expected: dict[str, list[str]] = {}
        for table_name, columns in schema_config.items():
            if isinstance(columns, dict):
                expected[table_name] = list(columns.keys())

        return expected

    def _get_latest_bronze_parquet(self, function_name: str) -> Path:
        """Get the latest bronze parquet file for a function."""
        bronze_file = self.path_manager.bronze_latest_file(self.source_module, function_name)

        if not bronze_file.exists():
            raise FileNotFoundError(f"Bronze data not found: {bronze_file}")

        logger.debug(f"Bronze file: {bronze_file}")
        return bronze_file

    def _init_duckdb(self):
        """Initialize DuckDB connection and load transformation functions."""
        if self.conn is not None:
            return

        self.conn = duckdb.connect(":memory:")
        logger.debug("Created DuckDB connection")

        # Load transformation functions if they exist
        if self.transform_sql_path.exists():
            with open(self.transform_sql_path) as f:
                transform_sql = f.read()
            self.conn.execute(transform_sql)
            logger.debug("Loaded transformation functions")

    def _build_select_expression(
        self,
        field_mapping: dict[str, Any],
        available_columns: set[str]
    ) -> str:
        """Build SQL SELECT expression for a single field mapping.

        Args:
            field_mapping: Single field mapping dict with source/target/transform
            available_columns: Set of available column names in bronze data

        Returns:
            SQL expression string
        """
        source = field_mapping.get('source')
        target = field_mapping.get('target')
        transform = field_mapping.get('transform')
        value = field_mapping.get('value')

        # Handle special source types
        if source == '_constant':
            # Constant value
            if value is None:
                expr = 'NULL'
            elif isinstance(value, bool):
                expr = 'TRUE' if value else 'FALSE'
            elif isinstance(value, (int, float)):
                expr = str(value)
            else:
                expr = f"'{value}'"

        elif source == '_metadata':
            # Metadata fields
            if value == 'current_timestamp':
                expr = 'CURRENT_TIMESTAMP'
            else:
                expr = f"'{value}'"

        elif isinstance(source, list):
            # Multiple source fields
            if transform:
                # Call transform function with multiple arguments
                transform_args = field_mapping.get('transform_args', {})
                args = ', '.join([f'"{col}"' for col in source])

                # Add transform_args if present
                for arg_name, arg_value in transform_args.items():
                    if isinstance(arg_value, str):
                        args += f", '{arg_value}'"
                    else:
                        args += f", {arg_value}"

                expr = f"{transform}({args})"
            else:
                # Just concatenate with pipe
                expr = f"CONCAT_WS('|', {', '.join([f'"{col}"' for col in source])})"

        elif source in available_columns:
            # Single source field
            if transform:
                transform_args = field_mapping.get('transform_args', {})
                if transform_args:
                    # Build arguments
                    args = [f'"{source}"']
                    for arg_name, arg_value in transform_args.items():
                        if isinstance(arg_value, str):
                            args.append(f"'{arg_value}'")
                        else:
                            args.append(str(arg_value))
                    expr = f"{transform}({', '.join(args)})"
                else:
                    expr = f'{transform}("{source}")'
            else:
                expr = f'"{source}"'

        else:
            # Source field not available - use NULL
            expr = 'NULL'

        return f"{expr} AS \"{target}\""

    def load(self) -> dict[str, Path]:
        """Load bronze → silver for all functions in this source.

        Returns:
            Dict mapping function names to silver parquet paths
        """
        self._init_duckdb()

        results = {}
        functions = self.config.get('functions', {})

        for function_name, function_config in functions.items():
            processing = function_config.get('processing')
            if not processing:
                logger.warning(f"No processing config for {function_name}, skipping")
                continue

            logger.info(f"Processing {self.source_module}.{function_name} → silver")

            # Get bronze parquet
            try:
                bronze_file = self._get_latest_bronze_parquet(function_name)
            except FileNotFoundError as e:
                logger.error(f"Bronze data not found: {e}")
                continue

            # Get available columns
            col_query = f"SELECT * FROM '{bronze_file}' LIMIT 0"
            result = self.conn.execute(col_query)
            available_columns = {desc[0] for desc in result.description}
            logger.debug(f"Available columns: {available_columns}")

            # Build SELECT expressions from field mappings
            field_mappings = processing.get('field_mapping', [])
            select_expressions = []

            for mapping in field_mappings:
                expr = self._build_select_expression(mapping, available_columns)
                select_expressions.append(f"    {expr}")

            # Determine target table name
            target_table = processing.get('target_table', function_name)

            # Add source column
            select_expressions.append(f"    '{self.source_module}' AS \"source\"")

            # Fill in any missing columns defined in silver schema with NULLs
            expected_columns = set(self.expected_columns.get(target_table, []))
            mapped_columns = {
                mapping.get('target')
                for mapping in field_mappings
                if isinstance(mapping, dict) and mapping.get('target')
            }
            mapped_columns.add('source')

            missing_columns = expected_columns - mapped_columns
            for column in sorted(missing_columns):
                select_expressions.append(f"    NULL AS \"{column}\"")

            # Build and execute query
            output_file = self.path_manager.silver_file(
                self.source_module, function_name, target_table
            )
            output_file.parent.mkdir(parents=True, exist_ok=True)

            select_clause = ',\n'.join(select_expressions)
            query = f"""
            COPY (
                SELECT
{select_clause}
                FROM '{bronze_file}'
            ) TO '{output_file}' (FORMAT PARQUET)
            """

            logger.debug(f"Executing transformation:\n{query}")

            try:
                self.conn.execute(query)

                # Get row count
                count = self.conn.execute(f"SELECT COUNT(*) FROM '{output_file}'").fetchone()[0]
                logger.info(f"✓ Created {output_file.name} with {count:,} rows")

                results[function_name] = output_file

            except Exception as e:
                logger.error(f"Failed to process {function_name}: {e}")
                logger.error(f"Query was:\n{query}")
                raise

        return results

    def get_table_function_map(self, silver_files: dict[str, Path]) -> dict[str, str]:
        """Map target table names to originating function names.

        Args:
            silver_files: Dict mapping function names to silver parquet paths

        Returns:
            Dict mapping target table names to function names
        """
        table_function_map: dict[str, str] = {}
        for function_name in silver_files.keys():
            processing_cfg = self.config.get('functions', {}).get(function_name, {}).get('processing', {})
            target_table = processing_cfg.get('target_table', function_name)
            table_function_map[target_table] = function_name

        return table_function_map
