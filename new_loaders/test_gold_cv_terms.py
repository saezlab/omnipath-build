#!/usr/bin/env python3
"""Test gold Parquet generation for CV terms (PSI-MI)."""

import sys
import logging
from pathlib import Path
import duckdb

sys.path.insert(0, str(Path(__file__).parent.parent))
from new_loaders import SourceProcessor

__all__ = [
    'test_psimi_cv_terms',
]

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)


def test_psimi_cv_terms():
    """Test PSI-MI CV terms generation."""
    logger.info("\n" + "="*60)
    logger.info("Testing PSI-MI CV Terms → Gold Parquet")
    logger.info("="*60)

    # Step 1: Process bronze → silver → gold
    with SourceProcessor('metabo', 'psimi') as processor:
        results = processor.process_full_pipeline()

    parquet_files = results['gold']

    logger.info(f"\n✓ Generated {len(parquet_files)} gold Parquet files:")
    for table_name, parquet_path in parquet_files.items():
        logger.info(f"  - {table_name}: {parquet_path}")

    # Step 2: Verify Parquet files exist
    assert 'cv_namespace' in parquet_files, "cv_namespace.parquet not created"
    assert 'cv_term' in parquet_files, "cv_term.parquet not created"

    # Step 3: Check data with DuckDB
    conn = duckdb.connect(':memory:')

    # Check namespace
    ns_count = conn.execute(f"""
        SELECT COUNT(*) FROM '{parquet_files['cv_namespace']}'
    """).fetchone()[0]
    logger.info(f"\n✓ cv_namespace.parquet: {ns_count} namespaces")

    psi_mi_count = conn.execute(f"""
        SELECT COUNT(*) FROM '{parquet_files['cv_namespace']}'
        WHERE name = 'PSI-MI'
    """).fetchone()[0]
    assert psi_mi_count == 1, "PSI-MI namespace not created"
    logger.info(f"  - Found PSI-MI namespace")

    # Check terms
    total_terms = conn.execute(f"""
        SELECT COUNT(*) FROM '{parquet_files['cv_term']}'
    """).fetchone()[0]
    logger.info(f"\n✓ cv_term.parquet: {total_terms} total terms")

    psimi_terms = conn.execute(f"""
        SELECT COUNT(*)
        FROM '{parquet_files['cv_term']}' ct
        JOIN '{parquet_files['cv_namespace']}' ns ON ct.namespace_id = ns.id
        WHERE ns.name = 'PSI-MI'
    """).fetchone()[0]
    logger.info(f"  - PSI-MI terms: {psimi_terms}")
    # Note: Silver has 1647 rows but 2 are duplicates (pubchem, ampylation assay)
    # so we expect 1645 unique terms after deduplication
    assert psimi_terms == 1645, f"Expected 1645 unique PSI-MI terms, got {psimi_terms}"

    # Check sample terms
    sample_terms = conn.execute(f"""
        SELECT ct.accession, ct.name, ct.description
        FROM '{parquet_files['cv_term']}' ct
        JOIN '{parquet_files['cv_namespace']}' ns ON ct.namespace_id = ns.id
        WHERE ns.name = 'PSI-MI'
        LIMIT 5
    """).fetchall()

    logger.info(f"\n✓ Sample PSI-MI terms:")
    for accession, name, description in sample_terms:
        desc_preview = (description[:60] + '...') if description and len(description) > 60 else description
        logger.info(f"  - {accession}: {name}")
        logger.info(f"    {desc_preview}")

    # Check for duplicates
    duplicates = conn.execute(f"""
        SELECT namespace_id, name, COUNT(*) as cnt
        FROM '{parquet_files['cv_term']}'
        GROUP BY namespace_id, name
        HAVING COUNT(*) > 1
    """).fetchall()

    if duplicates:
        logger.error(f"Found {len(duplicates)} duplicate terms!")
        for ns_id, name, cnt in duplicates[:5]:
            logger.error(f"  - namespace_id={ns_id}, name={name}, count={cnt}")
        assert False, "Duplicate CV terms found"
    else:
        logger.info(f"\n✓ No duplicate terms (namespace_id, name) constraint satisfied")

    conn.close()

    logger.info("\n" + "="*60)
    logger.info("✅ PSI-MI CV Terms test PASSED!")
    logger.info("="*60)


if __name__ == '__main__':
    test_psimi_cv_terms()
