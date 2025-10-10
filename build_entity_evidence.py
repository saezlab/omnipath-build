#!/usr/bin/env python3
"""
Build entity_evidence table from silver_entities.

The entity_evidence table stores annotations for each entity from each source.
This includes all metadata and annotations provided by the source database.

Schema: (entity_id, provenance_id, annotations)
- entity_id: Links to the entity table
- provenance_id: Links to provenance (source + reference)
- annotations: JSON field with all annotations

Usage:
    python build_entity_evidence.py --data-root /path/to/data --output-dir /path/to/output
"""

import polars as pl
from pathlib import Path
from glob import glob
import argparse
import sys

__all__ = [
    'build_entity_evidence',
    'main',
]


def build_entity_evidence(data_root: Path, output_dir: Path) -> pl.DataFrame:
    """
    Build entity_evidence table from silver_entities.

    This function:
    1. Reads silver_entities files
    2. Extracts entities with non-empty annotations
    3. Maps identifiers to entity_id using entity_identifier table
    4. Maps (source, reference) to provenance_id using provenance table
    5. Creates entity_evidence records

    Args:
        data_root: Path to data directory containing silver files
        output_dir: Path to output directory (to read entity_identifier and provenance tables)

    Returns:
        DataFrame with columns: id, entity_id, provenance_id, annotations
    """
    print("\nStep 1: Loading entity_identifier and provenance tables...")

    # Load entity_identifier table to map identifiers to entity_id
    entity_id_path = output_dir / "entity_identifier.parquet"
    if not entity_id_path.exists():
        raise FileNotFoundError(f"Entity identifier table not found at {entity_id_path}. Run Phase 1 first.")
    entity_identifiers = pl.read_parquet(entity_id_path)
    print(f"  Loaded {len(entity_identifiers)} entity identifiers")

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
    print(f"  Entity provenance lookup: {len(provenance_lookup)} entries")

    print("\nStep 2: Collecting entity evidence from silver_entities...")
    # Pattern for entity files
    entity_pattern = str(data_root / "*" / "*" / "silver" / "silver_entities.parquet")
    entity_files = glob(entity_pattern)

    if not entity_files:
        print(f"  No silver_entities files found")
        return pl.DataFrame({
            "id": [],
            "entity_id": [],
            "provenance_id": [],
            "annotations": []
        })

    print(f"  Found {len(entity_files)} silver_entities files")

    # Process each file
    all_evidence = []

    for file in entity_files:
        print(f"  Processing {Path(file).parent.parent.name}...")

        # Read entities with non-empty annotations
        df = pl.read_parquet(file)

        # Filter for entities with annotations
        df = df.filter(
            pl.col("annotations").is_not_null() &
            (pl.col("annotations").cast(pl.Utf8) != "[]") &
            (pl.col("annotations").cast(pl.Utf8) != "{}")
        )

        if len(df) == 0:
            print(f"    No entities with annotations")
            continue

        # Create identifier columns for mapping (we'll use inchikey as primary, then fallback)
        # We need to find which identifier exists for each row
        identifier_cols = ['inchikey', 'lipidmaps_id', 'chebi_id', 'pubchem_cid',
                          'hmdb_id', 'kegg_id', 'metanetx_id', 'ramp_id',
                          'swisslipids_id', 'drugbank_id', 'cas_number']

        # Get the first non-null identifier for each row
        evidence_records = []
        for row in df.iter_rows(named=True):
            # Find the first available identifier
            identifier = None
            identifier_type = None
            for col in identifier_cols:
                if col in row and row[col] is not None and str(row[col]) != 'null':
                    identifier = str(row[col])
                    identifier_type = col
                    break

            if identifier is None:
                continue

            evidence_records.append({
                'identifier': identifier,
                'identifier_type_name': identifier_type,
                'source_name': row['source_database'],
                'annotations': row['annotations']
            })

        if not evidence_records:
            print(f"    No valid entity evidence records")
            continue

        evidence_df = pl.DataFrame(evidence_records)
        print(f"    Found {len(evidence_df)} entity evidence records")

        all_evidence.append(evidence_df)

    if not all_evidence:
        print(f"  No entity evidence found")
        return pl.DataFrame({
            "id": [],
            "entity_id": [],
            "provenance_id": [],
            "annotations": []
        })

    # Combine all evidence
    combined_evidence = pl.concat(all_evidence)
    print(f"\n  Total entity evidence records: {len(combined_evidence)}")

    print("\nStep 3: Mapping identifiers to entity_id...")

    # Map identifier to entity_id using entity_identifier table
    result = combined_evidence.join(
        entity_identifiers.select(['identifier', 'identifier_type_name', 'entity_id']),
        on=['identifier', 'identifier_type_name'],
        how='left'
    )

    # Check for unmapped entities
    unmapped = result.filter(pl.col("entity_id").is_null())
    if len(unmapped) > 0:
        print(f"  WARNING: {len(unmapped)} entity evidence records have unmapped identifiers")
        print(f"  Sample unmapped identifiers:")
        print(unmapped.select(['identifier', 'identifier_type_name']).head(5))

    # Filter out unmapped records
    result = result.filter(pl.col("entity_id").is_not_null())
    print(f"  Mapped entity evidence records: {len(result)}")

    print("\nStep 4: Mapping to provenance_id...")

    # Map source_name to provenance_id
    result = result.join(
        provenance_lookup,
        on='source_name',
        how='left'
    )

    # Check for unmapped provenance
    unmapped_prov = result.filter(pl.col("provenance_id").is_null())
    if len(unmapped_prov) > 0:
        print(f"  WARNING: {len(unmapped_prov)} entity evidence records have unmapped provenance")
        print(f"  Sample unmapped sources:")
        print(unmapped_prov.select(['source_name']).unique())

    # Filter out unmapped provenance
    result = result.filter(pl.col("provenance_id").is_not_null())
    print(f"  Mapped entity evidence records: {len(result)}")

    print("\nStep 5: Creating final entity_evidence table...")

    # Select final columns
    result = result.select([
        'entity_id',
        'provenance_id',
        'annotations'
    ]).unique()

    # Add id column
    result = result.with_row_index(name="id", offset=1)

    # Reorder columns
    result = result.select(['id', 'entity_id', 'provenance_id', 'annotations'])

    print(f"  Final entity evidence records: {len(result)}")

    return result


