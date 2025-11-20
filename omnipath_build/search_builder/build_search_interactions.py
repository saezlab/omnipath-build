"""Build Meilisearch interaction documents aggregated by member pairs."""
from __future__ import annotations

import argparse
import logging
from pathlib import Path

import polars as pl
from polars import Field

from .schema import EntityTypeCv

__all__ = ["build_search_interactions"]

logger = logging.getLogger(__name__)

MAP_ENTRY_STRUCT = pl.Struct([
    Field("key", pl.Utf8),
    Field("value", pl.Utf8),
])
MAP_LIST_DTYPE = pl.List(MAP_ENTRY_STRUCT)

EVIDENCE_STRUCT = pl.Struct([
    Field("interaction_annotation_terms", MAP_LIST_DTYPE),
    Field("interaction_annotation_values", MAP_LIST_DTYPE),
    Field("interaction_annotation_units", MAP_LIST_DTYPE),
    Field("member_a_annotation_terms", MAP_LIST_DTYPE),
    Field("member_a_annotation_values", MAP_LIST_DTYPE),
    Field("member_a_annotation_units", MAP_LIST_DTYPE),
    Field("member_b_annotation_terms", MAP_LIST_DTYPE),
    Field("member_b_annotation_values", MAP_LIST_DTYPE),
    Field("member_b_annotation_units", MAP_LIST_DTYPE),
])
EVIDENCE_LIST_DTYPE = pl.List(EVIDENCE_STRUCT)

ANNOTATION_FIELD_NAMES = [
    "interaction_annotation_terms",
    "interaction_annotation_values",
    "interaction_annotation_units",
    "member_a_annotation_terms",
    "member_a_annotation_values",
    "member_a_annotation_units",
    "member_b_annotation_terms",
    "member_b_annotation_values",
    "member_b_annotation_units",
]


def _identifier_type_id(entity_identifiers: pl.DataFrame, accession: str) -> int:
    row = entity_identifiers.filter(pl.col("identifier") == accession)
    if row.is_empty():
        raise ValueError(f"Identifier accession {accession!r} not found in entity_identifier table")
    return row["entity_id"][0]


def _empty_map_list() -> pl.Expr:
    return pl.lit([], dtype=MAP_LIST_DTYPE)


def _optional_map_expr(column: str) -> pl.Expr:
    return (
        pl.when(pl.col(column).list.len() > 0)
        .then(pl.col(column))
        .otherwise(None)
        .alias(column)
    )


def _build_annotation_labels(entity_identifiers: pl.DataFrame,
                             annotation_ids: list[int],
                             name_type_id: int,
                             cv_term_type_id: int) -> pl.DataFrame:
    if not annotation_ids:
        return pl.DataFrame({"entity_id": pl.Series([], dtype=pl.Int64), "label": pl.Series([], dtype=pl.Utf8)})

    return (
        entity_identifiers
        .filter(pl.col("entity_id").is_in(annotation_ids))
        .group_by("entity_id")
        .agg([
            pl.col("identifier")
            .filter(pl.col("type_id") == name_type_id)
            .first()
            .alias("name"),
            pl.col("identifier")
            .filter(pl.col("type_id") == cv_term_type_id)
            .first()
            .alias("cv_term"),
        ])
        .with_columns(
            pl.coalesce([pl.col("name"), pl.col("cv_term"), pl.lit("UNKNOWN")]).alias("label")
        )
        .select(["entity_id", "label"])
    )


def _format_term_expr(id_col: str, label_col: str, allow_null: bool = False) -> pl.Expr:
    expr = (
        pl.when(pl.col(id_col).is_null())
        .then(pl.lit(None) if allow_null else pl.lit("UNKNOWN:unknown"))
        .otherwise(
            pl.coalesce([pl.col(label_col), pl.lit("UNKNOWN")]) +
            pl.lit(":") +
            pl.col(id_col).cast(pl.Utf8)
        )
    )
    return expr


def _empty_evidence_list() -> pl.Expr:
    return pl.lit([], dtype=EVIDENCE_LIST_DTYPE)


def _build_member_type_lookup(entities: pl.DataFrame,
                              entity_identifiers: pl.DataFrame,
                              name_type_id: int) -> pl.DataFrame:
    type_names = (
        entity_identifiers
        .filter(pl.col("type_id") == name_type_id)
        .select([
            pl.col("entity_id").alias("entity_type_id"),
            pl.col("identifier").alias("entity_type_name"),
        ])
    )

    return (
        entities
        .select(["entity_id", "entity_type_id"])
        .join(type_names, on="entity_type_id", how="left")
        .with_columns([
            (
                pl.coalesce([pl.col("entity_type_name"), pl.lit("UNKNOWN")]) +
                pl.lit(":") +
                pl.col("entity_type_id").cast(pl.Utf8)
            ).alias("member_type")
        ])
        .select(["entity_id", "member_type"])
    )


