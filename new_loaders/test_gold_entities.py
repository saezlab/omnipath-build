#!/usr/bin/env python3
"""Test gold Parquet generation for entities (HMDB)."""

import sys
import logging
from pathlib import Path
import duckdb

sys.path.insert(0, str(Path(__file__).parent.parent))
from new_loaders import SourceProcessor

__all__ = [
    'test_hmdb_entities',
]

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)


def test_hmdb_entities():
    """Test HMDB entities generation."""
    logger.info("\n" + "="*60)
    logger.info("Testing HMDB Entities → Gold Parquet")
    logger.info("="*60)

    # Step 1: Process bronze → silver → gold
    with SourceProcessor('metabo', 'hmdb') as processor:
        results = processor.process_full_pipeline()

    parquet_files = results['gold']

    logger.info(f"\n✓ Generated {len(parquet_files)} gold Parquet files:")
    for table_name, parquet_path in parquet_files.items():
        logger.info(f"  - {table_name}: {parquet_path}")

    # Step 2: Verify Parquet files exist
    assert 'entity' in parquet_files, "entity.parquet not created"
    assert 'entity_identifier' in parquet_files, "entity_identifier.parquet not created"
    assert 'compound' in parquet_files, "compound.parquet not created"
    assert 'cv_namespace' in parquet_files, "cv_namespace.parquet not created"
    assert 'cv_term' in parquet_files, "cv_term.parquet not created"
    assert 'source' in parquet_files, "source.parquet not created"
    assert 'provenance' in parquet_files, "provenance.parquet not created"

    # Step 3: Check data with DuckDB
    conn = duckdb.connect(':memory:')

    # Check entities
    entity_count = conn.execute(f"""
        SELECT COUNT(*) FROM '{parquet_files['entity']}'
    """).fetchone()[0]
    logger.info(f"\n✓ entity.parquet: {entity_count:,} entities")
    assert entity_count > 0, "No entities created"

    # Check compounds
    compound_count = conn.execute(f"""
        SELECT COUNT(*) FROM '{parquet_files['compound']}'
    """).fetchone()[0]
    logger.info(f"✓ compound.parquet: {compound_count:,} compounds")
    assert compound_count == entity_count, f"Mismatch: {compound_count} compounds vs {entity_count} entities"

    # Check identifiers
    identifier_count = conn.execute(f"""
        SELECT COUNT(*) FROM '{parquet_files['entity_identifier']}'
    """).fetchone()[0]
    logger.info(f"✓ entity_identifier.parquet: {identifier_count:,} identifiers")
    assert identifier_count > entity_count, "Should have more identifiers than entities"

    # Check sources
    source_count = conn.execute(f"""
        SELECT COUNT(*) FROM '{parquet_files['source']}'
    """).fetchone()[0]
    logger.info(f"✓ source.parquet: {source_count} sources")
    sources = conn.execute(f"""
        SELECT name FROM '{parquet_files['source']}'
    """).fetchall()
    logger.info(f"  Sources: {[s[0] for s in sources]}")
    assert source_count == 1, f"Expected 1 source (hmdb), got {source_count}"

    # Check provenance
    prov_count = conn.execute(f"""
        SELECT COUNT(*) FROM '{parquet_files['provenance']}'
    """).fetchone()[0]
    logger.info(f"✓ provenance.parquet: {prov_count} provenance records")
    assert prov_count >= 1, "Should have at least 1 provenance record"

    # Check CV namespaces and terms
    ns_count = conn.execute(f"""
        SELECT COUNT(*) FROM '{parquet_files['cv_namespace']}'
    """).fetchone()[0]
    logger.info(f"\n✓ cv_namespace.parquet: {ns_count} namespaces")

    namespaces = conn.execute(f"""
        SELECT name FROM '{parquet_files['cv_namespace']}'
    """).fetchall()
    logger.info(f"  Namespaces: {[ns[0] for ns in namespaces]}")
    assert 'entity_type' in [ns[0] for ns in namespaces], "entity_type namespace missing"
    assert 'identifier_type' in [ns[0] for ns in namespaces], "identifier_type namespace missing"

    cv_term_count = conn.execute(f"""
        SELECT COUNT(*) FROM '{parquet_files['cv_term']}'
    """).fetchone()[0]
    logger.info(f"✓ cv_term.parquet: {cv_term_count} CV terms")

    # Check entity types
    entity_types = conn.execute(f"""
        SELECT ct.name, COUNT(*) as cnt
        FROM '{parquet_files['entity']}' e
        JOIN '{parquet_files['cv_term']}' ct ON e.cv_term_id = ct.id
        GROUP BY ct.name
    """).fetchall()
    logger.info(f"\n✓ Entity types:")
    for etype, cnt in entity_types:
        logger.info(f"  - {etype}: {cnt:,} entities")

    # Check identifier types
    identifier_types = conn.execute(f"""
        SELECT ct.name, COUNT(*) as cnt
        FROM '{parquet_files['entity_identifier']}' ei
        JOIN '{parquet_files['cv_term']}' ct ON ei.cv_term_id = ct.id
        GROUP BY ct.name
        ORDER BY cnt DESC
    """).fetchall()
    logger.info(f"\n✓ Identifier types (top 10):")
    for itype, cnt in identifier_types[:10]:
        logger.info(f"  - {itype}: {cnt:,} identifiers")

    # Sample query with JOINs
    logger.info(f"\n✓ Sample entities with JOIN:")
    sample = conn.execute(f"""
        SELECT
            e.id,
            ei.identifier,
            ct_id.name as identifier_type,
            c.formula,
            c.molecular_weight
        FROM '{parquet_files['entity']}' e
        JOIN '{parquet_files['entity_identifier']}' ei ON ei.entity_id = e.id
        JOIN '{parquet_files['cv_term']}' ct_id ON ei.cv_term_id = ct_id.id
        LEFT JOIN '{parquet_files['compound']}' c ON c.entity_id = e.id
        WHERE ct_id.name = 'hmdb_id'
        LIMIT 5
    """).fetchall()

    for eid, identifier, id_type, formula, mw in sample:
        logger.info(f"  Entity {eid}: {identifier} ({id_type})")
        logger.info(f"    Formula: {formula}, MW: {mw}")

    # Check for duplicate entities (should not happen)
    logger.info(f"\n✓ Checking deduplication:")
    duplicate_check = conn.execute(f"""
        SELECT identifier, COUNT(*) as cnt
        FROM '{parquet_files['entity_identifier']}'
        WHERE identifier IN (
            SELECT identifier
            FROM '{parquet_files['entity_identifier']}'
            GROUP BY identifier
            HAVING COUNT(DISTINCT entity_id) > 1
        )
        GROUP BY identifier
        ORDER BY cnt DESC
        LIMIT 5
    """).fetchall()

    if duplicate_check:
        logger.warning(f"  Found {len(duplicate_check)} identifiers mapped to multiple entities:")
        for identifier, cnt in duplicate_check:
            logger.warning(f"    {identifier}: {cnt} mappings")
            # This can be valid for additional_identifiers, so just a warning
    else:
        logger.info(f"  ✓ All identifiers map to single entities")

    # Check foreign key integrity (entity_id references)
    logger.info(f"\n✓ Checking referential integrity:")

    orphaned_identifiers = conn.execute(f"""
        SELECT COUNT(*)
        FROM '{parquet_files['entity_identifier']}' ei
        LEFT JOIN '{parquet_files['entity']}' e ON ei.entity_id = e.id
        WHERE e.id IS NULL
    """).fetchone()[0]
    assert orphaned_identifiers == 0, f"Found {orphaned_identifiers} orphaned identifiers"
    logger.info(f"  ✓ No orphaned identifiers")

    orphaned_compounds = conn.execute(f"""
        SELECT COUNT(*)
        FROM '{parquet_files['compound']}' c
        LEFT JOIN '{parquet_files['entity']}' e ON c.entity_id = e.id
        WHERE e.id IS NULL
    """).fetchone()[0]
    assert orphaned_compounds == 0, f"Found {orphaned_compounds} orphaned compounds"
    logger.info(f"  ✓ No orphaned compounds")

    conn.close()

    logger.info("\n" + "="*60)
    logger.info("✅ HMDB Entities test PASSED!")
    logger.info("="*60)


if __name__ == '__main__':
    test_hmdb_entities()
