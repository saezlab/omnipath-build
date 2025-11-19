"""Build search index for interactions.

This module creates a denormalized search index for interactions, aggregating
all evidence by unique member pairs and organizing annotations by direction.
"""
from __future__ import annotations

import polars as pl
from polars import Field
from pathlib import Path
from typing import Literal
from collections import defaultdict

from .schema import (
    build_cv_term_mapping,
    INTERACTION_TYPE_ACCESSION,
    INTERACTION_TYPE_ACCESSIONS,
    DETECTION_METHOD_ACCESSIONS,
    BIOLOGICAL_EFFECT_ACCESSIONS,
    CAUSAL_STATEMENT_ACCESSIONS,
    CAUSAL_MECHANISM_ACCESSIONS,
    PHARMACOLOGICAL_ACTION_ACCESSIONS,
    BIOLOGICAL_ROLE_ACCESSIONS,
    AFFINITY_ANNOTATION_ACCESSIONS,
    INTERACTION_PARAMETER_ACCESSIONS,
    POSITIVE_SIGN_ACCESSIONS,
    NEGATIVE_SIGN_ACCESSIONS,
    SOURCE_ROLE_ACCESSIONS,
    TARGET_ROLE_ACCESSIONS,
    REFERENCE_IDENTIFIER_ACCESSIONS,
    CV_TERM_ACCESSION_TYPE,
)

REFERENCE_PREFIX_OVERRIDES = {
    "MI:0446": "PMID",
    "MI:0574": "DOI",
    "MI:1042": "PMC",
    "MI:2347": "BIORXIV",
    "OM:0206": "PATENT",
}


# =============================================================================
# Helper Functions: Direction & Sign Detection
# =============================================================================

def detect_direction_from_annotation(
    annotation_accession: str,
    member_position: Literal["a", "b"]
) -> Literal["a_to_b", "b_to_a", "undirected"] | None:
    """Detect interaction direction from an annotation CV term.

    Args:
        annotation_accession: CV term accession (e.g., "MI:0501")
        member_position: Which member this annotation belongs to ("a" or "b")

    Returns:
        Direction string or None if annotation doesn't indicate direction
    """
    # Source roles: member acts ON the other member
    if annotation_accession in SOURCE_ROLE_ACCESSIONS:
        return "a_to_b" if member_position == "a" else "b_to_a"

    # Target roles: member is acted upon BY the other member
    if annotation_accession in TARGET_ROLE_ACCESSIONS:
        return "b_to_a" if member_position == "a" else "a_to_b"

    # Causal statements and pharmacological actions: member acts ON the other
    if annotation_accession in (CAUSAL_STATEMENT_ACCESSIONS | PHARMACOLOGICAL_ACTION_ACCESSIONS):
        return "a_to_b" if member_position == "a" else "b_to_a"

    # No directional information
    return None


def detect_sign_from_annotation(annotation_accession: str) -> Literal["positive", "negative"] | None:
    """Detect effect sign (positive/negative) from an annotation CV term.

    Args:
        annotation_accession: CV term accession (e.g., "MI:2236")

    Returns:
        "positive", "negative", or None if annotation doesn't indicate sign
    """
    if annotation_accession in POSITIVE_SIGN_ACCESSIONS:
        return "positive"
    if annotation_accession in NEGATIVE_SIGN_ACCESSIONS:
        return "negative"
    return None


def categorize_annotation(annotation_accession: str) -> str:
    """Categorize an annotation by its CV term type.

    Args:
        annotation_accession: CV term accession (e.g., "MI:0915")

    Returns:
        Category name: "interaction_type", "detection_method", "causal_statement",
        "causal_mechanism", "pharmacological_action", "biological_role",
        "affinity", "interaction_parameter", or "other"
    """
    if annotation_accession in INTERACTION_TYPE_ACCESSIONS:
        return "interaction_type"
    if annotation_accession in DETECTION_METHOD_ACCESSIONS:
        return "detection_method"
    if annotation_accession in CAUSAL_STATEMENT_ACCESSIONS:
        return "causal_statement"
    if annotation_accession in CAUSAL_MECHANISM_ACCESSIONS:
        return "causal_mechanism"
    if annotation_accession in PHARMACOLOGICAL_ACTION_ACCESSIONS:
        return "pharmacological_action"
    if annotation_accession in BIOLOGICAL_ROLE_ACCESSIONS:
        return "biological_role"
    if annotation_accession in AFFINITY_ANNOTATION_ACCESSIONS:
        return "affinity"
    if annotation_accession in INTERACTION_PARAMETER_ACCESSIONS:
        return "interaction_parameter"
    return "other"


