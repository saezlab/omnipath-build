#!/usr/bin/env python3
"""Test gold Parquet generation for entities (HMDB) with small sample."""

import sys
import logging
from pathlib import Path
import duckdb

sys.path.insert(0, str(Path(__file__).parent.parent))
from new_loaders.gold_parquet_builder import GoldParquetBuilder

__all__ = [
    'test_hmdb_entities_small',
]

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)


def test_hmdb_entities_small():
    """Test HMDB entities generation with small sample."""
    logger.info("\n" + "="*60)
    logger.info("Testing HMDB Entities (Small Sample)")
    logger.info("="*60)

    # Step 1: Create a small sample from silver data
    silver_file = Path('/Users/jschaul/Code/omnipath_build/omnipath_build/databases/metabo/silver_parquet/hmdb_compounds_for_metabo_silver_entities.parquet')

    if not silver_file.exists():
        logger.error(f"Silver file not found: {silver_file}")
        return

    # Create sample file with first 100 rows
    conn = duckdb.connect(':memory:')
    sample_file = Path('/tmp/hmdb_sample_100.parquet')

    logger.info(f"Creating sample of 100 rows from silver data...")
    conn.execute(f"""
        COPY (
            SELECT * FROM '{silver_file}'
            LIMIT 100
        ) TO '{sample_file}' (FORMAT PARQUET)
    """)

    row_count = conn.execute(f"SELECT COUNT(*) FROM '{sample_file}'").fetchone()[0]
    logger.info(f"✓ Created sample with {row_count} rows")
    conn.close()

    # Step 2: Process sample with GoldParquetBuilder
    output_dir = Path('/tmp/test_gold_hmdb_small')
    output_dir.mkdir(parents=True, exist_ok=True)

    logger.info(f"\nBuilding gold tables from sample...")
    with GoldParquetBuilder('hmdb', output_dir) as builder:
        logger.info("Step 1: Building entities...")
        stats = builder.build_entities(sample_file)
        logger.info(f"✓ Stats: {stats}")

        logger.info("\nStep 2: Exporting to Parquet...")
        parquet_files = builder.export_all_tables()

    # Step 3: Verify results
    logger.info(f"\n✓ Generated {len(parquet_files)} gold Parquet files:")
    for table_name, parquet_path in parquet_files.items():
        conn = duckdb.connect(':memory:')
        count = conn.execute(f"SELECT COUNT(*) FROM '{parquet_path}'").fetchone()[0]
        logger.info(f"  - {table_name}: {count:,} rows")
        conn.close()

    # Step 4: Detailed checks
    conn = duckdb.connect(':memory:')

    # Check entities
    entity_count = conn.execute(f"""
        SELECT COUNT(*) FROM '{parquet_files['entity']}'
    """).fetchone()[0]
    logger.info(f"\n✓ Entities created: {entity_count}")

    # Check identifiers
    identifier_count = conn.execute(f"""
        SELECT COUNT(*) FROM '{parquet_files['entity_identifier']}'
    """).fetchone()[0]
    logger.info(f"✓ Identifiers created: {identifier_count}")

    # Check identifier distribution
    logger.info(f"\n✓ Identifiers per entity:")
    id_dist = conn.execute(f"""
        SELECT
            COUNT(*) as identifiers_per_entity,
            COUNT(DISTINCT entity_id) as entity_count
        FROM '{parquet_files['entity_identifier']}'
        GROUP BY entity_id
        ORDER BY identifiers_per_entity DESC
        LIMIT 5
    """).fetchall()

    for id_count, entity_cnt in id_dist:
        logger.info(f"  {entity_cnt} entities with {id_count} identifiers")

    # Sample entities with all their identifiers
    logger.info(f"\n✓ Sample entities with identifiers:")
    sample = conn.execute(f"""
        SELECT
            e.id,
            ct_type.name as entity_type,
            ct_id.name as identifier_type,
            ei.identifier,
            c.formula,
            c.molecular_weight
        FROM '{parquet_files['entity']}' e
        JOIN '{parquet_files['cv_term']}' ct_type ON e.cv_term_id = ct_type.id
        JOIN '{parquet_files['entity_identifier']}' ei ON ei.entity_id = e.id
        JOIN '{parquet_files['cv_term']}' ct_id ON ei.cv_term_id = ct_id.id
        LEFT JOIN '{parquet_files['compound']}' c ON c.entity_id = e.id
        ORDER BY e.id, ei.id
        LIMIT 10
    """).fetchall()

    current_entity = None
    for eid, etype, id_type, identifier, formula, mw in sample:
        if eid != current_entity:
            logger.info(f"\n  Entity {eid} ({etype}):")
            logger.info(f"    Formula: {formula}, MW: {mw}")
            current_entity = eid
        logger.info(f"    - {id_type}: {identifier[:50]}...")

    conn.close()

    logger.info("\n" + "="*60)
    logger.info("✅ Small sample test PASSED!")
    logger.info("="*60)


if __name__ == '__main__':
    test_hmdb_entities_small()
