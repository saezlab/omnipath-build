#!/usr/bin/env python3
"""Source-by-source processor using DuckDB for all transformations.

This processor handles the complete pipeline for a single source:
1. Read bronze Parquet
2. Apply silver transformations
3. Write silver Parquet
4. Apply gold transformations
5. Write to PostgreSQL gold
"""

import logging
import os
from pathlib import Path
from typing import Any

import yaml
import duckdb

from .gold_parquet_builder_v3 import GoldParquetBuilderV3
from .utils import PathManager

__all__ = [
    'SourceProcessor',
]

logger = logging.getLogger(__name__)


class SourceProcessor:
    """Processes a single source through bronze → silver → gold pipeline."""

    def __init__(
        self,
        database_name: str,
        source_module: str,
        base_path: Path | None = None
    ):
        """Initialize source processor.

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

        self.bronze_path = self.path_manager.bronze_module_path(source_module)
        self.silver_path = self.path_manager.silver_parquet_path()
        self.gold_path = self.path_manager.gold_parquet_path()
        self.resource_config_path = self.path_manager.resource_config_file(source_module)
        self.transform_sql_path = self.path_manager.transformation_functions_file()

        # Create silver output directory
        self.silver_path.mkdir(parents=True, exist_ok=True)
        self.gold_path.mkdir(parents=True, exist_ok=True)

        # Load resource configuration
        self.config = self._load_config()

        # Initialize DuckDB connection
        self.conn = None

        logger.info(f"Initialized SourceProcessor for {source_module} in {database_name}")

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

    def _get_latest_bronze_parquet(self, function_name: str) -> Path:
        """Get the latest bronze parquet file for a function."""
        function_path = self.bronze_path / function_name

        if not function_path.exists():
            raise FileNotFoundError(f"Bronze data not found: {function_path}")

        parquet_files = list(function_path.glob("*.parquet"))
        if not parquet_files:
            raise FileNotFoundError(f"No parquet files in: {function_path}")

        # Return latest by timestamp
        latest = sorted(parquet_files)[-1]
        logger.debug(f"Latest bronze file: {latest}")
        return latest

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
            if isinstance(value, bool):
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


    def _map_silver_files_to_tables(self, silver_files: dict[str, Path]) -> dict[str, Path]:
        """Map function names to their target table names.

        Args:
            silver_files: Dict mapping function names to silver parquet paths

        Returns:
            Dict mapping target table names to silver parquet paths
        """
        table_map = {}
        for function_name, parquet_path in silver_files.items():
            processing_cfg = self.config.get('functions', {}).get(function_name, {}).get('processing', {})
            target_table = processing_cfg.get('target_table', function_name)
            table_map[target_table] = parquet_path

        return table_map

    def process_to_silver(self) -> dict[str, Path]:
        """Process bronze → silver for all functions in this source.

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

            # Add source_database column
            select_expressions.append(f"    '{self.source_module}' AS \"source_database\"")

            # Build and execute query
            target_table = processing.get('target_table', function_name)
            output_file = self.path_manager.silver_parquet_file(
                self.source_module, function_name, target_table
            )

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

    def process_to_gold(self, silver_files: dict[str, Path]) -> dict[str, Path]:
        """Process silver → gold Parquet files using three-phase pipeline.

        Phase 1: Extract from silver files (pass1)
        Phase 2: Deduplicate
        Phase 3: Resolve foreign keys

        Args:
            silver_files: Dict mapping function names to silver parquet paths

        Returns:
            Dict mapping table names to gold parquet paths
        """
        if not silver_files:
            logger.warning("No silver files supplied for gold processing")
            return {}

        # Map function names to target table names
        silver_table_map = self._map_silver_files_to_tables(silver_files)

        logger.info("Processing %s silver → gold parquet (builder v3)", self.source_module)

        # Use the simplified pipeline
        with GoldParquetBuilderV3(self.gold_path, self.path_manager) as builder:
            return builder.run_full_pipeline(silver_table_map)

    def process_full_pipeline(self) -> dict[str, dict[str, Path]]:
        """Run full pipeline: bronze → silver → gold.

        Returns:
            Dict with 'silver' and 'gold' keys mapping to their respective outputs
        """
        # Process bronze → silver
        silver_files = self.process_to_silver()

        # Process silver → gold
        gold_files = self.process_to_gold(silver_files)

        return {
            'silver': silver_files,
            'gold': gold_files
        }