# =============================================================================
# Helper Functions: Aggregating Annotation Records
# =============================================================================

# Simplified annotation dtype - all annotations have same structure
ANNOTATION_DTYPE = pl.List(pl.Struct([
    Field("term", pl.Utf8),
    Field("value", pl.Utf8),
    Field("unit", pl.Utf8),
    Field("provenance_ids", pl.List(pl.Int64)),
]))


def _normalize_record_list(records: list[dict] | None) -> list[dict]:
    """Ensure we always iterate over a plain Python list of dicts."""
    if records is None:
        return []
    if isinstance(records, list):
        return records
    return list(records)


def aggregate_annotations(records: list[dict]) -> list[dict]:
    """Group annotations by term/value/unit and collect provenance IDs.

    This handles all annotation types with a unified structure.
    """
    grouped: dict[tuple[str, str | None, str | None], set[int]] = defaultdict(set)
    for record in _normalize_record_list(records):
        if not record:
            continue
        term = record.get("annotation_term")
        if term is None:
            continue
        value = record.get("annotation_value")
        unit = record.get("annotation_unit")
        if value is not None:
            value = str(value)
        if unit is not None:
            unit = str(unit)
        prov_id = record.get("provenance_id")
        if prov_id is None:
            continue
        grouped[(term, value, unit)].add(prov_id)

    return [
        {
            "term": term,
            "value": value,
            "unit": unit,
            "provenance_ids": sorted(provenance_ids),
        }
        for (term, value, unit), provenance_ids in sorted(
            grouped.items(), key=lambda item: (item[0][0], item[0][1] or "", item[0][2] or "")
        )
    ]


# =============================================================================
# Helper Functions: Reference Extraction
# =============================================================================

def extract_references(
    memberships: pl.DataFrame,
    member_positions_map: pl.DataFrame,
    cv_accession_to_id: dict[str, int],
    cv_id_to_accession: dict[int, str],
) -> pl.DataFrame:
    """Extract reference annotations from membership relations.

    References are represented as memberships where the parent entity is a
    reference CV term (e.g., PMID) and the member is an interaction entity.

    Args:
        memberships: Global membership table
        member_positions_map: Mapping of interaction_id -> member_a/b ids
        cv_accession_to_id: Mapping from CV accession to entity_id
        cv_id_to_accession: Mapping from entity_id to CV accession

    Returns:
        DataFrame with columns [interaction_id, member_a_id, member_b_id, source_id, reference]
    """
    reference_parent_ids = [
        cv_accession_to_id.get(acc)
        for acc in REFERENCE_IDENTIFIER_ACCESSIONS
        if acc in cv_accession_to_id
    ]

    if not reference_parent_ids:
        return pl.DataFrame(
            schema={
                "interaction_id": pl.Int64,
                "member_a_id": pl.Int64,
                "member_b_id": pl.Int64,
                "source_id": pl.Int64,
                "reference": pl.Utf8,
            }
        )

    reference_prefix_map = {
        cv_accession_to_id[acc]: REFERENCE_PREFIX_OVERRIDES.get(acc, acc)
        for acc in REFERENCE_IDENTIFIER_ACCESSIONS
        if acc in cv_accession_to_id
    }

    member_lookup = member_positions_map.rename({"interaction_id": "member_id"})

    references = (
        memberships
        .filter(pl.col("parent_id").is_in(reference_parent_ids))
        .filter(pl.col("annotation_value").is_not_null())
        .join(member_lookup, on="member_id", how="inner")
        .with_columns([
            pl.col("member_id").alias("interaction_id"),
            pl.col("parent_id").map_elements(
                lambda pid: reference_prefix_map.get(pid, cv_id_to_accession.get(pid, "REF")),
                return_dtype=pl.Utf8,
            ).alias("reference_prefix"),
        ])
        .with_columns([
            (pl.col("reference_prefix") + pl.lit(":") + pl.col("annotation_value")).alias("reference")
        ])
        .select([
            "interaction_id",
            "member_a_id",
            "member_b_id",
            "source_id",
            "reference",
        ])
        .unique()
    )

    return references


