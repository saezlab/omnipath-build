"""Build Meilisearch interaction documents aggregated by member pairs.

Updated for new entity_instance schema:
- entity_annotation links to entity_instance, not entity directly
- membership has polymorphic columns (parent_entity_id/parent_instance_id, member_entity_id/member_instance_id)
- evidence payload uses list annotations: {term, value, unit}
"""
from __future__ import annotations

import argparse
import logging
from pathlib import Path

import polars as pl
from polars import Field

from .schema import (
    EntityTypeCv,
    POSITIVE_SIGN_ACCESSIONS,
    NEGATIVE_SIGN_ACCESSIONS,
    SOURCE_ROLE_ACCESSIONS,
    TARGET_ROLE_ACCESSIONS,
    ACTIVATORY_PARAMETER_ACCESSIONS,
    INHIBITORY_PARAMETER_ACCESSIONS,
    CV_TERM_ACCESSION_TYPE,
)

__all__ = ["build_search_interactions"]

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
    Field("interaction_annotations", ANNOT_LIST_DTYPE),
    Field("member_a_annotations", ANNOT_LIST_DTYPE),
    Field("member_b_annotations", ANNOT_LIST_DTYPE),
])
EVIDENCE_LIST_DTYPE = pl.List(EVIDENCE_STRUCT)

DIRECTION_STRUCT = pl.Struct([Field("direction", pl.Utf8), Field("sign", pl.Int8)])
DIRECTION_LIST_DTYPE = pl.List(DIRECTION_STRUCT)


def _build_sign_df(pos_accs: frozenset[str], neg_accs: frozenset[str], name: str = "sign") -> pl.DataFrame:
    accessions = list(pos_accs | neg_accs)
    if not accessions:
        return pl.DataFrame(schema={"accession": pl.Utf8, name: pl.Int8})

    data = []
    for acc in pos_accs:
        data.append({"accession": acc, name: 1})
    for acc in neg_accs:
        data.append({"accession": acc, name: -1})

    return pl.DataFrame(data).with_columns(pl.col(name).cast(pl.Int8))


def _build_causal_traits() -> pl.DataFrame:
    data = []
    all_accs = POSITIVE_SIGN_ACCESSIONS | NEGATIVE_SIGN_ACCESSIONS | SOURCE_ROLE_ACCESSIONS | TARGET_ROLE_ACCESSIONS

    for acc in all_accs:
        data.append({
            "accession": acc,
            "sign": 1 if acc in POSITIVE_SIGN_ACCESSIONS else (-1 if acc in NEGATIVE_SIGN_ACCESSIONS else None),
            "is_source": acc in SOURCE_ROLE_ACCESSIONS,
            "is_target": acc in TARGET_ROLE_ACCESSIONS,
        })

    if not data:
        return pl.DataFrame(schema={"accession": pl.Utf8, "sign": pl.Int8, "is_source": pl.Boolean, "is_target": pl.Boolean})

    return pl.DataFrame(data).with_columns([
        pl.col("sign").cast(pl.Int8),
        pl.col("is_source").cast(pl.Boolean),
        pl.col("is_target").cast(pl.Boolean),
    ])


def _get_param_directions(doc_base: pl.DataFrame, df_ann_int: pl.DataFrame, param_signs: pl.DataFrame) -> pl.DataFrame:
    if param_signs.is_empty():
        return pl.DataFrame(schema={"interaction_id": pl.Utf8, "pair_key": pl.Utf8, "direction": pl.Utf8, "sign": pl.Int8})

    types_df = doc_base.select(
        "pair_key",
        pl.col("member_types").list.get(0).str.split(":").list.first().str.to_lowercase().str.strip_chars().alias("t_a"),
        pl.col("member_types").list.get(1).str.split(":").list.first().str.to_lowercase().str.strip_chars().alias("t_b"),
    )

    is_sm_a = types_df["t_a"].str.contains("small molecule")
    is_sm_b = types_df["t_b"].str.contains("small molecule")
    is_pro_a = types_df["t_a"] == "protein"
    is_pro_b = types_df["t_b"] == "protein"

    dir_map = types_df.with_columns([
        pl.when(is_sm_a & is_pro_b).then(pl.lit("a-b"))
        .when(is_sm_b & is_pro_a).then(pl.lit("b-a"))
        .alias("direction")
    ]).filter(pl.col("direction").is_not_null()).select("pair_key", "direction")

    if dir_map.is_empty():
        return pl.DataFrame(schema={"interaction_id": pl.Utf8, "pair_key": pl.Utf8, "direction": pl.Utf8, "sign": pl.Int8})

    return (
        df_ann_int
        .join(param_signs, left_on="ann_id", right_on="accession", how="inner")
        .join(dir_map, on="pair_key", how="inner")
        .select("interaction_id", "pair_key", "direction", "sign")
    )


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


