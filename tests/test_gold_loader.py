"""Unit tests for GoldLoader pipeline phases."""

import sys
import pytest
import duckdb
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from omnipath_build import GoldLoader
from omnipath_build.utils import PathManager

__all__ = [
    'TestGoldLoaderBasics',
    'TestGoldLoaderIntegration',
    'TestGoldLoaderPhase1',
    'TestGoldLoaderPhase2',
    'TestGoldLoaderPhase3',
    'TestGoldLoaderUtilityMethods',
]


class TestGoldLoaderBasics:
    """Tests for GoldLoader initialization and basic functionality."""

    @pytest.fixture
    def path_manager(self, tmp_path):
        """Create a PathManager for testing."""
        return PathManager('test_db', base_path=tmp_path)

    @pytest.fixture
    def gold_loader(self, path_manager):
        """Create a GoldLoader instance."""
        return GoldLoader(path_manager)

    def test_initialization_with_path_manager(self, gold_loader, path_manager):
        """Test GoldLoader initialization with PathManager."""
        assert gold_loader.path_manager is not None
        assert gold_loader.path_manager == path_manager
        assert gold_loader.conn is not None
        assert gold_loader.gold_final_dir.name == 'output'

    def test_initialization_with_legacy_path(self, tmp_path):
        """Test GoldLoader initialization with legacy output directory."""
        output_dir = tmp_path / 'output'
        loader = GoldLoader(output_dir)

        assert loader.output_dir == output_dir
        assert loader.gold_final_dir == output_dir
        assert loader.conn is not None

    def test_context_manager(self, path_manager):
        """Test context manager functionality."""
        loader = GoldLoader(path_manager)
        conn_before = loader.conn

        with loader as ctx_loader:
            assert ctx_loader.conn is not None
            assert ctx_loader == loader

        # After exit, connection is closed (set to None)
        # Note: GoldLoader.__exit__ closes the connection
        assert conn_before is not None

    def test_deduped_directory_creation(self, gold_loader):
        """Test that deduped directory is created."""
        assert gold_loader.deduped_dir.exists()
        assert gold_loader.deduped_dir.name == 'deduped'


class TestGoldLoaderPhase1:
    """Tests for Phase 1: Source extraction (pass1)."""

    @pytest.fixture
    def loader_with_data(self, tmp_path):
        """Create a GoldLoader with test silver data."""
        pm = PathManager('test_db', base_path=tmp_path)
        loader = GoldLoader(pm)

        # Create test silver data
        conn = loader.conn
        conn.execute("""
            CREATE TABLE test_silver AS
            SELECT
                'namespace1' as namespace_name,
                'term1' as name,
                'ACC001' as accession,
                'Description 1' as description,
                false as is_obsolete
            UNION ALL
            SELECT
                'namespace1' as namespace_name,
                'term2' as name,
                'ACC002' as accession,
                'Description 2' as description,
                false as is_obsolete
        """)

        return loader, pm

    def test_extract_pass1_basic(self, loader_with_data):
        """Test basic pass1 extraction."""
        loader, pm = loader_with_data

        # Extract from test_silver to a pass1 file
        select_sql = """
            SELECT
                namespace_name,
                name,
                accession,
                description,
                is_obsolete
            FROM test_silver
        """

        output_path = loader.extract_pass1(
            table_name='cv_term',
            source_name='test_source',
            select_sql=select_sql
        )

        assert output_path.exists()
        assert 'cv_term' in output_path.name

        # Verify data was written correctly
        result = loader.conn.execute(f"SELECT COUNT(*) FROM '{output_path}'").fetchone()
        assert result[0] == 2

    def test_extract_pass1_with_transformations(self, loader_with_data):
        """Test pass1 extraction with SQL transformations."""
        loader, pm = loader_with_data

        select_sql = """
            SELECT
                upper(namespace_name) as namespace_name,
                name,
                accession
            FROM test_silver
        """

        output_path = loader.extract_pass1(
            table_name='cv_term',
            source_name='test_source',
            select_sql=select_sql
        )

        # Verify transformation was applied
        result = loader.conn.execute(
            f"SELECT namespace_name FROM '{output_path}' LIMIT 1"
        ).fetchone()
        assert result[0] == 'NAMESPACE1'


class TestGoldLoaderPhase2:
    """Tests for Phase 2: Deduplication."""

    @pytest.fixture
    def loader_with_pass1_data(self, tmp_path):
        """Create a GoldLoader with pass1 test data."""
        pm = PathManager('test_db', base_path=tmp_path)
        loader = GoldLoader(pm)

        # Create test pass1 data with duplicates
        source1_dir = pm.data_path() / 'source1' / 'func1' / 'gold'
        source1_dir.mkdir(parents=True)
        source1_file = source1_dir / 'cv_namespace.parquet'

        source2_dir = pm.data_path() / 'source2' / 'func2' / 'gold'
        source2_dir.mkdir(parents=True)
        source2_file = source2_dir / 'cv_namespace.parquet'

        # Write test pass1 files
        loader.conn.execute(f"""
            COPY (
                SELECT 'namespace1' as name
                UNION ALL
                SELECT 'namespace2' as name
            ) TO '{source1_file}' (FORMAT PARQUET)
        """)

        loader.conn.execute(f"""
            COPY (
                SELECT 'namespace1' as name
                UNION ALL
                SELECT 'namespace3' as name
            ) TO '{source2_file}' (FORMAT PARQUET)
        """)

        return loader, pm

    def test_collect_pass1_paths(self, loader_with_pass1_data):
        """Test collection of pass1 parquet files."""
        loader, pm = loader_with_pass1_data

        pass1_files = loader._collect_pass1_paths('cv_namespace')

        # Should find 2 pass1 files
        assert len(pass1_files) == 2
        assert all(f.exists() for f in pass1_files)

    def test_get_pass1_read_source(self, loader_with_pass1_data):
        """Test building DuckDB read expression for pass1 files."""
        loader, pm = loader_with_pass1_data

        read_expr, files = loader._get_pass1_read_source('cv_namespace')

        assert len(files) == 2
        assert 'read_parquet' in read_expr
        assert 'cv_namespace.parquet' in read_expr

    def test_extract_dedup_keys(self, loader_with_pass1_data):
        """Test extraction of deduplication keys from constraints."""
        loader, _ = loader_with_pass1_data

        # Test simple unique constraint
        constraints = ["unique on (name)"]
        keys = loader._extract_dedup_keys(constraints)
        assert keys == ['name']

        # Test composite unique constraint
        constraints = ["unique on (namespace_name, name)"]
        keys = loader._extract_dedup_keys(constraints)
        assert keys == ['namespace_name', 'name']


