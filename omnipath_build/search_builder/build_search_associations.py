"""Build Meilisearch associations documents from global tables.

This module builds a searchable index of membership/association relationships:
- Food → Compound (food contains compound)
- Complex → Protein (complex has member)
- Pathway → Reaction (pathway includes reaction)
- etc.

Each association document represents a parent-member relationship with:
- Parent entity ID and type
- Member entity ID and type
- Sources that report this association
- Optional annotations (e.g., concentration for food-compound)
- Flattened annotation terms for filtering

Entity names and identifiers are fetched from the entity search index when needed.
"""
from __future__ import annotations

import argparse
import logging
from pathlib import Path

import polars as pl
from polars import Field

from .schema import (
    EntityTypeCv,
    INTERACTION_TYPE_ACCESSION,
)

__all__ = ["build_search_associations"]

logger = logging.getLogger(__name__)

# --- Schema Definitions ---
ANNOT_STRUCT = pl.Struct([Field("key", pl.Utf8), Field("value", pl.Utf8), Field("unit", pl.Utf8)])
ANNOT_LIST_DTYPE = pl.List(ANNOT_STRUCT)


def _resolve_member_entity_id(mem: pl.DataFrame, inst: pl.DataFrame) -> pl.DataFrame:
    """Resolve member to entity_id, handling both direct entity and instance references."""
    return (
        mem
        .join(
            inst.select([
                pl.col("id").alias("member_instance_id"),
                pl.col("entity_id").alias("instance_entity_id")
            ]),
            on="member_instance_id",
            how="left"
        )
        .with_columns(
            pl.coalesce([
                pl.col("instance_entity_id"),
                pl.col("member_entity_id")
            ]).alias("member_id")
        )
    )


def _resolve_parent_entity_id(mem: pl.DataFrame, inst: pl.DataFrame) -> pl.DataFrame:
    """Resolve parent to entity_id, handling both direct entity and instance references."""
    return (
        mem
        .join(
            inst.select([
                pl.col("id").alias("parent_instance_id"),
                pl.col("entity_id").alias("parent_instance_entity_id")
            ]),
            on="parent_instance_id",
            how="left"
        )
        .with_columns(
            pl.coalesce([
                pl.col("parent_instance_entity_id"),
                pl.col("parent_entity_id")
            ]).alias("parent_id")
        )
    )


