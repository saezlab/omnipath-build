#!/usr/bin/env python3
"""Simplified Database Manager for Parquet-based Pipeline.

This manager handles the parquet-based workflow:
- Initialize new databases with directory structure
- Load bronze data (PyPath → Parquet)
- Process sources through silver transformations
- Build gold parquet files

No PostgreSQL required - everything works with Parquet files.

Usage:
    # Initialize a new database
    python database_manager.py init --database metabo

    # Add resources from PyPath
    python database_manager.py add-resources --database metabo --resources hmdb,chebi

    # Load bronze data (PyPath → Parquet)
    python database_manager.py load-bronze --database metabo

    # Process sources (Bronze → Silver → Gold Parquet)
    python database_manager.py process --database metabo

    # Show database status
    python database_manager.py status --database metabo
"""

import argparse
import logging
import sys
from pathlib import Path
from typing import Any

import yaml

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent))
from utils import PathManager

__all__ = [
    'ParquetDatabaseManager',
    'main',
]


class ParquetDatabaseManager:
    """Manages parquet-based database lifecycle."""

    def __init__(self, database_name: str, base_path: Path | None = None) -> None:
        """Initialize database manager.

        Args:
            database_name: Name of the database (e.g., 'metabo')
            base_path: Base path for databases (defaults to ../databases)
        """
        self.database_name = database_name
        self.logger = logging.getLogger(self.__class__.__name__)

        # Use PathManager for all paths
        self.path_manager = PathManager(database_name, base_path)
        self.db_base_path = self.path_manager.db_path
        self.resource_path = self.path_manager.resource_path()
        self.bronze_path = self.path_manager.bronze_data_path()
        self.silver_path = self.path_manager.silver_config_path()
        self.silver_parquet_path = self.path_manager.silver_parquet_path()
        self.gold_parquet_path = self.path_manager.gold_parquet_path()

        self.logger.info(f'Database manager initialized for: {database_name}')
        self.logger.info(f'Base path: {self.db_base_path}')

    def init_database(self, force: bool = False) -> bool:
        """Initialize a new database with directory structure.

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
                self.logger.info(f'Force recreating database: {self.database_name}')
            else:
                self.logger.info(f'Initializing new database: {self.database_name}')

            # Create directory structure
            self._create_directory_structure()

            # Create template files
            self._create_template_files()

            if directory_exists and not force:
                self.logger.info(
                    f'Database {self.database_name} setup verified successfully!'
                )
            else:
                self.logger.info(
                    f'Database {self.database_name} initialized successfully!'
                )

            self.logger.info(f'Database directory: {self.db_base_path}')

            # Show next steps for new databases
            if not directory_exists or force:
                self.logger.info('Next steps:')
                self.logger.info(
                    f'  1. Add resources: python database_manager.py add-resources --database {self.database_name} --resources <resource_list>'
                )
                self.logger.info(
                    f'  2. Configure silver transformations in {self.silver_path}/transformation_functions.sql'
                )
                self.logger.info(
                    f'  3. Load bronze data: python database_manager.py load-bronze --database {self.database_name}'
                )
                self.logger.info(
                    f'  4. Process sources: python database_manager.py process --database {self.database_name}'
                )

            return True

        except (OSError, yaml.YAMLError, RuntimeError) as e:
            self.logger.error(f'Failed to initialize database: {e}')
            return False

    def load_bronze(
        self, module: str | None = None, force: bool = False
    ) -> bool:
        """Load bronze data from PyPath to Parquet files.

        Args:
            module: Specific module to load (None = all configured modules)
            force: Force re-download even if parquet exists

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

            self.logger.info(f'Loading bronze data for {self.database_name}')

            # Import bronze loader
            from new_loaders.bronze_loader import PyPathBronzeLoader

            # Note: bronze_loader still uses db_connector for DuckDB operations
            # We create a minimal config for DuckDB-only usage
            duck_config = {
                'memory_limit': '4GB',
                'max_memory': '4GB',
                'threads': 4,
            }

            # Create a simple connector wrapper for DuckDB
            class DuckDBOnlyConnector:
                def __init__(self, config):
                    import duckdb
                    self.conn = duckdb.connect(':memory:')
                    for key, value in config.items():
                        self.conn.execute(f"SET {key}='{value}'")
                    self.pg_config = {}  # Empty for compatibility

                def execute(self, sql, params=None):
                    return self.conn.execute(sql, params or [])

                def close(self):
                    if self.conn:
                        self.conn.close()

            connector = DuckDBOnlyConnector(duck_config)

            try:
                with PyPathBronzeLoader(
                    database_name=self.database_name,
                    db_connector=connector,
                ) as loader:
                    results = loader.load(
                        module_name=module,
                        force=force,
                    )
                    total_rows = sum(v for v in results.values() if v > 0)
                    self.logger.info(
                        f'✓ Bronze loading completed: {total_rows:,} rows loaded'
                    )
                    return True
            finally:
                connector.close()

        except (ImportError, RuntimeError, OSError) as e:
            self.logger.error(f'Failed to load bronze data: {e}')
            return False

    def process_sources(self, source: str | None = None) -> bool:
        """Process sources through silver and gold transformations.

        Args:
            source: Specific source module to process (None = all sources)

        Returns:
            True if successful, False otherwise
        """
        try:
            if not self.db_base_path.exists():
                self.logger.error(
                    f'Database directory not found: {self.db_base_path}'
                )
                return False

            self.logger.info(
                f'Processing sources for {self.database_name}'
                + (f' (source: {source})' if source else '')
            )

            # Get list of sources to process
            if source:
                sources = [source]
            else:
                sources = self._get_configured_sources()

            if not sources:
                self.logger.error('No sources found to process')
                return False

            # Import source processor
            from new_loaders.source_processor import SourceProcessor

            # Process each source
            for source_name in sources:
                self.logger.info(f'Processing source: {source_name}')
                try:
                    with SourceProcessor(
                        database_name=self.database_name,
                        source_module=source_name,
                        base_path=self.path_manager.base_path,
                    ) as processor:
                        results = processor.process_full_pipeline()
                        silver_count = len(results.get('silver', {}))
                        gold_count = len(results.get('gold', {}))
                        self.logger.info(f'✓ Completed: {source_name} ({silver_count} silver, {gold_count} gold tables)')
                except (RuntimeError, OSError) as e:
                    self.logger.error(f'Failed to process {source_name}: {e}')
                    return False

            self.logger.info('✓ All sources processed successfully')
            return True

        except (ImportError, RuntimeError, OSError) as e:
            self.logger.error(f'Failed to process sources: {e}')
            return False

    def show_status(self) -> bool:
        """Show the current status of the database.

        Returns:
            True if status retrieved successfully
        """
        try:
            self.logger.info(f'Database Status: {self.database_name}')
            self.logger.info('=' * 60)

            # Check directory structure
            self.logger.info(f'Base directory: {self.db_base_path}')
            self.logger.info(
                f'  Exists: {"✓" if self.db_base_path.exists() else "✗"}'
            )

            # Check subdirectories
            for subdir, name in [
                (self.resource_path, 'resource configs'),
                (self.bronze_path, 'bronze parquet'),
                (self.silver_path, 'silver config'),
                (self.silver_parquet_path, 'silver parquet'),
                (self.gold_parquet_path, 'gold parquet'),
            ]:
                exists = subdir.exists()
                file_count = len(list(subdir.glob('*'))) if exists else 0
                self.logger.info(
                    f'  {name}: {"✓" if exists else "✗"} ({file_count} files)'
                )

            # Check configured sources
            sources = self._get_configured_sources()
            if sources:
                self.logger.info(f'Configured sources: {len(sources)}')
                for source in sources:
                    self.logger.info(f'  - {source}')

            # Check bronze data
            if self.bronze_path.exists():
                bronze_modules = [
                    d.name for d in self.bronze_path.iterdir() if d.is_dir()
                ]
                if bronze_modules:
                    self.logger.info(f'Bronze data modules: {len(bronze_modules)}')
                    for module in bronze_modules:
                        module_path = self.bronze_path / module
                        functions = [
                            d.name for d in module_path.iterdir() if d.is_dir()
                        ]
                        self.logger.info(
                            f'  - {module}: {len(functions)} functions'
                        )

            return True

        except (OSError, RuntimeError) as e:
            self.logger.error(f'Failed to get database status: {e}')
            return False

    def add_resources(self, resources: list[str]) -> bool:
        """Add resource configurations from PyPath templates.

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

            # Import template generator
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
                output_file = self.path_manager.resource_config_file(module_name)

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
                        output_file, new_template
                    )
                else:
                    self.logger.info(f'Creating new module config: {module_name}')
                    enhanced_config = new_template

                if enhanced_config:
                    # Save config
                    self._save_resource_config(
                        enhanced_config, output_file, resource
                    )
                    added_count += 1
                    self.logger.info(f'Added resource configuration: {resource}')
                else:
                    self.logger.warning(f'Failed to process resource: {resource}')

            if added_count > 0:
                self.logger.info(
                    f'Successfully added {added_count} resource configurations'
                )
                self.logger.info('Next steps:')
                self.logger.info(f'  1. Edit resource configs in {self.resource_path}/')
                self.logger.info('  2. Configure field mappings if needed')
                self.logger.info(
                    f'  3. Run: python database_manager.py load-bronze --database {self.database_name}'
                )
                return True
            else:
                self.logger.error('No resources were added')
                return False

        except (OSError, yaml.YAMLError, ImportError, RuntimeError) as e:
            self.logger.error(f'Failed to add resources: {e}')
            return False

    def _create_directory_structure(self) -> None:
        """Create the database directory structure."""
        directories = [
            self.resource_path,
            self.bronze_path,
            self.silver_path,
            self.silver_parquet_path,
            self.gold_parquet_path,
        ]

        for directory in directories:
            directory.mkdir(parents=True, exist_ok=True)
            self.logger.debug(f'Created directory: {directory}')

    def _create_template_files(self) -> None:
        """Create template configuration files for the database."""
        try:
            # Create silver/transformation_functions.sql (empty template)
            silver_functions_file = self.path_manager.transformation_functions_file()
            if not silver_functions_file.exists():
                functions_content = f"""-- Transformation functions for {self.database_name} silver layer
