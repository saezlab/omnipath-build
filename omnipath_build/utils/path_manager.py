"""Centralized path management for OmniPath 2.0 pipeline."""

from pathlib import Path

__all__ = ['PathManager']


class PathManager:
    """Manages path construction for database directories and files."""

    # Directory names
    DATABASES = 'databases'
    RESOURCE = 'resource'
    DATA = 'data'
    BRONZE = 'bronze'
    SILVER = 'silver'
    GOLD = 'gold'
    GOLD_FINAL = 'gold_final'

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
    def resource_path(self) -> Path:
        """Get path to resource configs directory."""
        return self.db_path / self.RESOURCE

    def data_path(self) -> Path:
        """Get path to data directory (contains source-specific folders)."""
        return self.db_path / self.DATA

    def gold_final_path(self) -> Path:
        """Get path to gold_final directory (cross-source deduplicated)."""
        return self.db_path / self.GOLD_FINAL

    # Source-specific paths
    def source_path(self, source_name: str) -> Path:
        """Get path to a source directory."""
        return self.data_path() / source_name

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
        """Get path to silver parquet file."""
        silver_dir = self.source_silver_path(source_name, function_name)
        return silver_dir / f"{table_name}.parquet"

    def gold_file(self, source_name: str, function_name: str, table_name: str) -> Path:
        """Get path to source-specific gold parquet file."""
        gold_dir = self.source_gold_path(source_name, function_name)
        return gold_dir / f"{table_name}.parquet"

    def gold_final_file(self, table_name: str) -> Path:
        """Get path to final cross-source deduplicated gold file."""
        return self.gold_final_path() / f"{table_name}.parquet"

    def resource_config_file(self, module_name: str) -> Path:
        """Get path to resource config YAML file."""
        return self.resource_path() / f"{module_name}.yaml"
