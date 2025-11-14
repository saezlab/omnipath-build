# Refactored build_local_tables for new Entity schema - VECTORIZED
from __future__ import annotations
from pathlib import Path
from collections.abc import Iterable
import logging
import polars as pl

__all__ = ["build_local_tables"]
logger = logging.getLogger(__name__)

# --- Helpers -----------------------------------------------------------------

def _iter_parquet_files(root: Path) -> Iterable[Path]:
    """Iterate through all parquet files in subdirectories (recursively).

    This handles both flat structures like:
        data/guidetopharma/*.parquet
    And nested structures like:
        data/ontologies/gene_ontology/*.parquet
    """
    for d in sorted(root.glob("*")):
        if d.is_dir():
            # First, check for parquet files directly in this directory
            direct_parquet = list(d.glob("*.parquet"))
            if direct_parquet:
                # This is a source directory with parquet files
                yield from sorted(direct_parquet)
            else:
                # This might be a container directory (like ontologies/)
                # Look one level deeper for source subdirectories
                for subdir in sorted(d.glob("*")):
                    if subdir.is_dir():
                        yield from sorted(subdir.glob("*.parquet"))


def _load_source_data(root: Path) -> dict[str, list[tuple[Path, pl.LazyFrame]]]:
    """Load all parquet files grouped by source."""
    out: dict[str, list[tuple[Path, pl.LazyFrame]]] = {}
    for p in _iter_parquet_files(root):
        out.setdefault(p.parent.name, []).append((p, pl.scan_parquet(str(p))))
    logger.info(f"Found {len(out)} sources")
    return out


