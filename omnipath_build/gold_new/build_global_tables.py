#!/usr/bin/env python3
"""Build global tables from local tables using entity resolution.

This script consumes:
1. local_entity_*.parquet files (from build_local_tables.py)
2. record_to_global.parquet (from build_entity_identifiers.py)
3. entity_identifiers.parquet (final_identifiers from build_entity_identifiers.py)

And produces global tables with:
- Local entity IDs replaced by canonical entity IDs
- CV term accessions resolved to entity IDs
- Source provenance preserved
- Global sequential IDs assigned

Output tables:
  entity.parquet              (entity_id, entity_type_id, sources)
  entity_identifier.parquet   (entity_id, id_type_id, id_value, sources)
  entity_annotation.parquet   (entity_id, annotation_id, annotation_value, annotation_unit, sources)
  membership.parquet          (parent_id, member_id, sources)
  membership_annotation.parquet (membership_id, annotation_id, annotation_value, annotation_unit, sources)
"""

from __future__ import annotations
from pathlib import Path
import logging
import polars as pl

__all__ = ["build_global_tables"]

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Helper functions
# --------------------------------------------------------------------------- #

def _build_cv_term_mapping(entity_identifiers: pl.DataFrame) -> pl.DataFrame:
    """Build CV term mapping from entity_identifiers.

    CV terms are entities with id_type = "OM:0204" (CV_TERM_ACCESSION).

    Args:
        entity_identifiers: DataFrame with [entity_id, id_type/id_type_id, id_value, sources]

    Returns:
        DataFrame with [accession, cv_term_entity_id] mapping
    """
    # Check if we have id_type (accession) or id_type_id (already resolved)
    if "id_type" in entity_identifiers.columns:
        # Filter for CV term accessions (OM:0204)
        cv_terms = (
            entity_identifiers
            .filter(pl.col("id_type") == "OM:0204")
            .select([
                pl.col("id_value").alias("accession"),
                pl.col("entity_id").alias("cv_term_entity_id"),
            ])
            .unique(subset=["accession"])
        )
    else:
        # Already resolved: find the entity_id for "OM:0204" type
        # First, find which entity_id corresponds to the CV term "OM:0204" itself
        om0204_entity_id = (
            entity_identifiers
            .filter(pl.col("id_value") == "OM:0204")
            .select(pl.col("entity_id").first())
            .item()
        )

        # Then get all identifiers with that type
        cv_terms = (
            entity_identifiers
            .filter(pl.col("id_type_id") == om0204_entity_id)
            .select([
                pl.col("id_value").alias("accession"),
                pl.col("entity_id").alias("cv_term_entity_id"),
            ])
            .unique(subset=["accession"])
        )

    logger.info(f"Built CV term mapping with {len(cv_terms):,} unique accessions")
    return cv_terms


def _map_cv_term_column(
    df: pl.DataFrame,
    cv_term_mapping: pl.DataFrame,
    accession_col: str,
) -> pl.DataFrame:
    """Map a single CV term accession column to its corresponding entity ID.

    Args:
        df: DataFrame with CV term accession column
        cv_term_mapping: CV term mapping DataFrame [accession, cv_term_entity_id]
        accession_col: Name of the column containing CV term accessions

    Returns:
        DataFrame with new {col}_id column added and original accession column dropped
    """
    if accession_col not in df.columns:
        return df

    # Create mapping with correct column name
    mapping = cv_term_mapping.select([
        pl.col("accession").alias(accession_col),
        pl.col("cv_term_entity_id").alias(f"{accession_col}_id"),
    ])

    # Left join to add the _id column
    result = df.join(mapping, on=accession_col, how="left")

    # Drop the original accession column
    result = result.drop(accession_col)

    return result


def _map_cv_term_columns(
    df: pl.DataFrame,
    cv_term_mapping: pl.DataFrame,
    accession_cols: list[str],
) -> pl.DataFrame:
    """Map multiple CV term accession columns to corresponding entity IDs.

    Args:
        df: DataFrame with CV term accession columns
        cv_term_mapping: CV term mapping DataFrame [accession, cv_term_entity_id]
        accession_cols: List of column names containing CV term accessions

    Returns:
        DataFrame with new {col}_id columns added and original accession columns dropped
    """
    result = df
    for col in accession_cols:
        result = _map_cv_term_column(result, cv_term_mapping, col)
    return result


# --------------------------------------------------------------------------- #
# Main processing function
# --------------------------------------------------------------------------- #

