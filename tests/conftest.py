"""Pytest configuration and shared fixtures.

This file contains pytest fixtures that are shared across all test files.
"""

import pytest
from pathlib import Path

__all__ = [
    'databases_path',
    'omnipath_data_path',
    'project_root',
    'pytest_configure',
    'sample_bronze_file',
    'sample_silver_file',
]


@pytest.fixture
def project_root():
    """Get the project root directory."""
    return Path(__file__).parent.parent


@pytest.fixture
def databases_path(project_root):
    """Get the databases directory path."""
    return project_root / 'databases'


@pytest.fixture
def omnipath_data_path(databases_path):
    """Get the omnipath database data directory."""
    data_path = databases_path / 'omnipath' / 'data'
    if not data_path.exists():
        pytest.skip("Omnipath data directory not found")
    return data_path


@pytest.fixture
def sample_bronze_file(omnipath_data_path):
    """Get a sample bronze parquet file for testing."""
    bronze_file = omnipath_data_path / 'lipidmaps' / 'lipidmaps_lipids' / 'bronze' / 'latest.parquet'
    if not bronze_file.exists():
        pytest.skip("Sample bronze file not found")
    return bronze_file


@pytest.fixture
def sample_silver_file(omnipath_data_path):
    """Get a sample silver parquet file for testing."""
    silver_file = omnipath_data_path / 'lipidmaps' / 'lipidmaps_lipids' / 'silver' / 'silver_entities.parquet'
    if not silver_file.exists():
        pytest.skip("Sample silver file not found")
    return silver_file


# Marker for tests that require real data files
def pytest_configure(config):
    """Register custom markers."""
    config.addinivalue_line(
        "markers", "requires_data: mark test as requiring existing parquet data files"
    )
    config.addinivalue_line(
        "markers", "unit: mark test as a unit test"
    )
    config.addinivalue_line(
        "markers", "integration: mark test as an integration test"
    )
    config.addinivalue_line(
        "markers", "slow: mark test as slow running"
    )
