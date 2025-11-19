from __future__ import annotations
import polars as pl
from pathlib import Path

from .schema import (
    build_cv_term_mapping,
    EntityTypeCv
)

# ------------------------------------------------------------
# Annotation formatting helper
# ------------------------------------------------------------

def fmt_annotation_term(annotation_name: str | None,
                        annotation_id: int | None) -> str:
    """
    Always output EXACTLY:
        "{annotation_name}:{annotation_id}"
    """
    if annotation_name is None:
        return f"UNKNOWN:{annotation_id}"
    return f"{annotation_name}:{annotation_id}"

# ------------------------------------------------------------
# Main builder
# ------------------------------------------------------------

def build_search_interactions_v4(
    database_dir: Path,
    output_path: Path | None = None
) -> pl.DataFrame:
    """
    Final V4 builder (Fixed Grouping):
    Merged A, B, and Interaction annotations into unified Evidence objects
    keyed by Source.
    """

    # ------------------------------------------------------------
    # Load parquet tables
    # ------------------------------------------------------------
    entities = pl.read_parquet(database_dir / "entity.parquet")
    memberships = pl.read_parquet(database_dir / "membership.parquet")
    membership_ann = pl.read_parquet(database_dir / "membership_annotation.parquet")
    entity_ids = pl.read_parquet(database_dir / "entity_identifier.parquet")

    membership_ann_clean = membership_ann.drop("id").rename({
        "annotation_id": "annotation_id_ann",
        "annotation_value": "annotation_value_ann",
        "annotation_unit": "annotation_unit_ann",
    })

    # ------------------------------------------------------------
    # CV accession → entity_id mapping
    # ------------------------------------------------------------
    cv_map = build_cv_term_mapping(database_dir / "entity_identifier.parquet")
    cv_acc_to_id = dict(zip(cv_map["accession"], cv_map["entity_id"]))

    interaction_type_id = cv_acc_to_id[EntityTypeCv.INTERACTION.value]
    cv_term_type_id = cv_acc_to_id[EntityTypeCv.CV_TERM.value]

    # ------------------------------------------------------------
    # Identify interactions + CV term entities
    # ------------------------------------------------------------
    interactions = (
        entities
        .filter(pl.col("entity_type_id") == interaction_type_id)
        .select("entity_id")
        .rename({"entity_id": "interaction_id"})
    )

    cv_term_entities = (
        entities
        .filter(pl.col("entity_type_id") == cv_term_type_id)
        .select("entity_id")
        .rename({"entity_id": "cv_term_id"})
    )

    # ------------------------------------------------------------
    # Identify interaction members (A,B)
    # ------------------------------------------------------------
    mships = (
        memberships
        .select(["id", "parent_id", "member_id", "source_id"])
        .join(interactions, left_on="parent_id", right_on="interaction_id")
        .rename({"id": "membership_id", "parent_id": "interaction_id"})
    )

    pairs = (
        mships
        .group_by("interaction_id")
        .agg(pl.col("member_id").unique().sort().alias("members"))
        .filter(pl.col("members").list.len() == 2)
        .with_columns([
            pl.col("members").list.get(0).alias("member_a_id"),
            pl.col("members").list.get(1).alias("member_b_id"),
        ])
        .select(["interaction_id", "member_a_id", "member_b_id"])
    )

    # Attach A/B position to memberships
    member_entries = (
        mships
        .join(pairs, on="interaction_id")
        .with_columns([
            pl.when(pl.col("member_id") == pl.col("member_a_id")).then(pl.lit("a"))
            .when(pl.col("member_id") == pl.col("member_b_id")).then(pl.lit("b"))
            .otherwise(None)
            .alias("member_position")
        ])
        .filter(pl.col("member_position").is_not_null())
    )

    # ------------------------------------------------------------
    # Lookup names (identifier → name)
    # ------------------------------------------------------------
    name_lookup = (
        entity_ids
        .filter(pl.col("identifier").is_not_null())
        .select(["entity_id", "type_id", "identifier"])
        .rename({"identifier": "name"})
    )

    annotation_name_lookup = (
        name_lookup
        .group_by("entity_id")
        .agg(pl.col("name").first().alias("annotation_name"))
        .rename({"entity_id": "annotation_id"})
    )

    # ------------------------------------------------------------
    # Member TYPE lookup
    # ------------------------------------------------------------
    type_names = (
        name_lookup
        .rename({"entity_id": "etype_id", "name": "type_name"})
    )

    m_with_types = (
        member_entries
        .join(
            entities.select(["entity_id", "entity_type_id"]),
            left_on="member_id",
            right_on="entity_id"
        )
        .join(
            type_names,
            left_on="entity_type_id",
            right_on="etype_id",
            how="left"
        )
        .with_columns([
            (pl.col("type_name") + ":" + pl.col("entity_type_id").cast(pl.Utf8))
                .alias("member_type")
        ])
    )

    member_types = (
        m_with_types
        .select([
            "interaction_id", "member_a_id", "member_b_id",
            "member_position", "member_type"
        ])
        .unique()
        .group_by(["interaction_id", "member_a_id", "member_b_id"])
        .agg([
            pl.col("member_type")
              .filter(pl.col("member_position") == "a")
              .alias("member_a_type"),
            pl.col("member_type")
              .filter(pl.col("member_position") == "b")
              .alias("member_b_type"),
        ])
        .with_columns([
            pl.col("member_a_type").list.first().alias("member_a_type"),
            pl.col("member_b_type").list.first().alias("member_b_type"),
        ])
    )

    # ------------------------------------------------------------
    # Build unified annotation table
    # ------------------------------------------------------------
    # FIX: We removed 'evidence_id' (membership_id) from the selection below.
    # We will rely on 'source_id' to group evidences together.

    # Member-level annotations
    member_ann = (
        member_entries
        .join(membership_ann_clean, on="membership_id", how="left")
        .select([
            "interaction_id",
            "member_a_id",
            "member_b_id",
            "source_id",
            pl.col("member_position").alias("location"),
            pl.col("annotation_id_ann").alias("annotation_id"),
            pl.col("annotation_value_ann").cast(pl.Utf8).alias("value"),
            pl.col("annotation_unit_ann").cast(pl.Utf8).alias("unit"),
        ])
        .filter(pl.col("annotation_id").is_not_null())
    )

    # Interaction-level annotations
    interaction_ann = (
        memberships
        .join(cv_term_entities, left_on="parent_id", right_on="cv_term_id")
        .join(pairs, left_on="member_id", right_on="interaction_id")
        .join(
            membership_ann_clean,
            left_on="id",
            right_on="membership_id",
        )
        .select([
            pl.col("member_id").alias("interaction_id"),
            "member_a_id",
            "member_b_id",
            "source_id",
            pl.lit("interaction").alias("location"),
            pl.col("annotation_id_ann").alias("annotation_id"),
            pl.col("annotation_value_ann").cast(pl.Utf8).alias("value"),
            pl.col("annotation_unit_ann").cast(pl.Utf8).alias("unit"),
        ])
        .filter(pl.col("annotation_id").is_not_null())
    )

    # Combine annotation records
    all_annotations = pl.concat([member_ann, interaction_ann], how="vertical")

    # Join annotation names
    all_annotations = (
        all_annotations
        .join(annotation_name_lookup, on="annotation_id", how="left")
        .with_columns([
            pl.struct(["annotation_name", "annotation_id"])
            .map_elements(lambda r: fmt_annotation_term(r["annotation_name"], r["annotation_id"]), return_dtype=pl.Utf8)
            .alias("annotation_term")
        ])
    )

    # ------------------------------------------------------------
    # Inject SOURCE as a "reference" annotation
    # ------------------------------------------------------------
    source_names = (
        name_lookup
        .select(["entity_id", "name"])
        .group_by("entity_id")
        .agg(pl.col("name").first().alias("source_name"))
        .rename({"entity_id": "source_id"})
    )

    # FIX: Generate source annotations based on unique (interaction, source) tuple
    source_ann = (
        all_annotations
        .select([
            "interaction_id", "member_a_id", "member_b_id",
            "source_id"
        ])
        .unique()
        .join(source_names, on="source_id", how="left")
        .with_columns([
            pl.lit("interaction").alias("location"),
            pl.col("source_name").alias("value"),
            pl.lit(None).alias("unit"),
            pl.col("source_id").alias("annotation_id"),
            pl.lit("reference").alias("annotation_name"),
            (pl.lit("reference") + ":" + pl.col("source_id").cast(pl.Utf8))
                .alias("annotation_term"),
        ])
        .select([
            "interaction_id", "member_a_id", "member_b_id",
            "source_id",
            "location", "annotation_id", "value", "unit",
            "annotation_name", "annotation_term",
        ])
    )

    all_annotations = pl.concat([all_annotations, source_ann], how="vertical")

    # Build struct
    all_annotations = all_annotations.with_columns([
        pl.struct([
            pl.col("annotation_term"),
            pl.col("value"),
            pl.col("unit"),
        ]).alias("annotation_struct")
    ])

    # ------------------------------------------------------------
    # Group by Source (Evidence)
    # ------------------------------------------------------------
    # FIX: Group by 'source_id' instead of 'evidence_id' (membership_id)
    ann_grouped = (
        all_annotations
        .group_by([
            "interaction_id", "member_a_id", "member_b_id",
            "source_id", "location",
        ])
        .agg(pl.col("annotation_struct").alias("ann"))
    )

    # Reshape evidence into final lists
    # Pivot by location to create separate columns
    evidence_pivoted = (
        ann_grouped
        .pivot(
            index=["interaction_id", "member_a_id", "member_b_id", "source_id"],
            on="location",
            values="ann",
            aggregate_function="first" # List of structs
        )
        .with_columns([
            # Handle null columns and flatten nested lists
            pl.when(pl.col("interaction").is_not_null())
              .then(pl.col("interaction"))
              .otherwise(pl.lit([]).cast(pl.List(pl.Struct)))
              .alias("interaction_annotations"),
            pl.when(pl.col("a").is_not_null())
              .then(pl.col("a"))
              .otherwise(pl.lit([]).cast(pl.List(pl.Struct)))
              .alias("member_a_annotations"),
            pl.when(pl.col("b").is_not_null())
              .then(pl.col("b"))
              .otherwise(pl.lit([]).cast(pl.List(pl.Struct)))
              .alias("member_b_annotations"),
        ])
    )

    evidence = (
        evidence_pivoted
        .drop(["source_id", "interaction", "a", "b"])
        .with_columns([
            pl.struct([
                pl.col("interaction_annotations"),
                pl.col("member_a_annotations"),
                pl.col("member_b_annotations"),
            ]).alias("evidence_item")
        ])
        .group_by(["interaction_id", "member_a_id", "member_b_id"])
        .agg(pl.col("evidence_item").alias("evidence"))
    )

    # ------------------------------------------------------------
    # Build final output
    # ------------------------------------------------------------
    final = (
        evidence
        .join(member_types, on=["interaction_id", "member_a_id", "member_b_id"])
        .with_columns([
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
        .select(["interaction_id", "members", "evidence"])
    )

    if output_path:
        final.write_parquet(output_path)

    return final