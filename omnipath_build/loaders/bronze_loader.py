#!/usr/bin/env python3
"""Simplified PyPath Bronze Loader for OmniPath 2.0.

This simplified bronze loader uses only pypath.inputs methods with the two-tier
configuration system: auto-generated templates + database-specific configs.

Usage:
    # Load specific module for a database
    python bronze_loader_pypath.py --database omnipath --module uniprot_db

    # Load specific function
    python bronze_loader_pypath.py --database omnipath --module uniprot_db --function all_swissprots

    # Load all configured modules for a database
    python bronze_loader_pypath.py --database omnipath --all

    # Test configuration without loading data
    python bronze_loader_pypath.py --database omnipath --module uniprot_db --validate-only
"""

import sys
from typing import Any
from pathlib import Path
from datetime import datetime

import yaml

# Add current directory to path
sys.path.append(str(Path(__file__).parent.parent))

from utils import (
    BaseLoader,
    BronzeWriter,
    PyPathAdapter,
    BronzeLoaderError,
    log_execution_time,
)
from utils.constants import get_database_path
from utils.s3_config import get_s3_bronze_path, get_latest_s3_parquet

__all__ = [
    'PyPathBronzeLoader',
]


class PyPathBronzeLoader(BaseLoader):
    """Simplified Bronze Loader that uses only pypath.inputs methods.

    Uses two-tier configuration: templates + database-specific configs.
    """

    def __init__(self, database_name: str, db_connector: Any) -> None:  # noqa: ANN401
        """Initialize PyPath Bronze Loader.

        Args:
            database_name: Name of the database (e.g., 'omnipath')
            db_connector: Database connector instance
        """
        self.database_name = database_name
        super().__init__(db_connector=db_connector)

    def _initialize(self) -> None:
        """Initialize pypath bronze loader specific attributes."""
        # Bronze data is now stored in S3 (shared across databases)
        # Keep local path for resource configs only
        self.local_bronze_path = (
            get_database_path(self.database_name) / 'bronze' / 'data'
        )

        # Resource configs directory (replaces old templates directory)
        self.resource_configs_path = (
            get_database_path(self.database_name) / 'resource'
        )

        if not self.resource_configs_path.exists():
            raise BronzeLoaderError(
                f'Resource configs directory not found: {self.resource_configs_path}'
            )

        # Initialize PyPath adapter
        self.pypath_adapter = PyPathAdapter()

        # Initialize bronze writer for PostgreSQL operations
        self.bronze_writer = BronzeWriter(
            self.db_connector.pg_config, self.conn
        )

        self.logger.info('PyPath Bronze Loader initialized')
        self.logger.info(f'  Database: {self.database_name}')
        self.logger.info(f'  Resource configs: {self.resource_configs_path}')
        self.logger.info('  Bronze data storage: S3 (shared across databases)')

    def load(
        self,
        module_name: str | None = None,
        function_name: str | None = None,
        max_rows: int | None = None,
        force: bool = False,
        validate_only: bool = False,
    ) -> dict[str, int]:
        """Load data using pypath methods based on database configuration.

        Args:
            module_name: Specific module to load (None = all configured modules)
            function_name: Specific function within module (None = all enabled functions)
            max_rows: Maximum rows per function call
            force: Force re-download even if parquet exists
            validate_only: Only validate configuration, don't load data

        Returns:
            Dict mapping module.function to rows loaded
        """
        results = {}

        if module_name:
            modules = [module_name]
        else:
            modules = self.get_configured_modules()
            self.logger.info(f'Loading {len(modules)} configured modules')

        for mod_name in modules:
            try:
                self.logger.info(f'{"=" * 60}')
                self.logger.info(f'Processing module: {mod_name}')

                if validate_only:
                    self.logger.info(
                        'Validation mode - skipping actual data loading'
                    )
                    results[mod_name] = 0
                else:
                    module_results = self.load_module(
                        mod_name, function_name, max_rows, force
                    )
                    results.update(module_results)

            except (OSError, yaml.YAMLError, RuntimeError) as e:
                self.logger.error(f'Failed to process module {mod_name}: {e}')
                if validate_only:
                    results[mod_name] = -1  # Validation failed
                else:
                    results[mod_name] = 0

        return results

    def get_configured_modules(self) -> list[str]:
        """Get list of configured modules from YAML config files."""
        try:
            if not self.resource_configs_path.exists():
                self.logger.warning(
                    f'Resource configs directory not found: {self.resource_configs_path}'
                )
                return []

            modules = []
            for yaml_file in self.resource_configs_path.glob('*.yaml'):
                try:
                    with open(yaml_file) as f:
                        config = yaml.safe_load(f)
                        if config and 'module' in config:
                            modules.append(config['module'])
                except yaml.YAMLError as e:
                    self.logger.warning(
                        f'Could not parse config file {yaml_file}: {e}'
                    )
                    continue

            modules.sort()
            self.logger.info(
                f'Found {len(modules)} configured modules from YAML files'
            )
            return modules

        except OSError as e:
            self.logger.error(f'Could not read configured modules: {e}')
            return []

    def get_module_functions(
        self, module_name: str, function_name: str | None = None
    ) -> dict[str, dict[str, Any]]:
        """Get functions to load from YAML config files."""
        try:
            # Find the YAML file for this module
            yaml_file = None
            for config_file in self.resource_configs_path.glob('*.yaml'):
                try:
                    with open(config_file) as f:
                        config = yaml.safe_load(f)
                        if config and config.get('module') == module_name:
                            yaml_file = config_file
                            break
                except yaml.YAMLError as e:
                    self.logger.warning(
                        f'Could not parse config file {config_file}: {e}'
                    )
                    continue

            if not yaml_file:
                self.logger.warning(
                    f'No YAML config found for module: {module_name}'
                )
                return {}

            # Load the configuration
            with open(yaml_file) as f:
                config = yaml.safe_load(f)

            if not config or 'functions' not in config:
                self.logger.warning(
                    f'No functions defined in config for module: {module_name}'
                )
                return {}

            functions = {}
            for func_name, func_config in config['functions'].items():
                if function_name and func_name != function_name:
                    continue  # Skip if specific function requested and this isn't it

                functions[func_name] = {
                    'kwargs': func_config.get('kwargs', {}),
                    'description': func_config.get('description', ''),
                    'processing': func_config.get('processing', {}),
                }

            return functions

        except (OSError, yaml.YAMLError) as e:
            self.logger.error(
                f'Could not read module functions for {module_name}: {e}'
            )
            return {}

    @log_execution_time()
    def load_module(
        self,
        module_name: str,
        function_name: str | None = None,
        max_rows: int | None = None,
        force: bool = False,
    ) -> dict[str, int]:
        """Load all enabled functions from a module."""
        self.logger.info(f'Loading module: {module_name}')

        # Get functions from database metadata
        functions_to_load = self.get_module_functions(
            module_name, function_name
        )

        if not functions_to_load:
            self.logger.warning(
                f'No enabled functions found in module {module_name}'
            )
            return {}

        results = {}
        total_rows = 0

        for func_name, func_config in functions_to_load.items():
            method_name = f'{module_name}.{func_name}'
            self.logger.info(f'--- Loading function: {method_name} ---')

            try:
                # Check if parquet already exists in S3
                existing_file = self.get_latest_s3_parquet_file(
                    module_name, func_name
                )
                if existing_file and not force:
                    self.logger.info(
                        f'Parquet file already exists in S3: {existing_file}'
                    )
                    self.logger.info(
                        'Skipping download. Use --force to re-download.'
                    )

                    # Write sample to PostgreSQL bronze from existing S3 parquet
                    self.bronze_writer.write_to_bronze_from_s3(
                        module_name,
                        func_name,
                        existing_file,
                    )

                    # Get row count from S3
                    row_count = self.get_s3_parquet_row_count(existing_file)
                    results[method_name] = row_count
                    total_rows += row_count
                    continue

                # Load data via pypath
                parquet_file, row_count = self.load_function(
                    module_name, func_name, func_config, max_rows
                )

                results[method_name] = row_count
                total_rows += row_count

                self.logger.info(
                    f'✅ Loaded {self.format_row_count(row_count)} rows'
                )
                self.logger.info(f'💾 Saved to S3: {parquet_file}')

            except (OSError, RuntimeError) as e:
                self.logger.error(f'Failed to load function {func_name}: {e}')
                results[method_name] = 0

        self.logger.info(
            f'Module {module_name} total: {self.format_row_count(total_rows)} rows'
        )
        return results

    def load_function(
        self,
        module_name: str,
        func_name: str,
        func_config: dict[str, Any],
        max_rows: int | None = None,
    ) -> tuple[str, int]:
        """Load data from a specific pypath function."""

        # Generate S3 output path
        parquet_file = self._generate_s3_parquet_path(module_name, func_name)

        # Get kwargs from config
        kwargs = func_config.get('kwargs', {}).copy()

        # Add max_rows if specified
        if max_rows:
            # Try common parameter names for limiting results
            for limit_param in ['limit', 'max_results', 'nrows']:
                method_info = self.pypath_adapter.get_method_info(
                    f'{module_name}.{func_name}'
                )
                if method_info:
                    params = self.pypath_adapter.get_method_parameters(
                        method_info.full_name
                    )
                    if limit_param in params:
                        kwargs[limit_param] = max_rows
                        break

        # Filter out comment values (start with '#')
        kwargs = {
            k: v
            for k, v in kwargs.items()
            if not (isinstance(v, str) and v.startswith('#'))
        }

        self.logger.info(
            f'Calling {module_name}.{func_name} with kwargs: {kwargs}'
        )

        # Use PyPath adapter to get data and save to S3
        parquet_file, row_count = self.pypath_adapter.save_to_parquet(
            method_name=f'{module_name}.{func_name}',
            output_path=parquet_file,
            resource_id=module_name,
            dataset_name=func_name,
            **kwargs,
        )

        # Handle S3 upload if PyPathAdapter saved to temp file
        if hasattr(self.pypath_adapter, '_temp_parquet_path'):
            temp_path = self.pypath_adapter._temp_parquet_path
            self._upload_temp_to_s3(temp_path, parquet_file)
            # Clean up temp file
            import os

            os.unlink(temp_path)
            delattr(self.pypath_adapter, '_temp_parquet_path')

        # Write sample to PostgreSQL bronze from S3
        self.bronze_writer.write_to_bronze_from_s3(
            module_name, func_name, parquet_file
        )

        return parquet_file, row_count

    def _generate_s3_parquet_path(
        self, module_name: str, func_name: str
    ) -> str:
        """Generate S3 parquet file path with timestamp."""
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        return get_s3_bronze_path(module_name, func_name, timestamp)

    def get_latest_s3_parquet_file(
        self, module_name: str, func_name: str
    ) -> str | None:
        """Get the most recent parquet file for a function from S3."""
        return get_latest_s3_parquet(self.conn, module_name, func_name)

    def get_s3_parquet_row_count(self, s3_parquet_file: str) -> int:
        """Get row count from S3 parquet file."""
        result = self.execute_sql(
            f"SELECT COUNT(*) FROM read_parquet('{s3_parquet_file}')"
        ).fetchone()
        return result[0] if result else 0

    def _upload_temp_to_s3(self, temp_path: str, s3_path: str) -> None:
        """Upload temporary parquet file to S3 using DuckDB."""
        copy_sql = f"""
            COPY (
                SELECT * FROM read_parquet('{temp_path}')
            ) TO '{s3_path}' (FORMAT PARQUET, COMPRESSION ZSTD)
        """
        self.execute_sql(copy_sql)
        self.logger.debug(f'Uploaded {temp_path} to {s3_path}')