def _build_annotation_maps_for(
    df: pl.DataFrame,
    group_cols: list[str],
    prefix: str,
) -> pl.DataFrame | None:
    """Build annotation term/value/unit maps with sequential keys per group."""
    if df.is_empty():
        return None

    key_col = "ann_key"

    df_with_keys = (
        df
        .with_row_index("row_idx")
        .with_columns([
            (
                pl.col("row_idx") -
                pl.col("row_idx").min().over(group_cols) +
                1
            ).cast(pl.Utf8).alias(key_col),
        ])
        .with_columns([
            pl.struct([
                pl.col(key_col).alias("key"),
                pl.col("term_label").alias("value"),
            ]).alias("term_item"),
            pl.when(pl.col("value_str").is_not_null())
            .then(
                pl.struct([
                    pl.col(key_col).alias("key"),
                    pl.col("value_str").alias("value"),
                ])
            )
            .otherwise(None)
            .alias("value_item"),
            pl.when(pl.col("unit_label").is_not_null())
            .then(
                pl.struct([
                    pl.col(key_col).alias("key"),
                    pl.col("unit_label").alias("value"),
                ])
            )
            .otherwise(None)
            .alias("unit_item"),
        ])
    )

    result = (
        df_with_keys
        .group_by(group_cols)
        .agg([
            pl.col("term_item").alias(f"{prefix}_annotation_terms"),
            pl.col("value_item").drop_nulls().alias(f"{prefix}_annotation_values"),
            pl.col("unit_item").drop_nulls().alias(f"{prefix}_annotation_units"),
        ])
        .with_columns([
            pl.col(f"{prefix}_annotation_terms").cast(MAP_LIST_DTYPE),
            pl.col(f"{prefix}_annotation_values").cast(MAP_LIST_DTYPE),
            pl.col(f"{prefix}_annotation_units").cast(MAP_LIST_DTYPE),
        ])
    )

    return result


