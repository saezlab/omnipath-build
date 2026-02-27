#!/usr/bin/env python3
"""Build global tables from local tables using entity resolution.

This script consumes:
1. local_entity_*.parquet files (from build_local_tables.py)
2. record_to_global.parquet (from build_entity_identifiers.py)
3. entity_identifiers.parquet (from build_entity_identifiers.py)
4. instance_to_global.parquet (from build_entity_identifiers.py)

And produces global tables with:
- Local entity IDs replaced by canonical entity IDs
- Local instance IDs replaced by global instance IDs
- CV term accessions resolved to entity IDs
- Source provenance in separate resource tables
- Global sequential IDs assigned

Output tables:
  entity.parquet                      (entity_id, entity_type_id)
  entity_identifier.parquet           (id, entity_id, type_id, identifier)
  entity_instance.parquet             (id, entity_id, source_id)
  entity_annotation.parquet           (id, instance_id, cv_term_accession, value, unit_accession, source_id)
  membership.parquet                  (id, parent_entity_id, parent_instance_id, member_entity_id, member_instance_id, source_id)

Note: entity_identifier_resource.parquet is created by build_entity_identifiers.py and requires no transformation
Note: membership_annotation table is removed - annotations are now on entity_instances
"""

from __future__ import annotations
from pathlib import Path
import logging
import polars as pl

# Import resolver for CV term labels
try:
    from omnipath_build.utils.ontology_labels import get_default_resolver
    from omnipath_build.search_builder.schema import CV_TERM_ACCESSION_TYPE
except ImportError:
    # Fallback for when running as script/different context
    import sys
    sys.path.append(str(Path(__file__).parent.parent.parent))
    from omnipath_build.utils.ontology_labels import get_default_resolver
    from omnipath_build.search_builder.schema import CV_TERM_ACCESSION_TYPE

__all__ = ["build_global_tables"]

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Helper functions
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# Helper functions
# --------------------------------------------------------------------------- #
# (CV term mapping helpers removed as we now use accession strings directly)



# --------------------------------------------------------------------------- #
# Main processing function
# --------------------------------------------------------------------------- #