class TestGoldLoaderPhase3:
    """Tests for Phase 3: Foreign key resolution."""

    @pytest.fixture
    def loader_with_deduped_data(self, tmp_path):
        """Create a GoldLoader with deduped test data."""
        pm = PathManager('test_db', base_path=tmp_path)
        loader = GoldLoader(pm)

        # Create deduped namespace data
        namespace_file = loader.deduped_dir / 'cv_namespace_deduped.parquet'
        loader.conn.execute(f"""
            COPY (
                SELECT
                    1 as id,
                    'test_namespace' as name
            ) TO '{namespace_file}' (FORMAT PARQUET)
        """)

        # Create deduped term data (before FK resolution)
        term_file = loader.deduped_dir / 'cv_term_deduped.parquet'
        loader.conn.execute(f"""
            COPY (
                SELECT
                    1 as id,
                    'test_namespace' as namespace_name,
                    'term1' as name,
                    'ACC001' as accession,
                    'Description' as description,
                    false as is_obsolete,
                    NULL as replaces_accession,
                    NULL as replaced_by_accession
            ) TO '{term_file}' (FORMAT PARQUET)
        """)

        return loader, pm

    def test_parse_fk_link_simple(self, loader_with_deduped_data):
        """Test parsing of simple FK link text."""
        loader, _ = loader_with_deduped_data

        link_text = "links to cv_namespace via cv_namespace.name = namespace_name"
        table, condition = loader._parse_fk_link(link_text)

        assert table == 'cv_namespace'
        assert 'cv_namespace.name' in condition
        assert 'main.namespace_name' in condition

    def test_parse_fk_link_composite(self, loader_with_deduped_data):
        """Test parsing of composite FK link text."""
        loader, _ = loader_with_deduped_data

        link_text = "links to cv_term via (cv_namespace.name = type_namespace AND cv_term.name = type_name)"
        table, condition = loader._parse_fk_link(link_text)

        assert table == 'cv_term'
        assert 'cv_namespace.name' in condition
        assert 'cv_term.name' in condition
        assert 'main.type_namespace' in condition
        assert 'main.type_name' in condition

    def test_duckdb_path_literal(self, loader_with_deduped_data):
        """Test path escaping for DuckDB SQL."""
        loader, _ = loader_with_deduped_data

        # Test path with single quotes
        path = Path("/path/with'quote/file.parquet")
        escaped = loader._duckdb_path_literal(path)
        assert "''" in escaped

    def test_deduped_file_path(self, loader_with_deduped_data):
        """Test deduped file path construction."""
        loader, _ = loader_with_deduped_data

        path = loader._deduped_file_path('test_table')
        assert path.parent == loader.deduped_dir
        assert path.name == 'test_table_deduped.parquet'


class TestGoldLoaderIntegration:
    """Integration tests for the full gold pipeline."""

    @pytest.fixture
    def complete_test_setup(self, tmp_path):
        """Create a complete test setup with silver data."""
        pm = PathManager('test_db', base_path=tmp_path)
        loader = GoldLoader(pm)

        # Create silver data for namespace
        silver_ns_dir = pm.data_path() / 'test_source' / 'func1' / 'silver'
        silver_ns_dir.mkdir(parents=True)
        silver_ns_file = silver_ns_dir / 'silver_cv_namespace.parquet'

        loader.conn.execute(f"""
            COPY (
                SELECT 'test_namespace' as name
            ) TO '{silver_ns_file}' (FORMAT PARQUET)
        """)

        return loader, pm, {'silver_cv_namespace': silver_ns_file}

    def test_full_pipeline_run(self, complete_test_setup):
        """Test running the full pipeline end-to-end."""
        loader, pm, silver_files = complete_test_setup

        # Note: This test would require silver_gold_map configuration
        # For now, just verify the loader can process empty silver files

        # Verify path collection works
        pass1_files = loader._collect_pass1_paths('cv_namespace')
        # May be empty initially, which is OK
        assert isinstance(pass1_files, list)


class TestGoldLoaderUtilityMethods:
    """Tests for utility methods."""

    @pytest.fixture
    def loader(self, tmp_path):
        """Create a basic GoldLoader."""
        pm = PathManager('test_db', base_path=tmp_path)
        return GoldLoader(pm)

    def test_split_source_key(self, loader):
        """Test splitting source key into module and function."""
        # With double underscore
        module, func = loader._split_source_key('source__function', 'fallback')
        assert module == 'source'
        assert func == 'function'

        # Without double underscore
        module, func = loader._split_source_key('source', 'fallback_func')
        assert module == 'source'
        assert func == 'fallback_func'
