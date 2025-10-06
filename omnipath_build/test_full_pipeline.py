#!/usr/bin/env python3
"""Test full bronze → silver → gold pipeline for HMDB and PSI-MI."""

import sys
import logging
from pathlib import Path

# Add parent to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from omnipath_build import SourceProcessor

__all__ = [
    'main',
    'test_source',
]

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(levelname)s - %(message)s'
)

logger = logging.getLogger(__name__)

def test_source(source_name: str):
    """Test full pipeline for a source."""
    logger.info("\n" + "=" * 70)
    logger.info(f"Testing {source_name.upper()} Full Pipeline")
    logger.info("=" * 70)

    with SourceProcessor(
        database_name='metabo',
        source_module=source_name
    ) as processor:
        # Run full pipeline
        results = processor.process_full_pipeline()

    # Verify results
    logger.info(f"\n📊 Pipeline Results for {source_name}:")
    logger.info(f"  Silver files: {len(results['silver'])}")
    for name, path in results['silver'].items():
        size_mb = path.stat().st_size / (1024 * 1024)
        logger.info(f"    - {name}: {size_mb:.2f} MB")

    logger.info(f"  Gold files: {len(results['gold'])}")
    for name, path in results['gold'].items():
        size_mb = path.stat().st_size / (1024 * 1024)
        logger.info(f"    - {name}: {size_mb:.2f} MB")

    return results


def main():
    """Run tests for both sources."""
    # Test HMDB
    hmdb_results = test_source('hmdb')

    # Test PSI-MI
    psimi_results = test_source('psimi')

    # Summary
    logger.info("\n" + "=" * 70)
    logger.info("✅ ALL TESTS PASSED!")
    logger.info("=" * 70)
    logger.info("\nSummary:")
    logger.info(f"  HMDB:   {len(hmdb_results['silver'])} silver → {len(hmdb_results['gold'])} gold")
    logger.info(f"  PSI-MI: {len(psimi_results['silver'])} silver → {len(psimi_results['gold'])} gold")

    # Show where files are
    base_path = Path(__file__).parent.parent / "omnipath_build" / "databases" / "metabo"
    logger.info(f"\n📁 Files created:")
    logger.info(f"  Silver: {base_path / 'silver_parquet'}")
    logger.info(f"  Gold:   {base_path / 'gold_parquet'}")


if __name__ == '__main__':
    main()
