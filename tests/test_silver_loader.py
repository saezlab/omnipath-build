"""Unit tests for SilverLoader transformations."""

import sys
from pathlib import Path
import pytest
import duckdb
import yaml

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from omnipath_build import SilverLoader

__all__ = [
    'TestSilverLoader',
    'TestSilverLoaderEdgeCases',
]


class TestSilverLoader:
    """Tests for SilverLoader bronze to silver transformations."""

    @pytest.fixture
    def temp_db_structure(self, tmp_path):
        """Create a temporary database structure with config and test data."""
        # Create directory structure
        db_path = tmp_path / 'test_db'
        config_path = db_path / 'configuration'
        resources_path = config_path / 'resources'
        resources_path.mkdir(parents=True)

        # Create transformation functions SQL
        transform_sql_path = config_path / 'transformation_functions.sql'
        transform_sql_path.write_text("""
-- Test transformation function
CREATE OR REPLACE MACRO test_transform(val) AS upper(val);
        """)

        # Create test resource config
        test_config = {
            'metadata': {
                'name': 'Test Source',
                'description': 'Test data source'
            },
            'module': 'test_source',
            'functions': {
                'test_function': {
                    'description': 'Test function',
                    'processing': {
                        'target_table': 'silver_test',
                        'field_mapping': [
                            {'source': 'id', 'target': 'entity_id'},
                            {'source': 'name', 'target': 'entity_name', 'transform': 'test_transform'},
                            {'source': '_constant', 'target': 'entity_type', 'value': 'test'},
                            {'source': ['field1', 'field2'], 'target': 'combined'},
                            {'source': '_metadata', 'target': 'created_at', 'value': 'current_timestamp'}
                        ]
                    }
                }
            }
        }

        config_file = resources_path / 'test_source.yaml'
        with open(config_file, 'w') as f:
            yaml.dump(test_config, f)

        # Create bronze test data
        data_path = db_path / 'data' / 'test_source' / 'test_function' / 'bronze'
        data_path.mkdir(parents=True)
        bronze_file = data_path / 'latest.parquet'

        # Create bronze parquet with test data
        conn = duckdb.connect(':memory:')
        conn.execute(f"""
            COPY (
                SELECT
                    'id_1' as id,
                    'test name' as name,
                    'value1' as field1,
                    'value2' as field2
                UNION ALL
                SELECT
                    'id_2' as id,
                    'another test' as name,
                    'val1' as field1,
                    'val2' as field2
            ) TO '{bronze_file}' (FORMAT PARQUET)
        """)
        conn.close()

        return {
            'db_path': db_path,
            'base_path': tmp_path,
            'bronze_file': bronze_file,
            'config': test_config
        }

    def test_initialization(self, temp_db_structure):
        """Test SilverLoader initialization."""
        loader = SilverLoader(
            'test_db',
            'test_source',
            base_path=temp_db_structure['base_path']
        )

        assert loader.database_name == 'test_db'
        assert loader.source_module == 'test_source'
        assert loader.config is not None
        assert 'test_function' in loader.config['functions']

    def test_config_loading(self, temp_db_structure):
        """Test resource configuration loading."""
        loader = SilverLoader(
            'test_db',
            'test_source',
            base_path=temp_db_structure['base_path']
        )

        assert loader.config['module'] == 'test_source'
        assert 'functions' in loader.config
        assert loader.config['functions']['test_function']['processing']['target_table'] == 'silver_test'

    def test_build_select_expression_simple(self, temp_db_structure):
        """Test building SELECT expression for simple field mapping."""
        loader = SilverLoader(
            'test_db',
            'test_source',
            base_path=temp_db_structure['base_path']
        )

        available_columns = {'id', 'name', 'field1', 'field2'}

        # Simple field mapping
        mapping = {'source': 'id', 'target': 'entity_id'}
        expr = loader._build_select_expression(mapping, available_columns)
        assert expr == '"id" AS "entity_id"'

    def test_build_select_expression_with_transform(self, temp_db_structure):
        """Test building SELECT expression with transformation function."""
        loader = SilverLoader(
            'test_db',
            'test_source',
            base_path=temp_db_structure['base_path']
        )

        available_columns = {'id', 'name', 'field1', 'field2'}

        # Field mapping with transform
        mapping = {'source': 'name', 'target': 'entity_name', 'transform': 'test_transform'}
        expr = loader._build_select_expression(mapping, available_columns)
        assert expr == 'test_transform("name") AS "entity_name"'

    def test_build_select_expression_constant(self, temp_db_structure):
        """Test building SELECT expression for constant value."""
        loader = SilverLoader(
            'test_db',
            'test_source',
            base_path=temp_db_structure['base_path']
        )

        available_columns = {'id', 'name'}

        # Constant string
        mapping = {'source': '_constant', 'target': 'entity_type', 'value': 'test'}
        expr = loader._build_select_expression(mapping, available_columns)
        assert expr == "'test' AS \"entity_type\""

        # Constant boolean
        mapping = {'source': '_constant', 'target': 'is_active', 'value': True}
        expr = loader._build_select_expression(mapping, available_columns)
        assert expr == 'TRUE AS "is_active"'

    def test_build_select_expression_multiple_sources(self, temp_db_structure):
        """Test building SELECT expression for multiple source fields."""
        loader = SilverLoader(
            'test_db',
            'test_source',
            base_path=temp_db_structure['base_path']
        )

        available_columns = {'field1', 'field2'}

        # Multiple sources with default concatenation
        mapping = {'source': ['field1', 'field2'], 'target': 'combined'}
        expr = loader._build_select_expression(mapping, available_columns)
        assert 'CONCAT_WS' in expr
        assert '"field1"' in expr
        assert '"field2"' in expr

    def test_build_select_expression_metadata(self, temp_db_structure):
        """Test building SELECT expression for metadata fields."""
        loader = SilverLoader(
            'test_db',
            'test_source',
            base_path=temp_db_structure['base_path']
        )

        available_columns = {'id', 'name'}

        # Metadata timestamp
        mapping = {'source': '_metadata', 'target': 'created_at', 'value': 'current_timestamp'}
        expr = loader._build_select_expression(mapping, available_columns)
        assert expr == 'CURRENT_TIMESTAMP AS "created_at"'

    def test_load_transformation(self, temp_db_structure):
        """Test full bronze to silver transformation."""
        with SilverLoader(
            'test_db',
            'test_source',
            base_path=temp_db_structure['base_path']
        ) as loader:
            silver_files = loader.load()

            # Check that silver file was created
            assert 'test_function' in silver_files
            silver_file = silver_files['test_function']
            assert silver_file.exists()
            assert silver_file.name == 'silver_test.parquet'

            # Verify transformed data
            conn = duckdb.connect(':memory:')
            result = conn.execute(f"SELECT * FROM '{silver_file}'").fetchall()
            conn.close()

            # Should have 2 rows from bronze data
            assert len(result) == 2

    def test_load_with_missing_bronze_file(self, temp_db_structure):
        """Test loader behavior when bronze file doesn't exist."""
        # Create a config with a function that has no bronze data
        config_path = temp_db_structure['db_path'] / 'configuration' / 'resources'
        missing_config = {
            'metadata': {'name': 'Missing', 'description': 'Missing data'},
            'module': 'missing_source',
            'functions': {
                'missing_function': {
                    'processing': {
                        'target_table': 'silver_missing',
                        'field_mapping': [
                            {'source': 'id', 'target': 'entity_id'}
                        ]
                    }
                }
            }
        }

        config_file = config_path / 'missing_source.yaml'
        with open(config_file, 'w') as f:
            yaml.dump(missing_config, f)

        with SilverLoader(
            'test_db',
            'missing_source',
            base_path=temp_db_structure['base_path']
        ) as loader:
            silver_files = loader.load()

            # Should return empty dict when no bronze data available
            assert len(silver_files) == 0

    def test_context_manager(self, temp_db_structure):
        """Test context manager properly handles DuckDB connection."""
        loader = SilverLoader(
            'test_db',
            'test_source',
            base_path=temp_db_structure['base_path']
        )

        assert loader.conn is None

        with loader:
            # Connection should be created on first use
            loader._init_duckdb()
            assert loader.conn is not None

        # Connection should be closed after context exit
        assert loader.conn is None

    def test_get_table_function_map(self, temp_db_structure):
        """Test mapping of target tables to function names."""
        with SilverLoader(
            'test_db',
            'test_source',
            base_path=temp_db_structure['base_path']
        ) as loader:
            silver_files = loader.load()
            table_function_map = loader.get_table_function_map(silver_files)

            # silver_test table should map to test_function
            assert 'silver_test' in table_function_map
            assert table_function_map['silver_test'] == 'test_function'


