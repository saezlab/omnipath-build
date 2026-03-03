"""Build Meilisearch associations documents from global tables.

Association docs are parent-member pairs with evidence split:
- evidence: [{ evidence_serial, source, annotations }]
- sources: top-level union of evidence sources
- association_annotation_terms: root-level flattened filterable terms
"""
from __future__ import annotations

import argparse
import logging
from pathlib import Path

import polars as pl
from polars import Field

from .schema import INTERACTION_TYPE_ACCESSION

__all__ = ["build_search_associations"]

logger = logging.getLogger(__name__)

ANNOT_STRUCT = pl.Struct([
    Field("term", pl.Utf8),
    Field("value", pl.Utf8),
    Field("unit", pl.Utf8),
])
ANNOT_LIST_DTYPE = pl.List(ANNOT_STRUCT)

EVIDENCE_STRUCT = pl.Struct([
    Field("evidence_serial", pl.Int64),
    Field("source", pl.Utf8),
    Field("annotations", ANNOT_LIST_DTYPE),
])
EVIDENCE_LIST_DTYPE = pl.List(EVIDENCE_STRUCT)


def _resolve_member_entity_id(mem: pl.DataFrame, inst: pl.DataFrame) -> pl.DataFrame:
    return (
        mem
        .join(
            inst.select([
                pl.col("id").alias("member_instance_id"),
                pl.col("entity_id").alias("instance_entity_id"),
            ]),
            on="member_instance_id",
            how="left",
        )
        .with_columns(
            pl.coalesce([pl.col("instance_entity_id"), pl.col("member_entity_id")]).alias("member_id")
        )
    )


def _resolve_parent_entity_id(mem: pl.DataFrame, inst: pl.DataFrame) -> pl.DataFrame:
    return (
        mem
        .join(
            inst.select([
                pl.col("id").alias("parent_instance_id"),
                pl.col("entity_id").alias("parent_instance_entity_id"),
            ]),
            on="parent_instance_id",
            how="left",
        )
        .with_columns(
            pl.coalesce([pl.col("parent_instance_entity_id"), pl.col("parent_entity_id")]).alias("parent_id")
        )
    )


def _normalize_key_columns(df: pl.DataFrame) -> pl.DataFrame:
    """Normalize key-based global tables to legacy column names expected by builders."""
    if 'entity_key' in df.columns and 'entity_id' not in df.columns:
        return df.rename({'entity_key': 'entity_id'})
    return df