def build_search_interactions(global_tables_dir: Path, output_path: Path) -> Path:
    logger.info("=" * 80 + "\nBuilding Meilisearch interaction documents\n" + "=" * 80)

    tables = {n: pl.read_parquet(global_tables_dir / f"{n}.parquet") for n in [
        "entity",
        "membership",
        "entity_identifier",
        "entity_identifier_resource",
        "entity_instance",
        "entity_annotation",
    ]}
    logger.info("Loaded tables: %s", " ".join(f"{k}={len(v)}" for k, v in tables.items()))

    if (global_tables_dir / "cv_terms.parquet").exists():
        cv_terms = pl.read_parquet(global_tables_dir / "cv_terms.parquet")
    else:
        logger.warning("cv_terms.parquet not found in %s", global_tables_dir)
        cv_terms = pl.DataFrame(schema={"accession": pl.Utf8, "label": pl.Utf8})

    entity_type_labels = cv_terms.select([
        pl.col("accession").alias("entity_type"),
        pl.col("label").alias("entity_type_label"),
    ])
    annotation_labels = cv_terms.select([
        pl.col("accession").alias("cv_term_accession"),
        pl.col("label").alias("cv_term_label"),
    ])

    ent = _normalize_key_columns(tables["entity"])
    mem_raw = tables["membership"]
    ent_id = _normalize_key_columns(tables["entity_identifier"])
    ent_id_res = tables["entity_identifier_resource"]
    inst = _normalize_key_columns(tables["entity_instance"])
    ent_annot = tables["entity_annotation"]

    mem = _resolve_member_entity_id(mem_raw, inst)
    mem = _resolve_parent_entity_id(mem, inst)

    intr_tid = EntityTypeCv.INTERACTION.value

    mem_intr = mem.join(
        ent.filter(pl.col("entity_type") == intr_tid).select(pl.col("entity_id").alias("p_id")),
        left_on="parent_id",
        right_on="p_id",
    ).rename({"parent_id": "interaction_id"})

    pair_base = (
        mem_intr.group_by("interaction_id")
        .agg(pl.col("member_id").unique().sort().alias("m"))
        .filter(pl.col("m").list.len() == 2)
        .select([
            pl.col("interaction_id"),
            pl.col("m").list.get(0).alias("member_a_id"),
            pl.col("m").list.get(1).alias("member_b_id"),
            (pl.col("m").list.get(0).cast(pl.Utf8) + "-" + pl.col("m").list.get(1).cast(pl.Utf8)).alias("pair_key"),
        ])
    )

    if pair_base.is_empty():
        pl.DataFrame(schema={
            "interaction_id": pl.Int64,
            "interaction_key": pl.Utf8,
            "member_a_id": pl.Utf8,
            "member_b_id": pl.Utf8,
            "member_types": pl.List(pl.Utf8),
            "interaction_type": pl.Utf8,
            "is_directed": pl.Boolean,
            "sign": pl.Int8,
            "evidence": EVIDENCE_LIST_DTYPE,
            "interaction_annotation_terms": pl.List(pl.Utf8),
            "participant_annotation_terms": pl.List(pl.Utf8),
            "sources": pl.List(pl.Utf8),
        }).write_parquet(output_path)
        return output_path

    type_map = (
        ent
        .join(entity_type_labels, on="entity_type", how="left")
        .with_columns(
            pl.coalesce([
                pl.col("entity_type_label"),
                (pl.col("entity_type") + ":" + pl.col("entity_type")),
            ]).alias("fmt")
        )
        .select(pl.col("entity_id"), pl.col("fmt"))
    )

    doc_base = (
        pair_base.group_by("pair_key").agg([pl.col("member_a_id").first(), pl.col("member_b_id").first()])
        .join(type_map, left_on="member_a_id", right_on="entity_id").rename({"fmt": "ta"})
        .join(type_map, left_on="member_b_id", right_on="entity_id").rename({"fmt": "tb"})
        .select(
            "pair_key",
            "member_a_id",
            "member_b_id",
            pl.concat_list("ta", "tb").alias("member_types"),
            pl.concat_list("ta", "tb").list.sort().list.join("|").alias("interaction_type"),
        )
    )

    # sources: direct source_ref provenance
    interaction_sources = (
        ent_id_res.join(ent_id.select(['id', 'entity_id']), left_on='entity_identifier_id', right_on='id')
        .select(
            pl.col('entity_id').alias('interaction_id'),
            'source_ref',
        )
        .group_by('interaction_id')
        .agg(pl.col('source_ref').drop_nulls().unique().sort().alias('sources'))
    )

    pair_sources = (
        pair_base.select(["interaction_id", "pair_key"])
        .join(interaction_sources, on="interaction_id", how="left")
        .explode("sources")
        .group_by("pair_key")
        .agg(pl.col("sources").drop_nulls().unique().sort().alias("sources"))
    )

    # annotations from interaction/member instances
    interaction_instances = inst.select([
        pl.col("entity_id").alias("interaction_id"),
        pl.col("id").alias("interaction_instance_id"),
    ])

    int_ann = (
        pair_base.select(["interaction_id", "pair_key"])
        .join(interaction_instances, on="interaction_id", how="left")
        .join(
            ent_annot.select([
                pl.col("instance_id"),
                pl.col("cv_term_accession").alias("ann_id"),
                pl.col("value").cast(pl.Utf8).alias("annotation_value"),
                pl.col("unit_accession").alias("annotation_unit") if "unit_accession" in ent_annot.columns else pl.lit(None, pl.Utf8).alias("annotation_unit"),
            ]),
            left_on="interaction_instance_id",
            right_on="instance_id",
            how="inner",
        )
        .select([
            "interaction_id",
            "pair_key",
            pl.lit("interaction").alias("cat"),
            "ann_id",
            "annotation_value",
            "annotation_unit",
        ])
    )

    mem_with_instances = (
        mem_intr
        .join(pair_base, on="interaction_id")
        .select(["interaction_id", "pair_key", "member_id", "member_a_id", "member_b_id", "member_instance_id"])
        .with_columns(
            pl.when(pl.col("member_id") == pl.col("member_a_id")).then(pl.lit("member_a"))
            .when(pl.col("member_id") == pl.col("member_b_id")).then(pl.lit("member_b"))
            .alias("cat")
        )
        .filter(pl.col("cat").is_not_null())
        .filter(pl.col("member_instance_id").is_not_null())
    )

    mem_ann = (
        mem_with_instances
        .join(
            ent_annot.select([
                pl.col("instance_id"),
                pl.col("cv_term_accession").alias("ann_id"),
                pl.col("value").cast(pl.Utf8).alias("annotation_value"),
                pl.col("unit_accession").alias("annotation_unit") if "unit_accession" in ent_annot.columns else pl.lit(None, pl.Utf8).alias("annotation_unit"),
            ]),
            left_on="member_instance_id",
            right_on="instance_id",
            how="inner",
        )
        .select(["interaction_id", "pair_key", "cat", "ann_id", "annotation_value", "annotation_unit"])
    )

    all_ann = pl.concat([int_ann, mem_ann]) if not int_ann.is_empty() or not mem_ann.is_empty() else pl.DataFrame(
        schema={
            "interaction_id": pl.Utf8,
            "pair_key": pl.Utf8,
            "cat": pl.Utf8,
            "ann_id": pl.Utf8,
            "annotation_value": pl.Utf8,
            "annotation_unit": pl.Utf8,
        }
    )

    # keep for direction pipeline
    df_ann_int = int_ann.select(["interaction_id", "pair_key", "ann_id", "annotation_value", "annotation_unit"])
    df_ann_mem = mem_ann.select(["interaction_id", "pair_key", "cat", "ann_id", "annotation_value", "annotation_unit"])

    if all_ann.is_empty():
        evidence_by_pair = pair_base.select("pair_key").unique().with_columns(pl.lit([], dtype=EVIDENCE_LIST_DTYPE).alias("evidence"))
        interaction_terms_flat = pair_base.select("pair_key").unique().with_columns(pl.lit([], dtype=pl.List(pl.Utf8)).alias("interaction_annotation_terms"))
        participant_terms_flat = pair_base.select("pair_key").unique().with_columns(pl.lit([], dtype=pl.List(pl.Utf8)).alias("participant_annotation_terms"))
    else:
        ann_rows = (
            all_ann
            .join(annotation_labels, left_on="ann_id", right_on="cv_term_accession", how="left")
            .join(
                annotation_labels.rename({"cv_term_accession": "unit_acc", "cv_term_label": "unit_label"}),
                left_on="annotation_unit",
                right_on="unit_acc",
                how="left",
            )
            .with_columns([
                pl.col("cv_term_label").alias("term"),
                pl.col("ann_id").cast(pl.Utf8).alias("filter_term"),
                pl.col("annotation_value").cast(pl.Utf8).alias("value"),
                pl.when(pl.col("annotation_unit").is_not_null()).then(
                    pl.col("unit_label")
                ).otherwise(None).alias("unit"),
            ])
            .with_columns([
                (
                    (pl.col("value").is_null() | (pl.col("value") == ""))
                    & pl.col("unit").is_null()
                ).alias("is_filterable")
            ])
            .select(["pair_key", "interaction_id", "cat", "term", "filter_term", "value", "unit", "is_filterable"])
        )

        ann_by_cat = (
            ann_rows
            .group_by(["pair_key", "interaction_id", "cat"])
            .agg(pl.struct(["term", "value", "unit"]).unique().alias("annotations"))
            .pivot(on="cat", index=["pair_key", "interaction_id"], values="annotations", aggregate_function="first")
        )

        source_rows = (
            pair_base.select(["pair_key", "interaction_id"]).unique()
            .join(interaction_sources, on="interaction_id", how="left")
            .explode("sources")
            .rename({"sources": "source"})
        )

        evidence_rows = (
            source_rows
            .join(ann_by_cat, on=["pair_key", "interaction_id"], how="left")
            .with_columns([
                pl.coalesce(pl.col("interaction"), pl.lit([], dtype=ANNOT_LIST_DTYPE)).alias("interaction_annotations"),
                pl.coalesce(pl.col("member_a"), pl.lit([], dtype=ANNOT_LIST_DTYPE)).alias("member_a_annotations"),
                pl.coalesce(pl.col("member_b"), pl.lit([], dtype=ANNOT_LIST_DTYPE)).alias("member_b_annotations"),
                pl.coalesce(pl.col("source"), pl.lit("")).alias("source"),
            ])
            .sort(["pair_key", "source", "interaction_id"])
            .with_columns([
                pl.int_range(1, pl.len() + 1).over("pair_key").cast(pl.Int64).alias("evidence_serial"),
            ])
            .select([
                "pair_key",
                "interaction_id",
                pl.struct([
                    "evidence_serial",
                    "source",
                    "interaction_annotations",
                    "member_a_annotations",
                    "member_b_annotations",
                ]).alias("entry"),
            ])
        )

        evidence_by_pair = evidence_rows.group_by("pair_key").agg(pl.col("entry").alias("evidence"))

        interaction_terms_flat = (
            ann_rows
            .filter((pl.col("cat") == "interaction") & pl.col("is_filterable"))
            .group_by("pair_key")
            .agg(pl.col("filter_term").drop_nulls().unique().sort().alias("interaction_annotation_terms"))
        )

        # Participant-level ontology terms are derived from entity annotations
        # of participating entities (not from member instance annotations).
        participant_terms_flat = pair_base.select("pair_key").unique().with_columns(
            pl.lit([], dtype=pl.List(pl.Utf8)).alias("participant_annotation_terms")
        )

    # Participant-level ontology terms from participating entities
    participant_entity_terms = (
        ent_annot
        .filter(pl.col("cv_term_accession") == CV_TERM_ACCESSION_TYPE)
        .filter(pl.col("value").is_not_null() & (pl.col("value").cast(pl.Utf8) != ""))
        .join(
            inst.select([
                pl.col("id").alias("instance_id"),
                pl.col("entity_id").alias("annot_entity_id"),
            ]),
            on="instance_id",
            how="inner",
        )
        .with_columns(pl.col("value").cast(pl.Utf8).alias("term"))
        .group_by("annot_entity_id")
        .agg(pl.col("term").unique().sort().alias("entity_terms"))
    )

    participant_terms_flat = (
        pair_base
        .select([
            "pair_key",
            pl.concat_list("member_a_id", "member_b_id").alias("member_ids"),
        ])
        .explode("member_ids")
        .rename({"member_ids": "member_id"})
        .join(
            participant_entity_terms,
            left_on="member_id",
            right_on="annot_entity_id",
            how="left",
        )
        .explode("entity_terms")
        .group_by("pair_key")
        .agg(pl.col("entity_terms").drop_nulls().unique().sort().alias("participant_annotation_terms"))
    )

    # Split interaction documents by directedness/sign, then order members
    # according to direction (source -> target). Undirected docs keep canonical order.
    causal_traits = _build_causal_traits()
    term_sign_df = _build_sign_df(POSITIVE_SIGN_ACCESSIONS, NEGATIVE_SIGN_ACCESSIONS, name="sign")
    param_sign_df = _build_sign_df(ACTIVATORY_PARAMETER_ACCESSIONS, INHIBITORY_PARAMETER_ACCESSIONS, name="sign")

    df_dirs_mem = pl.DataFrame(schema={"interaction_id": pl.Utf8, "pair_key": pl.Utf8, "direction": pl.Utf8, "sign": pl.Int8})
    if not causal_traits.is_empty() and not df_ann_mem.is_empty():
        df_dirs_mem = (
            df_ann_mem
            .join(causal_traits, left_on="ann_id", right_on="accession", how="inner")
            .with_columns([
                pl.when((pl.col("cat") == "member_a") & pl.col("is_source")).then(pl.lit("a-b"))
                .when((pl.col("cat") == "member_b") & pl.col("is_target")).then(pl.lit("a-b"))
                .when((pl.col("cat") == "member_b") & pl.col("is_source")).then(pl.lit("b-a"))
                .when((pl.col("cat") == "member_a") & pl.col("is_target")).then(pl.lit("b-a"))
                .alias("direction")
            ])
            .filter(pl.col("direction") .is_not_null())
            .select("interaction_id", "pair_key", "direction", pl.col("sign").cast(pl.Int8))
            .unique()
        )

    df_dirs_param = _get_param_directions(doc_base, df_ann_int, param_sign_df).unique()

    interaction_term_signs = pl.DataFrame(schema={"interaction_id": pl.Utf8, "pair_key": pl.Utf8, "sign": pl.Int8})
    if not term_sign_df.is_empty() and not df_ann_int.is_empty():
        interaction_term_signs = (
            df_ann_int
            .join(term_sign_df, left_on="ann_id", right_on="accession", how="inner")
            .select("interaction_id", "pair_key", pl.col("sign").cast(pl.Int8))
            .unique()
        )

    all_direction_rows = pl.concat([df_dirs_mem, df_dirs_param], how="vertical") if (not df_dirs_mem.is_empty() or not df_dirs_param.is_empty()) else pl.DataFrame(
        schema={"interaction_id": pl.Utf8, "pair_key": pl.Utf8, "direction": pl.Utf8, "sign": pl.Int8}
    )

    all_sign_rows = pl.concat([
        all_direction_rows.select("interaction_id", "pair_key", "sign").filter(pl.col("sign").is_not_null()),
        interaction_term_signs,
    ], how="vertical") if (not all_direction_rows.is_empty() or not interaction_term_signs.is_empty()) else pl.DataFrame(
        schema={"interaction_id": pl.Utf8, "pair_key": pl.Utf8, "sign": pl.Int8}
    )

    dirs_by_interaction = (
        all_direction_rows
        .select("interaction_id", "pair_key", "direction")
        .filter(pl.col("direction").is_not_null())
        .unique()
        .group_by(["interaction_id", "pair_key"])
        .agg(pl.col("direction").sort())
    ) if not all_direction_rows.is_empty() else pl.DataFrame(schema={"interaction_id": pl.Utf8, "pair_key": pl.Utf8, "direction": pl.List(pl.Utf8)})

    signs_by_interaction = (
        all_sign_rows
        .unique()
        .group_by(["interaction_id", "pair_key"])
        .agg(pl.col("sign").sort())
    ) if not all_sign_rows.is_empty() else pl.DataFrame(schema={"interaction_id": pl.Utf8, "pair_key": pl.Utf8, "sign": pl.List(pl.Int8)})

    interaction_states = (
        pair_base
        .join(dirs_by_interaction, on=["interaction_id", "pair_key"], how="left")
        .join(signs_by_interaction, on=["interaction_id", "pair_key"], how="left")
    )

    directed_signed = (
        interaction_states
        .filter(pl.col("direction").is_not_null() & pl.col("sign").is_not_null())
        .explode("direction")
        .explode("sign")
        .rename({"direction": "state_direction", "sign": "state_sign"})
    )
    directed_unsigned = (
        interaction_states
        .filter(pl.col("direction").is_not_null() & pl.col("sign").is_null())
        .explode("direction")
        .with_columns(pl.lit(0, dtype=pl.Int8).alias("state_sign"))
        .rename({"direction": "state_direction"})
    )
    undirected_signed = (
        interaction_states
        .filter(pl.col("direction").is_null() & pl.col("sign").is_not_null())
        .explode("sign")
        .with_columns(pl.lit(None, dtype=pl.Utf8).alias("state_direction"))
        .rename({"sign": "state_sign"})
    )
    undirected_unsigned = (
        interaction_states
        .filter(pl.col("direction").is_null() & pl.col("sign").is_null())
        .with_columns([
            pl.lit(None, dtype=pl.Utf8).alias("state_direction"),
            pl.lit(0, dtype=pl.Int8).alias("state_sign"),
        ])
    )

    interaction_state_rows = pl.concat(
        [directed_signed, directed_unsigned, undirected_signed, undirected_unsigned],
        how="diagonal_relaxed",
    ).select([
        "interaction_id",
        "pair_key",
        "member_a_id",
        "member_b_id",
        pl.col("state_direction"),
        pl.col("state_sign").cast(pl.Int8).alias("sign"),
    ]).unique()

    interaction_state_rows = (
        interaction_state_rows
        .with_columns([
            pl.col("state_direction").is_not_null().alias("is_directed"),
            pl.when(pl.col("state_direction") == "b-a").then(pl.col("member_b_id")).otherwise(pl.col("member_a_id")).alias("ordered_member_a_id"),
            pl.when(pl.col("state_direction") == "b-a").then(pl.col("member_a_id")).otherwise(pl.col("member_b_id")).alias("ordered_member_b_id"),
        ])
        .with_columns(
            pl.concat_str([
                pl.col("ordered_member_a_id").cast(pl.Utf8),
                pl.lit("-"),
                pl.col("ordered_member_b_id").cast(pl.Utf8),
                pl.lit("|"),
                pl.when(pl.col("is_directed")).then(pl.lit("d")).otherwise(pl.lit("u")),
                pl.lit("|"),
                pl.col("sign").cast(pl.Utf8),
            ]).alias("interaction_key")
        )
    )

    type_map_raw = (
        ent
        .join(entity_type_labels, on="entity_type", how="left")
        .with_columns(
            pl.coalesce([
                pl.col("entity_type_label"),
                (pl.col("entity_type") + ":" + pl.col("entity_type")),
            ]).alias("fmt")
        )
        .select(pl.col("entity_id"), pl.col("fmt"))
    )

    doc_base = (
        interaction_state_rows
        .join(type_map_raw, left_on="ordered_member_a_id", right_on="entity_id", how="left").rename({"fmt": "ta"})
        .join(type_map_raw, left_on="ordered_member_b_id", right_on="entity_id", how="left").rename({"fmt": "tb"})
        .select([
            pl.col("interaction_id").alias("interaction_entity_id"),
            "pair_key",
            "interaction_key",
            pl.col("ordered_member_a_id").alias("member_a_id"),
            pl.col("ordered_member_b_id").alias("member_b_id"),
            pl.concat_list("ta", "tb").alias("member_types"),
            pl.concat_list("ta", "tb").list.sort().list.join("|").alias("interaction_type"),
            "is_directed",
            "sign",
        ])
        .group_by("interaction_key")
        .agg([
            pl.col("interaction_entity_id").unique().sort().alias("interaction_entity_ids"),
            pl.col("pair_key").first().alias("pair_key"),
            pl.col("member_a_id").first().alias("member_a_id"),
            pl.col("member_b_id").first().alias("member_b_id"),
            pl.col("member_types").first().alias("member_types"),
            pl.col("interaction_type").first().alias("interaction_type"),
            pl.col("is_directed").first().alias("is_directed"),
            pl.col("sign").first().alias("sign"),
        ])
    )

    evidence_by_doc = (
        evidence_rows
        .join(interaction_state_rows.select(["interaction_id", "pair_key", "interaction_key"]), on=["interaction_id", "pair_key"], how="inner")
        .select(["interaction_key", "entry"])
        .group_by("interaction_key")
        .agg(pl.col("entry").alias("evidence"))
    ) if 'evidence_rows' in locals() else pl.DataFrame(schema={"interaction_key": pl.Utf8, "evidence": EVIDENCE_LIST_DTYPE})

    sources_by_doc = (
        evidence_rows
        .join(interaction_state_rows.select(["interaction_id", "pair_key", "interaction_key"]), on=["interaction_id", "pair_key"], how="inner")
        .select(["interaction_key", pl.col("entry").struct.field("source").alias("source")])
        .group_by("interaction_key")
        .agg(pl.col("source").drop_nulls().unique().sort().alias("sources"))
    ) if 'evidence_rows' in locals() else pl.DataFrame(schema={"interaction_key": pl.Utf8, "sources": pl.List(pl.Utf8)})

    interaction_terms_by_doc = (
        ann_rows
        .filter((pl.col("cat") == "interaction") & pl.col("is_filterable"))
        .join(interaction_state_rows.select(["interaction_id", "pair_key", "interaction_key"]), on=["interaction_id", "pair_key"], how="inner")
        .group_by("interaction_key")
        .agg(pl.col("filter_term").drop_nulls().unique().sort().alias("interaction_annotation_terms"))
    ) if 'ann_rows' in locals() else pl.DataFrame(schema={"interaction_key": pl.Utf8, "interaction_annotation_terms": pl.List(pl.Utf8)})

    participant_terms_by_doc = (
        interaction_state_rows
        .select(["interaction_key", "pair_key"])
        .unique()
        .join(participant_terms_flat, on="pair_key", how="left")
    )

    result = (
        doc_base
        .join(evidence_by_doc, on="interaction_key", how="left")
        .join(sources_by_doc, on="interaction_key", how="left")
        .join(interaction_terms_by_doc, on="interaction_key", how="left")
        .join(participant_terms_by_doc, on=["interaction_key", "pair_key"], how="left")
        .with_columns([
            pl.coalesce(pl.col("evidence"), pl.lit([], dtype=EVIDENCE_LIST_DTYPE)).alias("evidence"),
            pl.coalesce(pl.col("sources"), pl.lit([], dtype=pl.List(pl.Utf8))).alias("sources"),
            pl.coalesce(pl.col("interaction_annotation_terms"), pl.lit([], dtype=pl.List(pl.Utf8))).alias("interaction_annotation_terms"),
            pl.coalesce(pl.col("participant_annotation_terms"), pl.lit([], dtype=pl.List(pl.Utf8))).alias("participant_annotation_terms"),
        ])
        .with_columns([
            pl.col("evidence").list.len().cast(pl.Int64).alias("evidence_count"),
        ])
        .drop(["pair_key", "participant_annotation_terms", "interaction_entity_ids"])
        .sort("interaction_key")
        .with_row_index("interaction_id", offset=1)
        .with_columns(pl.col("interaction_id").cast(pl.Int64))
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    result.write_parquet(output_path)
    logger.info("Wrote %s interaction documents to %s", len(result), output_path)
    return output_path


def main():
    p = argparse.ArgumentParser(description="Build Meilisearch interaction documents")
    p.add_argument("--global-tables-dir", type=Path, default=Path("omnipath_build/data/gold"))
    p.add_argument("--output", type=Path, default=Path("omnipath_build/data/gold/search_interactions.parquet"))
    p.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    args = p.parse_args()

    logging.basicConfig(level=getattr(logging, args.log_level), format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    build_search_interactions(args.global_tables_dir, args.output)


if __name__ == "__main__":
    main()
