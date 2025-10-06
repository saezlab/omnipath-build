# OmniPath Build Test Suite

This directory contains unit and integration tests for the OmniPath 2.0 build pipeline.

## Test Structure

```
tests/
├── conftest.py              # Shared pytest fixtures
├── test_placeholder.py      # Basic sanity tests
├── test_path_manager.py     # PathManager utility tests
├── test_silver_loader.py    # SilverLoader transformation tests
├── test_gold_loader.py      # GoldLoader pipeline tests
└── test_integration.py      # Integration tests using real data
```

## Test Categories

### Unit Tests

- **test_path_manager.py**: Tests for the PathManager utility class
  - Path construction for bronze/silver/gold layers
  - File path generation
  - Legacy compatibility

- **test_silver_loader.py**: Tests for bronze → silver transformations
  - Field mapping and transformations
  - SQL expression building
  - Constant and metadata handling
  - DuckDB integration

- **test_gold_loader.py**: Tests for the three-phase gold pipeline
  - Phase 1: Source extraction (pass1)
  - Phase 2: Deduplication
  - Phase 3: Foreign key resolution
  - Utility methods

### Integration Tests

- **test_integration.py**: End-to-end tests using real parquet data
  - Bronze/silver/gold data validation
  - Schema verification
  - Data transformations
  - InChIKey format validation

## Running Tests

### Run all tests
```bash
pytest
```

### Run specific test file
```bash
pytest tests/test_path_manager.py
```

### Run tests by category (using markers)
```bash
# Unit tests only
pytest -m unit

# Integration tests only
pytest -m integration

# Tests requiring real data
pytest -m requires_data
```

### Run with verbose output
```bash
pytest -v
```

### Run with coverage
```bash
pytest --cov=omnipath_build --cov-report=html
```

## Test Fixtures

Common fixtures are defined in `conftest.py`:

- `project_root`: Path to project root directory
- `databases_path`: Path to databases directory
- `omnipath_data_path`: Path to omnipath data directory
- `sample_bronze_file`: Sample bronze parquet file
- `sample_silver_file`: Sample silver parquet file

## Test Data

### Unit Tests
Unit tests create their own temporary test data using pytest's `tmp_path` fixture. They don't require existing data files.

### Integration Tests
Integration tests use real data from the `databases/omnipath/data` directory:

- **LipidMaps**: `databases/omnipath/data/lipidmaps/lipidmaps_lipids/`
  - Bronze: ~49,000 lipid structures
  - Silver: Transformed entity records
  - Gold: Deduplicated and FK-resolved entities

- **PSI-MI**: `databases/omnipath/data/psimi/psimi_ontology/`
  - Bronze: PSI-MI ontology terms
  - Silver: CV terms
  - Gold: Controlled vocabulary tables

If these files don't exist, integration tests will be automatically skipped.

## Writing New Tests

### Unit Test Example
```python
def test_my_feature(tmp_path):
    """Test description."""
    # Use tmp_path for temporary files
    test_file = tmp_path / "test.parquet"

    # Your test logic
    assert something is True
```

### Integration Test Example
```python
@pytest.mark.requires_data
def test_real_data_processing(sample_bronze_file):
    """Test with real data."""
    # sample_bronze_file is provided by conftest.py
    conn = duckdb.connect(":memory:")
    result = conn.execute(f"SELECT COUNT(*) FROM '{sample_bronze_file}'")
    assert result.fetchone()[0] > 0
```

## Test Markers

Available markers (defined in pytest.ini):
- `@pytest.mark.unit`: Unit tests
- `@pytest.mark.integration`: Integration tests
- `@pytest.mark.requires_data`: Tests requiring existing parquet files
- `@pytest.mark.slow`: Slow-running tests

## Dependencies

Tests require the following packages:
- `pytest`: Test framework
- `duckdb`: In-memory data processing
- `pyyaml`: YAML configuration parsing

Install test dependencies:
```bash
pip install pytest pyyaml duckdb
```

## CI/CD Integration

To run tests in CI/CD pipelines:

```yaml
# Example GitHub Actions workflow
- name: Run tests
  run: |
    pip install pytest pyyaml duckdb
    pytest -v --tb=short
```

For CI environments without real data, run only unit tests:
```bash
pytest -m "not requires_data"
```

## Coverage

To generate test coverage reports:

```bash
# Terminal output
pytest --cov=omnipath_build --cov-report=term

# HTML report
pytest --cov=omnipath_build --cov-report=html
# View: open htmlcov/index.html
```

## Troubleshooting

### Import Errors
If you get import errors, ensure the package is installed in development mode:
```bash
pip install -e .
```

### Missing Data Files
Integration tests require real parquet files. If you see "skipped" tests:
```bash
# This is expected if data files don't exist
pytest -v  # Shows which tests were skipped
```

### DuckDB Errors
Ensure you have a recent version of DuckDB:
```bash
pip install --upgrade duckdb
```

## Future Improvements

Potential enhancements for the test suite:
- [ ] Add tests for BronzeLoader with PyPath integration
- [ ] Add tests for data augmentation (AugmentLoader)
- [ ] Add performance benchmarks
- [ ] Add schema validation tests
- [ ] Add data quality checks
- [ ] Increase test coverage to >80%
