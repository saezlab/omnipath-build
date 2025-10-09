"""Integration tests using subset of existing data.

These tests use small samples of real data from the databases directory
to verify end-to-end functionality of the pipeline.
"""

import sys
import pytest
import duckdb
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from omnipath_build.utils import PathManager

__all__ = [
    'TestPathManagerIntegration',
    'TestRealDataIntegration',
    'TestSampleDataProcessing',
]


class TestRealDataIntegration:
    """Integration tests using real parquet data from the databases directory."""

    @pytest.fixture
    def project_root(self):
        """Get the project root directory."""
        # Assumes tests are in tests/ directory
        return Path(__file__).parent.parent

    @pytest.fixture
    def omnipath_data_path(self, project_root):
        """Get path to omnipath database data directory."""
        data_path = project_root / 'databases' / 'omnipath' / 'data'
        if not data_path.exists():
            pytest.skip("Omnipath data directory not found")
        return data_path

    def test_lipidmaps_bronze_exists(self, omnipath_data_path):
        """Test that LipidMaps bronze data exists and is readable."""
        bronze_file = omnipath_data_path / 'lipidmaps' / 'lipidmaps_lipids' / 'bronze' / 'latest.parquet'

        if not bronze_file.exists():
            pytest.skip("LipidMaps bronze data not found")

        # Verify we can read the parquet file
        conn = duckdb.connect(':memory:')
        result = conn.execute(f"SELECT COUNT(*) FROM '{bronze_file}'").fetchone()
        conn.close()

        assert result[0] > 0, "Bronze file should contain data"

    def test_lipidmaps_bronze_schema(self, omnipath_data_path):
        """Test LipidMaps bronze data has expected schema."""
        bronze_file = omnipath_data_path / 'lipidmaps' / 'lipidmaps_lipids' / 'bronze' / 'latest.parquet'

        if not bronze_file.exists():
            pytest.skip("LipidMaps bronze data not found")

        conn = duckdb.connect(':memory:')
        result = conn.execute(f"DESCRIBE SELECT * FROM '{bronze_file}'").fetchall()
        conn.close()

        # Get column names
        columns = [row[0] for row in result]

        # Verify expected columns exist
        expected_cols = ['id', 'name', 'inchikey', 'smiles', 'category', 'main_class']
        for col in expected_cols:
            assert col in columns, f"Expected column '{col}' not found in bronze data"

    def test_lipidmaps_silver_exists(self, omnipath_data_path):
        """Test that LipidMaps silver data exists and is readable."""
        silver_file = omnipath_data_path / 'lipidmaps' / 'lipidmaps_lipids' / 'silver' / 'silver_entities.parquet'

        if not silver_file.exists():
            pytest.skip("LipidMaps silver data not found")

        conn = duckdb.connect(':memory:')
        result = conn.execute(f"SELECT COUNT(*) FROM '{silver_file}'").fetchone()
        conn.close()

        assert result[0] > 0, "Silver file should contain data"

    def test_lipidmaps_silver_schema(self, omnipath_data_path):
        """Test LipidMaps silver data has expected schema."""
        silver_file = omnipath_data_path / 'lipidmaps' / 'lipidmaps_lipids' / 'silver' / 'silver_entities.parquet'

        if not silver_file.exists():
            pytest.skip("LipidMaps silver data not found")

        conn = duckdb.connect(':memory:')
        result = conn.execute(f"DESCRIBE SELECT * FROM '{silver_file}'").fetchall()
        conn.close()

        # Get column names
        columns = [row[0] for row in result]

        # Verify silver schema
        expected_cols = [
            'entity_type',
            'dedup_identifier',
            'dedup_identifier_type',
            'inchikey',
            'lipidmaps_id',
            'chebi_id',
            'source_database'
        ]
        for col in expected_cols:
            assert col in columns, f"Expected column '{col}' not found in silver data"

    def test_lipidmaps_silver_transformations(self, omnipath_data_path):
        """Test that silver transformations were applied correctly."""
        silver_file = omnipath_data_path / 'lipidmaps' / 'lipidmaps_lipids' / 'silver' / 'silver_entities.parquet'

        if not silver_file.exists():
            pytest.skip("LipidMaps silver data not found")

        conn = duckdb.connect(':memory:')

        # Check entity_type is set correctly
        result = conn.execute(
            f"SELECT DISTINCT entity_type FROM '{silver_file}'"
        ).fetchall()

        assert len(result) > 0
        assert result[0][0] == 'compound', "Entity type should be 'compound'"

        # Check inchikey values are normalized
        invalid_inchikeys = conn.execute(
            f"""
            SELECT COUNT(*) FROM '{silver_file}'
            WHERE inchikey IS NOT NULL
              AND NOT (inchikey ~ '^[A-Z0-9]{{14}}-[A-Z0-9]{{10}}-[A-Z]$')
            """
        ).fetchone()[0]
        assert invalid_inchikeys == 0, "All inchikey values should be normalized"

        # Check LipidMaps identifiers are normalized
        invalid_lipidmaps_ids = conn.execute(
            f"""
            SELECT COUNT(*) FROM '{silver_file}'
            WHERE lipidmaps_id IS NOT NULL
              AND NOT (lipidmaps_id LIKE 'LIPIDMAPS:%')
            """
        ).fetchone()[0]
        assert invalid_lipidmaps_ids == 0, "LipidMaps identifiers should use LIPIDMAPS: prefix"

        # Check deduplication columns are populated and aligned with inchikey
        dedup_mismatch = conn.execute(
            f"""
            SELECT COUNT(*) FROM '{silver_file}'
            WHERE dedup_identifier IS NOT NULL
              AND inchikey IS NOT NULL
              AND dedup_identifier != inchikey
            """
        ).fetchone()[0]
        assert dedup_mismatch == 0, "Dedup identifier should match inchikey for LipidMaps sources"

        dedup_type_counts = conn.execute(
            f"""
            SELECT COUNT(*) FROM '{silver_file}'
            WHERE dedup_identifier IS NOT NULL
              AND dedup_identifier_type != 'inchikey'
            """
        ).fetchone()[0]
        assert dedup_type_counts == 0, "Dedup identifier type should be 'inchikey'"

        # Check source_database is set
        result = conn.execute(
            f"SELECT DISTINCT source_database FROM '{silver_file}'"
        ).fetchall()

        assert result[0][0] == 'lipidmaps', "Source database should be 'lipidmaps'"

        conn.close()

    def test_psimi_ontology_bronze_to_silver(self, omnipath_data_path):
        """Test PSI-MI ontology data pipeline."""
        bronze_file = omnipath_data_path / 'psimi' / 'psimi_ontology' / 'bronze' / 'latest.parquet'
        silver_file = omnipath_data_path / 'psimi' / 'psimi_ontology' / 'silver' / 'silver_cv_terms.parquet'

        if not bronze_file.exists() or not silver_file.exists():
            pytest.skip("PSI-MI data not found")

        conn = duckdb.connect(':memory:')

        # Verify bronze data
        bronze_count = conn.execute(f"SELECT COUNT(*) FROM '{bronze_file}'").fetchone()[0]
        assert bronze_count > 0

        # Verify silver data
        silver_count = conn.execute(f"SELECT COUNT(*) FROM '{silver_file}'").fetchone()[0]
        assert silver_count > 0

        # Silver count should be <= bronze count (some may be filtered)
        assert silver_count <= bronze_count

        conn.close()

    def test_gold_output_exists(self, omnipath_data_path):
        """Test that gold output files exist."""
        output_path = omnipath_data_path.parent / 'output'

        if not output_path.exists():
            pytest.skip("Gold output directory not found")

        # Check for some expected gold tables
        gold_files = list(output_path.glob('*.parquet'))

        if len(gold_files) == 0:
            pytest.skip("No gold output files found")

        # Verify we can read at least one gold file
        conn = duckdb.connect(':memory:')
        for gold_file in gold_files[:3]:  # Check first 3 files
            result = conn.execute(f"SELECT COUNT(*) FROM '{gold_file}'").fetchone()
            assert result[0] >= 0, f"Gold file {gold_file.name} should be readable"

        conn.close()


