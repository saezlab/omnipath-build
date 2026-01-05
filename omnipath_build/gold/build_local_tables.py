# Refactored build_local_tables for new Entity schema - VECTORIZED
# With entity_instance support for contextual entity variants
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


class InstanceRegistry:
    """Track entity instances and their sequential IDs."""

    def __init__(self, source_id: int):
        self.source_id = source_id
        self._next_instance_id = 1
        self._instances: list[dict] = []

    def create_instance(self, entity_id: int) -> int:
        """Create a new instance for an entity, return instance ID."""
        instance_id = self._next_instance_id
        self._next_instance_id += 1
        self._instances.append({
            "local_entity_instance_id": instance_id,
            "local_entity_id": entity_id,
            "source_id": self.source_id,
        })
        return instance_id

    def to_dataframe(self) -> pl.DataFrame:
        """Return all instances as a DataFrame."""
        if not self._instances:
            return pl.DataFrame({
                "local_entity_instance_id": pl.Series([], dtype=pl.Int64),
                "local_entity_id": pl.Series([], dtype=pl.Int64),
                "source_id": pl.Series([], dtype=pl.Int64),
            })
        return pl.DataFrame(self._instances)


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
    instance_registry: InstanceRegistry,
) -> tuple[dict[str, pl.DataFrame], int]:
    """Process entities using vectorized operations - simple sequential ID assignment.
    
    New schema:
    - entity_instance: created for entities with annotations
    - entity_annotation: links to entity_instance (not entity directly)
    - membership: uses polymorphic entity/instance columns
    """

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
    # 3. CREATE ENTITY INSTANCES AND ENTITY ANNOTATIONS
    # ============================================================================
    # Entities with annotations get an entity_instance
    # entity_annotation links to the instance, not the entity directly
    cv_entity_chunks: list[pl.DataFrame] = []
    cv_identifier_chunks: list[pl.DataFrame] = []
    entity_instances = pl.DataFrame()
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

            # Create entity instances for entities with annotations
            entities_with_annots = (
                has_annotations
                .with_row_index("local_entity_id", offset=entity_start_id)
                .select(["local_entity_id"])
                .unique()
            )
            
            # Create instances using the registry
            instance_map_rows = []
            for row in entities_with_annots.iter_rows(named=True):
                entity_id = row["local_entity_id"]
                instance_id = instance_registry.create_instance(entity_id)
                instance_map_rows.append({
                    "local_entity_id": entity_id,
                    "local_entity_instance_id": instance_id,
                })
            
            instance_map = pl.DataFrame(instance_map_rows) if instance_map_rows else pl.DataFrame({
                "local_entity_id": pl.Series([], dtype=pl.Int64),
                "local_entity_instance_id": pl.Series([], dtype=pl.Int64),
            })

            # Now create entity annotations with CV term entity IDs, linked to instances
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
                    .join(instance_map, on="local_entity_id", how="left")
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
                        pl.col("local_entity_instance_id"),
                        pl.col("cv_term_entity_id"),
                        pl.col("value"),
                        pl.col("unit_entity_id"),
                    ])
                    .with_row_index("local_entity_annotation_id", offset=1)
                    .with_columns(pl.lit(source_id).alias("source_id"))
                )
                logger.info(f"    Created {len(entity_annotations):,} entity annotation records (linked to instances)")

    # ============================================================================
    # 4. PROCESS MEMBERSHIPS (vectorized explode + recursive handling)
    # ============================================================================
    # New membership schema:
    # - parent_entity_id / parent_instance_id (nullable, XOR)
    # - member_entity_id / member_instance_id (nullable, XOR)
    memberships = pl.DataFrame()
    member_entities: list[pl.DataFrame] = []
    member_identifiers: list[pl.DataFrame] = []
    member_memberships: list[pl.DataFrame] = []
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

            # Extract member data and membership annotations
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
                    .select(["membership_row_idx", "member", "membership_annotations"])
                    .unnest("member")
                )
                
                # Determine which members have annotations (from membership or entity itself)
                # Members with membership_annotations need instances
                has_membership_annots = (
                    membership_info
                    .filter(
                        pl.col("membership_annotations").is_not_null() &
                        (pl.col("membership_annotations").list.len() > 0)
                    )
                    .select("membership_row_idx")
                )
                
                member_base_start_id = next_id
                member_results, next_id = _process_entities_vectorized(
                    member_df.drop("membership_annotations"),
                    source_id,
                    next_id,
                    cv_registry,
                    instance_registry,
                )

                if "membership" in member_results and len(member_results["membership"]):
                    member_memberships.append(member_results["membership"])
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

                    # Process membership annotations -> create member instances + annotations
                    membership_with_annots = (
                        membership_info
                        .filter(
                            pl.col("membership_annotations").is_not_null() &
                            (pl.col("membership_annotations").list.len() > 0)
                        )
                    )
                    
                    member_instance_map_rows = []
                    
                    if len(membership_with_annots):
                        # First, extract all CV terms from membership annotations
                        all_membership_cv_terms = (
                            membership_with_annots
                            .select([pl.col("membership_annotations")])
                            .explode("membership_annotations")
                            .select([
                                pl.col("membership_annotations").struct.field("term").alias("cv_term"),
                                pl.col("membership_annotations").struct.field("units").alias("unit_term"),
                            ])
                        )

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

                        # Create instances for members with annotations
                        # Join with member_map to get member_entity_id
                        members_needing_instances = (
                            membership_with_annots
                            .join(member_map, on="membership_row_idx", how="left")
                        )
                        
                        for row in members_needing_instances.iter_rows(named=True):
                            member_entity_id = row["member_entity_id"]
                            membership_row_idx = row["membership_row_idx"]
                            instance_id = instance_registry.create_instance(member_entity_id)
                            member_instance_map_rows.append({
                                "membership_row_idx": membership_row_idx,
                                "member_entity_id": member_entity_id,
                                "member_instance_id": instance_id,
                            })

                        member_instance_map = pl.DataFrame(member_instance_map_rows) if member_instance_map_rows else pl.DataFrame({
                            "membership_row_idx": pl.Series([], dtype=pl.Int64),
                            "member_entity_id": pl.Series([], dtype=pl.Int64),
                            "member_instance_id": pl.Series([], dtype=pl.Int64),
                        })

                        # Create entity annotations from membership annotations
                        membership_annotation_data = (
                            membership_with_annots
                            .join(member_instance_map.select(["membership_row_idx", "member_instance_id"]), 
                                  on="membership_row_idx", how="left")
                            .select([
                                pl.col("member_instance_id").alias("local_entity_instance_id"),
                                pl.col("membership_annotations"),
                            ])
                            .explode("membership_annotations")
                            .select([
                                pl.col("local_entity_instance_id"),
                                pl.col("membership_annotations").struct.field("term").alias("term_accession"),
                                pl.when(pl.col("membership_annotations").struct.field("value").is_not_null())
                                  .then(pl.col("membership_annotations").struct.field("value").cast(pl.String))
                                  .otherwise(None)
                                  .alias("value"),
                                pl.col("membership_annotations").struct.field("units").alias("unit_accession"),
                            ])
                        )

                        if len(membership_annotation_data):
                            converted_annotations = (
                                membership_annotation_data
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
                                    pl.col("local_entity_instance_id"),
                                    pl.col("cv_term_entity_id"),
                                    pl.col("value"),
                                    pl.col("unit_entity_id"),
                                ])
                                .with_row_index("local_entity_annotation_id", offset=1)
                                .with_columns(pl.lit(source_id).alias("source_id"))
                            )
                            if len(converted_annotations):
                                member_entity_annotations.append(converted_annotations)
                                logger.info(f"    Converted {len(converted_annotations):,} membership annotations to entity annotations")

                    # Build member_instance_map DataFrame for joining
                    member_instance_map_df = pl.DataFrame(member_instance_map_rows) if member_instance_map_rows else pl.DataFrame({
                        "membership_row_idx": pl.Series([], dtype=pl.Int64),
                        "member_entity_id": pl.Series([], dtype=pl.Int64),
                        "member_instance_id": pl.Series([], dtype=pl.Int64),
                    })

                    # Create membership relationships with polymorphic columns
                    # parent_entity_id / parent_instance_id (parent is always entity for now)
                    # member_entity_id / member_instance_id (member is instance if has annotations)
                    memberships_base = (
                        membership_info
                        .join(member_map, on="membership_row_idx", how="left")
                        .select([
                            pl.col("membership_row_idx"),
                            pl.col("parent_entity_id"),
                            pl.col("member_entity_id"),
                        ])
                    )
                    
                    # Join with instance map to get member_instance_id where applicable
                    memberships_with_instances = (
                        memberships_base
                        .join(
                            member_instance_map_df.select(["membership_row_idx", "member_instance_id"]),
                            on="membership_row_idx",
                            how="left"
                        )
                    )
                    
                    # Build final membership table with polymorphic columns
                    # If member has instance, use member_instance_id and null member_entity_id
                    # Otherwise, use member_entity_id and null member_instance_id
                    memberships = (
                        memberships_with_instances
                        .select([
                            # Parent is always entity (for now)
                            pl.col("parent_entity_id"),
                            pl.lit(None).cast(pl.Int64).alias("parent_instance_id"),
                            # Member is either entity or instance
                            pl.when(pl.col("member_instance_id").is_not_null())
                              .then(None)
                              .otherwise(pl.col("member_entity_id"))
                              .cast(pl.Int64)
                              .alias("member_entity_id"),
                            pl.col("member_instance_id").cast(pl.Int64),
                        ])
                        .with_row_index("local_membership_id", offset=1)
                        .with_columns(pl.lit(source_id).alias("source_id"))
                    )
                    
                    logger.info(f"    Created {len(memberships):,} membership relationships")

                    # Collect member identifiers
                    if "entity_identifier" in member_results and len(member_results["entity_identifier"]):
                        member_identifiers.append(member_results["entity_identifier"])

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
    if all_memberships:
        combined_memberships = pl.concat(all_memberships, how="diagonal_relaxed")
        # Renumber local_membership_id to be sequential per source
        combined_memberships = (
            combined_memberships
            .with_row_index("new_local_membership_id", offset=1)
            .drop("local_membership_id")
            .rename({"new_local_membership_id": "local_membership_id"})
        )
        logger.info(f"    Total memberships: {len(combined_memberships):,}")

    # Combine entity annotations (local + recursive)
    entity_annotation_tables = []
    if len(entity_annotations) > 0:
        entity_annotation_tables.append(entity_annotations)
    if member_entity_annotations:
        entity_annotation_tables.extend([df for df in member_entity_annotations if len(df)])

    combined_entity_annotations = pl.DataFrame()
    if entity_annotation_tables:
        combined_entity_annotations = pl.concat(entity_annotation_tables, how="diagonal_relaxed")
        # Renumber annotation IDs
        combined_entity_annotations = (
            combined_entity_annotations
            .drop("local_entity_annotation_id")
            .with_row_index("local_entity_annotation_id", offset=1)
        )

    results = {
        "entity": combined_entities,
        "entity_identifier": combined_identifiers,
        "entity_annotation": combined_entity_annotations,
        "membership": combined_memberships,
        # Note: membership_annotation is removed - converted to entity_annotation
    }

    return results, next_id