def _process_entities_vectorized(df: pl.DataFrame, source_id: int, next_id: int) -> tuple[dict[str, pl.DataFrame], int]:
    """Process entities using vectorized operations (no row iteration!)."""

    if len(df) == 0:
        return {}, next_id

    # ============================================================================
    # 1. CREATE BASE ENTITY RECORDS
    # ============================================================================
    entities = df.select([
        pl.col("type").alias("entity_type"),
    ]).with_row_index("local_entity_id", offset=next_id)

    entities = entities.with_columns(pl.lit(source_id).alias("source_id"))
    next_id += len(entities)

    logger.info(f"    Created {len(entities):,} base entity records")

    # ============================================================================
    # 2. EXTRACT IDENTIFIERS (vectorized explode)
    # ============================================================================
    identifiers = pl.DataFrame()

    if "identifiers" in df.columns:
        # Filter entities that have identifiers
        has_identifiers = df.filter(
            pl.col("identifiers").is_not_null() &
            (pl.col("identifiers").list.len() > 0)
        )

        if len(has_identifiers):
            # Add entity IDs and explode identifiers list
            identifiers = (
                has_identifiers
                .with_row_index("local_entity_id", offset=next_id - len(df))
                .select([
                    pl.col("local_entity_id"),
                    pl.col("identifiers"),
                ])
                .explode("identifiers")
                .select([
                    pl.col("local_entity_id"),
                    pl.col("identifiers").struct.field("type").alias("type_id"),
                    pl.col("identifiers").struct.field("value").alias("identifier"),
                ])
                .with_row_index("local_entity_identifier_id", offset=1)
                .with_columns(pl.lit(source_id).alias("source_id"))
            )
            logger.info(f"    Extracted {len(identifiers):,} identifier records")

    # ============================================================================
    # 3. EXTRACT ANNOTATIONS (vectorized explode)
    # ============================================================================
    annotations = pl.DataFrame()

    if "annotations" in df.columns:
        has_annotations = df.filter(
            pl.col("annotations").is_not_null() &
            (pl.col("annotations").list.len() > 0)
        )

        if len(has_annotations):
            annotations = (
                has_annotations
                .with_row_index("local_entity_id", offset=next_id - len(df))
                .select([
                    pl.col("local_entity_id"),
                    pl.col("annotations"),
                ])
                .explode("annotations")
                .select([
                    pl.col("local_entity_id"),
                    pl.col("annotations").struct.field("term").alias("annotation_id"),
                    pl.when(pl.col("annotations").struct.field("value").is_not_null())
                      .then(pl.col("annotations").struct.field("value").cast(pl.String))
                      .otherwise(None)
                      .alias("annotation_value"),
                    pl.col("annotations").struct.field("units").alias("annotation_unit"),
                ])
                .with_row_index("local_entity_annotation_id", offset=1)
                .with_columns(pl.lit(source_id).alias("source_id"))
            )
            logger.info(f"    Extracted {len(annotations):,} annotation records")

    # ============================================================================
    # 4. PROCESS MEMBERSHIPS (vectorized explode + recursive handling)
    # ============================================================================
    memberships = pl.DataFrame()
    membership_annotations = pl.DataFrame()
    member_entities = []
    member_identifiers = []
    member_annotations = []

    if "membership" in df.columns:
        has_memberships = df.filter(
            pl.col("membership").is_not_null() &
            (pl.col("membership").list.len() > 0)
        )

        if len(has_memberships):
            # Add parent entity IDs
            with_parent_ids = has_memberships.with_row_index("parent_entity_id", offset=next_id - len(df))

            # Explode memberships
            exploded_memberships = (
                with_parent_ids
                .select([
                    pl.col("parent_entity_id"),
                    pl.col("membership"),
                ])
                .explode("membership")
            )

            # Extract is_parent flag and member data
            membership_info = exploded_memberships.select([
                pl.col("parent_entity_id"),
                pl.col("membership").struct.field("is_parent").fill_null(False).alias("is_parent"),
                pl.col("membership").struct.field("member").alias("member"),
                pl.col("membership").struct.field("annotations").alias("membership_annotations"),
            ])

            # Filter out null members
            membership_info = membership_info.filter(pl.col("member").is_not_null())

            if len(membership_info):
                # Recursively process member entities
                member_df = membership_info.select("member").unnest("member")
                member_results, next_id = _process_entities_vectorized(member_df, source_id, next_id)

                if "entity" in member_results and len(member_results["entity"]):
                    member_entity_df = member_results["entity"]
                    member_entities.append(member_entity_df)

                    # Extract member entity IDs (they're sequential starting from old next_id)
                    member_start_id = next_id - len(member_entity_df)

                    # Create membership relationships
                    memberships = (
                        membership_info
                        .with_row_index("_member_idx", offset=0)
                        .with_columns(
                            (pl.col("_member_idx") + member_start_id).alias("member_entity_id")
                        )
                        .select([
                            pl.when(pl.col("is_parent"))
                              .then(pl.col("member_entity_id"))
                              .otherwise(pl.col("parent_entity_id"))
                              .alias("parent_id"),
                            pl.when(pl.col("is_parent"))
                              .then(pl.col("parent_entity_id"))
                              .otherwise(pl.col("member_entity_id"))
                              .alias("member_id"),
                            pl.col("membership_annotations"),
                        ])
                        .with_row_index("local_membership_id", offset=1)
                        .with_columns(pl.lit(source_id).alias("source_id"))
                    )

                    # Extract membership annotations
                    has_membership_annots = memberships.filter(
                        pl.col("membership_annotations").is_not_null() &
                        (pl.col("membership_annotations").list.len() > 0)
                    )

                    if len(has_membership_annots):
                        membership_annotations = (
                            has_membership_annots
                            .select([
                                pl.col("local_membership_id"),
                                pl.col("membership_annotations"),
                            ])
                            .explode("membership_annotations")
                            .select([
                                pl.col("local_membership_id"),
                                pl.col("membership_annotations").struct.field("term").alias("annotation_id"),
                                pl.when(pl.col("membership_annotations").struct.field("value").is_not_null())
                                  .then(pl.col("membership_annotations").struct.field("value").cast(pl.String))
                                  .otherwise(None)
                                  .alias("annotation_value"),
                                pl.col("membership_annotations").struct.field("units").alias("annotation_unit"),
                            ])
                            .with_row_index("local_membership_annotation_id", offset=1)
                            .with_columns(pl.lit(source_id).alias("source_id"))
                        )
                        logger.info(f"    Extracted {len(membership_annotations):,} membership annotation records")

                    # Drop the annotations column from memberships table
                    memberships = memberships.drop("membership_annotations")
                    logger.info(f"    Created {len(memberships):,} membership relationships")

                    # Collect member identifiers and annotations
                    if "entity_identifier" in member_results and len(member_results["entity_identifier"]):
                        member_identifiers.append(member_results["entity_identifier"])
                    if "entity_annotation" in member_results and len(member_results["entity_annotation"]):
                        member_annotations.append(member_results["entity_annotation"])

    # ============================================================================
    # 5. COMBINE ALL RESULTS
    # ============================================================================
    results = {
        "entity": entities,
        "entity_identifier": identifiers,
        "entity_annotation": annotations,
        "membership": memberships,
        "membership_annotation": membership_annotations,
    }

    # Append member entities if any
    if member_entities:
        results["entity"] = pl.concat([entities] + member_entities, how="diagonal_relaxed")
        logger.info(f"    Total entities (including members): {len(results['entity']):,}")

    if member_identifiers:
        if len(identifiers):
            results["entity_identifier"] = pl.concat([identifiers] + member_identifiers, how="diagonal_relaxed")
        else:
            results["entity_identifier"] = pl.concat(member_identifiers, how="diagonal_relaxed")
        logger.info(f"    Total identifiers (including members): {len(results['entity_identifier']):,}")

    if member_annotations:
        if len(annotations):
            results["entity_annotation"] = pl.concat([annotations] + member_annotations, how="diagonal_relaxed")
        else:
            results["entity_annotation"] = pl.concat(member_annotations, how="diagonal_relaxed")
        logger.info(f"    Total annotations (including members): {len(results['entity_annotation']):,}")

    return results, next_id


