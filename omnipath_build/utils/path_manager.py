"""Centralized path management for OmniPath 2.0 pipeline."""

from pathlib import Path

__all__ = ['PathManager']


class PathManager:
    """Manages path construction for database directories and files."""

    # Directory names
    DATABASES = 'databases'
    CONFIGURATION = 'configuration'
    RESOURCES = 'resources'
    DATA = 'silver'
    BRONZE = 'bronze'
    SILVER = 'silver'
    GOLD = 'gold'
    OUTPUT = 'output'

    def __init__(self, database_name: str, base_path: Path | None = None) -> None:
        """Initialize path manager.

        Args:
            database_name: Name of the database
            base_path: Optional base path (defaults to omnipath_build/databases)
        """
        self.database_name = database_name

        if base_path is None:
            # Default: go up from utils/ to new_loaders/, then ../databases
            base_path = Path(__file__).parent.parent.parent / self.DATABASES

        self.base_path = Path(base_path)
        self.db_path = self.base_path / database_name

    # Main directories
    def configuration_path(self) -> Path:
        """Get path to configuration directory."""
        return self.db_path / self.CONFIGURATION

    def resources_path(self) -> Path:
        """Get path to resources configs directory."""
        return self.configuration_path() / self.RESOURCES

    def data_path(self) -> Path:
        """Get path to data directory (contains source-specific folders)."""
        return self.db_path / self.DATA

    def output_path(self) -> Path:
        """Get path to output directory (cross-source deduplicated final tables)."""
        return self.db_path / self.OUTPUT

    # Internal helpers
    def _normalize_source(self, source_name: str | Path) -> Path:
        """Convert dotted source identifiers to folder paths."""
        if isinstance(source_name, Path):
            return source_name
        cleaned = source_name.strip().strip('/').replace('.', '/')
        if not cleaned:
            raise ValueError('source name cannot be empty')
        return Path(cleaned)

    # Source-specific paths
    def source_path(self, source_name: str) -> Path:
        """Get path to a source directory."""
        return self.data_path() / self._normalize_source(source_name)

    def source_function_path(self, source_name: str, function_name: str) -> Path:
        """Get path to a source function directory."""
        return self.source_path(source_name) / function_name

    def source_bronze_path(self, source_name: str, function_name: str) -> Path:
        """Get path to bronze directory for a source function."""
        return self.source_function_path(source_name, function_name) / self.BRONZE

    def source_silver_path(self, source_name: str, function_name: str) -> Path:
        """Get path to silver directory for a source function."""
        return self.source_function_path(source_name, function_name) / self.SILVER

    def source_gold_path(self, source_name: str, function_name: str) -> Path:
        """Get path to gold directory for a source function."""
        return self.source_function_path(source_name, function_name) / self.GOLD

    # File paths
    def bronze_latest_file(self, source_name: str, function_name: str) -> Path:
        """Get path to latest bronze parquet file."""
        bronze_dir = self.source_bronze_path(source_name, function_name)
        return bronze_dir / 'latest.parquet'

    def silver_file(self, source_name: str, function_name: str, table_name: str) -> Path:
        """Get path to silver parquet file (directly in source directory).

        Avoid redundant names like <source>/<source>.parquet by using
        ``main.parquet`` when table_name equals the source leaf.
        """
        source_dir = self.source_path(source_name)
        source_leaf = self._normalize_source(source_name).name
        filename_stem = 'main' if table_name == source_leaf else table_name
        return source_dir / f"{filename_stem}.parquet"

    def gold_file(self, source_name: str, function_name: str, table_name: str) -> Path:
        """Get path to source-specific gold parquet file."""
        gold_dir = self.source_gold_path(source_name, function_name)
        return gold_dir / f"{table_name}.parquet"

    def output_file(self, table_name: str) -> Path:
        """Get path to final cross-source deduplicated output file."""
        return self.output_path() / f"{table_name}.parquet"

    def resource_config_file(self, module_name: str) -> Path:
        """Get path to resource config YAML file."""
        return self.resources_path() / f"{module_name}.yaml"

    def silver_tables_config(self) -> Path:
        """Get path to silver tables configuration YAML."""
        return self.configuration_path() / 'silver_tables.yaml'

    def gold_tables_config(self) -> Path:
        """Get path to gold tables configuration Python file."""
        return self.configuration_path() / 'gold_tables.py'
