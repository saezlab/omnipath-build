#!/usr/bin/env python3
"""
Build cv_namespace and cv_term tables for the gold layer (new schema).

This module consolidates controlled vocabulary terms from the silver layer and
from OmniPath-specific enumerations. It produces two tables:

* cv_namespace: unique namespaces with metadata (id, name, uri, description)
* cv_term: individual terms linked to namespaces (id, namespace_id, accession, ...)

Usage:
    python -m omnipath_build.gold_new.build_cv_terms --data-root ... --output-dir ...
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from glob import glob
from pathlib import Path
from typing import Iterable, Sequence

import polars as pl

from omnipath_build.utils import cv_term_enums

__all__ = [
    "build_cv_terms",
]

# Namespaces we know how to describe. Others fall back to null metadata.
_NAMESPACE_METADATA = {
    "PSI-MI": {
        "uri": "https://psidev.info/groups/controlled-vocabularies",
        "description": "Proteomics Standards Initiative molecular interactions ontology.",
    },
    "OmniPath": {
        "uri": None,
        "description": "OmniPath internal controlled vocabulary for supplemental terms.",
    },
}

# File patterns that may contain CV term records.
_CV_TERM_PATTERNS = (
    "silver_cv_terms.parquet",
    "*_cv_terms.parquet",
    "*_ontology.parquet",
)


@dataclass(frozen=True)
class _OmniPathTerm:
    """Helper container for OmniPath-specific CV terms."""

    accession: str
    name: str
    description: str | None = None


def _format_enum_name(name: str) -> str:
    """Convert ENUM_NAME to a human readable title."""
    return name.replace("_", " ").title()


def _iter_omnipath_terms() -> Iterable[_OmniPathTerm]:
    """Yield OmniPath-only CV terms derived from enum definitions."""
    enum_classes: Sequence[type[Enum]] = [
        cv_term_enums.EntityTypeCv,
        cv_term_enums.IdentifierNamespaceCv,
        cv_term_enums.BiologicalRoleCv,
        cv_term_enums.ExperimentalRoleCv,
        cv_term_enums.IdentificationMethodCv,
        cv_term_enums.BiologicalEffectCv,
        cv_term_enums.InteractionTypeCv,
        cv_term_enums.DetectionMethodCv,
        cv_term_enums.CausalMechanismCv,
        cv_term_enums.CausalStatementCv,
        cv_term_enums.ComplexExpansionCv,
        cv_term_enums.ReferenceTypeCv,
    ]

    for enum_cls in enum_classes:
        for member in enum_cls:
            accession = str(member.value)
            if accession.startswith("OM:"):
                yield _OmniPathTerm(
                    accession=accession,
                    name=_format_enum_name(member.name),
                    description=f"OmniPath controlled vocabulary term ({enum_cls.__name__}.{member.name}).",
                )


def _find_cv_term_files(data_root: Path) -> list[Path]:
    """Return a sorted list of CV term files under data_root."""
    files: set[Path] = set()
    for pattern in _CV_TERM_PATTERNS:
        for path_str in glob(str(data_root / "*" / pattern), recursive=True):
            files.add(Path(path_str))
    return sorted(files)


def _load_cv_term_file(path: Path) -> pl.DataFrame:
    """Load a CV term file and normalise its columns."""
    df = pl.read_parquet(path)

    # Ensure required columns exist; provide sensible defaults otherwise.
    if "namespace" not in df.columns:
        df = df.with_columns(pl.lit("PSI-MI").alias("namespace"))
    if "term_accession" not in df.columns:
        raise ValueError(f"Expected 'term_accession' column in {path}")
    if "term_name" not in df.columns:
        df = df.with_columns(pl.lit(None, dtype=pl.Utf8).alias("term_name"))
    if "term_definition" not in df.columns:
        df = df.with_columns(pl.lit(None, dtype=pl.Utf8).alias("term_definition"))

    # Optional lifecycle columns.
    if "is_obsolete" in df.columns:
        is_obsolete_expr = pl.col("is_obsolete").cast(pl.Boolean).alias("is_obsolete")
    else:
        is_obsolete_expr = pl.lit(False).alias("is_obsolete")

    if "replaces" in df.columns:
        replaces_expr = pl.col("replaces").cast(pl.Utf8).alias("replaces")
    else:
        replaces_expr = pl.lit(None, dtype=pl.Utf8).alias("replaces")

    if "replaced_by" in df.columns:
        replaced_by_expr = pl.col("replaced_by").cast(pl.Utf8).alias("replaced_by")
    else:
        replaced_by_expr = pl.lit(None, dtype=pl.Utf8).alias("replaced_by")

    df = df.select(
        [
            pl.col("namespace"),
            pl.col("term_accession").alias("accession"),
            pl.col("term_name").alias("name"),
            pl.col("term_definition").alias("description"),
            is_obsolete_expr,
            replaces_expr,
            replaced_by_expr,
        ]
    )

    df = df.with_columns(pl.lit(False).alias("fallback_row"))
    return df


def _build_combined_term_frame(files: Sequence[Path]) -> pl.DataFrame:
    """Combine silver CV term files with OmniPath-specific additions."""
    frames: list[pl.DataFrame] = []
    for path in files:
        try:
            frames.append(_load_cv_term_file(path))
        except Exception as exc:  # pragma: no cover - defensive logging
            print(f"⚠️  Skipping {path}: {exc}")

    if frames:
        combined = pl.concat(frames, how="diagonal_relaxed")
    else:
        combined = pl.DataFrame(
            schema={
                "namespace": pl.Utf8,
                "accession": pl.Utf8,
                "name": pl.Utf8,
                "description": pl.Utf8,
                "is_obsolete": pl.Boolean,
                "replaces": pl.Utf8,
                "replaced_by": pl.Utf8,
                "fallback_row": pl.Boolean,
            }
        )

    omnipath_records = [
        {
            "namespace": "OmniPath",
            "accession": term.accession,
            "name": term.name,
            "description": term.description,
            "is_obsolete": False,
            "replaces": None,
            "replaced_by": None,
            "fallback_row": True,
        }
        for term in _iter_omnipath_terms()
    ]

    if omnipath_records:
        combined = pl.concat(
            [
                combined,
                pl.DataFrame(omnipath_records),
            ],
            how="diagonal_relaxed",
        )

    # Deduplicate by accession, preferring non-fallback rows (i.e., source data)
    combined = combined.sort(["accession", "fallback_row"])
    aggregated = (
        combined.group_by("accession")
        .agg(
            [
                pl.col("namespace").drop_nulls().first().alias("namespace"),
                pl.col("name").drop_nulls().first().alias("name"),
                pl.col("description").drop_nulls().first().alias("description"),
                pl.col("is_obsolete").max().alias("is_obsolete"),
                pl.col("replaces").drop_nulls().first().alias("replaces"),
                pl.col("replaced_by").drop_nulls().first().alias("replaced_by"),
            ]
        )
        .with_columns(pl.col("name").fill_null(pl.col("accession")))
    )

    return aggregated.sort("accession")


def _build_namespace_table(cv_terms: pl.DataFrame) -> pl.DataFrame:
    """Create the cv_namespace table from aggregated term records."""
    namespaces = cv_terms.select(pl.col("namespace").alias("name")).unique().sort("name")

    metadata_records = [
        {
            "name": name,
            "uri": meta.get("uri"),
            "description": meta.get("description"),
        }
        for name, meta in _NAMESPACE_METADATA.items()
    ]
    metadata_df = (
        pl.DataFrame(metadata_records)
        if metadata_records
        else pl.DataFrame(schema={"name": pl.Utf8, "uri": pl.Utf8, "description": pl.Utf8})
    )

    namespace_df = namespaces.join(metadata_df, on="name", how="left")
    namespace_df = namespace_df.with_row_index("id", offset=1)
    return namespace_df.select(["id", "name", "uri", "description"])


def _attach_namespace_ids(
    cv_terms: pl.DataFrame, namespaces: pl.DataFrame
) -> pl.DataFrame:
    """Attach namespace_id FK to the cv_term dataframe."""
    return (
        cv_terms.join(
            namespaces.select(
                [
                    pl.col("id").alias("namespace_id"),
                    pl.col("name").alias("namespace_label"),
                ]
            ),
            left_on="namespace",
            right_on="namespace_label",
            how="left",
        )
        .drop("namespace_label", strict=False)
    )


def build_cv_terms(data_root: Path, output_dir: Path | None = None) -> tuple[pl.DataFrame, pl.DataFrame]:
    """
    Build cv_namespace and cv_term tables from silver data.

    Args:
        data_root: Root directory containing silver parquet files.
        output_dir: Optional directory to persist intermediate files.

    Returns:
        Tuple of (cv_namespace_df, cv_term_df).
    """
    data_root = Path(data_root)
    if output_dir is not None:
        output_dir = Path(output_dir)

    term_files = _find_cv_term_files(data_root)
    if term_files:
        print(f"\nStep 1: Found {len(term_files)} CV term file(s).")
    else:
        print("\nStep 1: No CV term files found; relying on OmniPath enumerations only.")

    term_records = _build_combined_term_frame(term_files)
    print(f"  Aggregated {len(term_records):,} unique CV term accession(s).")

    cv_namespace = _build_namespace_table(term_records)
    print(f"  Derived {len(cv_namespace):,} namespace(s).")

    term_with_ns = _attach_namespace_ids(term_records, cv_namespace)
    term_with_ids = term_with_ns.with_row_index("id", offset=1).with_columns(pl.col("id").cast(pl.Int64))

    accession_lookup = term_with_ids.select(
        [
            pl.col("accession").alias("lookup_accession"),
            pl.col("id").alias("lookup_id"),
        ]
    )

    term_with_relations = (
        term_with_ids.join(
            accession_lookup.rename(
                {
                    "lookup_accession": "replaces",
                    "lookup_id": "replaces_id",
                }
            ),
            on="replaces",
            how="left",
        )
        .join(
            accession_lookup.rename(
                {
                    "lookup_accession": "replaced_by",
                    "lookup_id": "replaced_by_id",
                }
            ),
            on="replaced_by",
            how="left",
        )
        .with_columns(
            [
                pl.col("replaces_id").cast(pl.Int64),
                pl.col("replaced_by_id").cast(pl.Int64),
            ]
        )
    )

    cv_term = (
        term_with_relations.select(
            [
                "id",
                "namespace_id",
                pl.col("accession"),
                pl.col("name"),
                pl.col("description"),
                pl.col("is_obsolete"),
                pl.col("replaces_id"),
                pl.col("replaced_by_id"),
            ]
        )
        .sort(["namespace_id", "accession"])
    )

    if output_dir:
        output_dir.mkdir(parents=True, exist_ok=True)
        cv_namespace_path = output_dir / "cv_namespace.parquet"
        cv_namespace.write_parquet(cv_namespace_path)
        cv_term_path = output_dir / "cv_term.parquet"
        cv_term.write_parquet(cv_term_path)
        print(f"  Saved cv_namespace → {cv_namespace_path}")
        print(f"  Saved cv_term → {cv_term_path}")

    return cv_namespace, cv_term


if __name__ == "__main__":  # pragma: no cover - convenience entry point
    import argparse

    parser = argparse.ArgumentParser(description="Build cv_term and cv_namespace tables.")
    parser.add_argument("--data-root", type=Path, required=True, help="Directory with silver parquet files.")
    parser.add_argument("--output-dir", type=Path, required=False, help="Optional output directory for parquet tables.")

    args = parser.parse_args()

    build_cv_terms(args.data_root, args.output_dir)