def _deduplicate_entities(tables: dict[str, pl.DataFrame], instance_df: pl.DataFrame) -> tuple[dict[str, pl.DataFrame], pl.DataFrame]:
    """
    Deduplicate entities based on identifier sets and remap all references.

    Strategy:
    1. Group entities by their identifier sets
    2. Keep the lowest ID for each unique identifier set
    3. Create ID mapping (old_id -> canonical_id)
    4. Remap all references in identifiers, memberships, instances, etc.
    """
    entity_df = tables.get("entity")
    identifier_df = tables.get("entity_identifier")

    if entity_df is None or len(entity_df) == 0:
        return tables, instance_df

    if identifier_df is None or len(identifier_df) == 0:
        # No identifiers to deduplicate on
        return tables, instance_df

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
        return tables, instance_df

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
    # 5. Remap entity instances
    # ============================================================================
    instances_dedup = instance_df
    if instance_df is not None and len(instance_df) > 0:
        instances_dedup = (
            instance_df
            .join(canonical_ids, left_on="local_entity_id", right_on="old_id", how="left")
            .with_columns(
                pl.coalesce([pl.col("canonical_id"), pl.col("local_entity_id")]).alias("local_entity_id")
            )
            .select([col for col in instance_df.columns])
        )

    # ============================================================================
    # 6. Remap memberships
    # ============================================================================
    membership_df = tables.get("membership")
    if membership_df is not None and len(membership_df) > 0:
        membership_cols = membership_df.columns

        # Remap parent_entity_id
        if "parent_entity_id" in membership_cols:
            memberships_dedup = (
                membership_df
                .join(canonical_ids, left_on="parent_entity_id", right_on="old_id", how="left")
                .with_columns(
                    pl.coalesce([pl.col("canonical_id"), pl.col("parent_entity_id")]).alias("parent_entity_id")
                )
                .drop("canonical_id")
            )
        else:
            memberships_dedup = membership_df

        # Remap member_entity_id
        if "member_entity_id" in memberships_dedup.columns:
            memberships_dedup = (
                memberships_dedup
                .join(canonical_ids, left_on="member_entity_id", right_on="old_id", how="left")
                .with_columns(
                    pl.when(pl.col("member_entity_id").is_not_null())
                      .then(pl.coalesce([pl.col("canonical_id"), pl.col("member_entity_id")]))
                      .otherwise(None)
                      .alias("member_entity_id")
                )
                .drop("canonical_id")
            )

        # Keep only original columns
        memberships_dedup = memberships_dedup.select([col for col in membership_cols if col in memberships_dedup.columns])

        # Remove duplicate memberships
        subset_cols = []
        for col in ["parent_entity_id", "parent_instance_id", "member_entity_id", "member_instance_id"]:
            if col in memberships_dedup.columns:
                subset_cols.append(col)

        if subset_cols:
            memberships_dedup = memberships_dedup.unique(subset=subset_cols)
    else:
        memberships_dedup = membership_df

    # ============================================================================
    # 7. Remap entity annotations (via instances)
    # ============================================================================
    # Entity annotations link to instances, not entities directly
    # So we don't need to remap them, but we need to remap cv_term_entity_id and unit_entity_id
    entity_annotation_df = tables.get("entity_annotation")
    if entity_annotation_df is not None and len(entity_annotation_df) > 0:
        annotation_cols = entity_annotation_df.columns
        entity_annotations_dedup = entity_annotation_df
        
        # Remap cv_term_entity_id
        if "cv_term_entity_id" in annotation_cols:
            entity_annotations_dedup = (
                entity_annotations_dedup
                .join(canonical_ids, left_on="cv_term_entity_id", right_on="old_id", how="left")
                .with_columns(
                    pl.coalesce([pl.col("canonical_id"), pl.col("cv_term_entity_id")]).alias("cv_term_entity_id")
                )
                .drop("canonical_id")
            )
        
        # Remap unit_entity_id
        if "unit_entity_id" in entity_annotations_dedup.columns:
            entity_annotations_dedup = (
                entity_annotations_dedup
                .join(canonical_ids, left_on="unit_entity_id", right_on="old_id", how="left")
                .with_columns(
                    pl.when(pl.col("unit_entity_id").is_not_null())
                      .then(pl.coalesce([pl.col("canonical_id"), pl.col("unit_entity_id")]))
                      .otherwise(None)
                      .alias("unit_entity_id")
                )
                .drop("canonical_id")
            )
        
        entity_annotations_dedup = entity_annotations_dedup.select([col for col in annotation_cols if col in entity_annotations_dedup.columns])
    else:
        entity_annotations_dedup = entity_annotation_df

    logger.info(f"  Deduplicated: {original_count:,} -> {len(entities_dedup):,} entities ({original_count - len(entities_dedup):,} removed)")

    return {
        "entity": entities_dedup,
        "entity_identifier": identifiers_dedup,
        "entity_annotation": entity_annotations_dedup,
        "membership": memberships_dedup,
    }, instances_dedup