def build_global_tables(
    local_tables_dir: str | Path,
    record_to_global_file: str | Path,
    entity_identifiers_file: str | Path,
    instance_to_global_file: str | Path,
    output_dir: str | Path,
):
    """Build global tables from local tables.

    Args:
        local_tables_dir: Directory containing local_*.parquet files
        record_to_global_file: Path to record_to_global.parquet mapping file
        entity_identifiers_file: Path to entity_identifiers.parquet file
        instance_to_global_file: Path to instance_to_global.parquet file
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

    # Load instance_to_global mapping
    instance_to_global = pl.read_parquet(instance_to_global_file)
    logger.info(f"Loaded instance_to_global: {len(instance_to_global):,} rows")

    logger.info(f"Loaded instance_to_global: {len(instance_to_global):,} rows")


    # ========================================================================
    # 2. PROCESS ENTITY TABLE
    # ========================================================================

    logger.info("\n" + "=" * 80)
    logger.info("Processing entity table")
    logger.info("=" * 80)

    # Get only base entity files, excluding annotation and identifier files
    entity_files = sorted([
        f for f in local_tables_dir.rglob("local_entity_*.parquet")
        if "annotation" not in f.name and "identifier" not in f.name and "membership" not in f.name and "instance" not in f.name
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

        # Aggregate by entity_id to get entity_type
        entities_global = (
            entities_combined
            .group_by("entity_id")
            .agg([
                pl.col("entity_type").first().alias("entity_type"),  # Should be same across sources
            ])
            .sort("entity_id")
        )

        # Map entity_type (accession) -> entity_type (string)
        # No mapping needed, entity_type is already a string accession
        entities_output = entities_global

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

    # Map type_id (already string) -> type_id (string)
    # No mapping needed
    entity_identifiers_output = entity_identifiers

    # Save the updated entity_identifier table
    entity_identifiers_output.write_parquet(output_dir / "entity_identifier.parquet")
    logger.info(f"✅ entity_identifier: {len(entity_identifiers_output):,} rows")

    # Note: entity_identifier_resource.parquet is already created by build_entity_identifiers.py
    # and requires no further transformation (all IDs are already correct)

    # ========================================================================
    # 3.5. BUILD SOURCE_ID -> SOURCE_ENTITY_ID MAPPING
    # ========================================================================

    logger.info("\n" + "=" * 80)
    logger.info("Building source_id -> source_entity_id mapping")
    logger.info("=" * 80)

    # Build mapping from source_id to source_entity_id
    # Sources always have local_entity_id = 1 (guaranteed by build_local_tables.py)
    source_mapping = (
        record_to_global
        .filter(pl.col("local_entity_id") == 1)
        .select(["source_id", pl.col("entity_id").alias("source_entity_id")])
    )
    logger.info(f"Built source mapping: {len(source_mapping):,} sources")

    # ========================================================================
    # 4. PROCESS ENTITY INSTANCE TABLE
    # ========================================================================

    logger.info("\n" + "=" * 80)
    logger.info("Processing entity_instance table")
    logger.info("=" * 80)

    # instance_to_global already has: source_id, local_entity_instance_id, instance_id, entity_id
    # We need to output: id, entity_id, source_id (where source_id is source_entity_id)
    
    if len(instance_to_global) > 0:
        entity_instances_output = (
            instance_to_global
            .join(source_mapping, on="source_id", how="left")
            .select([
                pl.col("instance_id").alias("id"),
                pl.col("entity_id"),
                pl.col("source_entity_id").alias("source_id"),
            ])
            .sort("id")
        )
        entity_instances_output.write_parquet(output_dir / "entity_instance.parquet")
        logger.info(f"✅ entity_instance: {len(entity_instances_output):,} rows")
    else:
        logger.warning("No entity instances found")
        pl.DataFrame({
            "id": pl.Series([], dtype=pl.Int64),
            "entity_id": pl.Series([], dtype=pl.Int64),
            "source_id": pl.Series([], dtype=pl.Int64),
        }).write_parquet(output_dir / "entity_instance.parquet")

    # ========================================================================
    # 5. PROCESS ENTITY ANNOTATION TABLE
    # ========================================================================

    logger.info("\n" + "=" * 80)
    logger.info("Processing entity_annotation table")
    logger.info("=" * 80)

    entity_annot_files = sorted(local_tables_dir.rglob("local_entity_annotation_*.parquet"))
    logger.info(f"Found {len(entity_annot_files)} entity_annotation files")

    entity_annot_parts = []
    for f in entity_annot_files:
        df = pl.read_parquet(f)
        logger.info(f"  {f.name}: {len(df):,} rows")

        # Map local_entity_instance_id -> global instance_id
        df_global = df.join(
            instance_to_global.select(["source_id", "local_entity_instance_id", "instance_id"]),
            on=["source_id", "local_entity_instance_id"],
            how="left"
        )

        # Map cv_term_accession (local) -> cv_term_accession (global/string)
        # Already strings, no mapping needed. Just ensure type is correct.
        if "cv_term_accession" not in df.columns:
             df_global = df_global.with_columns(pl.lit(None, dtype=pl.Utf8).alias("cv_term_accession"))

        # Map unit_accession (local) -> unit_accession (global/string)
        if "unit_accession" not in df.columns:
             df_global = df_global.with_columns(pl.lit(None, dtype=pl.Utf8).alias("unit_accession"))

        entity_annot_parts.append(df_global)

    if entity_annot_parts:
        # Combine all sources
        entity_annots_combined = pl.concat(entity_annot_parts, how="diagonal_relaxed")

        # Map source_id to source_entity_id
        entity_annots_with_source = (
            entity_annots_combined
            .join(source_mapping, on="source_id", how="left")
            .drop(["source_id", "local_entity_instance_id", "local_entity_annotation_id"])
            .rename({"source_entity_id": "source_id"})
            .sort(["instance_id", "cv_term_accession", "source_id"])
        )

        # Assign global sequential IDs
        entity_annots_output = entity_annots_with_source.with_row_index("id", offset=1)

        entity_annots_output.write_parquet(output_dir / "entity_annotation.parquet")
        logger.info(f"✅ entity_annotation: {len(entity_annots_output):,} rows")
    else:
        logger.warning("No entity_annotation files found")
        pl.DataFrame().write_parquet(output_dir / "entity_annotation.parquet")

    # ========================================================================
    # 6. PROCESS MEMBERSHIP TABLE
    # ========================================================================

    logger.info("\n" + "=" * 80)
    logger.info("Processing membership table")
    logger.info("=" * 80)

    # Get membership files (no more annotation files to exclude)
    membership_files = sorted(local_tables_dir.rglob("local_membership_*.parquet"))
    logger.info(f"Found {len(membership_files)} membership files")

    membership_parts = []
    for f in membership_files:
        df = pl.read_parquet(f)
        logger.info(f"  {f.name}: {len(df):,} rows")

        df_global = df

        # Map parent_entity_id (local) -> parent_entity_id (global)
        if "parent_entity_id" in df.columns:
            df_global = df_global.join(
                record_to_global.rename({
                    "local_entity_id": "parent_entity_id",
                    "entity_id": "parent_entity_id_global"
                }),
                on=["source_id", "parent_entity_id"],
                how="left"
            ).drop("parent_entity_id").rename({"parent_entity_id_global": "parent_entity_id"})

        # Map parent_instance_id (local) -> parent_instance_id (global)
        if "parent_instance_id" in df.columns:
            df_global = df_global.join(
                instance_to_global.rename({
                    "local_entity_instance_id": "parent_instance_id",
                    "instance_id": "parent_instance_id_global"
                }).select(["source_id", "parent_instance_id", "parent_instance_id_global"]),
                on=["source_id", "parent_instance_id"],
                how="left"
            ).drop("parent_instance_id").rename({"parent_instance_id_global": "parent_instance_id"})
        else:
            df_global = df_global.with_columns(pl.lit(None, dtype=pl.Int64).alias("parent_instance_id"))

        # Map member_entity_id (local) -> member_entity_id (global)
        if "member_entity_id" in df.columns:
            df_global = df_global.join(
                record_to_global.rename({
                    "local_entity_id": "member_entity_id",
                    "entity_id": "member_entity_id_global"
                }),
                on=["source_id", "member_entity_id"],
                how="left"
            ).drop("member_entity_id").rename({"member_entity_id_global": "member_entity_id"})
        else:
            df_global = df_global.with_columns(pl.lit(None, dtype=pl.Int64).alias("member_entity_id"))

        # Map member_instance_id (local) -> member_instance_id (global)
        if "member_instance_id" in df.columns:
            df_global = df_global.join(
                instance_to_global.rename({
                    "local_entity_instance_id": "member_instance_id",
                    "instance_id": "member_instance_id_global"
                }).select(["source_id", "member_instance_id", "member_instance_id_global"]),
                on=["source_id", "member_instance_id"],
                how="left"
            ).drop("member_instance_id").rename({"member_instance_id_global": "member_instance_id"})
        else:
            df_global = df_global.with_columns(pl.lit(None, dtype=pl.Int64).alias("member_instance_id"))

        membership_parts.append(df_global)

    if membership_parts:
        # Combine all sources
        memberships_combined = pl.concat(membership_parts, how="diagonal_relaxed")

        # Map source_id to source_entity_id
        memberships_with_source_entity = (
            memberships_combined
            .join(source_mapping, on="source_id", how="left")
            .drop(["local_membership_id", "source_id"])
            .rename({"source_entity_id": "source_id"})
            .sort(["parent_entity_id", "parent_instance_id", "member_entity_id", "member_instance_id", "source_id"])
        )

        # Assign global sequential IDs
        memberships_output = memberships_with_source_entity.with_row_index("id", offset=1)

        # Reorder columns for clarity
        output_cols = ["id", "parent_entity_id", "parent_instance_id", "member_entity_id", "member_instance_id", "source_id"]
        available_cols = [c for c in output_cols if c in memberships_output.columns]
        memberships_output = memberships_output.select(available_cols)

        memberships_output.write_parquet(output_dir / "membership.parquet")
        logger.info(f"✅ membership: {len(memberships_output):,} rows")
    else:
        logger.warning("No membership files found")
        pl.DataFrame().write_parquet(output_dir / "membership.parquet")

    # Note: membership_annotation table is removed in the new schema
    # Annotations are now on entity_instances via entity_annotation table

    # ========================================================================
    # 7. BUILD CV TERM LABEL MAPPING
    # ========================================================================

    logger.info("\n" + "=" * 80)
    logger.info("Building CV term label mappings")
    logger.info("=" * 80)

    # Collect all accessions
    accessions = set()

    # 1. Entity Types
    if not entities_output.is_empty():
        accessions.update(entities_output["entity_type"].unique().to_list())

    # 2. Identifier Types
    if not entity_identifiers_output.is_empty():
        accessions.update(entity_identifiers_output["type_id"].unique().to_list())
    
    # 3. Annotation Terms & Units
    if not entity_annots_output.is_empty():
        if "cv_term_accession" in entity_annots_output.columns:
            accessions.update(entity_annots_output["cv_term_accession"].drop_nulls().unique().to_list())
        if "unit_accession" in entity_annots_output.columns:
            accessions.update(entity_annots_output["unit_accession"].drop_nulls().unique().to_list())
        if {"cv_term_accession", "value"}.issubset(entity_annots_output.columns):
            cv_value_accessions = (
                entity_annots_output
                .filter(pl.col("cv_term_accession") == CV_TERM_ACCESSION_TYPE)
                .select(pl.col("value").drop_nulls().unique())
                .to_series()
                .to_list()
            )
            accessions.update(cv_value_accessions)

    logger.info(f"Resolving labels for {len(accessions)} unique CV terms...")
    
    # Resolve
    resolver = get_default_resolver()
    label_map = resolver.resolve_bulk(list(accessions))

    # Create DataFrame
    # We store the formatted label (e.g. "Protein:MI:0326")
    cv_data = [{"accession": acc, "label": fmt} for acc, fmt in label_map.items()]
    
    cv_terms = pl.DataFrame(cv_data, schema={"accession": pl.Utf8, "label": pl.Utf8})
    cv_terms.write_parquet(output_dir / "cv_terms.parquet")
    logger.info(f"✅ cv_terms: {len(cv_terms):,} rows")

    logger.info("\n" + "=" * 80)
    logger.info("🎉 Global tables complete!")
    logger.info("=" * 80)
