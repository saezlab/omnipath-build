#!/usr/bin/env python3
"""Test PSI-MI bronze → silver transformation."""

import sys
import logging
from pathlib import Path

# Add parent to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from new_loaders import SourceProcessor

__all__ = [
    'main',
]

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger(__name__)

def main():
    """Test PSI-MI processing."""
    logger.info("=" * 60)
    logger.info("Testing PSI-MI bronze → silver transformation")
    logger.info("=" * 60)

    # Create processor
    with SourceProcessor(
        database_name='metabo',
        source_module='psimi'
    ) as processor:

        # Process to silver
        logger.info("\n📊 Processing bronze → silver...")
        results = processor.process_to_silver()

        # Show results
        logger.info("\n✅ Processing complete!")
        logger.info(f"Created {len(results)} silver parquet file(s):")
        for function_name, parquet_file in results.items():
            size_mb = parquet_file.stat().st_size / (1024 * 1024)
            logger.info(f"  - {function_name}: {parquet_file.name} ({size_mb:.2f} MB)")

        # Verify the data
        logger.info("\n🔍 Verifying silver data...")
        import duckdb
        conn = duckdb.connect(":memory:")

        for function_name, parquet_file in results.items():
            logger.info(f"\nFunction: {function_name}")

            # Row count
            count = conn.execute(f"SELECT COUNT(*) FROM '{parquet_file}'").fetchone()[0]
            logger.info(f"  Rows: {count:,}")

            # Column names
            result = conn.execute(f"SELECT * FROM '{parquet_file}' LIMIT 0")
            columns = [desc[0] for desc in result.description]
            logger.info(f"  Columns: {', '.join(columns[:5])}... ({len(columns)} total)")

            # Sample row
            sample = conn.execute(f"SELECT * FROM '{parquet_file}' LIMIT 1").fetchone()
            logger.info(f"  Sample - namespace: {sample[0] if len(sample) > 0 else 'N/A'}")
            logger.info(f"  Sample - term_accession: {sample[1] if len(sample) > 1 else 'N/A'}")
            logger.info(f"  Sample - term_name: {sample[2] if len(sample) > 2 else 'N/A'}")

        conn.close()

    logger.info("\n" + "=" * 60)
    logger.info("✅ Test completed successfully!")
    logger.info("=" * 60)


if __name__ == '__main__':
    main()
