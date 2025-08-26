#!/usr/bin/env python3
"""Database Manager - Unified script to manage database lifecycle.

This script provides a single entry point for managing databases throughout their lifecycle:
- Initialize new databases with proper directory structure
- Load data through all pipeline stages (metadata → bronze → silver → gold)
- Update specific layers or modules
- Validate database integrity
- Show database status

Usage:
    # Initialize a new database
    python database_manager.py init --database omnipath

    # Load all data layers for a database
    python database_manager.py load --database omnipath

    # Update specific layer
    python database_manager.py update --database omnipath --layer bronze

    # Validate database
    python database_manager.py validate --database omnipath

    # Show database status
    python database_manager.py status --database omnipath
"""

import os
import sys
from typing import Any
import logging
from pathlib import Path
import argparse

import yaml

# Environment variables are loaded by uv run --env-file .env

# Add current directory to path
sys.path.append(str(Path(__file__).parent))

# Import loader classes
from utils.database import PostgresDuckDBConnector
from loaders.gold_loader import GoldLoader
from loaders.bronze_loader import PyPathBronzeLoader
from loaders.silver_loader import SilverLoader
from loaders.metadata_loader import MetadataLoader

__all__ = [
    'DatabaseLifecycleManager',
    'main',
]


class DatabaseLifecycleManager:
    """Manages the complete lifecycle of databases in the pipeline."""

    def __init__(self, database_name: str) -> None:
        """Initialize the database manager.

        Args:
            database_name: Name of the database to manage
        """
        self.database_name = database_name
        self.logger = logging.getLogger(self.__class__.__name__)

        # Database paths
        self.db_base_path = Path(__file__).parent / 'databases' / database_name
        self.metadata_path = self.db_base_path / 'metadata'
        self.resource_path = self.db_base_path / 'resource'
        self.bronze_data_path = self.db_base_path / 'bronze' / 'data'
        self.silver_path = self.db_base_path / 'silver'
        self.gold_path = self.db_base_path / 'gold'

        # Store connection configurations for lazy initialization
        self.pg_config = {
            'host': os.environ.get('POSTGRES_HOST', 'localhost'),
            'port': int(os.environ.get('POSTGRES_PORT', '5436')),
            'user': os.environ.get('POSTGRES_USER', 'postgres'),
            'password': os.environ.get('POSTGRES_PASSWORD', ''),
            'database': database_name,
        }

        self.duck_config = {
            'memory_limit': '4GB',
            'max_memory': '4GB',
            'threads': 4,
        }

        # Lazy initialization - connector will be created when first needed
        self._db_connector = None

        self.logger.info(f'Database manager initialized for: {database_name}')
        self.logger.info(
            f'PostgreSQL connection: {self.pg_config["user"]}@{self.pg_config["host"]}:{self.pg_config["port"]}'
        )

    @property
    def db_connector(self) -> PostgresDuckDBConnector:
        """Lazy initialization of database connector.

        Creates the connector only when first accessed.
        """
        if self._db_connector is None:
            self._db_connector = PostgresDuckDBConnector(
                pg_config=self.pg_config, duck_config=self.duck_config
            )
        return self._db_connector

    def init_database(self, force: bool = False) -> bool:
        """Initialize a new database with directory structure and PostgreSQL database.

        Args:
            force: If True, recreate database even if it exists

        Returns:
            True if successful, False otherwise
        """
        try:
            directory_exists = self.db_base_path.exists()

            if directory_exists and not force:
                self.logger.info(
                    f'Database directory exists, ensuring setup is complete: {self.database_name}'
                )
            elif directory_exists and force:
                self.logger.info(
                    f'Force recreating database: {self.database_name}'
                )
            else:
                self.logger.info(
                    f'Initializing new database: {self.database_name}'
                )

            # Create directory structure (safe with exist_ok=True)
            self._create_directory_structure()

            # Create template files (only if they don't exist)
            self._create_template_files()

            # Create PostgreSQL database (only if it doesn't exist)
            self._create_postgresql_database()

            if directory_exists and not force:
                self.logger.info(
                    f'Database {self.database_name} setup verified successfully!'
                )
            else:
                self.logger.info(
                    f'Database {self.database_name} initialized successfully!'
                )

            self.logger.info(f'Database directory: {self.db_base_path}')

            # Only show next steps for new databases or when forcing recreation
            if not directory_exists or force:
                self.logger.info('Next steps:')
                self.logger.info(
                    f'  1. Review and customize table definitions in {self.metadata_path}/tables.yaml'
                )
                self.logger.info(
                    f'  2. Add silver table definitions to {self.silver_path}/tables.yaml'
                )
                self.logger.info(
                    f'  3. Add transformation functions to {self.silver_path}/transformation_functions.sql'
                )
                self.logger.info(
                    f'  4. Add resources using: python database_manager.py add-resources --database {self.database_name} --resources <resource_list>'
                )
                self.logger.info(
                    f'  5. Run: python database_manager.py load --database {self.database_name}'
                )

            return True

        except (OSError, yaml.YAMLError, RuntimeError) as e:
            self.logger.error(f'Failed to initialize database: {e}')
            return False

    def load_database(self, layers: list[str] | None = None) -> bool:
        """Load data through all pipeline stages.

        Args:
            layers: List of layers to load. If None, loads all layers in order.

        Returns:
            True if successful, False otherwise
        """
        try:
            if not self.db_base_path.exists():
                self.logger.error(
                    f'Database directory not found: {self.db_base_path}'
                )
                self.logger.error(
                    "Run 'init' command first to initialize the database"
                )
                return False

            # Default layer order
            all_layers = ['metadata', 'bronze', 'silver', 'gold']
            layers_to_load = layers or all_layers

            self.logger.info(
                f'Loading database {self.database_name} - layers: {", ".join(layers_to_load)}'
            )

            results = {}

            for layer in layers_to_load:
                if layer not in all_layers:
                    self.logger.warning(f'Unknown layer: {layer}, skipping')
                    continue

                self.logger.info(f'Loading {layer} layer...')
                success = self._load_layer(layer)
                results[layer] = success

                if not success:
                    self.logger.error(
                        f'Failed to load {layer} layer, stopping pipeline'
                    )
                    return False

                self.logger.info(f'✓ {layer} layer loaded successfully')

            self.logger.info('Database loading completed successfully!')
            return True

        except (OSError, ImportError, RuntimeError) as e:
            self.logger.error(f'Failed to load database: {e}')
            return False

    def update_layer(self, layer: str, module: str | None = None) -> bool:
        """Update a specific layer or module within a layer.

        Args:
            layer: Layer to update (metadata, bronze, silver, gold)
            module: Specific module to update (for bronze layer)

        Returns:
            True if successful, False otherwise
        """
        try:
            self.logger.info(
                f'Updating {layer} layer'
                + (f' (module: {module})' if module else '')
            )

            return self._load_layer(layer, module)

        except (OSError, ImportError, RuntimeError) as e:
            self.logger.error(f'Failed to update {layer} layer: {e}')
            return False

    def validate_database(self) -> bool:
        """Validate all layers of the database.

        Returns:
            True if all validations pass, False otherwise
        """
        try:
            self.logger.info(f'Validating database: {self.database_name}')

            validation_results = {}

            # Validate metadata
            try:
                with MetadataLoader(
                    database_name=self.database_name,
                    db_connector=self.db_connector,
                ) as loader:
                    loader.load(validate_only=True)
                    validation_results['metadata'] = True
                    self.logger.info('✓ Metadata validation passed')
            except (ImportError, RuntimeError, OSError) as e:
                self.logger.error(f'✗ Metadata validation failed: {e}')
                validation_results['metadata'] = False

            # TODO: Add validation for other layers when available

            all_passed = all(validation_results.values())

            if all_passed:
                self.logger.info('✓ All validations passed')
            else:
                self.logger.error('✗ Some validations failed')

            return all_passed

        except (ImportError, RuntimeError) as e:
            self.logger.error(f'Failed to validate database: {e}')
            return False

    def show_status(self) -> bool:
        """Show the current status of the database.

        Returns:
            True if status retrieved successfully
        """
        try:
            self.logger.info(f'Database Status: {self.database_name}')
            self.logger.info('=' * 50)

            # Check directory structure
            self.logger.info(f'Base directory: {self.db_base_path}')
            self.logger.info(
                f'  Exists: {"✓" if self.db_base_path.exists() else "✗"}'
            )

            for subdir, name in [
                (self.metadata_path, 'metadata'),
                (self.resource_path, 'resource'),
                (self.bronze_data_path, 'bronze/data'),
                (self.silver_path, 'silver'),
                (self.gold_path, 'gold'),
            ]:
                exists = subdir.exists()
                file_count = len(list(subdir.glob('*'))) if exists else 0
                self.logger.info(
                    f'  {name}: {"✓" if exists else "✗"} ({file_count} files)'
                )

            # Check PostgreSQL database using the unified connector
            try:
                result = self.db_connector.execute(
                    'SELECT current_database()'
                ).fetchone()
                self.logger.info(f'PostgreSQL database: ✓ ({result[0]})')

                # Check for schemas
                result = self.db_connector.execute(
                    "SELECT schema_name FROM information_schema.schemata WHERE schema_name = 'metadata'"
                ).fetchall()

                self.logger.info(f'Metadata schema: {"✓" if result else "✗"}')

            except (OSError, RuntimeError) as e:
                self.logger.info(f'PostgreSQL database: ✗ ({str(e)[:50]}...)')

            return True

        except (OSError, RuntimeError) as e:
            self.logger.error(f'Failed to get database status: {e}')
            return False

    def _create_directory_structure(self) -> None:
        """Create the database directory structure."""
        directories = [
            self.metadata_path,
            self.resource_path,
            self.bronze_data_path,
            self.silver_path,
            self.gold_path,
        ]

        for directory in directories:
            directory.mkdir(parents=True, exist_ok=True)
            self.logger.debug(f'Created directory: {directory}')

    def _create_template_files(self) -> None:
        """Create template configuration files for the database."""
        try:
            # Create metadata/tables.yaml with minimal schema
            metadata_tables_file = self.metadata_path / 'tables.yaml'
            if not metadata_tables_file.exists():
                metadata_content = f"""# Metadata table definitions for {self.database_name}
# Define the structure of tables that will be created in the metadata schema

resources:
  name: VARCHAR
  description: TEXT
"""

                with open(metadata_tables_file, 'w', encoding='utf-8') as f:
                    f.write(metadata_content)
                self.logger.info(
                    f'Created metadata tables template: {metadata_tables_file}'
                )

            # Create silver/tables.yaml (empty template)
            silver_tables_file = self.silver_path / 'tables.yaml'
            if not silver_tables_file.exists():
                silver_content = f"""# Silver layer table definitions for {self.database_name}
# Define the structure of tables that will be created in the silver schema
# Example:
# my_table:
#   column1: VARCHAR
#   column2: INTEGER
#   column3: TEXT
"""

                with open(silver_tables_file, 'w', encoding='utf-8') as f:
                    f.write(silver_content)
                self.logger.info(
                    f'Created silver tables template: {silver_tables_file}'
                )

            # Create silver/transformation_functions.sql (empty template)
            silver_functions_file = (
                self.silver_path / 'transformation_functions.sql'
            )
            if not silver_functions_file.exists():
                functions_content = f"""-- Transformation functions for {self.database_name} silver layer
-- Define SQL functions that will be used to transform data from bronze to silver

-- Example function:
-- CREATE OR REPLACE FUNCTION clean_protein_name(raw_name TEXT)
-- RETURNS TEXT AS $$
-- BEGIN
--     RETURN TRIM(UPPER(raw_name));
-- END;
-- $$ LANGUAGE plpgsql;
"""

                with open(silver_functions_file, 'w', encoding='utf-8') as f:
                    f.write(functions_content)
                self.logger.info(
                    f'Created silver transformation functions template: {silver_functions_file}'
                )

        except (OSError, yaml.YAMLError) as e:
            self.logger.warning(f'Failed to create template files: {e}')

    def _create_postgresql_database(self) -> None:
        """Create PostgreSQL database if it doesn't exist."""
        try:
            import psycopg2
            from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

            # Connect to the 'postgres' database for administrative operations
            connection_params = {
                'host': self.pg_config['host'],
                'port': self.pg_config['port'],
                'user': self.pg_config['user'],
                'password': self.pg_config['password'],
                'database': 'postgres',  # Connect to default postgres database
            }

            # Remove empty password to avoid connection issues
            if not connection_params['password']:
                del connection_params['password']

            conn = psycopg2.connect(**connection_params)
            conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)

            with conn.cursor() as cursor:
                # Check if database exists
                cursor.execute(
                    'SELECT 1 FROM pg_database WHERE datname = %s',
                    [self.database_name],
                )
                result = cursor.fetchone()

                if not result:
                    # Create database
                    cursor.execute(f'CREATE DATABASE "{self.database_name}"')
                    self.logger.info(
                        f'Created PostgreSQL database: {self.database_name}'
                    )
                else:
                    self.logger.info(
                        f'PostgreSQL database already exists: {self.database_name}'
                    )

            conn.close()

        except (OSError, RuntimeError, ImportError) as e:
            self.logger.warning(f'Could not create PostgreSQL database: {e}')
            self.logger.info(
                'You may need to create it manually or ensure PostgreSQL is running'
            )

    def _load_layer(self, layer: str, module: str | None = None) -> bool:
        """Load a specific layer using the appropriate loader class.

        Args:
            layer: Layer name (metadata, bronze, silver, gold)
            module: Optional module name for bronze layer

        Returns:
            True if successful, False otherwise
        """
        try:
            if layer == 'metadata':
                # Create a separate connection for this loader
                metadata_connector = PostgresDuckDBConnector(
                    pg_config=self.pg_config, duck_config=self.duck_config
                )
                with MetadataLoader(
                    database_name=self.database_name,
                    db_connector=metadata_connector,
                ) as loader:
                    results = loader.load()
                    self.logger.info(
                        f'Metadata loader completed: {sum(results.values())} items loaded'
                    )

            elif layer == 'bronze':
                # Create a separate connection for this loader
                bronze_connector = PostgresDuckDBConnector(
                    pg_config=self.pg_config, duck_config=self.duck_config
                )
                with PyPathBronzeLoader(
                    database_name=self.database_name,
                    db_connector=bronze_connector,
                ) as loader:
                    if module:
                        results = loader.load(module_name=module)
                    else:
                        results = loader.load()  # Load all configured modules
                    total_rows = sum(v for v in results.values() if v > 0)
                    self.logger.info(
                        f'Bronze loader completed: {loader.format_row_count(total_rows)} rows loaded'
                    )

            elif layer == 'silver':
                # Create a separate connection for this loader
                silver_connector = PostgresDuckDBConnector(
                    pg_config=self.pg_config, duck_config=self.duck_config
                )
                with SilverLoader(
                    database_name=self.database_name,
                    db_connector=silver_connector,
                ) as loader:
                    results = loader.load()
                    total_rows = sum(results.values())
                    self.logger.info(
                        f'Silver loader completed: {loader.format_row_count(total_rows)} rows loaded'
                    )

            elif layer == 'gold':
                # Create a separate connection for this loader
                gold_connector = PostgresDuckDBConnector(
                    pg_config=self.pg_config, duck_config=self.duck_config
                )
                with GoldLoader(
                    database_name=self.database_name,
                    db_connector=gold_connector,
                ) as loader:
                    results = loader.load()
                    if results.get('total_rows'):
                        self.logger.info(
                            f'Gold loader completed: {loader.format_row_count(results["total_rows"])} rows processed'
                        )
                    else:
                        self.logger.info('Gold loader completed')

            else:
                self.logger.error(f'Unknown layer: {layer}')
                return False

            return True

        except (ImportError, RuntimeError, OSError) as e:
            self.logger.error(f'Failed to load {layer} layer: {e}')
            return False

    def add_resources(self, resources: list[str]) -> bool:
        """Add resource configurations from templates with metadata fields integrated.

        Args:
            resources: List of resource names to add

        Returns:
            True if successful, False otherwise
        """
        try:
            self.logger.info(
                f'Adding resources to {self.database_name}: {", ".join(resources)}'
            )

            # Check if database directory exists
            if not self.db_base_path.exists():
                self.logger.error(
                    f'Database directory not found: {self.db_base_path}'
                )
                self.logger.error(
                    "Run 'init' command first to initialize the database"
                )
                return False

            # Check if metadata tables exist
            metadata_tables_file = self.metadata_path / 'tables.yaml'
            if not metadata_tables_file.exists():
                self.logger.error(
                    f'Metadata tables definition not found: {metadata_tables_file}'
                )
                self.logger.error(
                    'Please define metadata tables first in metadata/tables.yaml'
                )
                return False

            # Load metadata table definitions
            with open(metadata_tables_file, encoding='utf-8') as f:
                metadata_tables = yaml.safe_load(f)

            # Get resource metadata fields (excluding auto-generated fields)
            resource_fields = self._get_insertable_metadata_fields(
                metadata_tables.get('resources', {})
            )

            # Import on-demand template generator
            from utils.simple_template_generator import generate_pypath_template

            # Ensure resource directory exists
            self.resource_path.mkdir(parents=True, exist_ok=True)

            # Process each resource
            added_count = 0
            for resource in resources:
                # Extract module name for file naming
                module_name = (
                    resource.split('.')[0] if '.' in resource else resource
                )
                output_file = self.resource_path / f'{module_name}.yaml'

                # Generate template on-demand
                self.logger.info(f'Generating template for {resource}...')
                new_template = generate_pypath_template(resource)

                if not new_template:
                    self.logger.warning(
                        f'Could not generate template for resource: {resource}'
                    )
                    self.logger.warning(
                        f"Make sure '{resource}' is a valid PyPath resource name"
                    )
                    continue

                # Handle incremental addition to existing files
                if output_file.exists():
                    self.logger.info(
                        f'Adding function to existing module config: {module_name}'
                    )
                    enhanced_config = self._merge_with_existing_config(
                        output_file, new_template, resource_fields
                    )
                else:
                    self.logger.info(
                        f'Creating new module config: {module_name}'
                    )
                    enhanced_config = self._add_metadata_fields_to_template(
                        new_template, resource_fields
                    )

                if enhanced_config:
                    # Save enhanced config
                    self._save_resource_config(
                        enhanced_config, output_file, resource
                    )
                    added_count += 1
                    self.logger.info(
                        f'Added resource configuration: {resource}'
                    )
                else:
                    self.logger.warning(
                        f'Failed to process resource: {resource}'
                    )

            if added_count > 0:
                self.logger.info(
                    f'Successfully added {added_count} resource configurations'
                )
                self.logger.info('Next steps:')
                self.logger.info(
                    f'  1. Edit resource configs in {self.resource_path}/'
                )
                self.logger.info(
                    '  2. Fill in metadata fields and field mappings'
                )
                self.logger.info(
                    f'  3. Run: python database_manager.py load --database {self.database_name}'
                )
                return True
            else:
                self.logger.error('No resources were added')
                return False

        except (OSError, yaml.YAMLError, ImportError, RuntimeError) as e:
            self.logger.error(f'Failed to add resources: {e}')
            return False

    def _get_insertable_metadata_fields(
        self, table_def: dict[str, str]
    ) -> list[str]:
        """Get list of metadata fields that can be inserted (excluding auto-generated ones)."""
        insertable_fields = []
        for field_name, field_type in table_def.items():
            # Skip fields with DEFAULT values (like created_at, updated_at)
            if 'DEFAULT' not in field_type.upper():
                insertable_fields.append(field_name)
        return insertable_fields

    def _add_metadata_fields_to_template(
        self, template: dict[str, Any], metadata_fields: list[str]
    ) -> dict[str, Any]:
        """Add metadata fields to template with placeholder values."""
        # Create a new dict with metadata at the top
        enhanced = {}

        # Add metadata section at the top with placeholder values
        if metadata_fields:
            metadata_section = {}
            for field in metadata_fields:
                metadata_section[field] = '?'  # User must fill in
            enhanced['metadata'] = metadata_section

        # Add the rest of the template
        enhanced.update(template)

        return enhanced

    def _merge_with_existing_config(
        self,
        existing_file: Path,
        new_template: dict[str, Any],
        resource_fields: list[str],
    ) -> dict[str, Any] | None:
        """Merge new function template with existing module configuration.

        Args:
            existing_file: Path to existing YAML file
            new_template: New template with function(s) to add
            resource_fields: Metadata fields to include

        Returns:
            Merged configuration, or None if merge failed
        """
        try:
            # Load existing config
            with open(existing_file, encoding='utf-8') as f:
                existing_config = yaml.safe_load(f)

            # Ensure existing config has the right structure
            if not existing_config or 'functions' not in existing_config:
                self.logger.error(
                    f'Invalid existing config structure in {existing_file}'
                )
                return None

            # Merge new functions into existing config
            for func_name, func_info in new_template.get(
                'functions', {}
            ).items():
                if func_name in existing_config['functions']:
                    self.logger.warning(
                        f'Function {func_name} already exists in {existing_file}, skipping'
                    )
                    continue

                existing_config['functions'][func_name] = func_info
                self.logger.info(
                    f'Added function {func_name} to existing config'
                )

            # Ensure metadata fields are present (they might not be in old configs)
            if 'metadata' not in existing_config:
                existing_config['metadata'] = {}

            for field in resource_fields:
                if field not in existing_config['metadata']:
                    existing_config['metadata'][field] = '?'

            return existing_config

        except (OSError, yaml.YAMLError, KeyError) as e:
            self.logger.error(
                f'Failed to merge with existing config {existing_file}: {e}'
            )
            return None

    def _save_resource_config(
        self, config: dict[str, Any], output_file: Path, resource_name: str
    ) -> None:
        """Save enhanced resource configuration to YAML file."""
        with open(output_file, 'w', encoding='utf-8') as f:
            # Write header comment
            f.write(f'# Resource Configuration for {resource_name}\n')
            f.write(f'# Generated from pypath_templates/{resource_name}.yaml\n')
            f.write(f'# Database: {self.database_name}\n\n')
            f.write(
                '# REQUIRED: Fill in all question marks (?) before loading\n\n'
            )

            # Write YAML content
            yaml.dump(
                config,
                f,
                default_flow_style=False,
                sort_keys=False,
                indent=2,
                width=120,
            )

    def close(self) -> None:
        """Close database connections and cleanup resources."""
        try:
            if self._db_connector is not None:
                self._db_connector.close()
                self.logger.info('Database connections closed successfully')
            else:
                self.logger.debug('No database connection to close')
        except (OSError, RuntimeError) as e:
            self.logger.warning(f'Error closing database connections: {e}')


