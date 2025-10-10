#!/usr/bin/env python3
"""
Build interaction_evidence table from silver_interactions.

The interaction_evidence table stores evidence for each interaction from each source.
This includes detection methods, causal statements, sentences, and context annotations.

Schema: (interaction_id, detection_method, causal_statement, sentence, is_directed,
         annotations, entity_a_context, entity_b_context, provenance_id)
- interaction_id: Links to the interaction table
- detection_method: How the interaction was detected
- causal_statement: Textual description of causality
- sentence: Supporting sentence from literature
- is_directed: Whether the interaction is directed
- annotations: JSON field with general interaction annotations
- entity_a_context: JSON field with context annotations for entity A
- entity_b_context: JSON field with context annotations for entity B
- provenance_id: Links to provenance (source + reference)

Usage:
    python build_interaction_evidence.py --data-root /path/to/data --output-dir /path/to/output
"""

import polars as pl
from pathlib import Path
from glob import glob
import argparse
import sys

__all__ = [
    'build_interaction_evidence',
    'main',
]


def build_interaction_evidence(data_root: Path, output_dir: Path) -> pl.DataFrame:
    """
    Build interaction_evidence table from silver_interactions.

    This function:
    1. Reads silver_interactions files
    2. Maps entity identifiers to entity_id
    3. Maps (entity_a_id, entity_b_id, interaction_type) to interaction_id
    4. Maps (source, reference) to provenance_id
    5. Creates interaction_evidence records

    Args:
        data_root: Path to data directory containing silver files
        output_dir: Path to output directory (to read interaction and provenance tables)

    Returns:
        DataFrame with columns: id, interaction_id, detection_method, causal_statement,
                               sentence, is_directed, annotations, entity_a_context,
                               entity_b_context, provenance_id
    """
    print("\nStep 1: Loading entity_identifier, interaction, and provenance tables...")

    # Load entity_identifier table to map identifiers to entity_id
    entity_id_path = output_dir / "entity_identifier.parquet"
    if not entity_id_path.exists():
        raise FileNotFoundError(f"Entity identifier table not found at {entity_id_path}. Run Phase 1 first.")
    entity_identifiers = pl.read_parquet(entity_id_path)
    print(f"  Loaded {len(entity_identifiers)} entity identifiers")

    # Load interaction table to map (entity_a_id, entity_b_id, type_id) to interaction_id
    interaction_path = output_dir / "interaction.parquet"
    if not interaction_path.exists():
        print(f"  No interaction table found (expected if no interaction sources yet)")
        return pl.DataFrame({
            "id": [],
            "interaction_id": [],
            "detection_method": [],
            "causal_statement": [],
            "sentence": [],
            "is_directed": [],
            "annotations": [],
            "entity_a_context": [],
            "entity_b_context": [],
            "provenance_id": []
        })

    interactions = pl.read_parquet(interaction_path)
    print(f"  Loaded {len(interactions)} interactions")

    # Load provenance table
    provenance_path = output_dir / "provenance.parquet"
    if not provenance_path.exists():
        raise FileNotFoundError(f"Provenance table not found at {provenance_path}. Run build_provenance first.")

    provenance = pl.read_parquet(provenance_path)

    # Load source and reference tables to build provenance lookup
    source_path = output_dir / "source.parquet"
    sources = pl.read_parquet(source_path)

    reference_path = output_dir / "reference.parquet"
    references = None
    if reference_path.exists() and pl.read_parquet(reference_path).shape[0] > 0:
        references = pl.read_parquet(reference_path)

    # Create provenance lookup: (source_name, reference_value) -> provenance_id
    provenance_lookup = provenance.join(
        sources.select([pl.col("id").alias("source_id"), pl.col("name").alias("source_name")]),
        on="source_id",
        how="left"
    )

    if references is not None:
        provenance_lookup = provenance_lookup.join(
            references.select([pl.col("id").alias("reference_id"), pl.col("identifier").alias("reference_value")]),
            on="reference_id",
            how="left"
        )
    else:
        provenance_lookup = provenance_lookup.with_columns([
            pl.lit(None, dtype=pl.Utf8).alias("reference_value")
        ])

    provenance_lookup = provenance_lookup.select([
        "source_name",
        "reference_value",
        pl.col("id").alias("provenance_id")
    ])

    print(f"  Loaded {len(provenance)} provenance records")

    print("\nStep 2: Collecting interaction evidence from silver_interactions...")
    # Pattern for interaction files
    interaction_pattern = str(data_root / "*" / "*" / "silver" / "silver_interactions.parquet")
    interaction_files = glob(interaction_pattern)

    if not interaction_files:
        print(f"  No silver_interactions files found (expected if no interaction sources yet)")
        return pl.DataFrame({
            "id": [],
            "interaction_id": [],
            "detection_method": [],
            "causal_statement": [],
            "sentence": [],
            "is_directed": [],
            "annotations": [],
            "entity_a_context": [],
            "entity_b_context": [],
            "provenance_id": []
        })

    print(f"  Found {len(interaction_files)} silver_interactions files")

    # Process each file
    all_evidence = []

    for file in interaction_files:
        print(f"  Processing {Path(file).parent.parent.name}...")

        # Read interaction evidence
        df = pl.read_parquet(file)

        # Select relevant columns
        evidence_cols = [
            'entity_a_identifier',
            'entity_a_identifier_type',
            'entity_b_identifier',
            'entity_b_identifier_type',
            'interaction_type',
            'source',
            'reference_value'
        ]

        # Optional columns
        optional_cols = [
            'detection_method',
            'causal_statement',
            'sentence',
            'is_directed',
            'interaction_annotations',
            'entity_a_context',
            'entity_b_context'
        ]

        # Build select expression with defaults for missing columns
        select_exprs = [pl.col(c) for c in evidence_cols]

        for col in optional_cols:
            if col in df.columns:
                select_exprs.append(pl.col(col))
            else:
                if col == 'is_directed':
                    select_exprs.append(pl.lit(None, dtype=pl.Boolean).alias(col))
                else:
                    select_exprs.append(pl.lit(None, dtype=pl.Utf8).alias(col))

        evidence_df = df.select(select_exprs)

        # Rename interaction_annotations to annotations
        if 'interaction_annotations' in evidence_df.columns:
            evidence_df = evidence_df.rename({'interaction_annotations': 'annotations'})
        else:
            evidence_df = evidence_df.with_columns([
                pl.lit(None, dtype=pl.Utf8).alias('annotations')
            ])

        print(f"    Found {len(evidence_df)} interaction evidence records")

        all_evidence.append(evidence_df)

    if not all_evidence:
        print(f"  No interaction evidence found")
        return pl.DataFrame({
            "id": [],
            "interaction_id": [],
            "detection_method": [],
            "causal_statement": [],
            "sentence": [],
            "is_directed": [],
            "annotations": [],
            "entity_a_context": [],
            "entity_b_context": [],
            "provenance_id": []
        })

    # Combine all evidence
    combined_evidence = pl.concat(all_evidence)
    print(f"\n  Total interaction evidence records: {len(combined_evidence)}")

    print("\nStep 3: Mapping entity identifiers to entity_id...")

    # Map entity_a identifier to entity_id
    result = combined_evidence.join(
        entity_identifiers.select([
            pl.col('identifier').alias('entity_a_identifier'),
            pl.col('identifier_type_name').alias('entity_a_identifier_type'),
            pl.col('entity_id').alias('entity_a_id')
        ]),
        on=['entity_a_identifier', 'entity_a_identifier_type'],
        how='left'
    )

    # Map entity_b identifier to entity_id
    result = result.join(
        entity_identifiers.select([
            pl.col('identifier').alias('entity_b_identifier'),
            pl.col('identifier_type_name').alias('entity_b_identifier_type'),
            pl.col('entity_id').alias('entity_b_id')
        ]),
        on=['entity_b_identifier', 'entity_b_identifier_type'],
        how='left'
    )

    # Check for unmapped entities
    unmapped_a = result.filter(pl.col("entity_a_id").is_null())
    if len(unmapped_a) > 0:
        print(f"  WARNING: {len(unmapped_a)} evidence records have unmapped entity_a identifiers")

    unmapped_b = result.filter(pl.col("entity_b_id").is_null())
    if len(unmapped_b) > 0:
        print(f"  WARNING: {len(unmapped_b)} evidence records have unmapped entity_b identifiers")

    # Filter out unmapped records
    result = result.filter(
        pl.col("entity_a_id").is_not_null() &
        pl.col("entity_b_id").is_not_null()
    )
    print(f"  Mapped interaction evidence records: {len(result)}")

    print("\nStep 4: Mapping to interaction_id...")

    # Map (entity_a_id, entity_b_id, interaction_type) to interaction_id
    # Note: interactions table has sorted entity pairs, so we need to match that
    # We need to get the type_id first

    # Load CV terms to map interaction_type to type_id
    cv_term_path = output_dir / "cv_term.parquet"
    cv_terms = pl.read_parquet(cv_term_path)

    cv_namespace_path = output_dir / "cv_namespace.parquet"
    cv_namespaces = pl.read_parquet(cv_namespace_path)

    # Create interaction type lookup
    cv_term_lookup = cv_terms.join(
        cv_namespaces.select([pl.col("id").alias("namespace_id"), pl.col("name").alias("namespace_name")]),
        on="namespace_id",
        how="left"
    ).select(['namespace_name', pl.col('name').alias('type_name'), pl.col('id').alias('type_id')])

    # Map interaction_type to type_id (assume PSI-MI namespace for interactions)
    result = result.with_columns([
        pl.lit("PSI-MI").alias("type_namespace_name")
    ])

    result = result.join(
        cv_term_lookup,
        left_on=['type_namespace_name', 'interaction_type'],
        right_on=['namespace_name', 'type_name'],
        how='left'
    )

    # Check for unmapped types
    unmapped_types = result.filter(pl.col("type_id").is_null())
    if len(unmapped_types) > 0:
        print(f"  WARNING: {len(unmapped_types)} evidence records have unmapped interaction types")
        print(f"  Sample unmapped types:")
        print(unmapped_types.select(['interaction_type']).unique().head(5))

    # Filter out unmapped types
    result = result.filter(pl.col("type_id").is_not_null())
    print(f"  Mapped interaction evidence records (by type): {len(result)}")

    # Now map to interaction_id
    # Note: interactions table has entity_a_id <= entity_b_id (sorted)
    # So we need to sort our entity pairs the same way
    result = result.with_columns([
        pl.when(pl.col("entity_a_id") <= pl.col("entity_b_id"))
        .then(pl.col("entity_a_id"))
        .otherwise(pl.col("entity_b_id"))
        .alias("sorted_entity_a_id"),
        pl.when(pl.col("entity_a_id") <= pl.col("entity_b_id"))
        .then(pl.col("entity_b_id"))
        .otherwise(pl.col("entity_a_id"))
        .alias("sorted_entity_b_id")
    ])

    result = result.join(
        interactions.select([
            pl.col('entity_a_id').alias('sorted_entity_a_id'),
            pl.col('entity_b_id').alias('sorted_entity_b_id'),
            'type_id',
            pl.col('id').alias('interaction_id')
        ]),
        on=['sorted_entity_a_id', 'sorted_entity_b_id', 'type_id'],
        how='left'
    )

    # Check for unmapped interactions
    unmapped_int = result.filter(pl.col("interaction_id").is_null())
    if len(unmapped_int) > 0:
        print(f"  WARNING: {len(unmapped_int)} evidence records have unmapped interactions")

    # Filter out unmapped interactions
    result = result.filter(pl.col("interaction_id").is_not_null())
    print(f"  Mapped interaction evidence records (by interaction_id): {len(result)}")

    print("\nStep 5: Mapping to provenance_id...")

    # Map (source, reference_value) to provenance_id
    result = result.join(
        provenance_lookup.rename({'source': 'source_name'}),
        left_on=['source', 'reference_value'],
        right_on=['source_name', 'reference_value'],
        how='left'
    )

    # Check for unmapped provenance
    unmapped_prov = result.filter(pl.col("provenance_id").is_null())
    if len(unmapped_prov) > 0:
        print(f"  WARNING: {len(unmapped_prov)} evidence records have unmapped provenance")
        print(f"  Sample unmapped provenance:")
        print(unmapped_prov.select(['source', 'reference_value']).unique().head(5))

    # Filter out unmapped provenance
    result = result.filter(pl.col("provenance_id").is_not_null())
    print(f"  Mapped interaction evidence records: {len(result)}")

    print("\nStep 6: Creating final interaction_evidence table...")

    # Select final columns
    result = result.select([
        'interaction_id',
        'detection_method',
        'causal_statement',
        'sentence',
        'is_directed',
        'annotations',
        'entity_a_context',
        'entity_b_context',
        'provenance_id'
    ]).unique()

    # Add id column
    result = result.with_row_index(name="id", offset=1)

    # Reorder columns
    result = result.select([
        'id',
        'interaction_id',
        'detection_method',
        'causal_statement',
        'sentence',
        'is_directed',
        'annotations',
        'entity_a_context',
        'entity_b_context',
        'provenance_id'
    ])

    print(f"  Final interaction evidence records: {len(result)}")

    return result


