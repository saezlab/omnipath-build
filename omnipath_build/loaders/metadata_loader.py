#!/usr/bin/env python3
"""Metadata Loader for PyPath-based configurations.

Loads YAML configuration files from databases/ structure into PostgreSQL using shared utilities.
"""

import sys
from typing import Any
from pathlib import Path

import yaml
import pandas as pd

# Add parent directory to path for utils import
sys.path.append(str(Path(__file__).parent.parent))

# Import utilities
from utils import BaseLoader, LoaderError, log_execution_time
from utils.constants import get_database_path

__all__ = [
    'MetadataLoader',
]


class MetadataLoader(BaseLoader):
    """Metadata Loader for PyPath-based configurations that loads YAML configurations to PostgreSQL.

    Always uses the databases/ directory structure and PyPathConfigValidator for validation.
    Inherits from BaseLoader for common functionality.
    """

    def __init__(self, database_name: str, db_connector: Any) -> None:  # noqa: ANN401
        """Initialize MetadataLoader with database name and connection.

        Args:
            database_name: Name of the database (e.g., 'omnipath')
            db_connector: Database connector instance
        """
        self.database_name = database_name
        super().__init__(db_connector=db_connector)

    def _initialize(self) -> None:
        """Initialize metadata-specific attributes."""
        # Use database-specific resource configs structure
        self.resource_configs_dir = (
            get_database_path(self.database_name) / 'resource'
        )

        # Validate that the resource configs directory exists
        if not self.resource_configs_dir.exists():
            raise LoaderError(
                f'Resource configs directory not found: {self.resource_configs_dir}'
            )

        self.logger.info(
            f'Using resource configs structure: {self.resource_configs_dir}'
        )

        # Create PostgreSQL schemas
        self._create_postgres_schemas()

        self.logger.info(
            f'Metadata loader initialized, resource configs dir: {self.resource_configs_dir}'
        )

    def _create_postgres_schemas(self) -> None:
        """Create required schemas in PostgreSQL."""
        try:
            self.create_schemas(['metadata'])
            self.logger.info('Created PostgreSQL metadata schema')
        except Exception as e:
            self.logger.error(f'Failed to create PostgreSQL schemas: {e}')
            raise LoaderError(f'Schema creation failed: {e}') from e

    def load(
        self, validate_only: bool = False, yaml_dir: str = None
    ) -> dict[str, int]:
        """Load all metadata into PostgreSQL.

        Args:
            validate_only: Only validate existing metadata without loading
            yaml_dir: Optional override for YAML directory path (should be within databases/ structure)

        Returns:
            Dict mapping table names to row counts
        """
        # Set resource configs directory if provided
        if yaml_dir:
            override_dir = Path(yaml_dir)
            if not override_dir.exists():
                raise LoaderError(
                    f'Resource configs directory not found: {override_dir}'
                )

            self.resource_configs_dir = override_dir
            self.logger.info(
                f'Using resource configs directory override: {self.resource_configs_dir}'
            )

        if validate_only:
            self.validate_metadata()
            return {}
        else:
            return self.load_all()

    @log_execution_time()
    def load_all(self) -> dict[str, int]:
        """Load all metadata into PostgreSQL."""
        self.logger.info('Starting metadata loading to PostgreSQL...')

        results = {}

        # Create schema and tables
        self._create_metadata_tables()

        # Load resources
        resources_count = self._load_resources()
        results['resources'] = resources_count

        # Validate results
        self.validate_metadata()

        self.logger.info('Metadata loading completed successfully')
        return results

    def _get_table_columns(self, table_name: str) -> dict[str, str]:
        """Get column definitions for a specific table from YAML."""
        tables_file = (
            get_database_path(self.database_name) / 'metadata' / 'tables.yaml'
        )
        if not tables_file.exists():
            raise LoaderError(
                f'Metadata tables definition file not found: {tables_file}'
            )

        with open(tables_file, encoding='utf-8') as f:
            tables_config = yaml.safe_load(f)

        if table_name not in tables_config:
            raise LoaderError(f"Table '{table_name}' not found in tables.yaml")

        return tables_config[table_name]

    def _get_insertable_columns(self, table_name: str) -> list:
        """Get list of columns that can be inserted (excluding auto-generated ones)."""
        columns = self._get_table_columns(table_name)
        insertable_columns = []

        for column_name, column_type in columns.items():
            # Skip columns with DEFAULT values (like created_at, updated_at)
            if 'DEFAULT' not in column_type.upper():
                insertable_columns.append(column_name)

        return insertable_columns

    def _build_insert_statement(
        self, table_name: str, dataframe_name: str
    ) -> str:
        """Build dynamic INSERT statement based on table definition and dataframe columns."""
        insertable_columns = self._get_insertable_columns(table_name)

        # Build column list for INSERT
        column_list = ', '.join(insertable_columns)
        select_list = ', '.join(insertable_columns)

        return f"""
            INSERT INTO pg.metadata.{table_name}
            ({column_list})
            SELECT {select_list}
            FROM {dataframe_name}
        """

    def _extract_value_for_column(
        self,
        column_name: str,
        config: dict[str, Any],
        context: str = 'resource',
    ) -> Any:  # noqa: ANN401
        """Generically extract a value for a given column from config data.

        Args:
            column_name: Name of the database column to populate
            config: Configuration dictionary to extract from
            context: Context hint ("resource" or "dataset" level)

        Returns:
            Extracted value or None if no suitable value found
        """
        # First check if there's a metadata section and the field is there
        if 'metadata' in config:
            metadata = config['metadata']
            # Handle list-based metadata structure
            if isinstance(metadata, list):
                # Merge list of dictionaries into a single dictionary
                merged_metadata = {}
                for item in metadata:
                    if isinstance(item, dict):
                        merged_metadata.update(item)
                metadata = merged_metadata

            if isinstance(metadata, dict) and column_name in metadata:
                return metadata[column_name]

        # Direct match strategy (for backward compatibility)
        if column_name in config:
            return config[column_name]

        # Common alias strategies based on column name
        alias_mappings = {
            'id': ['module', 'name'],
            'name': ['module', 'title', 'description'],
            'resource_id': ['module', 'id'],
            'target_table': ['processing.target_table'],
            'description': ['description', 'title'],
        }

        # Try aliases
        if column_name in alias_mappings:
            for alias in alias_mappings[column_name]:
                # Handle nested paths (e.g., "processing.target_table")
                if '.' in alias:
                    parts = alias.split('.')
                    value = config
                    try:
                        for part in parts:
                            value = value[part]
                        if value is not None:
                            return value
                    except (KeyError, TypeError):
                        continue
                # Handle direct aliases
                elif alias in config:
                    return config[alias]

        # For dataset context, try to extract from function config
        if context == 'dataset' and 'functions' in config:
            # This will be called per function, so config should already be the function config
            pass

        return None

    def _create_metadata_tables(self) -> None:
        """Create metadata tables in PostgreSQL from YAML definitions."""
        try:
            # Drop schema if it exists (full reset)
            self.execute_sql('DROP SCHEMA IF EXISTS pg.metadata CASCADE')

            # Create schema for metadata
            self.execute_sql('CREATE SCHEMA pg.metadata')

            # Load table definitions from YAML
            tables_file = (
                get_database_path(self.database_name)
                / 'metadata'
                / 'tables.yaml'
            )
            if not tables_file.exists():
                raise LoaderError(
                    f'Metadata tables definition file not found: {tables_file}'
                )

            with open(tables_file, encoding='utf-8') as f:
                tables_config = yaml.safe_load(f)

            # Create each table from YAML definition
            for table_name, columns in tables_config.items():
                column_defs = []
                for column_name, column_type in columns.items():
                    column_defs.append(f'{column_name} {column_type}')

                column_definitions = ',\n                    '.join(column_defs)

                create_table_sql = f"""
                CREATE TABLE pg.metadata.{table_name} (
                    {column_definitions}
                )
                """

                self.execute_sql(create_table_sql)
                self.logger.info(f'Created table: {table_name}')

            self.logger.info(
                'Created PostgreSQL metadata tables from YAML definitions'
            )

        except Exception as e:
            self.logger.error(f'Failed to create metadata tables: {e}')
            raise LoaderError(f'Table creation failed: {e}') from e

    def _load_resources(self) -> int:
        """Load resources from YAML files to PostgreSQL."""
        try:
            # Process YAML files
            resources_df = self._process_resource_files()

            resources_count = 0

            # Load resources
            if not resources_df.empty:
                # Register dataframe with DuckDB
                self.conn.register('temp_resources', resources_df)

                # Build dynamic INSERT statement
                insert_sql = self._build_insert_statement(
                    'resources', 'temp_resources'
                )
                self.execute_sql(insert_sql)

                # Cleanup
                self.conn.unregister('temp_resources')
                resources_count = len(resources_df)
                self.logger.info(
                    f'Loaded {resources_count} resources into PostgreSQL'
                )
            else:
                self.logger.warning('No resources found to load')

            return resources_count

        except Exception as e:
            self.logger.error(f'Failed to load resources: {e}')
            raise LoaderError(f'Resource loading failed: {e}') from e

    def _process_resource_files(self) -> pd.DataFrame:
        """Process all resource config files and return dataframe for resources."""
        resources_data = []

        # Get available columns from table schema
        resource_columns = self._get_table_columns('resources')

        # Process each config file in the directory
        for config_file in self.resource_configs_dir.glob('*.yaml'):
            self.logger.debug(f'Processing {config_file.name}...')

            try:
                config = self._load_yaml_file(config_file)

                # Skip if all values are still placeholders
                if self._has_unfilled_placeholders(config):
                    self.logger.debug(
                        f'Skipping {config_file.name} - has unfilled placeholders'
                    )
                    continue

                module_name = config.get('module')
                if not module_name:
                    self.logger.warning(
                        f"Config {config_file.name} missing 'module' field"
                    )
                    continue

                self.logger.info(
                    f'Processing config: {config_file.name} (module: {module_name})'
                )

                # Create resource entry using generic extraction
                resource = {}
                for column_name in resource_columns.keys():
                    value = self._extract_value_for_column(
                        column_name, config, 'resource'
                    )
                    if value is not None:
                        resource[column_name] = value

                resources_data.append(resource)

                # Create dataset entries for each function

            except yaml.YAMLError as e:
                self.logger.error(f'Error processing {config_file.name}: {e}')
                continue

        self.logger.info(
            f'Processed {len(resources_data)} resources from resource configs'
        )
        return pd.DataFrame(resources_data)

    def _load_yaml_file(self, file_path: Path) -> dict[str, Any]:
        """Load a YAML file and return its contents."""
        with open(file_path, encoding='utf-8') as f:
            return yaml.safe_load(f)

    def _has_unfilled_placeholders(self, config: dict[str, Any]) -> bool:
        """Check if config has unfilled '?' placeholders."""
        for func_config in config.get('functions', {}).values():
            # Check kwargs
            for value in func_config.get('kwargs', {}).values():
                if value == '?':
                    return True

            # Check processing
            processing = func_config.get('processing', {})
            if processing.get('target_table') == '?':
                return True

            for mapping in processing.get('field_mapping', []):
                if mapping.get('target') == '?':
                    return True

        return False

    def _normalize_to_list(self, value: Any) -> list:  # noqa: ANN401
        """Normalize field values that might be arrays or strings to a list.

        Args:
            value: The field value to normalize

        Returns:
            List of string values
        """
        if value is None:
            return [None]

        if isinstance(value, list):
            if len(value) == 0:
                return [None]
            return [str(v) for v in value]

        if isinstance(value, str):
            return [value]

        # Convert other types to string
        return [str(value)]

    def validate_metadata(self) -> None:
        """Validate loaded metadata and show statistics."""
        self.logger.info('Validating loaded metadata...')

        try:
            # Get resource count
            resource_count = self.execute_sql(
                'SELECT COUNT(*) FROM pg.metadata.resources'
            ).fetchone()[0]
            self.logger.info(f'Resources: {resource_count}')

            # Show sample resources (generic approach)
            self.logger.info('\nSample resources:')
            resource_columns = list(self._get_table_columns('resources').keys())
            column_list = ', '.join(resource_columns)

            resources = self.execute_sql(f"""
                SELECT {column_list}
                FROM pg.metadata.resources
                ORDER BY {resource_columns[0]}
                LIMIT 5
            """).fetchall()

            for resource in resources:
                # Display first few columns dynamically
                display_parts = []
                for i, col_name in enumerate(
                    resource_columns[:3]
                ):  # Show first 3 columns
                    if i < len(resource):
                        display_parts.append(f'{col_name}={resource[i]}')
                self.logger.info(f'  {", ".join(display_parts)}')

        except Exception as e:
            self.logger.error(f'Failed to validate metadata: {e}')
            raise LoaderError(f'Metadata validation failed: {e}') from e

    def get_resource_count(self) -> int:
        """Get total number of resources."""
        result = self.execute_sql(
            'SELECT COUNT(*) FROM pg.metadata.resources'
        ).fetchone()
        return result[0] if result else 0
