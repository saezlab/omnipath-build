#!/usr/bin/env python3
"""
Derive aggregate tables from global evidence outputs (refactored).

Goals of this refactor
- Reduce repetition via small helpers (empty_df, make_bridge, join_accessions).
- Centralize enums for direction & sign with readable names while keeping numeric codes.
- Keep transformations primarily in Polars (lazy where useful) while returning eager frames.
- Make empty-table behaviour consistent & typed.
- Keep original behaviours and column names stable for downstreams.

Notable behaviour kept from the original implementation:
- Interaction aggregate has ONE ROW per UNORDERED pair (a_id, b_id).
- Direction summarized in dir_code ∈ {-1,0,1,2}:
    2=bidirectional, 1=forward_only (a->b), -1=reverse_only (b->a), 0=undirected_only.
- Sign summarized in sign_code ∈ {-1,0,1,2}: -1=negative, 0=no_sign, 1=positive, 2=mixed.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import IntEnum
from pathlib import Path
from typing import Iterable, Optional

import polars as pl

logger = logging.getLogger(__name__)

# ---- controlled vocab / constants -------------------------------------------

_POSITIVE_STATEMENTS = {"MI:2235", "MI:2236", "MI:2237", "MI:2238", "MI:2239"}
_NEGATIVE_STATEMENTS = {"MI:2240", "MI:2241", "MI:2242", "MI:2243", "MI:2244"}

_DIRECTED_INTERACTION_TYPES = {"MI:0217"}  # phosphorylation reaction


class DirCode(IntEnum):
    BIDIRECTIONAL = 2
    FORWARD_ONLY = 1
    REVERSE_ONLY = -1
    UNDIRECTED_ONLY = 0


class SignCode(IntEnum):
    NEG = -1
    ZERO = 0
    POS = 1
    MIXED = 2


@dataclass(frozen=True)
class AggregatePaths:
    """Bundle of all expected input/output paths."""

    entity_evidence: Path
    interaction_evidence: Path
    membership_evidence: Path
    entity_identifiers: Path

    entity_aggregate: Path
    interaction_aggregate: Path
    membership_aggregate: Path

    entity_bridge: Path
    interaction_bridge: Path
    membership_bridge: Path


# ---- small helpers -----------------------------------------------------------

def _require_exists(path: Path) -> None:
    if not path.exists():
        raise FileNotFoundError(f"Expected file not found: {path}")


def _empty_df(schema: dict[str, pl.DataType]) -> pl.DataFrame:
    """Create a typed empty DataFrame from a schema mapping."""
    return pl.DataFrame({k: pl.Series(k, [], dtype=v) for k, v in schema.items()})


def _make_bridge_empty(id_col: str, ev_col: str) -> pl.DataFrame:
    return pl.DataFrame(
        {
            id_col: pl.Series(id_col, [], dtype=pl.Int64),
            ev_col: pl.Series(ev_col, [], dtype=pl.Int64),
            "source_id": pl.Series("source_id", [], dtype=pl.Int32),
        }
    )


def _fill_empty_list(expr: pl.Expr, dtype: pl.DataType) -> pl.Expr:
    name = expr.meta.output_name()
    return (
        pl.when(expr.is_null())
        .then(pl.lit([], dtype=dtype))
        .otherwise(expr)
        .alias(name)
    )


def _hash_pair_cols(a: str, b: str, out: str = "hash") -> pl.Expr:
    return pl.struct([a, b]).hash(seed=0).alias(out)


def _join_accessions(
    lazy: pl.LazyFrame,
    cv_lookup_df: pl.DataFrame,
    joins: Iterable[tuple[str, str]],
) -> pl.LazyFrame:
    """Join accession strings for a set of *_id columns.

    joins: iterable of (id_col, acc_col)
    """
    out = lazy
    lkd = cv_lookup_df.lazy()
    for id_col, acc_col in joins:
        out = out.join(
            lkd.rename({"cv_term_id": id_col, "accession": acc_col}),
            on=id_col,
            how="left",
        )
    return out


def _load_cv_lookup(entity_identifiers_file: Path) -> dict[int, str]:
    """Return {cv_term_id: accession} mapping from entity_identifiers (OM:0204)."""
    logger.info("Building CV term lookup from %s", entity_identifiers_file)
    cv_terms = (
        pl.scan_parquet(str(entity_identifiers_file))
        .filter(pl.col("id_type") == "OM:0204")
        .select(
            pl.col("entity_id").alias("cv_term_id"),
            pl.col("id_value").alias("accession"),
        )
        .unique(subset=["cv_term_id"])  # prefer first if duplicates
        .collect()
    )
    lookup = dict(cv_terms.iter_rows())
    logger.info("Loaded %d CV accessions", len(lookup))
    return lookup


# ---- entity aggregation ------------------------------------------------------

def _build_entity_aggregate(
    entity_evidence: pl.DataFrame, membership_evidence: pl.DataFrame
) -> tuple[pl.DataFrame, pl.DataFrame]:
    if entity_evidence.is_empty():
        schema = {
            "entity_id": pl.Int64,
            "entity_type_id": pl.Int64,
            "source_ids": pl.List(pl.Int32),
            "source_count": pl.Int32,
            "entity_evidence_ids": pl.List(pl.Int64),
            "annotation_union": entity_evidence.schema.get(
                "annotations",
                pl.List(pl.Struct({"term": pl.Utf8, "value": pl.Utf8, "units": pl.Utf8})),
            ),
            "member_count": pl.Int32,
            "parent_count": pl.Int32,
        }
        return _empty_df(schema), _make_bridge_empty("entity_id", "entity_evidence_id")

    annotations_dtype = entity_evidence.schema["annotations"]

    logger.info("Aggregating %d entity evidence rows", len(entity_evidence))

    entity_bridge = entity_evidence.select(
        pl.col("entity_id").cast(pl.Int64),
        pl.col("id").cast(pl.Int64).alias("entity_evidence_id"),
        pl.col("source_id").cast(pl.Int32),
    )

    entity_agg = (
        entity_evidence.group_by("entity_id")
        .agg(
            [
                pl.col("entity_type_id").drop_nulls().unique().alias("entity_type_ids"),
                pl.col("source_id").cast(pl.Int32).unique().sort().alias("source_ids"),
                pl.col("id").cast(pl.Int64).sort().alias("entity_evidence_ids"),
            ]
        )
        .with_columns(
            [
                pl.col("source_ids").list.len().cast(pl.Int32).alias("source_count"),
                pl.when(pl.col("entity_type_ids").list.len() == 1)
                .then(pl.col("entity_type_ids").list.get(0, null_on_oob=True))
                .otherwise(pl.lit(None, dtype=pl.Int64))
                .alias("entity_type_id"),
            ]
        )
    )

    if (n_conf := entity_agg.filter(pl.col("entity_type_ids").list.len() > 1).height):
        logger.warning("Found %d entities with conflicting entity_type_id assignments", n_conf)

    annotations = (
        entity_evidence.select(["entity_id", "annotations"])  # keep dtype
        .explode("annotations")
        .drop_nulls()
        .unique()
        .group_by("entity_id")
        .agg(pl.col("annotations"))
    )

    member_counts = (
        membership_evidence.group_by("parent_entity_id")
        .agg(pl.col("entity_id").n_unique().cast(pl.Int32).alias("member_count"))
        .rename({"parent_entity_id": "entity_id"})
        if not membership_evidence.is_empty()
        else pl.DataFrame({"entity_id": [], "member_count": []})
    )

    parent_counts = (
        membership_evidence.group_by("entity_id")
        .agg(pl.col("parent_entity_id").n_unique().cast(pl.Int32).alias("parent_count"))
        if not membership_evidence.is_empty()
        else pl.DataFrame({"entity_id": [], "parent_count": []})
    )

    entity_aggregate = (
        entity_agg.join(annotations, on="entity_id", how="left")
        .join(member_counts, on="entity_id", how="left")
        .join(parent_counts, on="entity_id", how="left")
        .with_columns(
            [
                _fill_empty_list(pl.col("source_ids"), pl.List(pl.Int32)),
                _fill_empty_list(pl.col("entity_evidence_ids"), pl.List(pl.Int64)),
                _fill_empty_list(pl.col("annotations"), annotations_dtype).alias("annotation_union"),
                pl.col("member_count").fill_null(0).cast(pl.Int32),
                pl.col("parent_count").fill_null(0).cast(pl.Int32),
            ]
        )
        .drop("entity_type_ids")
        .select(
            [
                pl.col("entity_id").cast(pl.Int64),
                "entity_type_id",
                "source_ids",
                "source_count",
                "entity_evidence_ids",
                "annotation_union",
                "member_count",
                "parent_count",
            ]
        )
        .sort("entity_id")
    )

    return entity_aggregate, entity_bridge


# ---- interaction aggregation -------------------------------------------------

def _build_interaction_aggregate(
    interaction_evidence: pl.DataFrame, cv_lookup: dict[int, str]
) -> tuple[pl.DataFrame, pl.DataFrame]:
    if interaction_evidence.is_empty():
        schema = {
            "interaction_id": pl.Int64,
            "a_id": pl.Int64,
            "b_id": pl.Int64,
            "dir_code": pl.Int8,
            "sign_code": pl.Int8,
            "interaction_evidence_ids": pl.List(pl.Int64),
            "source_ids": pl.List(pl.Int32),
            "source_count": pl.Int32,
            "evidence_count": pl.Int32,
        }
        return _empty_df(schema), _make_bridge_empty("interaction_id", "interaction_evidence_id")

    logger.info("Aggregating %d interaction evidence rows", len(interaction_evidence))

    cv_lookup_df = pl.DataFrame(
        {"cv_term_id": list(cv_lookup.keys()), "accession": list(cv_lookup.values())},
        schema={"cv_term_id": pl.Int64, "accession": pl.Utf8},
    )

    lazy = interaction_evidence.lazy()

    # Join accessions for interaction type / statement / mechanism
    lazy = _join_accessions(
        lazy,
        cv_lookup_df,
        [
            ("interaction_type_id", "interaction_type_acc"),
            ("causal_statement_id", "causal_statement_acc"),
            ("causal_mechanism_id", "causal_mechanism_acc"),
        ],
    )

    processed = (
        lazy
        .with_columns(
            [
                pl.when(pl.col("causal_statement_acc").is_in(_POSITIVE_STATEMENTS))
                .then(pl.lit(1))
                .when(pl.col("causal_statement_acc").is_in(_NEGATIVE_STATEMENTS))
                .then(pl.lit(-1))
                .otherwise(pl.lit(0))
                .alias("sign_vote"),
                (
                    (pl.col("causal_statement_id").is_not_null())
                    | (pl.col("causal_mechanism_id").is_not_null())
                    | pl.col("interaction_type_acc").is_in(_DIRECTED_INTERACTION_TYPES)
                ).alias("is_directed"),
                pl.min_horizontal("entity_id_a", "entity_id_b").alias("a_id"),
                pl.max_horizontal("entity_id_a", "entity_id_b").alias("b_id"),
            ]
        )
        .with_columns(
            [
                # dir_marker: 1 if evidence supports a->b, -1 if b->a, 0 if undirected
                pl.when(~pl.col("is_directed")).then(pl.lit(0))
                .when((pl.col("entity_id_a") == pl.col("a_id")) & (pl.col("entity_id_b") == pl.col("b_id")))
                .then(pl.lit(1))
                .otherwise(pl.lit(-1))
                .alias("dir_marker"),
                _hash_pair_cols("a_id", "b_id", out="interaction_id"),
            ]
        )
        .select(
            [
                pl.col("interaction_id").reinterpret(signed=True).cast(pl.Int64),
                pl.col("a_id").cast(pl.Int64),
                pl.col("b_id").cast(pl.Int64),
                pl.col("id").cast(pl.Int64).alias("interaction_evidence_id"),
                pl.col("source_id").cast(pl.Int32),
                pl.col("sign_vote"),
                pl.col("dir_marker").cast(pl.Int8),
            ]
        )
        .collect()
    )

    interaction_bridge = processed.select(["interaction_id", "interaction_evidence_id", "source_id"])

    interaction_agg = (
        processed.group_by(["interaction_id", "a_id", "b_id"]).agg(
            [
                pl.col("interaction_evidence_id").sort().alias("interaction_evidence_ids"),
                pl.col("source_id").unique().sort().alias("source_ids"),
                ((pl.col("sign_vote") == 1).cast(pl.Int32).sum()).alias("positive_votes"),
                ((pl.col("sign_vote") == -1).cast(pl.Int32).sum()).alias("negative_votes"),
                ((pl.col("dir_marker") == 1).cast(pl.Int32).sum()).alias("fwd_count"),
                ((pl.col("dir_marker") == -1).cast(pl.Int32).sum()).alias("rev_count"),
                ((pl.col("dir_marker") == 0).cast(pl.Int32).sum()).alias("undir_count"),
            ]
        )
        .with_columns(
            [
                pl.col("interaction_evidence_ids").list.len().cast(pl.Int32).alias("evidence_count"),
                pl.when((pl.col("positive_votes") + pl.col("negative_votes")) == 0)
                .then(pl.lit(SignCode.ZERO))
                .when(pl.col("positive_votes") > pl.col("negative_votes"))
                .then(pl.lit(SignCode.POS))
                .when(pl.col("negative_votes") > pl.col("positive_votes"))
                .then(pl.lit(SignCode.NEG))
                .otherwise(pl.lit(SignCode.MIXED))
                .cast(pl.Int8)
                .alias("sign_code"),
                pl.col("source_ids").list.len().cast(pl.Int32).alias("source_count"),
            ]
        )
        .with_columns(
            [
                pl.when((pl.col("fwd_count") > 0) & (pl.col("rev_count") > 0))
                .then(pl.lit(DirCode.BIDIRECTIONAL))
                .when((pl.col("fwd_count") > 0) & (pl.col("rev_count") == 0))
                .then(pl.lit(DirCode.FORWARD_ONLY))
                .when((pl.col("rev_count") > 0) & (pl.col("fwd_count") == 0))
                .then(pl.lit(DirCode.REVERSE_ONLY))
                .when((pl.col("fwd_count") == 0) & (pl.col("rev_count") == 0) & (pl.col("undir_count") > 0))
                .then(pl.lit(DirCode.UNDIRECTED_ONLY))
                .otherwise(pl.lit(DirCode.UNDIRECTED_ONLY))
                .cast(pl.Int8)
                .alias("dir_code"),
            ]
        )
        .with_columns(
            [
                pl.col("interaction_evidence_ids").cast(pl.List(pl.Int64)),
                pl.col("source_ids").cast(pl.List(pl.Int32)),
            ]
        )
        .drop(["positive_votes", "negative_votes", "fwd_count", "rev_count", "undir_count"])
        .select(
            [
                "interaction_id",
                "a_id",
                "b_id",
                "dir_code",
                "sign_code",
                "interaction_evidence_ids",
                "source_ids",
                "source_count",
                "evidence_count",
            ]
        )
        .sort(["a_id", "b_id"])
    )

    return interaction_agg, interaction_bridge


# ---- membership aggregation --------------------------------------------------

def _build_membership_aggregate(
    membership_evidence: pl.DataFrame,
) -> tuple[pl.DataFrame, pl.DataFrame]:
    if membership_evidence.is_empty():
        schema = {
            "membership_id": pl.Int64,
            "parent_entity_id": pl.Int64,
            "entity_id": pl.Int64,
            "role_ids": pl.List(pl.Int64),
            "source_ids": pl.List(pl.Int32),
            "source_count": pl.Int32,
            "membership_evidence_ids": pl.List(pl.Int64),
            "stoichiometry_values": pl.List(pl.Float64),
        }
        return _empty_df(schema), _make_bridge_empty("membership_id", "membership_evidence_id")

    logger.info("Aggregating %d membership evidence rows", len(membership_evidence))

    processed = (
        membership_evidence.lazy()
        .with_columns(_hash_pair_cols("parent_entity_id", "entity_id", out="membership_id"))
        .select(
            [
                pl.col("membership_id").reinterpret(signed=True).alias("membership_id"),
                pl.col("parent_entity_id").cast(pl.Int64),
                pl.col("entity_id").cast(pl.Int64),
                pl.col("id").cast(pl.Int64).alias("membership_evidence_id"),
                pl.col("source_id").cast(pl.Int32),
                pl.col("role_id").cast(pl.Int64),
                pl.col("stoichiometry"),
            ]
        )
        .collect()
    )

    membership_bridge = processed.select(["membership_id", "membership_evidence_id", "source_id"])

    membership_agg = (
        processed.group_by(["membership_id", "parent_entity_id", "entity_id"]).agg(
            [
                pl.col("membership_evidence_id").sort().alias("membership_evidence_ids"),
                pl.col("source_id").unique().sort().alias("source_ids"),
                pl.col("role_id").drop_nulls().unique().sort().alias("role_ids"),
                pl.col("stoichiometry").drop_nulls().unique().sort().alias("stoichiometry_values"),
            ]
        )
        .with_columns(
            [
                pl.when(pl.col("role_ids").list.len() == 0)
                .then(pl.lit(None, dtype=pl.List(pl.Int64)))
                .otherwise(pl.col("role_ids")).alias("role_ids"),
                pl.when(pl.col("stoichiometry_values").list.len() == 0)
                .then(pl.lit(None, dtype=pl.List(pl.Float64)))
                .otherwise(pl.col("stoichiometry_values")).alias("stoichiometry_values"),
                pl.col("membership_evidence_ids").list.len().cast(pl.Int32).alias("evidence_count"),
                pl.col("source_ids").list.len().cast(pl.Int32).alias("source_count"),
            ]
        )
        .with_columns(
            [
                pl.col("membership_evidence_ids").cast(pl.List(pl.Int64)),
                pl.col("source_ids").cast(pl.List(pl.Int32)),
            ]
        )
        .drop("evidence_count")
        .sort(["parent_entity_id", "entity_id"])
    )

    if (n_conf := membership_agg.filter(pl.col("role_ids").list.len() > 1).height):
        logger.warning("Found %d membership aggregates with conflicting role assignments", n_conf)

    return membership_agg, membership_bridge


# ---- orchestration -----------------------------------------------------------

def _resolve_paths(global_dir: Path, out_dir: Optional[Path]) -> AggregatePaths:
    effective_out = out_dir or global_dir
    effective_out.mkdir(parents=True, exist_ok=True)

    entity_evidence = global_dir / "entity_evidence.parquet"
    interaction_evidence = global_dir / "interaction_evidence.parquet"
    membership_evidence = global_dir / "membership_evidence.parquet"
    entity_identifiers = global_dir / "entity_identifiers.parquet"

    for required in (entity_evidence, interaction_evidence, membership_evidence, entity_identifiers):
        _require_exists(required)

    return AggregatePaths(
        entity_evidence=entity_evidence,
        interaction_evidence=interaction_evidence,
        membership_evidence=membership_evidence,
        entity_identifiers=entity_identifiers,
        entity_aggregate=effective_out / "entity_aggregate.parquet",
        interaction_aggregate=effective_out / "interaction_aggregate.parquet",
        membership_aggregate=effective_out / "membership_aggregate.parquet",
        entity_bridge=effective_out / "entity_to_evidence.parquet",
        interaction_bridge=effective_out / "interaction_to_evidence.parquet",
        membership_bridge=effective_out / "membership_to_evidence.parquet",
    )


def _read_and_clean(
    path: Path,
    columns: list[str],
    required_non_null: list[str],
) -> pl.DataFrame:
    df = pl.read_parquet(path, columns=columns)
    if required_non_null:
        mask = None
        for c in required_non_null:
            cond = pl.col(c).is_not_null()
            mask = cond if mask is None else (mask & cond)
        missing = df.filter(~mask).height
        if missing:
            logger.warning("Dropping %d rows from %s due to nulls in %s", missing, path.name, required_non_null)
        df = df.filter(mask)
    return df


def build_aggregate_tables(global_dir: Path, out_dir: Optional[Path] = None) -> dict[str, pl.DataFrame]:
    """Build aggregate tables for entities, interactions, and memberships."""
    global_dir = Path(global_dir)
    out_dir = Path(out_dir) if out_dir else None
    paths = _resolve_paths(global_dir, out_dir)
    logger.info("Loading evidence tables from %s", paths.entity_evidence.parent)

    entity_evidence = _read_and_clean(
        paths.entity_evidence,
        columns=["id", "entity_id", "entity_type_id", "source_id", "annotations"],
        required_non_null=["entity_id"],
    )
    interaction_evidence = _read_and_clean(
        paths.interaction_evidence,
        columns=[
            "id",
            "entity_id_a",
            "entity_id_b",
            "source_id",
            "interaction_type_id",
            "causal_mechanism_id",
            "causal_statement_id",
        ],
        required_non_null=["entity_id_a", "entity_id_b"],
    )
    membership_evidence = _read_and_clean(
        paths.membership_evidence,
        columns=["id", "parent_entity_id", "entity_id", "role_id", "source_id", "stoichiometry"],
        required_non_null=["parent_entity_id", "entity_id"],
    )

    cv_lookup = _load_cv_lookup(paths.entity_identifiers)

    entity_aggregate, entity_bridge = _build_entity_aggregate(entity_evidence, membership_evidence)
    interaction_aggregate, interaction_bridge = _build_interaction_aggregate(interaction_evidence, cv_lookup)
    membership_aggregate, membership_bridge = _build_membership_aggregate(membership_evidence)

    logger.info("Writing aggregates to %s", paths.entity_aggregate.parent)
    for df, path in [
        (entity_aggregate, paths.entity_aggregate),
        (interaction_aggregate, paths.interaction_aggregate),
        (membership_aggregate, paths.membership_aggregate),
        (entity_bridge, paths.entity_bridge),
        (interaction_bridge, paths.interaction_bridge),
        (membership_bridge, paths.membership_bridge),
    ]:
        df.write_parquet(path)

    return {
        "entity_aggregate": entity_aggregate,
        "interaction_aggregate": interaction_aggregate,
        "membership_aggregate": membership_aggregate,
        "entity_to_evidence": entity_bridge,
        "interaction_to_evidence": interaction_bridge,
        "membership_to_evidence": membership_bridge,
    }


if __name__ == "__main__":
    import typer

    logging.basicConfig(level=logging.INFO)
    typer.run(build_aggregate_tables)
