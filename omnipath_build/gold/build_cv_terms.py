#!/usr/bin/env python3
"""
CV Terms builder for gold tables.

This module aggregates CV terms from:
1. silver_cv_terms.parquet files (ontology terms from sources)
2. Auto-generated terms from silver table fields (identifier types, etc.)

Similar to the old augment_loader approach but using silver tables.
"""

import polars as pl
from pathlib import Path
from glob import glob
from typing import Tuple

__all__ = [
    'build_cv_terms',
]


def build_cv_terms(data_root: Path, output_dir: Path) -> Tuple[pl.DataFrame, pl.DataFrame]:
    """
    Build cv_namespace and cv_term tables from silver data.

    This function:
    1. Reads all silver_cv_terms.parquet files
    2. Auto-generates CV terms from silver tables (identifier types, etc.)
    3. Deduplicates by (namespace, name) pairs
    4. Creates cv_namespace table with unique namespaces
    5. Creates cv_term table mapping to namespace_id

    Args:
        data_root: Path to data directory containing silver files
        output_dir: Path to output directory (to read entity_identifier table)

    Returns:
        Tuple of (cv_namespace DataFrame, cv_term DataFrame)
    """
    print("\nStep 1: Finding silver_cv_terms files...")
    pattern = str(data_root / "*" / "*" / "silver" / "silver_cv_terms.parquet")
    cv_files = glob(pattern)

    if cv_files:
        print(f"  Found {len(cv_files)} silver_cv_terms files")
    else:
        print(f"  No silver_cv_terms files found")

    # Step 2: Read and concatenate all CV term files (if any exist)
    print("\nStep 2: Reading silver_cv_terms files...")
    cv_term_records = []

    if cv_files:
        for file in cv_files:
            df = pl.read_parquet(file)
            # Select only the columns we need from the schema
            df = df.select([
                'term_accession',
                'term_name',
                'namespace',
                'term_definition',
                'source_database'
            ])
            # Convert to records for easier handling
            for row in df.iter_rows(named=True):
                cv_term_records.append({
                    'namespace': row['namespace'],
                    'name': row['term_name'],
                    'accession': row['term_accession'],
                    'description': row['term_definition']
                })
        print(f"  Loaded {len(cv_term_records):,} CV terms from files")
    else:
        print(f"  No CV terms loaded from files")

    # Step 3: Auto-generate CV terms from silver tables
    print("\nStep 3: Auto-generating CV terms from silver tables...")

    # Read entity_identifier table to get identifier types
    entity_id_path = output_dir / "entity_identifier.parquet"
    if entity_id_path.exists():
        df_eid = pl.read_parquet(entity_id_path)
        # Get unique (namespace, identifier_type_name) pairs
        unique_id_types = df_eid.select([
            pl.col('identifier_type_namespace_name').alias('namespace'),
            pl.col('identifier_type_name').alias('name')
        ]).unique()

        for row in unique_id_types.iter_rows(named=True):
            if row['namespace'] and row['name']:
                cv_term_records.append({
                    'namespace': row['namespace'],
                    'name': row['name'],
                    'accession': None,
                    'description': f"Auto-generated identifier type: {row['name']}"
                })
        print(f"  Added {len(unique_id_types):,} identifier type terms")

    # Look for silver_interactions to get interaction types, detection methods, etc.
    interaction_pattern = str(data_root / "*" / "*" / "silver" / "silver_interactions.parquet")
    interaction_files = glob(interaction_pattern)

    interaction_term_count = 0
    if interaction_files:
        for file in interaction_files:
            df_int = pl.read_parquet(file)

            # Interaction types
            if 'interaction_type' in df_int.columns:
                for val in df_int['interaction_type'].unique().drop_nulls():
                    cv_term_records.append({
                        'namespace': 'PSI-MI',
                        'name': str(val),
                        'accession': str(val),  # Assume it's already an accession
                        'description': f"Interaction type from silver data"
                    })
                    interaction_term_count += 1

            # Detection methods
            if 'detection_method' in df_int.columns:
                for val in df_int['detection_method'].unique().drop_nulls():
                    cv_term_records.append({
                        'namespace': 'PSI-MI',
                        'name': str(val),
                        'accession': str(val),
                        'description': f"Detection method from silver data"
                    })
                    interaction_term_count += 1

        print(f"  Added {interaction_term_count:,} interaction-related terms")
    else:
        print(f"  No interaction files found, skipping interaction term generation")

    print(f"  Total CV term records collected: {len(cv_term_records):,}")

    # Step 4: Convert to DataFrame and deduplicate by (namespace, name)
    print("\nStep 4: Converting to DataFrame and deduplicating...")
    print(f"  Before deduplication: {len(cv_term_records):,}")

    # Create DataFrame
    if not cv_term_records:
        print("  No CV terms to process!")
        return pl.DataFrame(), pl.DataFrame()

    all_cv_terms = pl.DataFrame(cv_term_records)

    # Deduplicate by (namespace, name) pairs - keep first occurrence
    deduplicated = all_cv_terms.unique(subset=['namespace', 'name'], keep='first')

    print(f"  After deduplication: {len(deduplicated):,}")
    print(f"  Duplicates removed: {len(all_cv_terms) - len(deduplicated):,}")

    # Step 5: Create cv_namespace table
    print("\nStep 5: Creating cv_namespace table...")
    unique_namespaces = deduplicated.select('namespace').unique().sort('namespace')

    cv_namespace = unique_namespaces.with_columns([
        pl.lit(None).alias('uri'),
        pl.lit(None).alias('description')
    ]).with_row_index('id', offset=1)

    # Reorder columns: id, name, uri, description
    cv_namespace = cv_namespace.select(['id',
                                       pl.col('namespace').alias('name'),
                                       'uri',
                                       'description'])

    print(f"  Total unique namespaces: {len(cv_namespace):,}")
    print(f"  Namespaces: {cv_namespace['name'].to_list()}")

    # Step 6: Create namespace_id mapping
    print("\nStep 6: Creating cv_term table with namespace mapping...")

    # Join to get namespace_id
    cv_term = deduplicated.join(
        cv_namespace.select(['id', 'name']),
        left_on='namespace',
        right_on='name',
        how='left'
    ).rename({'id': 'namespace_id'})

    # Create final cv_term table - use columns from our records
    cv_term = cv_term.select([
        pl.col('namespace_id'),
        pl.col('accession'),
        pl.col('name'),
        pl.col('description')
    ]).with_columns([
        pl.lit(False).alias('is_obsolete'),
        pl.lit(None).cast(pl.String).alias('replaces'),
        pl.lit(None).cast(pl.String).alias('replaced_by')
    ])

    # Add auto-increment ID
    cv_term = cv_term.with_row_index('id', offset=1)

    # Reorder columns to match schema
    cv_term = cv_term.select([
        'id',
        'namespace_id',
        'accession',
        'name',
        'description',
        'is_obsolete',
        'replaces',
        'replaced_by'
    ])

    print(f"  Total CV terms: {len(cv_term):,}")

    return cv_namespace, cv_term
