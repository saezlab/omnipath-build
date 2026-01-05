# Refactored build_local_tables for new Entity schema - VECTORIZED
from __future__ import annotations
from pathlib import Path
from collections.abc import Iterable
import logging
import polars as pl
from pypath.internals.cv_terms.entity_types import EntityTypeCv

__all__ = ["build_local_tables"]
logger = logging.getLogger(__name__)

# --- Helpers -----------------------------------------------------------------


class CvTermRegistry:
    """Track CV term entities per source to avoid duplication."""

    def __init__(self):
        self._term_to_id: dict[str, int] = {}
        self._lookup_df = pl.DataFrame(
            {
                "accession": pl.Series([], dtype=pl.String),
                "cv_entity_id": pl.Series([], dtype=pl.Int64),
            }
        )

    @property
    def lookup(self) -> pl.DataFrame:
        return self._lookup_df

    def ensure_terms(
        self,
        terms: Iterable[str],
        *,
        source_id: int,
        next_id: int,
    ) -> tuple[pl.DataFrame, pl.DataFrame, int]:
        """Create missing CV term entities and identifiers."""
        new_terms = sorted(
            term for term in terms if term and term not in self._term_to_id
        )
        if not new_terms:
            return pl.DataFrame(), pl.DataFrame(), next_id

        new_ids = list(range(next_id, next_id + len(new_terms)))
        self._lookup_df = pl.concat(
            [
                self._lookup_df,
                pl.DataFrame({"accession": new_terms, "cv_entity_id": new_ids}),
            ],
            how="vertical_relaxed",
        )

        self._term_to_id.update(zip(new_terms, new_ids))

        cv_entities = pl.DataFrame(
            {
                "local_entity_id": new_ids,
                "entity_type": [EntityTypeCv.CV_TERM.value] * len(new_terms),
                "source_id": [source_id] * len(new_terms),
            }
        )

        cv_identifiers = (
            pl.DataFrame(
                {
                    "local_entity_id": new_ids,
                    "type_id": ["OM:0204"] * len(new_terms),
                    "identifier": new_terms,
                    "source_id": [source_id] * len(new_terms),
                }
            ).with_row_index("local_entity_identifier_id", offset=1)
        )

        return cv_entities, cv_identifiers, next_id + len(new_terms)

    def lookup_mapping(self, column_name: str, value_name: str) -> pl.DataFrame:
        """Return a reusable lookup table for joins."""
        if len(self._lookup_df) == 0:
            return pl.DataFrame(
                {
                    column_name: pl.Series([], dtype=pl.String),
                    value_name: pl.Series([], dtype=pl.Int64),
                }
            )

        return self._lookup_df.rename(
            {"accession": column_name, "cv_entity_id": value_name}
        )


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