class TestPathManagerIntegration:
    """Integration tests for PathManager with real database structure."""

    @pytest.fixture
    def project_root(self):
        """Get the project root directory."""
        return Path(__file__).parent.parent

    @pytest.fixture
    def omnipath_pm(self, project_root):
        """Create PathManager for omnipath database."""
        base_path = project_root / 'databases'
        if not base_path.exists():
            pytest.skip("Databases directory not found")
        return PathManager('omnipath', base_path=base_path)

    def test_path_manager_real_paths(self, omnipath_pm):
        """Test PathManager with real omnipath database paths."""
        # Test configuration paths
        config_path = omnipath_pm.configuration_path()
        assert config_path.exists(), "Configuration directory should exist"

        resources_path = omnipath_pm.resources_path()
        if resources_path.exists():
            yaml_files = list(resources_path.glob('*.yaml'))
            assert len(yaml_files) > 0, "Should have resource config files"

    def test_path_manager_data_structure(self, omnipath_pm):
        """Test PathManager navigates real data structure correctly."""
        data_path = omnipath_pm.data_path()

        if not data_path.exists():
            pytest.skip("Data directory not found")

        # Check that data directory has source subdirectories
        sources = [d for d in data_path.iterdir() if d.is_dir()]
        assert len(sources) > 0, "Data directory should have source subdirectories"

    def test_path_manager_lipidmaps_paths(self, omnipath_pm):
        """Test PathManager paths for specific source (lipidmaps)."""
        # Test bronze path
        bronze_file = omnipath_pm.bronze_latest_file('lipidmaps', 'lipidmaps_lipids')

        if not bronze_file.exists():
            pytest.skip("LipidMaps bronze file not found")

        assert 'bronze' in str(bronze_file)
        assert bronze_file.name == 'latest.parquet'

        # Test silver path
        silver_file = omnipath_pm.silver_file('lipidmaps', 'lipidmaps_lipids', 'silver_entities')

        if silver_file.exists():
            assert 'silver' in str(silver_file)
            assert silver_file.name == 'silver_entities.parquet'