def build_search_associations(global_tables_dir: Path, output_path: Path) -> Path:
    """Build Meilisearch association documents from membership table.
    
    Args:
        global_tables_dir: Path to directory containing global parquet tables
        output_path: Path to write output parquet file
        
    Returns:
        Path to output file
    """
    logger.info("=" * 80 + "\nBuilding Meilisearch association documents\n" + "=" * 80)

    # 1. Load Data
    tables = {n: pl.read_parquet(global_tables_dir / f"{n}.parquet")
              for n in ["entity", "membership", "entity_identifier", "entity_instance", "entity_annotation"]}
    logger.info("Loaded tables: %s", " ".join(f"{k}={len(v)}" for k, v in tables.items()))

    # Load CV term label mappings
    if (global_tables_dir / "cv_terms.parquet").exists():
        cv_terms = pl.read_parquet(global_tables_dir / "cv_terms.parquet")
        logger.info(f"Loaded {len(cv_terms)} CV term labels")
    else:
        logger.warning(f"cv_terms.parquet not found in {global_tables_dir}")
        cv_terms = pl.DataFrame(schema={"accession": pl.Utf8, "label": pl.Utf8})

    ent = tables["entity"]
    mem_raw = tables["membership"]
    ent_id = tables["entity_identifier"]
    inst = tables["entity_instance"]
    ent_annot = tables["entity_annotation"]

    # 2. Resolve polymorphic membership columns
    mem = _resolve_member_entity_id(mem_raw, inst)
    mem = _resolve_parent_entity_id(mem, inst)
    
    # 3. Filter: Exclude interactions (those are in interactions index)
    # Get interaction entity IDs
    interaction_ids = set(
        ent.filter(pl.col("entity_type") == INTERACTION_TYPE_ACCESSION)["entity_id"].to_list()
    )
    logger.info(f"Excluding {len(interaction_ids)} interaction entities from associations")
    
    # Filter membership to non-interaction parents
    mem_assoc = mem.filter(~pl.col("parent_id").is_in(list(interaction_ids)))
    logger.info(f"Building associations from {len(mem_assoc)} membership rows")
    
    if mem_assoc.is_empty():
        logger.warning("No association memberships found; writing empty file.")
        empty_schema = {
            "association_id": pl.Int64,
            "association_key": pl.Utf8,
            "parent_entity_id": pl.Int64,
            "parent_entity_type": pl.Utf8,
            "member_entity_id": pl.Int64,
            "member_entity_type": pl.Utf8,
            "sources": pl.List(pl.Utf8),
            "annotations": ANNOT_LIST_DTYPE,
            "association_annotation_terms": pl.List(pl.Utf8),
        }
        pl.DataFrame(schema=empty_schema).write_parquet(output_path)
        return output_path
    
    # 4. Build entity info lookups
    # Name identifier type
    name_tid = "OM:0202"  # IdentifierNamespaceCv.NAME

    # Entity type labels
    entity_type_labels = cv_terms.select([
        pl.col("accession").alias("entity_type"),
        pl.col("label").alias("entity_type_label")
    ])

    # Entity types with labels
    entity_types = (
        ent
        .join(entity_type_labels, on="entity_type", how="left")
        .with_columns(
            pl.coalesce([
                pl.col("entity_type_label"),
                (pl.col("entity_type") + ":" + pl.col("entity_type"))
            ]).alias("entity_type_formatted")
        )
        .select("entity_id", pl.col("entity_type_formatted").alias("entity_type"))
    )

    # Source names for display
    source_names = (
        ent_id.filter(pl.col("type_id") == name_tid)
        .group_by("entity_id")
        .agg(pl.col("identifier").first().alias("source_name"))
    )
    
    # 5. Build base association documents
    # Aggregate by (parent_id, member_id) pair
    assoc_base = (
        mem_assoc
        .group_by(["parent_id", "member_id"])
        .agg([
            pl.col("source_id").unique().alias("source_ids"),
            pl.col("member_instance_id").filter(pl.col("member_instance_id").is_not_null()).unique().alias("member_instance_ids"),
        ])
        .with_columns(
            (pl.col("parent_id").cast(pl.Utf8) + "_" + pl.col("member_id").cast(pl.Utf8)).alias("association_key")
        )
    )
    
    logger.info(f"Aggregated to {len(assoc_base)} unique parent-member associations")
    
    # 6. Join parent entity info
    assoc_with_parent = (
        assoc_base
        .join(entity_types.rename({"entity_id": "parent_id", "entity_type": "parent_entity_type"}), on="parent_id", how="left")
    )

    # 7. Join member entity info
    assoc_with_member = (
        assoc_with_parent
        .join(entity_types.rename({"entity_id": "member_id", "entity_type": "member_entity_type"}), on="member_id", how="left")
    )
    
    # 8. Format sources
    assoc_with_sources = (
        assoc_with_member
        .explode("source_ids")
        .join(source_names, left_on="source_ids", right_on="entity_id", how="left")
        .with_columns(
            pl.coalesce([
                pl.col("source_name"),
                pl.lit("Source")
            ]).alias("source_display")
        )
        .group_by("association_key")
        .agg([
            pl.col("parent_id").first().alias("parent_entity_id"),
            pl.col("parent_entity_type").first(),
            pl.col("member_id").first().alias("member_entity_id"),
            pl.col("member_entity_type").first(),
            pl.col("source_display").unique().sort().alias("sources"),
            pl.col("member_instance_ids").first(),
        ])
    )
    
    # 9. Build annotations (from member instances)
    # Get annotation labels
    annotation_labels = cv_terms.select([
        pl.col("accession").alias("cv_term_accession"),
        pl.col("label").alias("cv_term_label")
    ])
    
    unit_labels = cv_terms.select([
        pl.col("accession").alias("unit_accession"),
        pl.col("label").alias("unit_label")
    ])
    
    # For each association, collect annotations from member instances
    annot_expanded = (
        assoc_with_sources
        .select(["association_key", "member_instance_ids"])
        .explode("member_instance_ids")
        .filter(pl.col("member_instance_ids").is_not_null())
        .join(
            ent_annot.select([
                pl.col("instance_id"),
                pl.col("cv_term_accession"),
                pl.col("value"),
                pl.col("unit_accession") if "unit_accession" in ent_annot.columns else pl.lit(None, pl.Utf8).alias("unit_accession"),
            ]),
            left_on="member_instance_ids",
            right_on="instance_id",
            how="inner"
        )
    )
    
    if not annot_expanded.is_empty():
        annotations_agg = (
            annot_expanded
            .join(annotation_labels, on="cv_term_accession", how="left")
            .join(unit_labels, on="unit_accession", how="left")
            .with_columns([
                pl.coalesce([
                    pl.col("cv_term_label"),
                    pl.col("cv_term_accession")
                ]).alias("key"),
                pl.col("value").cast(pl.Utf8),
                pl.coalesce([
                    pl.col("unit_label"),
                    pl.col("unit_accession")
                ]).alias("unit"),
            ])
            .group_by("association_key")
            .agg(
                pl.struct(["key", "value", "unit"]).alias("annotations")
            )
        )
        
        result = (
            assoc_with_sources
            .join(annotations_agg, on="association_key", how="left")
        )
    else:
        result = assoc_with_sources.with_columns(
            pl.lit(None, dtype=ANNOT_LIST_DTYPE).alias("annotations")
        )
    
    # 10. Add flattened annotation terms for Meilisearch filtering
    # Similar to interaction_annotation_terms in interactions index
    logger.info("Computing flattened annotation terms for Meilisearch filtering")
    
    # Flatten annotation terms from the annotations list
    # We want just the keys (term labels) for filtering, excluding those with values
    if "annotations" in result.columns:
        temp_terms = (
            result.select(["association_key", "annotations"])
            .filter(pl.col("annotations").is_not_null())
            .filter(pl.col("annotations").list.len() > 0)
            .explode("annotations")
            .select([
                "association_key",
                pl.col("annotations").struct.field("key").alias("term"),
                pl.col("annotations").struct.field("value").alias("value"),
            ])
            # Only include terms that don't have associated values (standalone terms)
            .filter(
                pl.col("value").is_null() | (pl.col("value") == "")
            )
            .group_by("association_key")
            .agg(pl.col("term").unique().alias("association_annotation_terms"))
        )
        
        result = result.join(temp_terms, on="association_key", how="left")
    
    # 11. Final cleanup and output
    result = (
        result
        .with_columns([
            pl.col("sources").fill_null(pl.lit([], dtype=pl.List(pl.Utf8))),
            pl.col("annotations").fill_null(pl.lit([], dtype=ANNOT_LIST_DTYPE)),
            pl.col("parent_entity_type").fill_null(pl.lit("")),
            pl.col("member_entity_type").fill_null(pl.lit("")),
            pl.col("association_annotation_terms").fill_null(pl.lit([], dtype=pl.List(pl.Utf8))) if "association_annotation_terms" in result.columns else pl.lit([], dtype=pl.List(pl.Utf8)).alias("association_annotation_terms"),
        ])
        .select([
            "association_key",
            "parent_entity_id",
            "parent_entity_type",
            "member_entity_id",
            "member_entity_type",
            "sources",
            "annotations",
            "association_annotation_terms",
        ])
        .sort("association_key")
        .with_row_index("association_id", offset=1)
        .with_columns(pl.col("association_id").cast(pl.Int64))
        .select([
            "association_id",
            "association_key",
            "parent_entity_id",
            "parent_entity_type",
            "member_entity_id",
            "member_entity_type",
            "sources",
            "annotations",
            "association_annotation_terms",
        ])
    )
    
    output_path.parent.mkdir(parents=True, exist_ok=True)
    result.write_parquet(output_path)
    logger.info(f"Wrote {len(result)} association documents to {output_path}")
    return output_path


def main():
    parser = argparse.ArgumentParser(description="Build Meilisearch association documents")
    parser.add_argument("--global-tables-dir", type=Path, default=Path("omnipath_build/data/gold"))
    parser.add_argument("--output", type=Path, default=Path("omnipath_build/data/gold/search_associations.parquet"))
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    build_search_associations(args.global_tables_dir, args.output)


if __name__ == "__main__":
    main()