class TestSilverLoaderEdgeCases:
    """Tests for edge cases and error handling."""

    def test_missing_config_file(self, tmp_path):
        """Test loader behavior when config file doesn't exist."""
        db_path = tmp_path / 'test_db'
        config_path = db_path / 'configuration' / 'resources'
        config_path.mkdir(parents=True)

        with pytest.raises(FileNotFoundError):
            SilverLoader('test_db', 'nonexistent_source', base_path=tmp_path)

    def test_unavailable_source_column(self, tmp_path):
        """Test handling of source column not in bronze data."""
        # Create minimal test structure
        db_path = tmp_path / 'test_db'
        config_path = db_path / 'configuration' / 'resources'
        resources_path = config_path
        resources_path.mkdir(parents=True)

        # Config references a column that doesn't exist
        test_config = {
            'metadata': {'name': 'Test', 'description': 'Test'},
            'module': 'test',
            'functions': {
                'func': {
                    'processing': {
                        'target_table': 'silver_test',
                        'field_mapping': [
                            {'source': 'existing_col', 'target': 'col1'},
                            {'source': 'missing_col', 'target': 'col2'}
                        ]
                    }
                }
            }
        }

        config_file = resources_path / 'test.yaml'
        with open(config_file, 'w') as f:
            yaml.dump(test_config, f)

        # Create bronze data without missing_col
        data_path = db_path / 'data' / 'test' / 'func' / 'bronze'
        data_path.mkdir(parents=True)
        bronze_file = data_path / 'latest.parquet'

        conn = duckdb.connect(':memory:')
        conn.execute(f"COPY (SELECT 'val' as existing_col) TO '{bronze_file}' (FORMAT PARQUET)")
        conn.close()

        loader = SilverLoader('test_db', 'test', base_path=tmp_path)
        available = {'existing_col'}

        # Missing column should map to NULL
        mapping = {'source': 'missing_col', 'target': 'col2'}
        expr = loader._build_select_expression(mapping, available)
        assert 'NULL' in expr