-- Define SQL functions that will be used to transform data from bronze to silver
-- These functions are loaded into DuckDB for transformations

-- Example function:
-- CREATE OR REPLACE FUNCTION clean_protein_name(raw_name TEXT)
-- RETURNS TEXT AS (
--     TRIM(UPPER(raw_name))
-- );
"""
                with open(silver_functions_file, 'w', encoding='utf-8') as f:
                    f.write(functions_content)
                self.logger.info(
                    f'Created silver transformation functions template: {silver_functions_file}'
                )

        except (OSError, yaml.YAMLError) as e:
            self.logger.warning(f'Failed to create template files: {e}')

    def _get_configured_sources(self) -> list[str]:
        """Get list of configured sources from YAML files."""
        try:
            if not self.resource_path.exists():
                return []

            sources = []
            for yaml_file in self.resource_path.glob('*.yaml'):
                try:
                    with open(yaml_file) as f:
                        config = yaml.safe_load(f)
                        if config and 'module' in config:
                            sources.append(config['module'])
                except yaml.YAMLError as e:
                    self.logger.warning(
                        f'Could not parse config file {yaml_file}: {e}'
                    )
                    continue

            return sorted(set(sources))

        except OSError as e:
            self.logger.error(f'Could not read configured sources: {e}')
            return []

    def _merge_with_existing_config(
        self,
        existing_file: Path,
        new_template: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Merge new function template with existing module configuration."""
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
            for func_name, func_info in new_template.get('functions', {}).items():
                if func_name in existing_config['functions']:
                    self.logger.warning(
                        f'Function {func_name} already exists in {existing_file}, skipping'
                    )
                    continue

                existing_config['functions'][func_name] = func_info
                self.logger.info(f'Added function {func_name} to existing config')

            return existing_config

        except (OSError, yaml.YAMLError, KeyError) as e:
            self.logger.error(
                f'Failed to merge with existing config {existing_file}: {e}'
            )
            return None

    def _save_resource_config(
        self, config: dict[str, Any], output_file: Path, resource_name: str
    ) -> None:
        """Save resource configuration to YAML file."""
        with open(output_file, 'w', encoding='utf-8') as f:
            # Write header comment
            f.write(f'# Resource Configuration for {resource_name}\n')
            f.write(f'# Database: {self.database_name}\n\n')

            # Write YAML content
            yaml.dump(
                config,
                f,
                default_flow_style=False,
                sort_keys=False,
                indent=2,
                width=120,
            )