def build_search_associations(global_tables_dir: Path, output_path: Path) -> Path:
    logger.info("=" * 80 + "\nBuilding Meilisearch association documents\n" + "=" * 80)

    tables = {n: pl.read_parquet(global_tables_dir / f"{n}.parquet") for n in [
        "entity",
        "membership",
        "entity_identifier",
        "entity_instance",
        "entity_annotation",
    ]}
    logger.info("Loaded tables: %s", " ".join(f"{k}={len(v)}" for k, v in tables.items()))

    if (global_tables_dir / "cv_terms.parquet").exists():
        cv_terms = pl.read_parquet(global_tables_dir / "cv_terms.parquet")
    else:
        logger.warning("cv_terms.parquet not found in %s", global_tables_dir)
        cv_terms = pl.DataFrame(schema={"accession": pl.Utf8, "label": pl.Utf8})

    ent = _normalize_key_columns(tables["entity"])
    mem_raw = tables["membership"]
    ent_id = _normalize_key_columns(tables["entity_identifier"])
    inst = _normalize_key_columns(tables["entity_instance"])
    ent_annot = tables["entity_annotation"]

    mem = _resolve_member_entity_id(mem_raw, inst)
    mem = _resolve_parent_entity_id(mem, inst)

    interaction_ids = set(ent.filter(pl.col("entity_type") == INTERACTION_TYPE_ACCESSION)["entity_id"].to_list())
    mem_assoc = mem.filter(~pl.col("parent_id").is_in(list(interaction_ids)))

    if mem_assoc.is_empty():
        pl.DataFrame(schema={
            "association_id": pl.Int64,
            "association_key": pl.Utf8,
            "parent_entity_id": pl.Utf8,
            "parent_entity_type": pl.Utf8,
            "member_entity_id": pl.Utf8,
            "member_entity_type": pl.Utf8,
            "sources": pl.List(pl.Utf8),
            "evidence": EVIDENCE_LIST_DTYPE,
            "association_annotation_terms": pl.List(pl.Utf8),
        }).write_parquet(output_path)
        return output_path

    entity_type_labels = cv_terms.select([
        pl.col("accession").alias("entity_type"),
        pl.col("label").alias("entity_type_label"),
    ])
    annotation_labels = cv_terms.select([
        pl.col("accession").alias("cv_term_accession"),
        pl.col("label").alias("cv_term_label"),
    ])

    entity_types = (
        ent
        .join(entity_type_labels, on="entity_type", how="left")
        .with_columns(
            pl.coalesce([
                pl.col("entity_type_label"),
                (pl.col("entity_type") + ":" + pl.col("entity_type")),
            ]).alias("entity_type_formatted")
        )
        .select("entity_id", pl.col("entity_type_formatted").alias("entity_type"))
    )

    assoc_rows = (
        mem_assoc
        .select(["parent_id", "member_id", "source_ref", "member_instance_id"])
        .with_columns([
            (pl.col("parent_id").cast(pl.Utf8) + "_" + pl.col("member_id").cast(pl.Utf8)).alias("association_key"),
            pl.coalesce([pl.col('source_ref'), pl.lit('')]).alias('source'),
        ])
    )

    # Annotation bundles per member instance
    ann_rows = (
        ent_annot
        .select([
            pl.col("instance_id").alias("member_instance_id"),
            pl.col("cv_term_accession").alias("ann_id"),
            pl.col("value").cast(pl.Utf8).alias("value"),
            pl.col("unit_accession").alias("unit_accession") if "unit_accession" in ent_annot.columns else pl.lit(None, pl.Utf8).alias("unit_accession"),
        ])
        .join(annotation_labels, left_on="ann_id", right_on="cv_term_accession", how="left")
        .join(
            annotation_labels.rename({"cv_term_accession": "unit_acc", "cv_term_label": "unit_label"}),
            left_on="unit_accession",
            right_on="unit_acc",
            how="left",
        )
        .with_columns([
            (pl.coalesce([pl.col("cv_term_label"), pl.col("ann_id")]) + ":" + pl.col("ann_id")).alias("term"),
            pl.when(pl.col("unit_accession").is_not_null()).then(
                pl.coalesce([pl.col("unit_label"), pl.col("unit_accession")]) + ":" + pl.col("unit_accession")
            ).otherwise(None).alias("unit"),
            (((pl.col("value").is_null()) | (pl.col("value") == "")) & pl.col("unit_accession").is_null()).alias("is_filterable"),
        ])
        .select(["member_instance_id", "term", "value", "unit", "is_filterable"])
    )

    ann_bundle = (
        ann_rows
        .group_by("member_instance_id")
        .agg([
            pl.struct(["term", "value", "unit"]).unique().alias("annotations"),
            pl.col("term").filter(pl.col("is_filterable")).unique().sort().alias("filterable_terms"),
        ])
    )

    evidence_rows = (
        assoc_rows
        .join(ann_bundle, on="member_instance_id", how="left")
        .with_columns([
            pl.coalesce(pl.col("source"), pl.lit("")).alias("source"),
            pl.coalesce(pl.col("annotations"), pl.lit([], dtype=ANNOT_LIST_DTYPE)).alias("annotations"),
            pl.coalesce(pl.col("filterable_terms"), pl.lit([], dtype=pl.List(pl.Utf8))).alias("filterable_terms"),
        ])
        .with_columns(
            pl.col("annotations")
            .list.eval(
                pl.element().struct.field("term")
                + pl.lit("=")
                + pl.coalesce([pl.element().struct.field("value"), pl.lit("")])
                + pl.lit("|")
                + pl.coalesce([pl.element().struct.field("unit"), pl.lit("")])
            )
            .list.sort()
            .list.join("||")
            .alias("ann_sig")
        )
        .group_by(["association_key", "parent_id", "member_id", "source", "ann_sig"])
        .agg([
            pl.col("annotations").first().alias("annotations"),
            pl.col("filterable_terms").explode().drop_nulls().unique().sort().alias("filterable_terms"),
        ])
        .sort(["association_key", "source", "ann_sig"])
        .with_columns(pl.int_range(1, pl.len() + 1).over("association_key").cast(pl.Int64).alias("evidence_serial"))
        .select([
            "association_key",
            "parent_id",
            "member_id",
            "source",
            "filterable_terms",
            pl.struct(["evidence_serial", "source", "annotations"]).alias("evidence_entry"),
        ])
    )

    assoc_docs = (
        evidence_rows
        .group_by(["association_key", "parent_id", "member_id"])
        .agg([
            pl.col("source").drop_nulls().unique().sort().alias("sources"),
            pl.col("evidence_entry").alias("evidence"),
            pl.col("filterable_terms").explode().drop_nulls().unique().sort().alias("association_annotation_terms"),
        ])
    )

    result = (
        assoc_docs
        .join(entity_types.rename({"entity_id": "parent_id", "entity_type": "parent_entity_type"}), on="parent_id", how="left")
        .join(entity_types.rename({"entity_id": "member_id", "entity_type": "member_entity_type"}), on="member_id", how="left")
        .with_columns([
            pl.col("sources").fill_null(pl.lit([], dtype=pl.List(pl.Utf8))),
            pl.col("evidence").fill_null(pl.lit([], dtype=EVIDENCE_LIST_DTYPE)),
            pl.col("association_annotation_terms").fill_null(pl.lit([], dtype=pl.List(pl.Utf8))),
            pl.col("parent_entity_type").fill_null(pl.lit("")),
            pl.col("member_entity_type").fill_null(pl.lit("")),
        ])
        .select([
            "association_key",
            pl.col("parent_id").alias("parent_entity_id"),
            "parent_entity_type",
            pl.col("member_id").alias("member_entity_id"),
            "member_entity_type",
            "sources",
            "evidence",
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
            "evidence",
            "association_annotation_terms",
        ])
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    result.write_parquet(output_path)
    logger.info("Wrote %s association documents to %s", len(result), output_path)
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