def build_provenance_mapping(
    annotated_memberships: pl.DataFrame,
    references: pl.DataFrame,
    entity_identifiers: pl.DataFrame,
    cv_accession_to_id: dict[str, int]
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Build provenance records and mapping.

    Args:
        annotated_memberships: DataFrame with membership annotations
        references: DataFrame with extracted references
        entity_identifiers: Entity identifier table
        cv_accession_to_id: Mapping from CV accession to entity_id

    Returns:
        Tuple of (provenance_df, annotated_memberships_with_prov_id)
        - provenance_df: columns [interaction_id, member_a_id, member_b_id, prov_id, source_entity_id, source_name, references]
        - annotated_memberships_with_prov_id: original data with added provenance_id column
    """
    # Get unique sources from both memberships and annotations
    membership_sources = (
        annotated_memberships
        .select(["interaction_id", "member_a_id", "member_b_id", "source_id"])
        .unique()
    )

    annotation_sources = (
        annotated_memberships
        .filter(pl.col("source_id_annot").is_not_null())
        .select([
            "interaction_id",
            "member_a_id",
            "member_b_id",
            pl.col("source_id_annot").alias("source_id")
        ])
        .unique()
    )

    all_sources = (
        pl.concat([membership_sources, annotation_sources])
        .unique()
    )

    # Get source names from entity_identifiers
    name_type_id = cv_accession_to_id.get("OM:0202")  # NAME identifier type
    if name_type_id is None:
        # Fallback: use source_id as both entity_id and name
        sources_with_names = (
            all_sources
            .with_columns([
                pl.col("source_id").alias("source_entity_id"),
                pl.col("source_id").cast(pl.Utf8).alias("source_name"),
            ])
        )
    else:
        source_names = (
            entity_identifiers
            .filter(pl.col("type_id") == name_type_id)
            .select(["entity_id", "identifier"])
            .rename({"entity_id": "source_entity_id", "identifier": "source_name"})
        )

        # Join sources with names
        sources_with_names = (
            all_sources
            .join(
                source_names,
                left_on="source_id",
                right_on="source_entity_id",
                how="left",
                coalesce=False  # Keep both columns
            )
            .with_columns([
                # Fill missing fields with source_id (for cases where name lookup fails)
                pl.when(pl.col("source_entity_id").is_null())
                .then(pl.col("source_id"))
                .otherwise(pl.col("source_entity_id"))
                .alias("source_entity_id_final"),
                pl.when(pl.col("source_name").is_null())
                .then(pl.col("source_id").cast(pl.Utf8))
                .otherwise(pl.col("source_name"))
                .alias("source_name_final"),
            ])
            .select([
                "interaction_id",
                "member_a_id",
                "member_b_id",
                "source_id",
                pl.col("source_entity_id_final").alias("source_entity_id"),
                pl.col("source_name_final").alias("source_name"),
            ])
        )

    # Aggregate references by source
    references_by_source = (
        references
        .group_by(["interaction_id", "member_a_id", "member_b_id", "source_id"])
        .agg(pl.col("reference").unique())
    )

    # Join sources with references
    provenance = (
        sources_with_names
        .join(
            references_by_source,
            on=["interaction_id", "member_a_id", "member_b_id", "source_id"],
            how="left"
        )
        .with_columns([
            # Fill null references with empty list
            pl.when(pl.col("reference").is_null())
            .then(pl.lit([]))
            .otherwise(pl.col("reference"))
            .alias("references")
        ])
        # Assign sequential provenance IDs per interaction
        .sort(["interaction_id", "member_a_id", "member_b_id", "source_id"])
        .with_columns([
            pl.int_range(pl.len()).over(["interaction_id", "member_a_id", "member_b_id"]).alias("prov_id")
        ])
        .select([
            "interaction_id",
            "member_a_id",
            "member_b_id",
            "prov_id",
            "source_id",
            "source_entity_id",
            "source_name",
            "references",
        ])
    )

    # Add provenance_id to annotated_memberships
    # Map (interaction, member_a, member_b, source_id) -> prov_id
    prov_id_mapping = provenance.select([
        "interaction_id",
        "member_a_id",
        "member_b_id",
        "source_id",
        "prov_id",
    ])

    annotated_with_prov = (
        annotated_memberships
        .join(
            prov_id_mapping,
            on=["interaction_id", "member_a_id", "member_b_id", "source_id"],
            how="left"
        )
        .rename({"prov_id": "membership_prov_id"})
        .join(
            prov_id_mapping.rename({
                "source_id": "source_id_annot",
                "prov_id": "annotation_prov_id"
            }),
            on=["interaction_id", "member_a_id", "member_b_id", "source_id_annot"],
            how="left"
        )
        .with_columns([
            # Use annotation source if available, otherwise membership source
            pl.when(pl.col("annotation_prov_id").is_not_null())
            .then(pl.col("annotation_prov_id"))
            .otherwise(pl.col("membership_prov_id"))
            .alias("provenance_id")
        ])
    )

    return provenance, annotated_with_prov


# =============================================================================
# Main Pipeline Functions
# =============================================================================

def build_search_interactions(
    database_dir: Path,
    output_path: Path | None = None,
    test_mode: bool = False,
) -> pl.DataFrame:
    """Build search index for interactions.

    Args:
        database_dir: Path to directory containing parquet tables
        output_path: Optional path to write output parquet file
        test_mode: If True, limit to first 1000 interactions for testing

    Returns:
        DataFrame with denormalized interaction search documents
    """
    print("Loading data tables...")

    # Load core tables
    entities = pl.read_parquet(database_dir / "entity.parquet")
    memberships = pl.read_parquet(database_dir / "membership.parquet")
    membership_annotations = pl.read_parquet(database_dir / "membership_annotation.parquet")
    entity_identifiers = pl.read_parquet(database_dir / "entity_identifier.parquet")

    # Build CV term mappings
    print("Building CV term mappings...")
    cv_term_mapping = build_cv_term_mapping(database_dir / "entity_identifier.parquet")

    # Create bidirectional lookup: accession <-> entity_id
    cv_accession_to_id = dict(zip(
        cv_term_mapping['accession'].to_list(),
        cv_term_mapping['entity_id'].to_list()
    ))
    cv_id_to_accession = {v: k for k, v in cv_accession_to_id.items()}

    # Get interaction type entity_id
    interaction_type_id = cv_accession_to_id.get(INTERACTION_TYPE_ACCESSION)
    if interaction_type_id is None:
        raise ValueError(f"Interaction type ({INTERACTION_TYPE_ACCESSION}) not found in CV terms")

    print(f"Interaction type entity_id: {interaction_type_id}")

    # Filter to interactions only
    interactions = entities.filter(pl.col("entity_type_id") == interaction_type_id)

    if test_mode:
        print("TEST MODE: Finding 1000 interactions with annotations...")
        # Get memberships for all interactions
        all_interaction_memberships = memberships.filter(
            pl.col("parent_id").is_in(interactions["entity_id"])
        )
        # Find membership_ids that have annotations
        annotated_membership_ids = membership_annotations["membership_id"].unique()
        # Filter to interactions whose memberships have annotations
        annotated_memberships_for_test = all_interaction_memberships.filter(
            pl.col("id").is_in(annotated_membership_ids)
        )
        # Get unique interaction IDs
        annotated_interaction_ids = annotated_memberships_for_test["parent_id"].unique()
        # Limit to first 1000
        test_interaction_ids = annotated_interaction_ids.head(1000)
        interactions = interactions.filter(pl.col("entity_id").is_in(test_interaction_ids))

    print(f"Processing {interactions.height} interactions...")

    # Get memberships for interactions
    interaction_memberships = (
        memberships
        .join(interactions.select("entity_id"), left_on="parent_id", right_on="entity_id")
        .rename({"parent_id": "interaction_id", "id": "membership_id"})
    )

    print(f"Found {interaction_memberships.height} interaction memberships")

    # Normalize member pairs: ensure consistent (member_a, member_b) ordering
    # Group by interaction_id and assign positions based on member_id ordering
    print("Normalizing member pairs...")

    # For each interaction, get both members and determine which is 'a' and 'b'
    member_pairs = (
        interaction_memberships
        .group_by("interaction_id")
        .agg([
            pl.col("member_id").unique().sort().alias("sorted_members"),
            pl.col("membership_id").alias("membership_ids"),
            pl.col("source_id").alias("source_ids"),
        ])
        .filter(pl.col("sorted_members").list.len() == 2)  # Only binary interactions
    )

    print(f"Found {member_pairs.height} binary interactions")

    # Create a mapping of interaction_id -> (member_a_id, member_b_id)
    member_positions_map = (
        member_pairs
        .with_columns([
            pl.col("sorted_members").list.get(0).alias("member_a_id"),
            pl.col("sorted_members").list.get(1).alias("member_b_id"),
        ])
        .select(["interaction_id", "member_a_id", "member_b_id"])
    )

    # Join memberships with the position mapping
    memberships_with_positions = (
        interaction_memberships
        .join(
            member_positions_map,
            on="interaction_id",
        )
        .with_columns([
            # Determine if this member is 'a' or 'b'
            pl.when(pl.col("member_id") == pl.col("member_a_id"))
            .then(pl.lit("a"))
            .when(pl.col("member_id") == pl.col("member_b_id"))
            .then(pl.lit("b"))
            .otherwise(pl.lit(None))
            .alias("member_position")
        ])
        .filter(pl.col("member_position").is_not_null())
    )

    print(f"Normalized {memberships_with_positions.height} memberships with positions")

    # Join with member entity types
    print("Resolving member entity types...")
    memberships_with_types = (
        memberships_with_positions
        .join(
            entities.select(["entity_id", "entity_type_id"]),
            left_on="member_id",
            right_on="entity_id",
        )
        .rename({"entity_type_id": "member_entity_type_id"})
    )

    # Get entity type names
    # First, find the NAME identifier type entity_id
    name_type_id = cv_accession_to_id.get("OM:0202")  # NAME identifier type
    if name_type_id is None:
        raise ValueError("NAME identifier type (OM:0202) not found in CV terms")

    # Get names for entity types
    entity_type_names = (
        entity_identifiers
        .filter(pl.col("type_id") == name_type_id)
        .select(["entity_id", "identifier"])
        .rename({"entity_id": "type_entity_id", "identifier": "type_name"})
    )

    memberships_with_type_names = (
        memberships_with_types
        .join(
            entity_type_names,
            left_on="member_entity_type_id",
            right_on="type_entity_id",
        )
        .with_columns([
            # Format as "{entity_id}:{type_name}"
            (pl.col("member_entity_type_id").cast(pl.Utf8) + ":" +
             pl.col("type_name").str.to_titlecase().str.replace_all(" ", ""))
            .alias("member_type_formatted")
        ])
    )

    print(f"Resolved entity types for {memberships_with_type_names.height} memberships")

    member_type_lookup = (
        memberships_with_type_names
        .select([
            "interaction_id",
            "member_a_id",
            "member_b_id",
            "member_position",
            "member_type_formatted",
        ])
        .unique()
        .group_by(["interaction_id", "member_a_id", "member_b_id"])
        .agg([
            pl.col("member_type_formatted").filter(pl.col("member_position") == "a").alias("member_a_type"),
            pl.col("member_type_formatted").filter(pl.col("member_position") == "b").alias("member_b_type"),
        ])
        .with_columns([
            pl.col("member_a_type").list.first().alias("member_a_type"),
            pl.col("member_b_type").list.first().alias("member_b_type"),
        ])
        .select([
            "interaction_id",
            "member_a_id",
            "member_b_id",
            "member_a_type",
            "member_b_type",
        ])
    )

    # Join with membership annotations
    print("Joining membership annotations...")
    annotated_memberships = (
        memberships_with_type_names
        .join(
            membership_annotations.select([
                "membership_id",
                "annotation_id",
                "annotation_value",
                "annotation_unit",
                "source_id",
            ]),
            on="membership_id",
            how="left",
            suffix="_annot"
        )
    )

    print(f"Joined {annotated_memberships.height} annotation records")

    # Resolve annotation CV terms
    print("Resolving annotation CV terms...")

    # Get CV term accessions for annotation_ids
    cv_term_identifiers = (
        entity_identifiers
        .filter(pl.col("type_id") == cv_accession_to_id[CV_TERM_ACCESSION_TYPE])
        .select(["entity_id", "identifier"])
        .rename({"entity_id": "cv_entity_id", "identifier": "annotation_accession"})
    )

    annotated_memberships = (
        annotated_memberships
        .join(
            cv_term_identifiers,
            left_on="annotation_id",
            right_on="cv_entity_id",
            how="left"
        )
    )

    # Also get annotation names
    annotated_memberships = (
        annotated_memberships
        .join(
            entity_type_names.rename({"type_entity_id": "annot_entity_id", "type_name": "annotation_name"}),
            left_on="annotation_id",
            right_on="annot_entity_id",
            how="left"
        )
        .with_columns([
            # Format annotation term as "{entity_id}:{name}"
            pl.when(pl.col("annotation_name").is_not_null())
            .then(
                pl.col("annotation_id").cast(pl.Utf8) + ":" +
                pl.col("annotation_name").str.to_titlecase().str.replace_all(" ", "")
            )
            .otherwise(pl.col("annotation_id").cast(pl.Utf8))
            .alias("annotation_term")
        ])
    )

    # Categorize annotations and detect directions/signs
    print("Categorizing annotations and detecting directions...")
    annotated_memberships = annotated_memberships.with_columns([
        pl.col("annotation_accession").map_elements(
            lambda acc: categorize_annotation(acc) if acc is not None else "other",
            return_dtype=pl.Utf8
        ).alias("annotation_category"),
        pl.struct(["annotation_accession", "member_position"]).map_elements(
            lambda row: detect_direction_from_annotation(row["annotation_accession"], row["member_position"])
            if row["annotation_accession"] is not None else None,
            return_dtype=pl.Utf8
        ).alias("detected_direction"),
        pl.col("annotation_accession").map_elements(
            lambda acc: detect_sign_from_annotation(acc) if acc is not None else None,
            return_dtype=pl.Utf8
        ).alias("detected_sign"),
    ])

    # For annotations without detected direction, assign "undirected"
    annotated_memberships = annotated_memberships.with_columns([
        pl.when(pl.col("detected_direction").is_null())
        .then(pl.lit("undirected"))
        .otherwise(pl.col("detected_direction"))
        .alias("direction")
    ])

    print(f"Categorized {annotated_memberships.height} annotations")

    # Extract references
    print("Extracting references...")
    references = extract_references(memberships, member_positions_map, cv_accession_to_id, cv_id_to_accession)
    print(f"Extracted {references.height} reference annotations")

    # Build provenance mapping
    print("Building provenance mapping...")
    provenance, annotated_with_prov = build_provenance_mapping(
        annotated_memberships,
        references,
        entity_identifiers,
        cv_accession_to_id
    )
    print(f"Built {provenance.height} provenance records")

    # Aggregate annotations by direction
    print("Aggregating annotations by direction...")

    # Filter to keep only rows with valid annotations (not references, not null)
    non_reference_annotations = (
        annotated_with_prov
        .filter(pl.col("annotation_id").is_not_null())  # Must have an annotation
    )

    # Group by interaction, members, direction, and member position
    # Separate annotations into: direction-level, member_a-level, and member_b-level
    direction_aggregations = (
        non_reference_annotations
        .group_by(["interaction_id", "member_a_id", "member_b_id", "direction"])
        .agg([
            # Direction-level annotations (not member-specific)
            pl.when(pl.col("annotation_category") != "biological_role")
            .then(pl.struct([
                "annotation_term",
                "annotation_value",
                "annotation_unit",
                "provenance_id"
            ]))
            .drop_nulls()
            .alias("direction_annotations_raw"),

            # Member A annotations (biological roles)
            pl.when((pl.col("annotation_category") == "biological_role") & (pl.col("member_position") == "a"))
            .then(pl.struct([
                "annotation_term",
                "annotation_value",
                "annotation_unit",
                "provenance_id"
            ]))
            .drop_nulls()
            .alias("member_a_annotations_raw"),

            # Member B annotations (biological roles)
            pl.when((pl.col("annotation_category") == "biological_role") & (pl.col("member_position") == "b"))
            .then(pl.struct([
                "annotation_term",
                "annotation_value",
                "annotation_unit",
                "provenance_id"
            ]))
            .drop_nulls()
            .alias("member_b_annotations_raw"),

            # Aggregate signs for this direction
            pl.col("detected_sign").drop_nulls().unique().alias("signs"),
        ])
    )

    # Determine final sign per direction
    direction_aggregations = direction_aggregations.with_columns([
        pl.when(pl.col("signs").list.len() == 0)
        .then(pl.lit("unknown"))
        .when(pl.col("signs").list.len() == 1)
        .then(pl.col("signs").list.first())
        .when((pl.col("signs").list.contains("positive")) & (pl.col("signs").list.contains("negative")))
        .then(pl.lit("mixed"))
        .when(pl.col("signs").list.contains("positive"))
        .then(pl.lit("positive"))
        .when(pl.col("signs").list.contains("negative"))
        .then(pl.lit("negative"))
        .otherwise(pl.lit("unknown"))
        .alias("sign")
    ])

    # Aggregate annotations
    direction_aggregations = direction_aggregations.with_columns([
        pl.col("direction_annotations_raw").map_elements(aggregate_annotations, return_dtype=ANNOTATION_DTYPE).alias("direction_annotations"),
        pl.col("member_a_annotations_raw").map_elements(aggregate_annotations, return_dtype=ANNOTATION_DTYPE).alias("member_a_annotations"),
        pl.col("member_b_annotations_raw").map_elements(aggregate_annotations, return_dtype=ANNOTATION_DTYPE).alias("member_b_annotations"),
    ])

    print(f"Created {direction_aggregations.height} direction bundles")

    direction_aggregations = direction_aggregations.drop([
        "direction_annotations_raw",
        "member_a_annotations_raw",
        "member_b_annotations_raw",
        "signs",
    ])

    # Final aggregation by interaction
    print("Final aggregation by interaction...")

    # Prepare provenance data
    provenance_aggregated = (
        provenance
        .group_by(["interaction_id", "member_a_id", "member_b_id"])
        .agg([
            pl.struct([
                # Format as "SourceName:entity_id"
                (pl.col("source_name") + ":" + pl.col("source_entity_id").cast(pl.Utf8)).alias("source"),
                "references"
            ]).alias("provenance")
        ])
    )

    # Join direction bundles with member types and provenance
    final_interactions = (
        direction_aggregations
        .group_by(["interaction_id", "member_a_id", "member_b_id"])
        .agg([
            pl.struct([
                "direction",
                "sign",
                "direction_annotations",
                "member_a_annotations",
                "member_b_annotations",
            ]).alias("directions")
        ])
        .join(member_type_lookup, on=["interaction_id", "member_a_id", "member_b_id"])
        .join(provenance_aggregated, on=["interaction_id", "member_a_id", "member_b_id"])
        .with_columns([
            # Create members struct with a and b keys
            pl.struct([
                pl.struct([
                    pl.col("member_a_id").alias("id"),
                    pl.col("member_a_type").alias("type"),
                ]).alias("a"),
                pl.struct([
                    pl.col("member_b_id").alias("id"),
                    pl.col("member_b_type").alias("type"),
                ]).alias("b"),
            ]).alias("members")
        ])
        .select(["interaction_id", "members", "directions", "provenance"])
    )

    print(f"Final output: {final_interactions.height} unique interactions")

    # Write output if path provided
    if output_path:
        print(f"Writing output to {output_path}...")
        final_interactions.write_parquet(output_path)

    return final_interactions


# =============================================================================
# CLI Entry Point
# =============================================================================

def main():
    """CLI entry point for building interaction search index."""
    from pathlib import Path
    import sys

    if len(sys.argv) < 2:
        print("Usage: python -m omnipath_build.search_builder.build_search_interactions <database_dir> [--test]")
        sys.exit(1)

    database_dir = Path(sys.argv[1])
    test_mode = "--test" in sys.argv

    output_path = database_dir / ("search_interactions_test.parquet" if test_mode else "search_interactions.parquet")

    result = build_search_interactions(database_dir, output_path, test_mode=test_mode)

    print(f"\nSearch interactions built successfully!")
    print(f"Output: {output_path}")
    print(f"Shape: {result.shape}")


if __name__ == "__main__":
    main()