def main():
    """Command-line interface."""
    parser = argparse.ArgumentParser(
        description="Build interaction_evidence table from silver_interactions",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Build interaction_evidence table
    python build_interaction_evidence.py --data-root databases/omnipath/data --output-dir output/gold
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
    print("BUILD INTERACTION_EVIDENCE TABLE")
    print("=" * 70)
    print(f"Data root: {args.data_root}")
    print(f"Output directory: {args.output_dir}")

    # Build interaction_evidence table
    try:
        interaction_evidence = build_interaction_evidence(args.data_root, args.output_dir)

        # Save to output directory
        print("\nStep 7: Saving interaction_evidence table...")
        output_path = args.output_dir / "interaction_evidence.parquet"
        interaction_evidence.write_parquet(output_path)
        print(f"  Saved to: {output_path}")

        # Print summary
        print("\n" + "=" * 70)
        print("Summary:")
        print("=" * 70)
        print(f"  Total interaction evidence records: {len(interaction_evidence)}")

        if len(interaction_evidence) > 0:
            print(f"\n  Unique interactions with evidence: {interaction_evidence['interaction_id'].n_unique()}")
            print(f"  Unique provenance sources: {interaction_evidence['provenance_id'].n_unique()}")

            print("\n  Sample interaction evidence records:")
            print(interaction_evidence.head(5))

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