def build_search_interactions(
    global_tables_dir: Path,
    output_path: Path,
) -> Path:
    """Build Meilisearch interaction documents grouped by unordered member pairs."""
    logger.info("=" * 80)
    logger.info("Building Meilisearch interaction documents")
    logger.info("=" * 80)

    entities = pl.read_parquet(global_tables_dir / "entity.parquet")
    memberships = pl.read_parquet(global_tables_dir / "membership.parquet")
    membership_annotations = pl.read_parquet(global_tables_dir / "membership_annotation.parquet")
    entity_identifiers = pl.read_parquet(global_tables_dir / "entity_identifier.parquet")

    logger.info(
        "Loaded tables: entities=%s memberships=%s membership_annotations=%s identifiers=%s",
        len(entities),
        len(memberships),
        len(membership_annotations),
        len(entity_identifiers),
    )

    name_type_id = _identifier_type_id(entity_identifiers, "OM:0202")
    cv_term_type_id = _identifier_type_id(entity_identifiers, "OM:0201")
    interaction_type_id = _identifier_type_id(entity_identifiers, EntityTypeCv.INTERACTION.value)

    interaction_entities = (
        entities
        .filter(pl.col("entity_type_id") == interaction_type_id)
        .select(pl.col("entity_id").alias("interaction_id"))
    )

    interaction_memberships = (
        memberships
        .join(interaction_entities, left_on="parent_id", right_on="interaction_id", how="inner")
        .rename({"parent_id": "interaction_id"})
    )

    pair_base = (
        interaction_memberships
        .group_by("interaction_id")
        .agg(pl.col("member_id").unique().sort().alias("members"))
        .filter(pl.col("members").list.len() == 2)
        .with_columns([
            pl.col("members").list.get(0).alias("member_a_id"),
            pl.col("members").list.get(1).alias("member_b_id"),
        ])
        .with_columns([
            (
                pl.col("member_a_id").cast(pl.Utf8) +
                pl.lit("-") +
                pl.col("member_b_id").cast(pl.Utf8)
            ).alias("pair_key")
        ])
        .select(["interaction_id", "member_a_id", "member_b_id", "pair_key"])
    )

    if pair_base.is_empty():
        logger.warning("No interactions with two members found; writing empty file.")
        pl.DataFrame({
            "interaction_key": pl.Series([], dtype=pl.Utf8),
            "member_a_id": pl.Series([], dtype=pl.Int64),
            "member_b_id": pl.Series([], dtype=pl.Int64),
            "member_types": pl.Series([], dtype=pl.List(pl.Utf8)),
            "evidence": pl.Series([], dtype=EVIDENCE_LIST_DTYPE),
        }).write_parquet(output_path)
        return output_path

    logger.info("Identified %s interaction entities with two members", len(pair_base))

    member_type_lookup = _build_member_type_lookup(entities, entity_identifiers, name_type_id)

    doc_base = (
        pair_base
        .group_by("pair_key")
        .agg([
            pl.col("member_a_id").first().alias("member_a_id"),
            pl.col("member_b_id").first().alias("member_b_id"),
        ])
        .join(
            member_type_lookup.rename({"entity_id": "member_a_id", "member_type": "member_a_type"}),
            on="member_a_id",
            how="left",
        )
        .join(
            member_type_lookup.rename({"entity_id": "member_b_id", "member_type": "member_b_type"}),
            on="member_b_id",
            how="left",
        )
        .with_columns(
            pl.concat_list([pl.col("member_a_type"), pl.col("member_b_type")]).alias("member_types")
        )
        .select(["pair_key", "member_a_id", "member_b_id", "member_types"])
    )

    pair_memberships = (
        interaction_memberships
        .join(pair_base, on="interaction_id", how="inner")
        .with_columns([
            pl.when(pl.col("member_id") == pl.col("member_a_id"))
            .then(pl.lit("a"))
            .when(pl.col("member_id") == pl.col("member_b_id"))
            .then(pl.lit("b"))
            .otherwise(pl.lit(None))
            .alias("member_label")
        ])
        .filter(pl.col("member_label").is_not_null())
        .select([
            pl.col("id").alias("membership_id"),
            "interaction_id",
            "pair_key",
            "member_label",
        ])
    )

    interaction_annotation_rows = (
        memberships
        .join(
            pair_base.select(["interaction_id", "pair_key"]),
            left_on="member_id",
            right_on="interaction_id",
            how="inner",
        )
        .select([
            pl.col("member_id").alias("interaction_id"),
            "pair_key",
            pl.col("parent_id").alias("annotation_id"),
            pl.col("annotation_value"),
            pl.col("annotation_unit"),
        ])
    )

    member_annotation_rows = pl.DataFrame()
    if len(membership_annotations):
        member_annotation_rows = (
            membership_annotations
            .join(pair_memberships, on="membership_id", how="inner")
            .select([
                "interaction_id",
                "pair_key",
                "member_label",
                "annotation_id",
                "annotation_value",
                pl.col("annotation_unit").alias("annotation_unit"),
            ])
        )

    annotation_series = [
        interaction_annotation_rows.get_column("annotation_id") if not interaction_annotation_rows.is_empty() else pl.Series([], dtype=pl.Int64),
        interaction_annotation_rows.get_column("annotation_unit") if not interaction_annotation_rows.is_empty() else pl.Series([], dtype=pl.Int64),
    ]

    if not member_annotation_rows.is_empty():
        annotation_series.extend([
            member_annotation_rows.get_column("annotation_id"),
            member_annotation_rows.get_column("annotation_unit"),
        ])

    annotation_ids = (
        pl.concat(annotation_series)
        .drop_nulls()
        .unique()
        .to_list()
        if annotation_series
        else []
    )

    annotation_labels = _build_annotation_labels(entity_identifiers, annotation_ids, name_type_id, cv_term_type_id)
    annotation_label_lookup = annotation_labels.rename({"entity_id": "annotation_id", "label": "annotation_label"})
    unit_label_lookup = annotation_labels.rename({"entity_id": "annotation_unit", "label": "unit_label"})

    logger.info("Resolving annotation metadata for %s unique annotation IDs", len(annotation_ids))

    interaction_annots = (
        interaction_annotation_rows
        .join(annotation_label_lookup, on="annotation_id", how="left")
        .join(unit_label_lookup, on="annotation_unit", how="left")
        .with_columns([
            _format_term_expr("annotation_id", "annotation_label").alias("term_label"),
            _format_term_expr("annotation_unit", "unit_label", allow_null=True).alias("unit_label"),
            pl.col("annotation_value").cast(pl.Utf8).alias("value_str"),
        ])
    ) if not interaction_annotation_rows.is_empty() else pl.DataFrame()

    member_annots = (
        member_annotation_rows
        .join(annotation_label_lookup, on="annotation_id", how="left")
        .join(unit_label_lookup, on="annotation_unit", how="left")
        .with_columns([
            _format_term_expr("annotation_id", "annotation_label").alias("term_label"),
            _format_term_expr("annotation_unit", "unit_label", allow_null=True).alias("unit_label"),
            pl.col("annotation_value").cast(pl.Utf8).alias("value_str"),
        ])
    ) if not member_annotation_rows.is_empty() else pl.DataFrame()

    interaction_maps = _build_annotation_maps_for(
        interaction_annots,
        ["interaction_id", "pair_key"],
        "interaction",
    ) if not interaction_annots.is_empty() else None

    member_a_base = member_annots.filter(pl.col("member_label") == "a") if not member_annots.is_empty() else pl.DataFrame()
    member_b_base = member_annots.filter(pl.col("member_label") == "b") if not member_annots.is_empty() else pl.DataFrame()

    member_a_maps = _build_annotation_maps_for(
        member_a_base,
        ["interaction_id", "pair_key"],
        "member_a",
    ) if not member_a_base.is_empty() else None
    member_b_maps = _build_annotation_maps_for(
        member_b_base,
        ["interaction_id", "pair_key"],
        "member_b",
    ) if not member_b_base.is_empty() else None

    evidence_rows = pair_base.select(["pair_key", "interaction_id"])
    for maps in (interaction_maps, member_a_maps, member_b_maps):
        if maps is not None and not maps.is_empty():
            evidence_rows = evidence_rows.join(maps, on=["interaction_id", "pair_key"], how="left")

    fill_exprs = []
    for col in ANNOTATION_FIELD_NAMES:
        if col in evidence_rows.columns:
            fill_exprs.append(pl.coalesce([pl.col(col), _empty_map_list()]).alias(col))
        else:
            fill_exprs.append(_empty_map_list().alias(col))
    evidence_rows = evidence_rows.with_columns(fill_exprs)

    evidence_rows = (
        evidence_rows
        .with_columns([
            (
                (pl.col("interaction_annotation_terms").list.len() > 0) |
                (pl.col("member_a_annotation_terms").list.len() > 0) |
                (pl.col("member_b_annotation_terms").list.len() > 0)
            ).alias("has_annotations")
        ])
        .filter(pl.col("has_annotations"))
        .drop("has_annotations")
    )

    optional_exprs = [_optional_map_expr(col) for col in ANNOTATION_FIELD_NAMES]
    evidence_rows = (
        evidence_rows
        .with_columns([
            pl.struct(optional_exprs).alias("evidence_entry")
        ])
        .select(["pair_key", "evidence_entry"])
    )

    pair_evidence = (
        evidence_rows
        .group_by("pair_key")
        .agg(pl.col("evidence_entry").alias("evidence"))
        .with_columns([
            pl.coalesce([pl.col("evidence"), _empty_evidence_list()]).alias("evidence")
        ])
    )

    total_evidence = 0
    if len(pair_evidence):
        total_evidence = (
            pair_evidence
            .select(pl.col("evidence").list.len().sum().alias("total_evidence"))
            .item()
        )
    logger.info(
        "Aggregated %s evidence entries across %s documents",
        total_evidence,
        len(pair_evidence),
    )

    result = (
        doc_base
        .join(pair_evidence, on="pair_key", how="left")
        .with_columns([
            pl.coalesce([pl.col("evidence"), _empty_evidence_list()]).alias("evidence")
        ])
        .select([
            pl.col("pair_key").alias("interaction_key"),
            "member_a_id",
            "member_b_id",
            "member_types",
            "evidence",
        ])
    )

    result.write_parquet(output_path)
    logger.info("Wrote %s interaction documents to %s", len(result), output_path)
    return output_path


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build Meilisearch interaction documents")
    parser.add_argument(
        "--global-tables-dir",
        type=Path,
        default=Path("databases/omnipath/output"),
        help="Directory containing global parquet tables (default: databases/omnipath/output)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("databases/omnipath/output/search_interactions.parquet"),
        help="Output path for search_interactions parquet (default: databases/omnipath/output/search_interactions.parquet)",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level (default: INFO)",
    )
    return parser.parse_args()


def main():
    args = _parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    build_search_interactions(args.global_tables_dir, args.output)


if __name__ == "__main__":
    main()