def _process_entities_vectorized(
    df: pl.DataFrame,
    source_id: int,
    next_id: int,
    cv_registry: CvTermRegistry,
) -> tuple[dict[str, pl.DataFrame], int]:
    """Process entities using vectorized operations - simple sequential ID assignment."""

    if len(df) == 0:
        return {}, next_id

    # ============================================================================
    # 1. CREATE BASE ENTITY RECORDS (VECTORIZED)
    # ============================================================================
    entities = df.select([
        pl.col("type").alias("entity_type"),
    ]).with_row_index("local_entity_id", offset=next_id)

    entities = entities.with_columns(pl.lit(source_id).alias("source_id"))
    next_id += len(entities)
    entity_start_id = next_id - len(entities)

    logger.info(f"    Created {len(entities):,} base entity records")

    # ============================================================================
    # 2. EXTRACT IDENTIFIERS (VECTORIZED)
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
                .with_row_index("local_entity_id", offset=entity_start_id)
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
    # 3. CREATE ENTITIES FOR CV TERMS AND BUILD ENTITY ANNOTATIONS
    # ============================================================================
    # Extract all unique CV terms from annotations (term, value, unit)
    # and create local entities for them
    cv_entity_chunks: list[pl.DataFrame] = []
    cv_identifier_chunks: list[pl.DataFrame] = []
    entity_annotations = pl.DataFrame()

    if "annotations" in df.columns:
        has_annotations = df.filter(
            pl.col("annotations").is_not_null() &
            (pl.col("annotations").list.len() > 0)
        )

        if len(has_annotations):
            # Extract all CV term accessions from annotations
            all_cv_terms = (
                has_annotations
                .select([pl.col("annotations")])
                .explode("annotations")
                .select([
                    pl.col("annotations").struct.field("term").alias("cv_term"),
                    pl.col("annotations").struct.field("units").alias("unit_term"),
                ])
            )

            # Collect unique CV terms from both term and units fields
            unique_terms = set()
            unique_terms.update(
                all_cv_terms
                .get_column("cv_term")
                .drop_nulls()
                .to_list()
            )
            unique_terms.update(
                all_cv_terms
                .get_column("unit_term")
                .drop_nulls()
                .to_list()
            )

            if unique_terms:
                new_cv_entities, new_cv_identifiers, next_id = cv_registry.ensure_terms(
                    unique_terms,
                    source_id=source_id,
                    next_id=next_id,
                )
                if len(new_cv_entities):
                    cv_entity_chunks.append(new_cv_entities)
                    logger.info(f"    Created {len(new_cv_entities):,} CV term entities")
                if len(new_cv_identifiers):
                    cv_identifier_chunks.append(new_cv_identifiers)

            # Now create entity annotations with CV term entity IDs
            annotation_data = (
                has_annotations
                .with_row_index("local_entity_id", offset=entity_start_id)
                .select([
                    pl.col("local_entity_id"),
                    pl.col("annotations"),
                ])
                .explode("annotations")
                .select([
                    pl.col("local_entity_id"),
                    pl.col("annotations").struct.field("term").alias("term_accession"),
                    pl.col("annotations").struct.field("value").cast(pl.String).alias("value"),
                    pl.col("annotations").struct.field("units").alias("unit_accession"),
                ])
            )

            if len(annotation_data):
                entity_annotations = (
                    annotation_data
                    .join(
                        cv_registry.lookup_mapping("term_accession", "cv_term_entity_id"),
                        on="term_accession",
                        how="left",
                    )
                    .join(
                        cv_registry.lookup_mapping("unit_accession", "unit_entity_id"),
                        on="unit_accession",
                        how="left",
                    )
                    .filter(pl.col("cv_term_entity_id").is_not_null())
                    .select([
                        pl.col("local_entity_id"),
                        pl.col("cv_term_entity_id"),
                        pl.col("value"),
                        pl.col("unit_entity_id"),
                    ])
                    .with_row_index("local_entity_annotation_id", offset=1)
                    .with_columns(pl.lit(source_id).alias("source_id"))
                )
                logger.info(f"    Created {len(entity_annotations):,} entity annotation records")

    # ============================================================================
    # 4. PROCESS MEMBERSHIPS (vectorized explode + recursive handling)
    # ============================================================================
    memberships = pl.DataFrame()
    membership_annotations = pl.DataFrame()
    member_entities: list[pl.DataFrame] = []
    member_identifiers: list[pl.DataFrame] = []
    member_memberships: list[pl.DataFrame] = []
    member_membership_annotations: list[pl.DataFrame] = []
    member_entity_annotations: list[pl.DataFrame] = []

    if "membership" in df.columns:
        has_memberships = df.filter(
            pl.col("membership").is_not_null() &
            (pl.col("membership").list.len() > 0)
        )

        if len(has_memberships):
            # Add parent entity IDs
            with_parent_ids = has_memberships.with_row_index("parent_entity_id", offset=entity_start_id)

            # Explode memberships
            exploded_memberships = (
                with_parent_ids
                .select([
                    pl.col("parent_entity_id"),
                    pl.col("membership"),
                ])
                .explode("membership")
            )

            # Extract member data
            membership_info = exploded_memberships.select([
                pl.col("parent_entity_id"),
                pl.col("membership").struct.field("member").alias("member"),
                pl.col("membership").struct.field("annotations").alias("membership_annotations"),
            ])

            # Filter out null members
            membership_info = membership_info.filter(pl.col("member").is_not_null())

            if len(membership_info):
                membership_info = membership_info.with_row_index("membership_row_idx")

                # Recursively process member entities
                member_df = (
                    membership_info
                    .select(["membership_row_idx", "member"])
                    .unnest("member")
                )
                member_base_start_id = next_id
                member_results, next_id = _process_entities_vectorized(
                    member_df,
                    source_id,
                    next_id,
                    cv_registry,
                )

                if "membership" in member_results and len(member_results["membership"]):
                    member_memberships.append(member_results["membership"])
                if "membership_annotation" in member_results and len(member_results["membership_annotation"]):
                    member_membership_annotations.append(member_results["membership_annotation"])
                if "entity_annotation" in member_results and len(member_results["entity_annotation"]):
                    member_entity_annotations.append(member_results["entity_annotation"])

                if "entity" in member_results and len(member_results["entity"]):
                    member_entity_df = member_results["entity"]
                    member_entities.append(member_entity_df)

                    member_count = len(membership_info)
                    member_map = (
                        membership_info
                        .select("membership_row_idx")
                        .with_columns(
                            pl.arange(
                                member_base_start_id,
                                member_base_start_id + member_count,
                                eager=True,
                            ).alias("member_entity_id")
                        )
                    )

                    # Create membership relationships with aligned IDs
                    memberships = (
                        membership_info
                        .join(member_map, on="membership_row_idx", how="left")
                        .select([
                            pl.col("parent_entity_id").alias("parent_id"),
                            pl.col("member_entity_id").alias("member_id"),
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
                        # First, extract all CV terms from membership annotations (term and units)
                        all_membership_cv_terms = (
                            has_membership_annots
                            .select([pl.col("membership_annotations")])
                            .explode("membership_annotations")
                            .select([
                                pl.col("membership_annotations").struct.field("term").alias("cv_term"),
                                pl.col("membership_annotations").struct.field("units").alias("unit_term"),
                            ])
                        )

                        # Collect unique CV terms from both term and units fields
                        unique_membership_terms = set()
                        unique_membership_terms.update(
                            all_membership_cv_terms
                            .get_column("cv_term")
                            .drop_nulls()
                            .to_list()
                        )
                        unique_membership_terms.update(
                            all_membership_cv_terms
                            .get_column("unit_term")
                            .drop_nulls()
                            .to_list()
                        )

                        if unique_membership_terms:
                            new_cv_entities, new_cv_identifiers, next_id = cv_registry.ensure_terms(
                                unique_membership_terms,
                                source_id=source_id,
                                next_id=next_id,
                            )
                            if len(new_cv_entities):
                                cv_entity_chunks.append(new_cv_entities)
                                logger.info(
                                    f"    Created {len(new_cv_entities):,} CV term entities for membership annotations"
                                )
                            if len(new_cv_identifiers):
                                cv_identifier_chunks.append(new_cv_identifiers)

                        # Now extract membership annotations with CV term entity IDs
                        membership_annotation_data = (
                            has_membership_annots
                            .select([
                                pl.col("local_membership_id"),
                                pl.col("membership_annotations"),
                            ])
                            .explode("membership_annotations")
                            .select([
                                pl.col("local_membership_id"),
                                pl.col("membership_annotations").struct.field("term").alias("term_accession"),
                                pl.when(pl.col("membership_annotations").struct.field("value").is_not_null())
                                  .then(pl.col("membership_annotations").struct.field("value").cast(pl.String))
                                  .otherwise(None)
                                  .alias("annotation_value"),
                                pl.col("membership_annotations").struct.field("units").alias("unit_accession"),
                            ])
                        )

                        membership_annotations = pl.DataFrame()
                        if len(membership_annotation_data):
                            membership_annotations = (
                                membership_annotation_data
                                .join(
                                    cv_registry.lookup_mapping("term_accession", "annotation_id"),
                                    on="term_accession",
                                    how="left",
                                )
                                .join(
                                    cv_registry.lookup_mapping("unit_accession", "annotation_unit"),
                                    on="unit_accession",
                                    how="left",
                                )
                                .filter(pl.col("annotation_id").is_not_null())
                                .select([
                                    pl.col("local_membership_id"),
                                    pl.col("annotation_id"),
                                    pl.col("annotation_value"),
                                    pl.col("annotation_unit"),
                                ])
                                .with_row_index("local_membership_annotation_id", offset=1)
                                .with_columns(pl.lit(source_id).alias("source_id"))
                            )
                            logger.info(f"    Created {len(membership_annotations):,} membership annotation records")

                    # Drop the annotations column from memberships table
                    memberships = memberships.drop("membership_annotations")
                    logger.info(f"    Created {len(memberships):,} membership relationships")

                    # Collect member identifiers and member memberships (which include their entity annotations)
                    if "entity_identifier" in member_results and len(member_results["entity_identifier"]):
                        member_identifiers.append(member_results["entity_identifier"])
                    # Note: member entity annotations are now converted to memberships in recursive call
                    # We don't collect them separately anymore

    # ============================================================================
    # 5. COMBINE ALL RESULTS
    # ============================================================================
    # Combine CV term entities with regular entities
    all_entities = [entities]
    if cv_entity_chunks:
        all_entities.extend([chunk for chunk in cv_entity_chunks if len(chunk)])
    if member_entities:
        all_entities.extend(member_entities)

    combined_entities = pl.concat(all_entities, how="diagonal_relaxed") if len(all_entities) > 1 else entities
    logger.info(f"    Total entities (including CV terms and members): {len(combined_entities):,}")

    # Combine CV term identifiers with regular identifiers
    all_identifiers = []
    if len(identifiers) > 0:
        all_identifiers.append(identifiers)
    if cv_identifier_chunks:
        all_identifiers.extend([chunk for chunk in cv_identifier_chunks if len(chunk)])
    if member_identifiers:
        all_identifiers.extend(member_identifiers)

    combined_identifiers = pl.concat(all_identifiers, how="diagonal_relaxed") if all_identifiers else pl.DataFrame()
    if len(combined_identifiers) > 0:
        logger.info(f"    Total identifiers (including CV terms and members): {len(combined_identifiers):,}")

    # Merge memberships (structural + recursive)
    all_memberships = []
    if len(memberships) > 0:
        all_memberships.append(memberships)
    if member_memberships:
        all_memberships.extend([df for df in member_memberships if len(df)])

    # Combine and renumber membership IDs to ensure uniqueness
    combined_memberships = pl.DataFrame()
    membership_id_map = pl.DataFrame()
    if all_memberships:
        combined_memberships = pl.concat(all_memberships, how="diagonal_relaxed")
        # Renumber local_membership_id to be sequential per source
        membership_id_map = (
            combined_memberships
            .select(["source_id", "local_membership_id"])
            .with_row_index("new_local_membership_id", offset=1)
            .rename({"local_membership_id": "old_local_membership_id"})
        )
        combined_memberships = (
            combined_memberships
            .with_row_index("new_local_membership_id", offset=1)
            .drop("local_membership_id")
            .rename({"new_local_membership_id": "local_membership_id"})
        )
        logger.info(f"    Total memberships: {len(combined_memberships):,}")

    # Combine membership annotations (local + recursive)
    membership_annotation_tables = []
    if len(membership_annotations) > 0:
        membership_annotation_tables.append(membership_annotations)
    if member_membership_annotations:
        membership_annotation_tables.extend([df for df in member_membership_annotations if len(df)])

    combined_membership_annotations = pl.DataFrame()
    if membership_annotation_tables:
        combined_membership_annotations = pl.concat(membership_annotation_tables, how="diagonal_relaxed")
        if len(membership_id_map):
            combined_membership_annotations = (
                combined_membership_annotations
                .join(
                    membership_id_map,
                    left_on=["source_id", "local_membership_id"],
                    right_on=["source_id", "old_local_membership_id"],
                    how="inner",
                )
                .drop("local_membership_id")
                .rename({"new_local_membership_id": "local_membership_id"})
            )

    # Combine entity annotations (local + recursive)
    entity_annotation_tables = []
    if len(entity_annotations) > 0:
        entity_annotation_tables.append(entity_annotations)
    if member_entity_annotations:
        entity_annotation_tables.extend([df for df in member_entity_annotations if len(df)])

    combined_entity_annotations = pl.DataFrame()
    if entity_annotation_tables:
        combined_entity_annotations = pl.concat(entity_annotation_tables, how="diagonal_relaxed")

    results = {
        "entity": combined_entities,
        "entity_identifier": combined_identifiers,
        "entity_annotation": combined_entity_annotations,
        "membership": combined_memberships,
        "membership_annotation": combined_membership_annotations,
    }

    return results, next_id


def _deduplicate_entities(tables: dict[str, pl.DataFrame]) -> dict[str, pl.DataFrame]:
    """
    Deduplicate entities based on identifier sets and remap all references.

    Strategy:
    1. Group entities by their identifier sets
    2. Keep the lowest ID for each unique identifier set
    3. Create ID mapping (old_id -> canonical_id)
    4. Remap all references in identifiers, memberships, etc.
    """
    entity_df = tables.get("entity")
    identifier_df = tables.get("entity_identifier")

    if entity_df is None or len(entity_df) == 0:
        return tables

    if identifier_df is None or len(identifier_df) == 0:
        # No identifiers to deduplicate on
        return tables

    logger.info("  Starting entity deduplication...")
    original_count = len(entity_df)

    # ============================================================================
    # 1. Build identifier sets for each entity
    # ============================================================================
    # Group identifiers by entity and create sorted identifier set strings
    identifier_sets = (
        identifier_df
        .sort(["local_entity_id", "type_id", "identifier"])
        .group_by("local_entity_id")
        .agg([
            pl.concat_str([
                pl.col("type_id"),
                pl.lit(":"),
                pl.col("identifier")
            ]).sort().str.join(delimiter="|").alias("identifier_set")
        ])
    )

    # ============================================================================
    # 2. Find duplicates and create ID mapping
    # ============================================================================
    # For each unique identifier set, keep the minimum entity ID
    canonical_ids = (
        identifier_sets
        .group_by("identifier_set")
        .agg([
            pl.col("local_entity_id").min().alias("canonical_id"),
            pl.col("local_entity_id").alias("duplicate_ids"),
        ])
        .explode("duplicate_ids")
        .select([
            pl.col("duplicate_ids").alias("old_id"),
            pl.col("canonical_id"),
        ])
    )

    # Create lookup for remapping
    id_mapping = canonical_ids.filter(pl.col("old_id") != pl.col("canonical_id"))

    if len(id_mapping) == 0:
        logger.info("  No duplicates found")
        return tables

    logger.info(f"  Found {len(id_mapping):,} duplicate entities to merge")

    # ============================================================================
    # 3. Deduplicate entities table
    # ============================================================================
    # Keep only canonical entities
    entities_dedup = (
        entity_df
        .join(canonical_ids, left_on="local_entity_id", right_on="old_id", how="left")
        .with_columns(
            pl.coalesce([pl.col("canonical_id"), pl.col("local_entity_id")]).alias("local_entity_id")
        )
        .select([col for col in entity_df.columns])  # Keep only original columns
        .unique(subset=["local_entity_id"])
    )

    # ============================================================================
    # 4. Remap identifiers
    # ============================================================================
    identifiers_dedup = (
        identifier_df
        .join(canonical_ids, left_on="local_entity_id", right_on="old_id", how="left")
        .with_columns(
            pl.coalesce([pl.col("canonical_id"), pl.col("local_entity_id")]).alias("local_entity_id")
        )
        .select([col for col in identifier_df.columns])  # Keep only original columns
        .unique(subset=["local_entity_id", "type_id", "identifier"])
        .drop("local_entity_identifier_id")
        .with_row_index("local_entity_identifier_id", offset=1)
    )

    # ============================================================================
    # 5. Remap memberships
    # ============================================================================
    membership_df = tables.get("membership")
    if membership_df is not None and len(membership_df) > 0:
        membership_cols = membership_df.columns

        # Remap parent_id
        memberships_dedup = (
            membership_df
            .join(canonical_ids, left_on="parent_id", right_on="old_id", how="left")
            .with_columns(
                pl.coalesce([pl.col("canonical_id"), pl.col("parent_id")]).alias("parent_id")
            )
            .select(membership_cols)  # Keep only original columns
        )

        # Remap member_id
        memberships_dedup = (
            memberships_dedup
            .join(canonical_ids, left_on="member_id", right_on="old_id", how="left")
            .with_columns(
                pl.coalesce([pl.col("canonical_id"), pl.col("member_id")]).alias("member_id")
            )
            .select(membership_cols)  # Keep only original columns
        )

        # Remove duplicate memberships while preserving original IDs.
        # Include annotation_value to keep multiple distinct annotation rows.
        subset_cols = ["parent_id", "member_id"]
        if "annotation_value" in memberships_dedup.columns:
            subset_cols.append("annotation_value")

        memberships_dedup = memberships_dedup.unique(subset=subset_cols)
    else:
        memberships_dedup = membership_df

    # ============================================================================
    # 6. Remap entity annotations
    # ============================================================================
    entity_annotation_df = tables.get("entity_annotation")
    if entity_annotation_df is not None and len(entity_annotation_df) > 0:
        annotation_cols = entity_annotation_df.columns
        entity_annotations_dedup = (
            entity_annotation_df
            .join(canonical_ids, left_on="local_entity_id", right_on="old_id", how="left")
            .with_columns(
                pl.coalesce([pl.col("canonical_id"), pl.col("local_entity_id")]).alias("local_entity_id")
            )
            .select(annotation_cols)
        )
        entity_annotations_dedup = (
            entity_annotations_dedup
            .join(canonical_ids, left_on="cv_term_entity_id", right_on="old_id", how="left")
            .with_columns(
                pl.coalesce([pl.col("canonical_id"), pl.col("cv_term_entity_id")]).alias("cv_term_entity_id")
            )
            .select(annotation_cols)
        )
        if "unit_entity_id" in entity_annotations_dedup.columns:
            entity_annotations_dedup = (
                entity_annotations_dedup
                .join(canonical_ids, left_on="unit_entity_id", right_on="old_id", how="left")
                .with_columns(
                    pl.coalesce([pl.col("canonical_id"), pl.col("unit_entity_id")]).alias("unit_entity_id")
                )
                .select(annotation_cols)
            )
    else:
        entity_annotations_dedup = entity_annotation_df

    logger.info(f"  Deduplicated: {original_count:,} -> {len(entities_dedup):,} entities ({original_count - len(entities_dedup):,} removed)")

    return {
        "entity": entities_dedup,
        "entity_identifier": identifiers_dedup,
        "entity_annotation": entity_annotations_dedup,
        "membership": memberships_dedup,
        "membership_annotation": tables.get("membership_annotation"),
    }


def _save_tables(tables: dict[str, pl.DataFrame], output_dir: Path, source_name: str):
    """Save processed tables to parquet files."""
    for table_name, df in tables.items():
        if df is not None and len(df) > 0:
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
        cv_registry = CvTermRegistry()

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
            tables, next_id = _process_entities_vectorized(df, source_id, next_id, cv_registry)

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

        # Deduplicate entities based on identifier sets
        if final_tables:
            final_tables = _deduplicate_entities(final_tables)

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