def build_global_tables(
    local_tables_dir: str | Path,
    record_to_global_file: str | Path,
    entity_identifiers_file: str | Path,
    output_dir: str | Path,
):
    """Build global tables from local tables.

    Args:
        local_tables_dir: Directory containing local_*.parquet files
        record_to_global_file: Path to record_to_global.parquet mapping file
        entity_identifiers_file: Path to entity_identifiers.parquet file
        output_dir: Output directory for global tables
    """
    local_tables_dir = Path(local_tables_dir)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # ========================================================================
    # 1. LOAD MAPPINGS
    # ========================================================================

    logger.info("=" * 80)
    logger.info("Loading mappings")
    logger.info("=" * 80)

    # Load record_to_global mapping (source_id, local_entity_id) -> entity_id
    record_to_global = pl.read_parquet(record_to_global_file)
    logger.info(f"Loaded record_to_global: {len(record_to_global):,} rows")

    # Load entity identifiers (includes CV terms)
    entity_identifiers = pl.read_parquet(entity_identifiers_file)
    logger.info(f"Loaded entity_identifiers: {len(entity_identifiers):,} rows")

    # Build CV term mapping
    cv_term_mapping = _build_cv_term_mapping(entity_identifiers)

    # ========================================================================
    # 2. PROCESS ENTITY TABLE
    # ========================================================================

    logger.info("\n" + "=" * 80)
    logger.info("Processing entity table")
    logger.info("=" * 80)

    # Get only base entity files, excluding annotation and identifier files
    entity_files = sorted([
        f for f in local_tables_dir.glob("local_entity_*.parquet")
        if "annotation" not in f.name and "identifier" not in f.name and "membership" not in f.name
    ])
    logger.info(f"Found {len(entity_files)} entity files")

    entity_parts = []
    for f in entity_files:
        df = pl.read_parquet(f)
        logger.info(f"  {f.name}: {len(df):,} rows")

        # Join with record_to_global to get entity_id
        df_global = df.join(
            record_to_global,
            on=["source_id", "local_entity_id"],
            how="left"
        )

        entity_parts.append(df_global)

    if entity_parts:
        # Combine all sources
        entities_combined = pl.concat(entity_parts, how="diagonal_relaxed")

        # Aggregate by entity_id to collect sources and entity_type
        entities_global = (
            entities_combined
            .group_by("entity_id")
            .agg([
                pl.col("entity_type").first().alias("entity_type"),  # Should be same across sources
                pl.col("source_id").unique().sort().alias("sources"),
            ])
            .sort("entity_id")
        )

        # Map entity_type (accession) -> entity_type_id
        entities_output = _map_cv_term_columns(entities_global, cv_term_mapping, ["entity_type"])

        entities_output.write_parquet(output_dir / "entity.parquet")
        logger.info(f"✅ entity: {len(entities_output):,} rows")
    else:
        logger.warning("No entity files found")
        pl.DataFrame().write_parquet(output_dir / "entity.parquet")

    # ========================================================================
    # 3. PROCESS ENTITY IDENTIFIER TABLE
    # ========================================================================

    logger.info("\n" + "=" * 80)
    logger.info("Processing entity_identifier table")
    logger.info("=" * 80)

    # entity_identifiers is already processed by build_entity_identifiers step
    # Just verify it exists and report stats
    logger.info("Entity identifiers already processed in entity_identifiers step")
    logger.info(f"✅ entity_identifier: {len(entity_identifiers):,} rows")

    # ========================================================================
    # 4. PROCESS ENTITY ANNOTATION TABLE
    # ========================================================================

    logger.info("\n" + "=" * 80)
    logger.info("Processing entity_annotation table")
    logger.info("=" * 80)

    annotation_files = sorted(local_tables_dir.glob("local_entity_annotation_*.parquet"))
    logger.info(f"Found {len(annotation_files)} entity_annotation files")

    annotation_parts = []
    for f in annotation_files:
        df = pl.read_parquet(f)
        logger.info(f"  {f.name}: {len(df):,} rows")

        # Join with record_to_global to get entity_id
        df_global = df.join(
            record_to_global,
            on=["source_id", "local_entity_id"],
            how="left"
        )

        annotation_parts.append(df_global)

    if annotation_parts:
        # Combine all sources
        annotations_combined = pl.concat(annotation_parts, how="diagonal_relaxed")

        # Aggregate by (entity_id, annotation_id, annotation_value, annotation_unit) to collect sources
        annotations_global = (
            annotations_combined
            .group_by(["entity_id", "annotation_id", "annotation_value", "annotation_unit"])
            .agg([
                pl.col("source_id").unique().sort().alias("sources"),
            ])
            .sort("entity_id")
        )

        # Map annotation_id (accession) -> annotation_id (entity_id)
        # Note: annotation_id is already the accession, we need to rename the result
        annotations_output = _map_cv_term_column(
            annotations_global,
            cv_term_mapping,
            "annotation_id"
        )

        # Assign global sequential IDs
        annotations_output = annotations_output.with_row_index("id", offset=1)

        annotations_output.write_parquet(output_dir / "entity_annotation.parquet")
        logger.info(f"✅ entity_annotation: {len(annotations_output):,} rows")
    else:
        logger.warning("No entity_annotation files found")
        pl.DataFrame().write_parquet(output_dir / "entity_annotation.parquet")

    # ========================================================================
    # 5. PROCESS MEMBERSHIP TABLE
    # ========================================================================

    logger.info("\n" + "=" * 80)
    logger.info("Processing membership table")
    logger.info("=" * 80)

    # Get membership files, but exclude annotation files
    membership_files = sorted([
        f for f in local_tables_dir.glob("local_membership_*.parquet")
        if "annotation" not in f.name
    ])
    logger.info(f"Found {len(membership_files)} membership files")

    membership_parts = []
    for f in membership_files:
        df = pl.read_parquet(f)
        logger.info(f"  {f.name}: {len(df):,} rows")

        # Join parent_id with record_to_global
        df_with_parent = df.join(
            record_to_global.rename({"local_entity_id": "parent_id"}),
            on=["source_id", "parent_id"],
            how="left"
        ).rename({"entity_id": "parent_entity_id"})

        # Join member_id with record_to_global
        df_global = df_with_parent.join(
            record_to_global.rename({"local_entity_id": "member_id"}),
            on=["source_id", "member_id"],
            how="left"
        ).rename({"entity_id": "member_entity_id"})

        membership_parts.append(df_global)

    membership_to_global = None  # Will store mapping from (source_id, local_membership_id) -> global membership_id

    if membership_parts:
        # Combine all sources
        memberships_combined = pl.concat(membership_parts, how="diagonal_relaxed")

        # Keep track of local membership IDs before aggregation
        local_membership_mapping = memberships_combined.select([
            "source_id",
            "local_membership_id",
            "parent_entity_id",
            "member_entity_id",
        ])

        # Aggregate by (parent_entity_id, member_entity_id) to collect sources
        memberships_global = (
            memberships_combined
            .group_by(["parent_entity_id", "member_entity_id"])
            .agg([
                pl.col("source_id").unique().sort().alias("sources"),
            ])
            .rename({
                "parent_entity_id": "parent_id",
                "member_entity_id": "member_id",
            })
            .sort(["parent_id", "member_id"])
        )

        # Assign global sequential IDs
        memberships_output = memberships_global.with_row_index("id", offset=1)

        # Create mapping from (source_id, local_membership_id) to global membership_id
        membership_to_global = (
            local_membership_mapping
            .join(
                memberships_output.select([
                    pl.col("id").alias("membership_id"),
                    pl.col("parent_id").alias("parent_entity_id"),
                    pl.col("member_id").alias("member_entity_id"),
                ]),
                on=["parent_entity_id", "member_entity_id"],
                how="left"
            )
            .select(["source_id", "local_membership_id", "membership_id"])
            .unique()
        )

        memberships_output.write_parquet(output_dir / "membership.parquet")
        logger.info(f"✅ membership: {len(memberships_output):,} rows")
    else:
        logger.warning("No membership files found")
        pl.DataFrame().write_parquet(output_dir / "membership.parquet")

    # ========================================================================
    # 6. PROCESS MEMBERSHIP ANNOTATION TABLE
    # ========================================================================

    logger.info("\n" + "=" * 80)
    logger.info("Processing membership_annotation table")
    logger.info("=" * 80)

    membership_annot_files = sorted(local_tables_dir.glob("local_membership_annotation_*.parquet"))
    logger.info(f"Found {len(membership_annot_files)} membership_annotation files")

    membership_annot_parts = []
    for f in membership_annot_files:
        df = pl.read_parquet(f)
        logger.info(f"  {f.name}: {len(df):,} rows")

        # Join with membership_to_global to get global membership_id
        if membership_to_global is not None:
            df_global = df.join(
                membership_to_global,
                on=["source_id", "local_membership_id"],
                how="left"
            )
            membership_annot_parts.append(df_global)
        else:
            logger.warning(f"  Skipping {f.name} - no membership mapping available")

    if membership_annot_parts:
        # Combine all sources
        membership_annots_combined = pl.concat(membership_annot_parts, how="diagonal_relaxed")

        # Aggregate by (membership_id, annotation_id, annotation_value, annotation_unit) to collect sources
        membership_annots_global = (
            membership_annots_combined
            .group_by(["membership_id", "annotation_id", "annotation_value", "annotation_unit"])
            .agg([
                pl.col("source_id").unique().sort().alias("sources"),
            ])
            .sort("membership_id")
        )

        # Map annotation_id (accession) -> annotation_id (entity_id)
        membership_annots_output = _map_cv_term_column(
            membership_annots_global,
            cv_term_mapping,
            "annotation_id"
        )

        # Assign global sequential IDs
        membership_annots_output = membership_annots_output.with_row_index("id", offset=1)

        membership_annots_output.write_parquet(output_dir / "membership_annotation.parquet")
        logger.info(f"✅ membership_annotation: {len(membership_annots_output):,} rows")
    else:
        logger.warning("No membership_annotation files found or no membership mapping available")
        pl.DataFrame().write_parquet(output_dir / "membership_annotation.parquet")

    logger.info("\n" + "=" * 80)
    logger.info("🎉 Global tables complete!")
    logger.info("=" * 80)


if __name__ == "__main__":
    import typer
    typer.run(build_global_tables)
