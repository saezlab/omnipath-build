from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

__all__ = [
    'DATABASES_DIR',
    'DEFAULT_INPUTS_PACKAGE',
    'DEFAULT_SILVER_ROOT',
    'PathManager',
    'SilverPathLayout',
    'default_silver_dir',
    'load_local_env',
]

DATABASES_DIR = 'databases'
DEFAULT_SILVER_ROOT = Path('data/silver')
DEFAULT_INPUTS_PACKAGE = 'pypath.inputs_v2'


def load_local_env() -> None:
    for base in (Path.cwd(), *Path.cwd().parents):
        env_path = base / '.env'
        if env_path.exists():
            load_dotenv(env_path, override=False)
            break


def default_silver_dir(source: str) -> Path:
    load_local_env()
    silver_root = Path(os.environ.get('OMNIPATH_BUILD_SILVER_ROOT', DEFAULT_SILVER_ROOT))
    return silver_root / source


class SilverPathLayout:
    """Manages path construction for silver discovery/materialization."""

    DATABASES = DATABASES_DIR
    CONFIGURATION = 'configuration'
    RESOURCES = 'resources'
    DATA = 'silver'
    BRONZE = 'bronze'
    SILVER = 'silver'
    GOLD = 'gold'
    OUTPUT = 'output'

    def __init__(self, database_name: str, base_path: Path | None = None) -> None:
        self.database_name = database_name
        if base_path is None:
            base_path = Path(__file__).parent.parent.parent / self.DATABASES
        self.base_path = Path(base_path)
        self.db_path = self.base_path / database_name

    def configuration_path(self) -> Path:
        return self.db_path / self.CONFIGURATION

    def resources_path(self) -> Path:
        return self.configuration_path() / self.RESOURCES

    def data_path(self) -> Path:
        return self.db_path / self.DATA

    def output_path(self) -> Path:
        return self.db_path / self.OUTPUT

    def _normalize_source(self, source_name: str | Path) -> Path:
        if isinstance(source_name, Path):
            return source_name
        cleaned = source_name.strip().strip('/').replace('.', '/')
        if not cleaned:
            raise ValueError('source name cannot be empty')
        return Path(cleaned)

    def source_path(self, source_name: str) -> Path:
        return self.data_path() / self._normalize_source(source_name)

    def source_function_path(self, source_name: str, function_name: str) -> Path:
        return self.source_path(source_name) / function_name

    def source_bronze_path(self, source_name: str, function_name: str) -> Path:
        return self.source_function_path(source_name, function_name) / self.BRONZE

    def source_silver_path(self, source_name: str, function_name: str) -> Path:
        return self.source_function_path(source_name, function_name) / self.SILVER

    def source_gold_path(self, source_name: str, function_name: str) -> Path:
        return self.source_function_path(source_name, function_name) / self.GOLD

    def bronze_latest_file(self, source_name: str, function_name: str) -> Path:
        return self.source_bronze_path(source_name, function_name) / 'latest.parquet'

    def silver_file(self, source_name: str, function_name: str, table_name: str) -> Path:
        source_dir = self.source_path(source_name)
        source_leaf = self._normalize_source(source_name).name
        filename_stem = 'main' if table_name == source_leaf else table_name
        return source_dir / f'{filename_stem}.parquet'

    def artifact_file(
        self,
        source_name: str,
        function_name: str,
        extension: str,
        file_stem: str | None = None,
    ) -> Path:
        source_dir = self.source_path(source_name)
        stem = file_stem or function_name
        ext = extension.lstrip('.')
        return source_dir / f'{stem}.{ext}'

    def gold_file(self, source_name: str, function_name: str, table_name: str) -> Path:
        gold_dir = self.source_gold_path(source_name, function_name)
        return gold_dir / f'{table_name}.parquet'

    def output_file(self, table_name: str) -> Path:
        return self.output_path() / f'{table_name}.parquet'

    def resource_config_file(self, module_name: str) -> Path:
        return self.resources_path() / f'{module_name}.yaml'

    def silver_tables_config(self) -> Path:
        return self.configuration_path() / 'silver_tables.yaml'

    def gold_tables_config(self) -> Path:
        return self.configuration_path() / 'gold_tables.py'


PathManager = SilverPathLayout
