#!/usr/bin/env python3
"""
Build membership table from silver_entities members.

The membership table stores complex member relationships.
Each row represents a member of a complex (parent-child relationship).

Schema: (parent_id, member_id, role_id, stoichiometry, provenance_id)
- parent_id: The complex entity
- member_id: The member entity
- role_id: The role CV term (e.g., 'member', 'substrate', 'product')
- stoichiometry: Number of this member in the complex
- provenance_id: Links to provenance (source + reference)

Usage:
    python build_membership.py --data-root /path/to/data --output-dir /path/to/output
"""

import polars as pl
from pathlib import Path
from glob import glob
import argparse
import sys
import json

__all__ = [
    'build_membership',
]


def build_membership(data_root: Path, output_dir: Path) -> pl.DataFrame:
    """
    Build membership table from silver_entities members.

    This function:
    1. Reads silver_entities files
    2. Extracts members JSON field
    3. Maps parent and member identifiers to entity_id
    4. Maps role to role_id (CV term)
    5. Maps (source, reference) to provenance_id
    6. Creates membership records

    Args:
        data_root: Path to data directory containing silver files
        output_dir: Path to output directory (to read entity_identifier, cv_term, and provenance tables)

    Returns:
        DataFrame with columns: id, parent_id, member_id, role_id, stoichiometry, provenance_id
    """
    print("\nStep 1: Loading entity_identifier, cv_term, and provenance tables...")

    # Load entity_identifier table to map identifiers to entity_id
    entity_id_path = output_dir / "entity_identifier.parquet"
    if not entity_id_path.exists():
        raise FileNotFoundError(f"Entity identifier table not found at {entity_id_path}. Run Phase 1 first.")
    entity_identifiers = pl.read_parquet(entity_id_path)
    print(f"  Loaded {len(entity_identifiers)} entity identifiers")

    # Load CV terms to map role names to role_id
    cv_term_path = output_dir / "cv_term.parquet"
    if not cv_term_path.exists():
        raise FileNotFoundError(f"CV term table not found at {cv_term_path}. Run Phase 1 first.")
    cv_terms = pl.read_parquet(cv_term_path)

    cv_namespace_path = output_dir / "cv_namespace.parquet"
    cv_namespaces = pl.read_parquet(cv_namespace_path)

    # Create role lookup: (namespace, role_name) -> role_id
    cv_term_lookup = cv_terms.join(
        cv_namespaces.select([pl.col("id").alias("namespace_id"), pl.col("name").alias("namespace_name")]),
        on="namespace_id",
        how="left"
    ).select(['namespace_name', pl.col('name').alias('role_name'), pl.col('id').alias('role_id')])

    print(f"  Loaded {len(cv_terms)} CV terms")

    # Load provenance table to map (source, reference) to provenance_id
    provenance_path = output_dir / "provenance.parquet"
    if not provenance_path.exists():
        raise FileNotFoundError(f"Provenance table not found at {provenance_path}. Run build_provenance first.")

    provenance = pl.read_parquet(provenance_path)

    # Load source table to map source_id back to source_name
    source_path = output_dir / "source.parquet"
    sources = pl.read_parquet(source_path)

    # Create a lookup table: source_name -> provenance_id (for entity provenance without references)
    provenance_lookup = provenance.join(
        sources.select([pl.col("id").alias("source_id"), pl.col("name").alias("source_name")]),
        on="source_id",
        how="left"
    ).filter(
        pl.col("reference_id").is_null()  # Entity provenance has no references
    ).select(["source_name", pl.col("id").alias("provenance_id")])

    print(f"  Loaded {len(provenance)} provenance records")

    print("\nStep 2: Collecting membership from silver_entities members...")
    # Pattern for entity files
    entity_pattern = str(data_root / "*" / "*" / "silver" / "silver_entities.parquet")
    entity_files = glob(entity_pattern)

    if not entity_files:
        print(f"  No silver_entities files found")
        return pl.DataFrame({
            "id": [],
            "parent_id": [],
            "member_id": [],
            "role_id": [],
            "stoichiometry": [],
            "provenance_id": []
        })

    print(f"  Found {len(entity_files)} silver_entities files")

    # Process each file
    all_membership = []

    for file in entity_files:
        print(f"  Processing {Path(file).parent.parent.name}...")

        # Read entities with members
        df = pl.read_parquet(file)

        # Check if members column exists
        if 'members' not in df.columns:
            print(f"    No members column")
            continue

        # Filter for entities with members
        df = df.filter(
            pl.col("members").is_not_null() &
            (pl.col("members").cast(pl.Utf8) != "null") &
            (pl.col("members").cast(pl.Utf8) != "[]")
        )

        if len(df) == 0:
            print(f"    No entities with members")
            continue

        # Create identifier columns for mapping (we'll use the first available identifier as parent)
        identifier_cols = ['inchikey', 'lipidmaps_id', 'chebi_id', 'pubchem_cid',
                          'hmdb_id', 'kegg_id', 'metanetx_id', 'ramp_id',
                          'swisslipids_id', 'drugbank_id', 'cas_number']

        # Parse members JSON and create membership records
        membership_records = []
        for row in df.iter_rows(named=True):
            # Find the parent identifier
            parent_identifier = None
            parent_identifier_type = None
            for col in identifier_cols:
                if col in row and row[col] is not None and str(row[col]) != 'null':
                    parent_identifier = str(row[col])
                    parent_identifier_type = col
                    break

            if parent_identifier is None:
                continue

            # Parse members JSON
            try:
                members_json = row['members']
                if isinstance(members_json, str):
                    members = json.loads(members_json)
                elif isinstance(members_json, bytes):
                    members = json.loads(members_json.decode('utf-8'))
                else:
                    members = members_json

                if not isinstance(members, list):
                    continue

                # Create membership record for each member
                for member in members:
                    if not isinstance(member, dict):
                        continue

                    member_id = member.get('member_id')
                    member_id_type = member.get('member_id_type')
                    role = member.get('role', 'member')
                    stoichiometry = member.get('stoichiometry')

                    if not member_id or not member_id_type:
                        continue

                    membership_records.append({
                        'parent_identifier': parent_identifier,
                        'parent_identifier_type': parent_identifier_type,
                        'member_identifier': str(member_id),
                        'member_identifier_type': str(member_id_type),
                        'role_name': str(role) if role else 'member',
                        'stoichiometry': float(stoichiometry) if stoichiometry is not None else None,
                        'source_name': row['source']
                    })

            except (json.JSONDecodeError, TypeError, AttributeError) as e:
                # Skip malformed JSON
                continue

        if not membership_records:
            print(f"    No valid membership records")
            continue

        membership_df = pl.DataFrame(membership_records)
        print(f"    Found {len(membership_df)} membership records")

        all_membership.append(membership_df)

    if not all_membership:
        print(f"  No membership records found")
        return pl.DataFrame({
            "id": [],
            "parent_id": [],
            "member_id": [],
            "role_id": [],
            "stoichiometry": [],
            "provenance_id": []
        })

    # Combine all membership records
    combined_membership = pl.concat(all_membership)
    print(f"\n  Total membership records: {len(combined_membership)}")

    print("\nStep 3: Mapping parent identifiers to entity_id...")

    # Map parent identifier to entity_id
    result = combined_membership.join(
        entity_identifiers.select([
            pl.col('identifier').alias('parent_identifier'),
            pl.col('identifier_type_name').alias('parent_identifier_type'),
            pl.col('entity_id').alias('parent_id')
        ]),
        on=['parent_identifier', 'parent_identifier_type'],
        how='left'
    )

    # Check for unmapped parents
    unmapped = result.filter(pl.col("parent_id").is_null())
    if len(unmapped) > 0:
        print(f"  WARNING: {len(unmapped)} membership records have unmapped parent identifiers")
        print(f"  Sample unmapped parent identifiers:")
        print(unmapped.select(['parent_identifier', 'parent_identifier_type']).head(5))

    # Filter out unmapped records
    result = result.filter(pl.col("parent_id").is_not_null())
    print(f"  Mapped membership records (by parent): {len(result)}")

    print("\nStep 4: Mapping member identifiers to entity_id...")

    # Map member identifier to entity_id
    result = result.join(
        entity_identifiers.select([
            pl.col('identifier').alias('member_identifier'),
            pl.col('identifier_type_name').alias('member_identifier_type'),
            pl.col('entity_id').alias('member_id')
        ]),
        on=['member_identifier', 'member_identifier_type'],
        how='left'
    )

    # Check for unmapped members
    unmapped = result.filter(pl.col("member_id").is_null())
    if len(unmapped) > 0:
        print(f"  WARNING: {len(unmapped)} membership records have unmapped member identifiers")
        print(f"  Sample unmapped member identifiers:")
        print(unmapped.select(['member_identifier', 'member_identifier_type']).head(5))

    # Filter out unmapped records
    result = result.filter(pl.col("member_id").is_not_null())
    print(f"  Mapped membership records (by member): {len(result)}")

    print("\nStep 5: Mapping role names to role_id...")

    # Map role to role_id (assume OmniPath namespace)
    result = result.with_columns([
        pl.lit("OmniPath").alias("namespace_name")
    ])

    result = result.join(
        cv_term_lookup,
        left_on=['namespace_name', 'role_name'],
        right_on=['namespace_name', 'role_name'],
        how='left'
    )

    # Check for unmapped roles
    unmapped_roles = result.filter(pl.col("role_id").is_null())
    if len(unmapped_roles) > 0:
        print(f"  WARNING: {len(unmapped_roles)} membership records have unmapped roles")
        print(f"  Sample unmapped roles:")
        print(unmapped_roles.select(['role_name']).unique())

    # Filter out unmapped roles
    result = result.filter(pl.col("role_id").is_not_null())
    print(f"  Mapped membership records (by role): {len(result)}")

    print("\nStep 6: Mapping to provenance_id...")

    # Map source_name to provenance_id
    result = result.join(
        provenance_lookup,
        on='source_name',
        how='left'
    )

    # Check for unmapped provenance
    unmapped_prov = result.filter(pl.col("provenance_id").is_null())
    if len(unmapped_prov) > 0:
        print(f"  WARNING: {len(unmapped_prov)} membership records have unmapped provenance")
        print(f"  Sample unmapped sources:")
        print(unmapped_prov.select(['source_name']).unique())

    # Filter out unmapped provenance
    result = result.filter(pl.col("provenance_id").is_not_null())
    print(f"  Mapped membership records: {len(result)}")

    print("\nStep 7: Creating final membership table...")

    # Select final columns
    result = result.select([
        'parent_id',
        'member_id',
        'role_id',
        'stoichiometry',
        'provenance_id'
    ]).unique()

    # Add id column
    result = result.with_row_index(name="id", offset=1)

    # Reorder columns
    result = result.select(['id', 'parent_id', 'member_id', 'role_id', 'stoichiometry', 'provenance_id'])

    print(f"  Final membership records: {len(result)}")

    return result