def main() -> None:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description='Database Manager - Manage database lifecycle',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Initialize new database
  python database_manager.py init --database omnipath

  # Add resources from templates
  python database_manager.py add-resources --database omnipath --resources signor,biogrid

  # Load all data layers
  python database_manager.py load --database omnipath

  # Update specific layer
  python database_manager.py update --database omnipath --layer bronze

  # Validate database
  python database_manager.py validate --database omnipath

  # Show status
  python database_manager.py status --database omnipath
        """,
    )

    parser.add_argument(
        'command',
        choices=[
            'init',
            'add-resources',
            'load',
            'update',
            'validate',
            'status',
        ],
        help='Command to execute',
    )

    parser.add_argument(
        '--database', '-d', required=True, help='Database name (e.g., omnipath)'
    )

    parser.add_argument(
        '--layer',
        choices=['metadata', 'bronze', 'silver', 'gold'],
        help='Specific layer to update (for update command)',
    )

    parser.add_argument(
        '--layers',
        nargs='+',
        choices=['metadata', 'bronze', 'silver', 'gold'],
        help='Layers to load (for load command)',
    )

    parser.add_argument(
        '--module', help='Specific module to update (for bronze layer)'
    )

    parser.add_argument(
        '--resources',
        help='Comma-separated list of resources to add (for add-resources command)',
    )

    parser.add_argument(
        '--force',
        action='store_true',
        help='Force operation even if database exists',
    )

    parser.add_argument(
        '--log-level',
        default='INFO',
        choices=['DEBUG', 'INFO', 'WARNING', 'ERROR'],
        help='Set logging level',
    )

    args = parser.parse_args()

    # Setup logging
    log_level = getattr(logging, args.log_level.upper())
    logging.basicConfig(
        level=log_level,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[logging.StreamHandler()],
    )

    # Create manager
    manager = DatabaseLifecycleManager(args.database)

    # Execute command
    try:
        success = False

        if args.command == 'init':
            success = manager.init_database(force=args.force)

        elif args.command == 'add-resources':
            if not args.resources:
                parser.error(
                    '--resources is required for add-resources command'
                )
            resources = [r.strip() for r in args.resources.split(',')]
            success = manager.add_resources(resources)

        elif args.command == 'load':
            success = manager.load_database(layers=args.layers)

        elif args.command == 'update':
            if not args.layer:
                parser.error('--layer is required for update command')
            success = manager.update_layer(args.layer, args.module)

        elif args.command == 'validate':
            success = manager.validate_database()

        elif args.command == 'status':
            success = manager.show_status()

    except KeyboardInterrupt:
        logging.getLogger().info('Operation cancelled by user')
        success = False
    except (OSError, RuntimeError, ImportError) as e:
        logging.getLogger().error(f'Unexpected error: {e}')
        success = False
    finally:
        # Always close the database connection
        manager.close()

    sys.exit(0 if success else 1)


if __name__ == '__main__':
    main()
