#!/usr/bin/env python3
"""Refactored Silver Loader for OmniPath 2.0.

Transforms bronze parquet data and writes to Parquet files in silver layer.
"""

import sys
from typing import Any
from pathlib import Path

import yaml

# Add parent directory to path for utils import
sys.path.append(str(Path(__file__).parent.parent))

# Import utilities
from utils import BaseLoader, SilverLoaderError, log_execution_time
from utils.constants import get_database_path

__all__ = [
    'SilverLoader',
]


class SilverLoader(BaseLoader):
    """Refactored Silver Loader that transforms bronze data to silver layer.

    Inherits from BaseLoader for common functionality.
    """

    def __init__(self, database_name: str, db_connector: Any) -> None:  # noqa: ANN401
        """Initialize SilverLoader with database name and connection.

        Args:
            database_name: Name of the database (e.g., 'omnipath')
            db_connector: Database connector instance
        """
        self.database_name = database_name
        self.silver_config_path = get_database_path(database_name) / 'silver'
        self.tables_yaml_path = self.silver_config_path / 'tables.yaml'
        self.table_definitions = self._load_table_definitions()
        super().__init__(db_connector=db_connector)

    def _load_table_definitions(self) -> dict[str, dict[str, str]]:
        """Load table definitions from YAML file."""
        if not self.tables_yaml_path.exists():
            raise SilverLoaderError(
                f'Table definitions file not found: {self.tables_yaml_path}'
            )

        import yaml

        with open(self.tables_yaml_path) as f:
            return yaml.safe_load(f)

    def _initialize(self) -> None:
        """Initialize silver-specific attributes."""
        # Use database-specific bronze data path
        self.bronze_path = (
            get_database_path(self.database_name) / 'bronze' / 'data'
        )

        # Silver data output path
        self.silver_data_path = self.silver_config_path / 'data'
        self.silver_data_path.mkdir(parents=True, exist_ok=True)

        # Resource configs directory for reading YAML configurations
        self.resource_configs_path = (
            get_database_path(self.database_name) / 'resource'
        )

        # Load SQL transformation functions
        transforms_path = (
            self.silver_config_path / 'transformation_functions.sql'
        )
        self._load_transformation_functions(transforms_path)

        # Create silver data directories for each table
        self._create_silver_directories()

        self.logger.info(
            f'Silver loader initialized, bronze path: {self.bronze_path}'
        )
        self.logger.info(f'Silver data path: {self.silver_data_path}')

    def _load_transformation_functions(self, transforms_path: Path) -> None:
        """Load SQL transformation functions from file."""
        if not transforms_path.exists():
            self.logger.warning(
                f'Transformation functions file not found: {transforms_path}'
            )
            return

        try:
            with open(transforms_path) as f:
                sql_content = f.read()

            # Execute the SQL to register all transformation functions
            self.execute_sql(sql_content)
            self.logger.info(
                f'Loaded SQL transformation functions from {transforms_path}'
            )

        except Exception as e:
            self.logger.error(f'Failed to load transformation functions: {e}')
            raise SilverLoaderError(f'Transform loading failed: {e}') from e

    def _create_silver_directories(self) -> None:
        """Create silver data directories for each table."""
        try:
            for table_name in self.table_definitions.keys():
                table_dir = self.silver_data_path / table_name
                table_dir.mkdir(parents=True, exist_ok=True)
                self.logger.debug(f'Created silver directory: {table_dir}')

            self.logger.info('Created silver data directories')
        except Exception as e:
            self.logger.error(f'Failed to create silver directories: {e}')
            raise SilverLoaderError(f'Directory creation failed: {e}') from e

    def load(self, resource_id: str | None = None) -> dict[str, int]:
        """Load silver data for one or all resources.

        Args:
            resource_id: Specific resource to load (None = all resources)

        Returns:
            Dict mapping table names to row counts
        """
        results = {}

        if resource_id:
            resources = [resource_id]
        else:
            resources = self.get_all_resources()
            self.logger.info(f'Loading {len(resources)} resources')

        for res_id in resources:
            try:
                self.logger.info(f'Loading silver data for resource: {res_id}')

                resource_results = self.load_resource(res_id)

                # Merge results
                for table, count in resource_results.items():
                    if table in results:
                        results[table] += count
                    else:
                        results[table] = count

                self.logger.info(f'Completed resource {res_id}')

            except (OSError, yaml.YAMLError, RuntimeError) as e:
                self.logger.error(f'Failed to load resource {res_id}: {e}')
                # Continue with other resources

        return results

    @log_execution_time()
    def load_resource(self, resource_id: str) -> dict[str, int]:
        """Load and transform data from bronze to PostgreSQL silver for a resource.

        Args:
            resource_id: The resource identifier

        Returns:
            Dict mapping silver table names to row counts
        """
        # Get resource info
        resource_info = self.get_resource_info(resource_id)
        if not resource_info:
            raise SilverLoaderError(f"Resource '{resource_id}' not found")

        # Get datasets with data_processing
        datasets = self.get_datasets_with_processing(resource_id)
        if not datasets:
            self.logger.warning(
                f'No datasets with data_processing found for {resource_id}'
            )
            return {}

        results = {}

        for dataset in datasets:
            bronze_file = self.get_latest_bronze(resource_id, dataset['name'])

            if not bronze_file:
                self.logger.warning(
                    f'No bronze parquet file found for {resource_id}/{dataset["name"]}'
                )
                continue

            # Get target table from data_processing
            data_processing = dataset['data_processing']
            target_table = data_processing.get('target_table')

            if not target_table:
                self.logger.warning(
                    f'No target_table specified for {resource_id}/{dataset["name"]}'
                )
                continue

            # Transform and write to Parquet
            try:
                row_count = self._transform_and_write_parquet(
                    resource_info,
                    dataset['name'],
                    bronze_file,
                    target_table,
                    data_processing,
                )

                key = f'{target_table} (from {dataset["name"]})'
                results[key] = row_count

            except (OSError, RuntimeError) as e:
                self.logger.error(
                    f'Failed to transform {dataset["name"]} to {target_table}: {e}'
                )
                results[f'{target_table} (from {dataset["name"]})'] = 0

        return results

    def get_all_resources(self) -> list[str]:
        """Get all resource IDs from YAML config files."""
        try:
            if not self.resource_configs_path.exists():
                self.logger.warning(
                    f'Resource configs directory not found: {self.resource_configs_path}'
                )
                return []

            resources = []
            for yaml_file in self.resource_configs_path.glob('*.yaml'):
                try:
                    with open(yaml_file) as f:
                        config = yaml.safe_load(f)
                        if (
                            config
                            and 'module' in config
                            and 'functions' in config
                        ):
                            # Check if any function has processing configuration
                            has_processing = any(
                                func_config.get('processing')
                                for func_config in config['functions'].values()
                            )
                            if has_processing:
                                resources.append(config['module'])
                except yaml.YAMLError as e:
                    self.logger.warning(
                        f'Could not parse config file {yaml_file}: {e}'
                    )
                    continue

            resources.sort()
            return resources

        except OSError as e:
            self.logger.error(f'Could not read resource configurations: {e}')
            return []

    def get_resource_info(self, resource_id: str) -> dict[str, Any] | None:
        """Get resource metadata from YAML config files."""
        try:
            # Find the YAML file for this resource
            for yaml_file in self.resource_configs_path.glob('*.yaml'):
                try:
                    with open(yaml_file) as f:
                        config = yaml.safe_load(f)
                        if config and config.get('module') == resource_id:
                            return {
                                'id': resource_id,
                                'name': resource_id,
                                'description': f'Resource configuration from {yaml_file.name}',
                            }
                except yaml.YAMLError as e:
                    self.logger.warning(
                        f'Could not parse config file {yaml_file}: {e}'
                    )
                    continue

        except OSError as e:
            self.logger.error(f'Could not read resource configuration: {e}')

        # Fallback: create default resource info
        return {
            'id': resource_id,
            'name': resource_id,
            'description': f'Resource {resource_id}',
        }

    def get_datasets_with_processing(
        self, resource_id: str
    ) -> list[dict[str, Any]]:
        """Get datasets with data processing configurations from YAML files."""
        try:
            # Find the YAML file for this resource
            for yaml_file in self.resource_configs_path.glob('*.yaml'):
                try:
                    with open(yaml_file) as f:
                        config = yaml.safe_load(f)
                        if config and config.get('module') == resource_id:
                            datasets = []
                            functions = config.get('functions', {})

                            for func_name, func_config in functions.items():
                                processing_config = func_config.get(
                                    'processing'
                                )
                                if processing_config:
                                    datasets.append(
                                        {
                                            'name': func_name,
                                            'data_processing': processing_config,
                                        }
                                    )

                            return datasets

                except yaml.YAMLError as e:
                    self.logger.warning(
                        f'Could not parse config file {yaml_file}: {e}'
                    )
                    continue

            return []

        except OSError as e:
            self.logger.error(f'Could not read dataset configurations: {e}')
            # Fallback: scan bronze directory for datasets
            resource_dir = self.bronze_path / resource_id
            if resource_dir.exists():
                return [
                    {'name': d.name, 'data_processing': {}}
                    for d in resource_dir.iterdir()
                    if d.is_dir()
                ]
            return []

    def get_latest_bronze(
        self, resource_id: str, dataset_name: str
    ) -> Path | None:
        """Get the latest bronze parquet file for a dataset."""
        dataset_dir = self.bronze_path / resource_id / dataset_name

        if not dataset_dir.exists():
            return None

        parquet_files = list(dataset_dir.glob('*.parquet'))
        if not parquet_files:
            return None

        # Sort by filename (timestamp) and return the latest
        return sorted(parquet_files)[-1]

    def _transform_and_write_parquet(
        self,
        resource_info: dict[str, Any],
        dataset_name: str,
        bronze_file: Path,
        target_table: str,
        data_processing: dict[str, Any],
    ) -> int:
        """Transform data from bronze parquet and write to silver Parquet file."""
        # dataset_name is kept for interface consistency but not used in current implementation
        source_database = resource_info['id']
        output_path = (
            self.silver_data_path / target_table / f'{source_database}.parquet'
        )

        self.logger.info(f'Transforming {bronze_file} -> {output_path}')

        # Check if parquet file exists
        if not bronze_file.exists():
            self.logger.warning(
                f'Bronze parquet file {bronze_file} does not exist'
            )
            return 0

        # Get available columns for validation
        available_columns = self._get_available_columns(bronze_file)
        if not available_columns:
            self.logger.warning(f'No columns found in {bronze_file}')
            return 0

        # Get field mappings from data_processing
        field_mappings = data_processing.get('field_mapping', [])

        # Get all columns for the silver table from YAML definitions
        if target_table not in self.table_definitions:
            raise SilverLoaderError(f'Unknown target table: {target_table}')

        silver_columns = list(self.table_definitions[target_table].keys())

        # Build SELECT clause
        select_parts = []

        # Build expression for each silver column
        for col in silver_columns:
            if col == 'loaded_at':
                # Set loaded_at to current timestamp
                select_parts.append(f'CURRENT_TIMESTAMP as "{col}"')
            elif col == 'source_database':
                # Always set source_database to the resource ID
                select_parts.append(f'\'{source_database}\' as "{col}"')
            else:
                expr = self._build_column_expression(
                    col, field_mappings, available_columns, target_table
                )
                select_parts.append(f'{expr} as "{col}"')

        # Ensure output directory exists
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # Build and execute COPY query to write Parquet
        select_clause = ',\n            '.join(select_parts)

        transform_sql = f"""
            COPY (
                SELECT
                    {select_clause}
                FROM read_parquet('{bronze_file}')
            ) TO '{output_path}' (FORMAT PARQUET, COMPRESSION ZSTD)
        """

        self.logger.debug(f'Executing SQL:\n{transform_sql}')

        try:
            self.execute_sql(transform_sql)

            # Get row count from the written file
            row_count = self.execute_sql(f"""
                SELECT COUNT(*) FROM read_parquet('{output_path}')
            """).fetchone()[0]

            self.logger.info(
                f'Wrote {self.format_row_count(row_count)} rows to {output_path}'
            )
            return row_count

        except Exception as e:
            self.logger.error(f'SQL execution failed: {e}')
            raise SilverLoaderError(f'Transform failed: {e}') from e

    def _get_available_columns(self, bronze_file: Path) -> set[str]:
        """Get available columns from bronze parquet file."""
        try:
            result = self.execute_sql(f"""
                SELECT * FROM read_parquet('{bronze_file}') LIMIT 0
            """).description

            columns = {desc[0] for desc in result}
            self.logger.debug(f'Available columns in {bronze_file}: {columns}')
            return columns

        except RuntimeError as e:
            self.logger.warning(
                f'Could not get column names from {bronze_file}: {e}'
            )
            return set()

    def _build_column_expression(
        self,
        column_name: str,
        field_mappings: list[dict[str, Any]],
        available_columns: set[str],
        target_table: str,
    ) -> str:
        """Build SQL expression for a single column from field mapping list."""
        # Find mapping for this target column
        for mapping in field_mappings:
            if mapping.get('target') == column_name:
                source = mapping.get('source')
                transform = mapping.get('transform')

                if source == '_constant':
                    # Constant value mapping
                    value = mapping.get('value', '')
                    if isinstance(value, bool):
                        return 'TRUE' if value else 'FALSE'
                    elif isinstance(value, int | float):
                        return str(value)
                    else:
                        return f"'{value}'"
                elif source == '_metadata':
                    # Metadata mapping
                    value = mapping.get('value', '')
                    if value == 'current_timestamp':
                        return 'CURRENT_TIMESTAMP'
                    else:
                        return f"'{value}'"
                elif source in available_columns:
                    # Regular field mapping
                    if transform:
                        return self._build_transform_expression(
                            source, transform, mapping
                        )
                    else:
                        # Check if target column is numeric and handle empty strings
                        target_column_type = self.table_definitions[
                            target_table
                        ].get(column_name, '')
                        if any(
                            numeric_type in target_column_type.upper()
                            for numeric_type in [
                                'FLOAT',
                                'INTEGER',
                                'NUMERIC',
                                'DECIMAL',
                            ]
                        ):
                            return f"""CASE WHEN TRIM("{source}"::TEXT) = \'\' OR "{source}" IS NULL THEN NULL
                            ELSE "{source}"
                            END"""
                        else:
                            return f'"{source}"'
                else:
                    # Source field not available
                    return 'NULL'

        # No mapping found for this target column
        return 'NULL'

    def _build_transform_expression(
        self, source_field: str, transform_func: str, mapping: dict[str, Any]
    ) -> str:
        """Build SQL expression using transformation function."""
        # Get transform arguments from mapping
        transform_args = mapping.get('transform_args', {})

        if not transform_args:
            # Simple function call with just the field
            return f'{transform_func}("{source_field}")'

        # Build argument list for multi-parameter functions
        arg_parts = [
            f'"{source_field}"'
        ]  # First argument is always the source field

        # Add additional arguments from transform_args
        for _arg_name, arg_value in transform_args.items():
            if isinstance(arg_value, str):
                # Handle field references or string literals
                if arg_value.endswith('_field'):
                    # Column reference - remove _field suffix
                    column_name = arg_value[:-6]
                    arg_parts.append(f'"{column_name}"')
                else:
                    # String literal
                    arg_parts.append(f"'{arg_value}'")
            elif isinstance(arg_value, int | float):
                arg_parts.append(str(arg_value))
            elif isinstance(arg_value, bool):
                arg_parts.append('TRUE' if arg_value else 'FALSE')
            else:
                arg_parts.append(f"'{arg_value}'")

        return f'{transform_func}({", ".join(arg_parts)})'

    def validate_silver_data(self) -> None:
        """Validate silver Parquet files and show statistics."""
        self.logger.info('Validating silver Parquet data...')

        if not self.silver_data_path.exists():
            self.logger.warning(
                f'Silver data directory does not exist: {self.silver_data_path}'
            )
            return

        for table_dir in self.silver_data_path.iterdir():
            if not table_dir.is_dir():
                continue

            table_name = table_dir.name
            self.logger.info(f'\n{table_name.upper()} table:')

            try:
                parquet_files = list(table_dir.glob('*.parquet'))
                if not parquet_files:
                    self.logger.info('  No Parquet files found')
                    continue

                # Get row counts by source file
                total_rows = 0
                for parquet_file in parquet_files:
                    source_db = parquet_file.stem
                    row_count = self.execute_sql(f"""
                        SELECT COUNT(*) FROM read_parquet('{parquet_file}')
                    """).fetchone()[0]

                    self.logger.info(
                        f'  {source_db}: {self.format_row_count(row_count)} rows'
                    )
                    total_rows += row_count

                self.logger.info(
                    f'  Total: {self.format_row_count(total_rows)} rows'
                )

                # Show sample data from first file
                if parquet_files:
                    sample = self.execute_sql(f"""
                        SELECT * FROM read_parquet('{parquet_files[0]}') LIMIT 2
                    """).fetchall()

                    if sample:
                        self.logger.info('  Sample rows:')
                        cols = [desc[0] for desc in self.conn.description]
                        for row in sample:
                            self.logger.info(
                                f'    {dict(zip(cols, row, strict=False))}'
                            )

            except RuntimeError as e:
                self.logger.error(f'Failed to validate table {table_name}: {e}')