def main() -> None:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description='Parquet Database Manager - Manage parquet-based database lifecycle',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Initialize new database
  python database_manager.py init --database metabo

  # Add resources from PyPath
  python database_manager.py add-resources --database metabo --resources hmdb,chebi

  # Load bronze data
  python database_manager.py load-bronze --database metabo

  # Process sources (bronze → silver → gold)
  python database_manager.py process --database metabo

  # Show status
  python database_manager.py status --database metabo
        """,
    )

    parser.add_argument(
        'command',
        choices=[
            'init',
            'add-resources',
            'load-bronze',
            'process',
            'status',
        ],
        help='Command to execute',
    )

    parser.add_argument(
        '--database', '-d', required=True, help='Database name (e.g., metabo)'
    )

    parser.add_argument(
        '--module',
        help='Specific module to load (for load-bronze command)',
    )

    parser.add_argument(
        '--source',
        help='Specific source to process (for process command)',
    )

    parser.add_argument(
        '--resources',
        help='Comma-separated list of resources to add (for add-resources command)',
    )

    parser.add_argument(
        '--force',
        action='store_true',
        help='Force operation even if files exist',
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
    manager = ParquetDatabaseManager(args.database)

    # Execute command
    try:
        success = False

        if args.command == 'init':
            success = manager.init_database(force=args.force)

        elif args.command == 'add-resources':
            if not args.resources:
                parser.error('--resources is required for add-resources command')
            resources = [r.strip() for r in args.resources.split(',')]
            success = manager.add_resources(resources)

        elif args.command == 'load-bronze':
            success = manager.load_bronze(module=args.module, force=args.force)

        elif args.command == 'process':
            success = manager.process_sources(source=args.source)

        elif args.command == 'status':
            success = manager.show_status()

    except KeyboardInterrupt:
        logging.getLogger().info('Operation cancelled by user')
        success = False
    except (OSError, RuntimeError, ImportError) as e:
        logging.getLogger().error(f'Unexpected error: {e}')
        success = False

    sys.exit(0 if success else 1)


if __name__ == '__main__':
    main()