def _save_tables(tables: dict[str, pl.DataFrame], instance_df: pl.DataFrame, output_dir: Path, source_name: str):
    """Save processed tables to parquet files."""
    for table_name, df in tables.items():
        if df is not None and len(df) > 0:
            output_path = output_dir / f"local_{table_name}_{source_name}.parquet"
            df.write_parquet(output_path)
            logger.info(f"  Saved {table_name}: {len(df):,} records -> {output_path.name}")
    
    # Save entity instances
    if instance_df is not None and len(instance_df) > 0:
        output_path = output_dir / f"local_entity_instance_{source_name}.parquet"
        instance_df.write_parquet(output_path)
        logger.info(f"  Saved entity_instance: {len(instance_df):,} records -> {output_path.name}")


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
        }

        next_id = 1
        cv_registry = CvTermRegistry()
        instance_registry = InstanceRegistry(source_id)

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
            tables, next_id = _process_entities_vectorized(df, source_id, next_id, cv_registry, instance_registry)

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

        # Get instance DataFrame
        instance_df = instance_registry.to_dataframe()
        if len(instance_df) > 0:
            logger.info(f"  Total entity_instance: {len(instance_df):,} records")

        # Deduplicate entities based on identifier sets
        if final_tables:
            final_tables, instance_df = _deduplicate_entities(final_tables, instance_df)

        # Save tables
        if final_tables:
            _save_tables(final_tables, instance_df, local_tables_dir, source_name)
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