def main():
    """Command-line interface."""
    parser = argparse.ArgumentParser(
        description="Build entity_evidence table from silver_entities",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Build entity_evidence table
    python build_entity_evidence.py --data-root databases/omnipath/data --output-dir output/gold
        """
    )

    parser.add_argument(
        '--data-root',
        type=Path,
        default=Path("databases/omnipath/data"),
        help='Path to data directory containing silver files (default: databases/omnipath/data)'
    )

    parser.add_argument(
        '--output-dir',
        type=Path,
        default=Path("output/gold"),
        help='Path to output directory for gold tables (default: output/gold)'
    )

    args = parser.parse_args()

    # Validate data root exists
    if not args.data_root.exists():
        print(f"Error: Data root not found: {args.data_root}", file=sys.stderr)
        sys.exit(1)

    # Ensure output directory exists
    args.output_dir.mkdir(parents=True, exist_ok=True)

    print("=" * 70)
    print("BUILD ENTITY_EVIDENCE TABLE")
    print("=" * 70)
    print(f"Data root: {args.data_root}")
    print(f"Output directory: {args.output_dir}")

    # Build entity_evidence table
    try:
        entity_evidence = build_entity_evidence(args.data_root, args.output_dir)

        # Save to output directory
        print("\nStep 6: Saving entity_evidence table...")
        output_path = args.output_dir / "entity_evidence.parquet"
        entity_evidence.write_parquet(output_path)
        print(f"  Saved to: {output_path}")

        # Print summary
        print("\n" + "=" * 70)
        print("Summary:")
        print("=" * 70)
        print(f"  Total entity evidence records: {len(entity_evidence)}")

        if len(entity_evidence) > 0:
            print(f"\n  Unique entities with evidence: {entity_evidence['entity_id'].n_unique()}")
            print(f"  Unique provenance sources: {entity_evidence['provenance_id'].n_unique()}")

            print("\n  Sample entity evidence records:")
            print(entity_evidence.head(5))

        print("\n" + "=" * 70)
        print("DONE")
        print("=" * 70)

    except Exception as e:
        print(f"\nError: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
