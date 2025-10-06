"""Unit tests for PathManager utility."""

import sys
from pathlib import Path
import pytest

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from omnipath_build.utils import PathManager

__all__ = [
    'TestPathManager',
]


class TestPathManager:
    """Tests for PathManager path construction."""

    @pytest.fixture
    def path_manager(self, tmp_path):
        """Create a PathManager with a temporary base path."""
        return PathManager('test_db', base_path=tmp_path)

    def test_initialization(self, path_manager, tmp_path):
        """Test PathManager initialization."""
        assert path_manager.database_name == 'test_db'
        assert path_manager.base_path == tmp_path
        assert path_manager.db_path == tmp_path / 'test_db'

    def test_configuration_paths(self, path_manager, tmp_path):
        """Test configuration directory paths."""
        assert path_manager.configuration_path() == tmp_path / 'test_db' / 'configuration'
        assert path_manager.resources_path() == tmp_path / 'test_db' / 'configuration' / 'resources'

    def test_data_and_output_paths(self, path_manager, tmp_path):
        """Test data and output directory paths."""
        assert path_manager.data_path() == tmp_path / 'test_db' / 'data'
        assert path_manager.output_path() == tmp_path / 'test_db' / 'output'

    def test_source_specific_paths(self, path_manager, tmp_path):
        """Test source-specific directory paths."""
        source = 'lipidmaps'
        function = 'lipidmaps_lipids'

        assert path_manager.source_path(source) == tmp_path / 'test_db' / 'data' / source
        assert path_manager.source_function_path(source, function) == \
            tmp_path / 'test_db' / 'data' / source / function
        assert path_manager.source_bronze_path(source, function) == \
            tmp_path / 'test_db' / 'data' / source / function / 'bronze'
        assert path_manager.source_silver_path(source, function) == \
            tmp_path / 'test_db' / 'data' / source / function / 'silver'
        assert path_manager.source_gold_path(source, function) == \
            tmp_path / 'test_db' / 'data' / source / function / 'gold'

    def test_file_paths(self, path_manager, tmp_path):
        """Test file path construction."""
        source = 'lipidmaps'
        function = 'lipidmaps_lipids'
        table = 'silver_entities'

        assert path_manager.bronze_latest_file(source, function) == \
            tmp_path / 'test_db' / 'data' / source / function / 'bronze' / 'latest.parquet'
        assert path_manager.silver_file(source, function, table) == \
            tmp_path / 'test_db' / 'data' / source / function / 'silver' / f'{table}.parquet'
        assert path_manager.gold_file(source, function, table) == \
            tmp_path / 'test_db' / 'data' / source / function / 'gold' / f'{table}.parquet'
        assert path_manager.output_file(table) == \
            tmp_path / 'test_db' / 'output' / f'{table}.parquet'

    def test_resource_config_paths(self, path_manager, tmp_path):
        """Test resource configuration file paths."""
        assert path_manager.resource_config_file('lipidmaps') == \
            tmp_path / 'test_db' / 'configuration' / 'resources' / 'lipidmaps.yaml'

    def test_legacy_compatibility(self, path_manager, tmp_path):
        """Test legacy path methods."""
        # Legacy methods should return same as new methods
        assert path_manager.resource_path() == path_manager.resources_path()
        assert path_manager.gold_final_path() == path_manager.output_path()
        assert path_manager.gold_final_file('test_table') == path_manager.output_file('test_table')

    def test_default_base_path(self):
        """Test default base path when not specified."""
        pm = PathManager('omnipath')
        # Should default to databases/ directory relative to module location
        assert pm.database_name == 'omnipath'
        assert 'databases' in str(pm.base_path)
        assert pm.db_path.name == 'omnipath'