def _save_tables(tables: dict[str, pl.DataFrame], output_dir: Path, source_name: str):
    """Save processed tables to parquet files."""
    for table_name, df in tables.items():
        if len(df) > 0:
            output_path = output_dir / f"local_{table_name}_{source_name}.parquet"
            df.write_parquet(output_path)
            logger.info(f"  Saved {table_name}: {len(df):,} records -> {output_path.name}")


# --- Main --------------------------------------------------------------------

def build_local_tables(
    data_root: Path,
    output_dir: Path,
):
    """
    Build local tables from Entity parquet files using vectorized operations.

    Source IDs are auto-generated from discovered source names (alphabetically sorted).

    Args:
        data_root: Root directory containing source subdirectories with parquet files
        output_dir: Output directory for local tables
    """
    data = _load_source_data(data_root)

    # Auto-generate source IDs from discovered data (sorted alphabetically for consistency)
    name2id = {name: idx + 1 for idx, name in enumerate(sorted(data.keys()))}
    logger.info(f"Auto-generated source IDs for {len(name2id)} discovered sources")

    # Create output directory
    local_tables_dir = output_dir / "local_tables"
    local_tables_dir.mkdir(parents=True, exist_ok=True)

    # Process each source
    for source_name, files in data.items():
        source_id = name2id[source_name]

        logger.info("\n" + "="*70)
        logger.info(f"Processing source: {source_name} (id={source_id})")
        logger.info("="*70)

        # Collect all tables for this source
        all_tables = {
            'entity': [],
            'entity_identifier': [],
            'entity_annotation': [],
            'membership': [],
            'membership_annotation': [],
        }

        next_id = 1

        # Sort files to ensure resource.parquet is processed first
        # This guarantees that the source entity always gets local_entity_id = 1
        def sort_key(item):
            path, _ = item
            return (0 if path.name == 'resource.parquet' else 1, path.name)

        sorted_files = sorted(files, key=sort_key)

        # Process each file
        for file_path, lazy_frame in sorted_files:
            logger.info(f"  Processing {file_path.name}")

            # Collect the dataframe
            df = lazy_frame.collect()
            if len(df) == 0:
                logger.info(f"    Empty file, skipping")
                continue

            logger.info(f"    Found {len(df):,} entities")

            # Process entities using vectorized operations
            tables, next_id = _process_entities_vectorized(df, source_id, next_id)

            # Accumulate results
            for table_name, table_df in tables.items():
                if isinstance(table_df, pl.DataFrame) and len(table_df) > 0:
                    all_tables[table_name].append(table_df)

        # Combine all tables for this source
        final_tables = {}
        for table_name, table_list in all_tables.items():
            if table_list:
                final_tables[table_name] = pl.concat(table_list, how="diagonal_relaxed")
                logger.info(f"  Total {table_name}: {len(final_tables[table_name]):,} records")

        # Save tables
        if final_tables:
            _save_tables(final_tables, local_tables_dir, source_name)
        else:
            logger.warning(f"  No data to save for {source_name}")

        logger.info(f"Completed processing {source_name}")

    logger.info("\n" + "="*70)
    logger.info("Local table building complete!")
    logger.info("="*70)


# --- Utilities for debugging -------------------------------------------------

def inspect_entity_schema(parquet_file: Path):
    """Utility to inspect the schema of Entity parquet files."""
    df = pl.read_parquet(parquet_file)
    print(f"\nFile: {parquet_file.name}")
    print(f"Rows: {len(df):,}")
    print("\nSchema:")
    for col, dtype in df.schema.items():
        print(f"  {col}: {dtype}")

    # Sample first row to understand structure
    if len(df) > 0:
        print("\nFirst row sample:")
        first = df.head(1).to_dicts()[0]
        for key, value in first.items():
            if isinstance(value, list) and value:
                print(f"  {key}: {value[:1]}...")  # Show first item of lists
            else:
                print(f"  {key}: {value}")
    return df