class TestSampleDataProcessing:
    """Tests that process small samples of real data."""

    @pytest.fixture
    def project_root(self):
        """Get the project root directory."""
        return Path(__file__).parent.parent

    def test_process_small_bronze_sample(self, project_root):
        """Test processing a small sample of bronze data."""
        bronze_file = project_root / 'databases' / 'omnipath' / 'data' / 'lipidmaps' / 'lipidmaps_lipids' / 'bronze' / 'latest.parquet'

        if not bronze_file.exists():
            pytest.skip("Bronze data not found")

        conn = duckdb.connect(':memory:')

        # Extract a small sample
        sample = conn.execute(
            f"""
            SELECT id, name, inchikey, category
            FROM '{bronze_file}'
            LIMIT 10
            """
        ).fetchall()

        conn.close()

        # Verify we got data
        assert len(sample) > 0
        assert len(sample) <= 10

        # Verify data structure
        for row in sample:
            assert len(row) == 4
            assert row[0] is not None, "ID should not be null"

    def test_verify_inchikey_format(self, project_root):
        """Test that InChIKey identifiers have correct format."""
        silver_file = project_root / 'databases' / 'omnipath' / 'data' / 'lipidmaps' / 'lipidmaps_lipids' / 'silver' / 'silver_entities.parquet'

        if not silver_file.exists():
            pytest.skip("Silver data not found")

        conn = duckdb.connect(':memory:')

        # Check InChIKey format (should be 27 characters: XXXXXXXXXXXXXX-XXXXXXXXXX-X)
        result = conn.execute(
            f"""
            SELECT identifier
            FROM '{silver_file}'
            WHERE identifier IS NOT NULL
            LIMIT 10
            """
        ).fetchall()

        conn.close()

        # InChIKey format: 14 chars - 10 chars - 1 char
        for row in result:
            inchikey = row[0]
            if inchikey:
                parts = inchikey.split('-')
                if len(parts) == 3:  # Standard InChIKey format
                    assert len(parts[0]) == 14, f"First part should be 14 chars: {inchikey}"
                    assert len(parts[1]) == 10, f"Second part should be 10 chars: {inchikey}"
                    assert len(parts[2]) == 1, f"Third part should be 1 char: {inchikey}"
