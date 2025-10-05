"""Centralized path management for OmniPath 2.0 pipeline."""

from pathlib import Path

__all__ = ['PathManager']


class PathManager:
    """Manages path construction for database directories and files."""

    # Directory names
    DATABASES = 'databases'
    RESOURCE = 'resource'
    BRONZE = 'bronze'
    BRONZE_DATA = 'data'
    SILVER = 'silver'
    SILVER_PARQUET = 'silver_parquet'
    GOLD_PARQUET = 'gold_parquet'
    PASS1 = 'pass1'
    DEDUPED = 'deduped'

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
        return self.db_path / self.RESOURCE

    def bronze_data_path(self) -> Path:
        return self.db_path / self.BRONZE / self.BRONZE_DATA

    def silver_config_path(self) -> Path:
        return self.db_path / self.SILVER

    def silver_parquet_path(self) -> Path:
        return self.db_path / self.SILVER_PARQUET

    def gold_parquet_path(self) -> Path:
        return self.db_path / self.GOLD_PARQUET

    # Module-level paths
    def bronze_module_path(self, module_name: str) -> Path:
        return self.bronze_data_path() / module_name

    def bronze_function_path(self, module_name: str, function_name: str) -> Path:
        return self.bronze_module_path(module_name) / function_name

    def resource_config_file(self, module_name: str) -> Path:
        return self.resource_path() / f"{module_name}.yaml"

    def transformation_functions_file(self) -> Path:
        return self.silver_config_path() / 'transformation_functions.sql'

    def silver_parquet_file(
        self, module_name: str, function_name: str, target_table: str
    ) -> Path:
        return (
            self.silver_parquet_path()
            / f"{module_name}_{function_name}_{target_table}.parquet"
        )

    # Gold builder paths
    def gold_pass1_path(self) -> Path:
        return self.gold_parquet_path() / self.PASS1

    def gold_deduped_path(self) -> Path:
        return self.gold_parquet_path() / self.DEDUPED

    def gold_pass1_file(self, table_name: str, source_name: str) -> Path:
        return self.gold_pass1_path() / f"{table_name}_pass1_{source_name}.parquet"

    def gold_deduped_file(self, table_name: str) -> Path:
        return self.gold_deduped_path() / f"{table_name}_deduped.parquet"

    def gold_final_file(self, table_name: str) -> Path:
        return self.gold_parquet_path() / f"{table_name}.parquet"
