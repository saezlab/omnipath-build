"""OmniPath Build: Parquet-based data pipeline with numbered loaders.

Pipeline stages:
0. Database Manager - Database lifecycle & orchestration
1. Bronze Loader   - PyPath → Bronze parquet
2. Silver Loader   - Bronze → Silver transformations
3. Gold Loader     - Silver → Gold (3-phase: extract → dedup → FK resolve)
4. Augment Loader  - Data augmentation (CV terms, compounds, publications)
"""

# Import numbered loaders using importlib (Python requires this for numeric module names)
import importlib

_db_manager = importlib.import_module('.0_database_manager', package='omnipath_build')
DatabaseManager = _db_manager.DatabaseManager

_bronze = importlib.import_module('.1_bronze_loader', package='omnipath_build')
PyPathBronzeLoader = _bronze.PyPathBronzeLoader

_silver = importlib.import_module('.2_silver_loader', package='omnipath_build')
SilverLoader = _silver.SilverLoader

_gold = importlib.import_module('.3_gold_loader', package='omnipath_build')
run_gold_loader = _gold.run_gold_loader


__all__ = [
    'DatabaseManager',
    'PyPathBronzeLoader',
    'SilverLoader',
    'run_gold_loader',
    'RDKit_AVAILABLE',
]
