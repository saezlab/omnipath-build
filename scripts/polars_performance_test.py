#!/usr/bin/env python3
"""Profile common analytics queries against the Omnipath parquet export using Polars."""

from __future__ import annotations

import argparse
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Sequence, TypeVar

import polars as pl
import psycopg2
# Try to load dotenv if available
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


EXCLUDED_FILENAMES = {"entity_identifier_record_to_global.parquet"}
ID_TYPE_ALIASES = {
    "uniprot": "MI:1097",
}

T = TypeVar("T")


@dataclass
class QueryMetricsEntry:
    name: str
    elapsed: float
    rows: int | None


class QueryMetrics:
    """Collect elapsed times and result sizes for individual query executions."""

    def __init__(self) -> None:
        self.entries: list[QueryMetricsEntry] = []

    def record(self, name: str, elapsed: float, rows: int | None = None) -> None:
        self.entries.append(QueryMetricsEntry(name=name, elapsed=elapsed, rows=rows))

    def time(
        self,
        name: str,
        func: Callable[[], T],
        rows_fn: Callable[[T], int] | None = None,
    ) -> tuple[T, float]:
        start = time.perf_counter()
        result = func()
        elapsed = time.perf_counter() - start
        rows = rows_fn(result) if rows_fn else None
        self.record(name, elapsed, rows)
        return result, elapsed

    def print_summary(self) -> None:
        if not self.entries:
            return

        width = max(len(entry.name) for entry in self.entries)
        print("\nQuery timings:")
        for entry in self.entries:
            rows = entry.rows if entry.rows is not None else "-"
            print(f"  {entry.name:<{width}} {entry.elapsed:>8.4f}s rows={rows}")


def table_has_column(
    conn: psycopg2.extensions.connection,
    schema: str,
    table: str,
    column: str,
) -> bool:
    """Return True if schema.table exposes the specified column."""
    query = """
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = %s
          AND table_name = %s
          AND column_name = %s
        LIMIT 1
    """
    with conn.cursor() as cur:
        cur.execute(query, (schema, table, column))
        return cur.fetchone() is not None


def load_tables(root: Path, exclude: set[str]) -> Dict[str, pl.DataFrame]:
    """Read all parquet tables under root into memory and report load timings."""
    if not root.exists():
        raise FileNotFoundError(f"Parquet root {root} does not exist")

    tables: Dict[str, pl.DataFrame] = {}
    total_time = 0.0
    total_bytes = 0
    local_tables_root = root / "local_tables"
    files = sorted(
        path
        for path in root.rglob("*.parquet")
        if path.name not in exclude and not path.is_relative_to(local_tables_root)
    )
    for path in files:
        start = time.perf_counter()
        df = pl.read_parquet(path)
        elapsed = time.perf_counter() - start
        total_time += elapsed
        estimated_bytes = df.estimated_size()
        total_bytes += estimated_bytes
        key = str(path.relative_to(root)).removesuffix(".parquet")
        tables[key] = df
        print(
            f"Loaded {key:<55} {df.height:>10} rows x {df.width:<3} cols "
            f"in {elapsed:6.2f}s (~{estimated_bytes / (1024 ** 2):6.1f} MB)"
        )

    print(
        f"\nLoaded {len(tables)} tables in {total_time:.2f}s "
        f"(~{total_bytes / (1024 ** 3):.2f} GB resident)\n"
    )
    return tables


def resolve_id_type(id_type: str | None) -> str | None:
    if not id_type:
        return None
    return ID_TYPE_ALIASES.get(id_type.lower(), id_type)


def prefix_search(
    entity_ids: pl.DataFrame,
    prefix: str,
    id_type_id: int | None = None,
    limit: int = 10,
) -> tuple[pl.DataFrame, float]:
    """Filter entity identifiers by prefix (optional type constraint) and time the query."""
    if id_type_id is not None:
        expr = (pl.col("id_type_id") == id_type_id) & pl.col("id_value").str.starts_with(prefix)
    else:
        expr = pl.col("id_value").str.starts_with(prefix)

    start = time.perf_counter()
    result = entity_ids.filter(expr).head(limit)
    elapsed = time.perf_counter() - start
    return result, elapsed


def find_entity_ids(
    entity_ids: pl.DataFrame, id_type_id: int, identifier: str
) -> List[int]:
    """Return all entity ids matching the provided identifier."""
    matches = (
        entity_ids.filter(
            (pl.col("id_type_id") == id_type_id) & (pl.col("id_value") == identifier)
        )
        .select("entity_id")
        .unique()
    )
    if matches.is_empty():
        return []
    return matches.get_column("entity_id").to_list()


def interactions_for_entities(
    interactions: pl.DataFrame, target_entity_ids: Sequence[int]
) -> tuple[pl.DataFrame, float]:
    """Filter interactions touching any of the supplied entity ids."""
    if not target_entity_ids:
        return pl.DataFrame(), 0.0

    target_series = pl.Series("entity_id", list(target_entity_ids)).implode()
    start = time.perf_counter()
    result = interactions.filter(
        pl.col("a_id").is_in(target_series) | pl.col("b_id").is_in(target_series)
    )
    elapsed = time.perf_counter() - start
    return result, elapsed


def enrich_interactions_with_identifiers(
    interactions: pl.DataFrame,
    entity_ids: pl.DataFrame,
    id_type_id: int | None,
) -> pl.DataFrame:
    """Attach identifier values for interaction partners."""
    if interactions.is_empty() or id_type_id is None:
        return interactions

    identifier_lookup = (
        entity_ids.filter(pl.col("id_type_id") == id_type_id)
        .select(["entity_id", "id_value"])
        .unique()
    )
    if identifier_lookup.is_empty():
        return interactions

    return (
        interactions.join(
            identifier_lookup.rename({"id_value": "a_identifier"}),
            left_on="a_id",
            right_on="entity_id",
            how="left",
        )
        .join(
            identifier_lookup.rename({"id_value": "b_identifier"}),
            left_on="b_id",
            right_on="entity_id",
            how="left",
        )
    )


def summarize_interaction_evidence(
    interactions: pl.DataFrame,
    interaction_evidence: pl.DataFrame,
    sources: pl.DataFrame,
    limit: int = 10,
) -> pl.DataFrame:
    """Aggregate evidence counts by source for the supplied interactions."""
    if interactions.is_empty():
        return pl.DataFrame()

    exploded = (
        interactions.select(["interaction_id", "interaction_evidence_ids"])
        .explode("interaction_evidence_ids")
        .drop_nulls()
        .rename({"interaction_evidence_ids": "evidence_id"})
    )
    if exploded.is_empty():
        return pl.DataFrame()

    evidence_lookup = interaction_evidence.rename({"id": "evidence_id"})
    joined = exploded.join(evidence_lookup, on="evidence_id", how="inner")
    summary = (
        joined.group_by("source_id")
        .agg(
            pl.len().alias("evidence_records"),
            pl.col("interaction_id").n_unique().alias("unique_interactions"),
        )
        .sort("evidence_records", descending=True)
        .head(limit)
    )

    if summary.is_empty():
        return summary

    source_lookup = sources.rename({"id": "source_id"})
    return summary.join(source_lookup, on="source_id", how="left")


def top_interactions_by_evidence(
    interactions: pl.DataFrame,
    entity_ids: pl.DataFrame,
    id_type_id: int | None,
    limit: int = 10,
) -> pl.DataFrame:
    """Return the interactions with the highest evidence counts."""
    if interactions.is_empty():
        return interactions

    top = interactions.sort("evidence_count", descending=True).head(limit)
    return enrich_interactions_with_identifiers(top, entity_ids, id_type_id)


def membership_summary_for_entities(
    membership: pl.DataFrame,
    target_entity_ids: Sequence[int],
    entity_ids: pl.DataFrame,
    id_type_id: int | None,
    limit: int = 10,
) -> pl.DataFrame:
    """List membership entries where the target entity participates."""
    if not target_entity_ids:
        return pl.DataFrame()

    target_series = pl.Series("entity_id", list(target_entity_ids)).implode()
    filtered = membership.filter(pl.col("entity_id").is_in(target_series))
    if filtered.is_empty():
        return filtered

    if id_type_id is None:
        return filtered.head(limit)

    parent_lookup = (
        entity_ids.filter(pl.col("id_type_id") == id_type_id)
        .select(["entity_id", "id_value"])
        .unique()
        .rename({"entity_id": "parent_entity_id", "id_value": "parent_identifier"})
    )
    if parent_lookup.is_empty():
        return filtered.head(limit)

    return (
        filtered.join(parent_lookup, on="parent_entity_id", how="left")
        .head(limit)
    )


# PostgreSQL query functions
def pg_prefix_search(
    conn: psycopg2.extensions.connection,
    prefix: str,
    id_type_id: int | None = None,
    limit: int = 10,
    schema: str = 'public',
    use_small_identifier_column: bool = False,
) -> tuple[List[tuple], float]:
    """PostgreSQL version of prefix_search.

    Note: Uses a partial index that excludes very long id_values (>1000 chars).
    Most biological identifiers are short, so this covers the vast majority efficiently.
    """
    pattern = f"{prefix}%"
    start = time.perf_counter()
    results: list[tuple] = []
    with conn.cursor() as cur:
        if use_small_identifier_column:
            if id_type_id is not None:
                cur.execute(
                    f"""
                    SELECT entity_id, id_type_id, id_value
                    FROM {schema}.entity_identifiers
                    WHERE id_type_id = %s
                      AND id_value_small LIKE %s
                    LIMIT %s
                    """,
                    (id_type_id, pattern, limit),
                )
            else:
                cur.execute(
                    f"""
                    SELECT entity_id, id_type_id, id_value
                    FROM {schema}.entity_identifiers
                    WHERE id_value_small LIKE %s
                    LIMIT %s
                    """,
                    (pattern, limit),
                )
            results = cur.fetchall()

            remaining = limit - len(results)
            if remaining > 0:
                if id_type_id is not None:
                    cur.execute(
                        f"""
                        SELECT entity_id, id_type_id, id_value
                        FROM {schema}.entity_identifiers
                        WHERE id_type_id = %s
                          AND id_value_small IS NULL
                          AND id_value LIKE %s
                        LIMIT %s
                        """,
                        (id_type_id, pattern, remaining),
                    )
                else:
                    cur.execute(
                        f"""
                        SELECT entity_id, id_type_id, id_value
                        FROM {schema}.entity_identifiers
                        WHERE id_value_small IS NULL
                          AND id_value LIKE %s
                        LIMIT %s
                        """,
                        (pattern, remaining),
                    )
                results.extend(cur.fetchall())
        else:
            if id_type_id is not None:
                cur.execute(
                    f"""
                    SELECT entity_id, id_type_id, id_value
                    FROM {schema}.entity_identifiers
                    WHERE id_type_id = %s AND id_value LIKE %s
                    LIMIT %s
                    """,
                    (id_type_id, pattern, limit),
                )
            else:
                cur.execute(
                    f"""
                    SELECT entity_id, id_type_id, id_value
                    FROM {schema}.entity_identifiers
                    WHERE id_value LIKE %s
                    LIMIT %s
                    """,
                    (pattern, limit),
                )
            results = cur.fetchall()
    elapsed = time.perf_counter() - start
    return results, elapsed


def pg_find_entity_ids(
    conn: psycopg2.extensions.connection,
    id_type_id: int,
    identifier: str,
    schema: str = 'public',
    use_small_identifier_column: bool = False,
) -> List[int]:
    """PostgreSQL version of find_entity_ids.

    Note: Uses a partial index that excludes very long id_values (>1000 chars).
    """
    with conn.cursor() as cur:
        if use_small_identifier_column:
            cur.execute(
                f"""
                SELECT DISTINCT entity_id
                FROM {schema}.entity_identifiers
                WHERE id_type_id = %s
                  AND id_value_small = %s
                """,
                (id_type_id, identifier),
            )
            rows = cur.fetchall()
            if rows:
                return [row[0] for row in rows]

            cur.execute(
                f"""
                SELECT DISTINCT entity_id
                FROM {schema}.entity_identifiers
                WHERE id_type_id = %s
                  AND id_value_small IS NULL
                  AND id_value = %s
                """,
                (id_type_id, identifier),
            )
            return [row[0] for row in cur.fetchall()]

        cur.execute(
            f"""
            SELECT DISTINCT entity_id
            FROM {schema}.entity_identifiers
            WHERE id_type_id = %s AND id_value = %s
            """,
            (id_type_id, identifier),
        )
        return [row[0] for row in cur.fetchall()]


def pg_interactions_for_entities(
    conn: psycopg2.extensions.connection,
    target_entity_ids: Sequence[int],
    schema: str = 'public',
) -> tuple[List[tuple], float]:
    """PostgreSQL version of interactions_for_entities."""
    if not target_entity_ids:
        return [], 0.0

    # Run two indexed passes (a_id and b_id) and avoid a planner-enforced sort
    # by using UNION ALL; cheap Python-side dedupe keeps semantics identical.
    query = f"""
        SELECT interaction_id, a_id, b_id, evidence_count
        FROM {schema}.interaction
        WHERE a_id = ANY(%s)
        UNION ALL
        SELECT interaction_id, a_id, b_id, evidence_count
        FROM {schema}.interaction
        WHERE b_id = ANY(%s)
    """
    start = time.perf_counter()
    with conn.cursor() as cur:
        cur.execute(query, (list(target_entity_ids), list(target_entity_ids)))
        rows = cur.fetchall()
    deduped: dict[int, tuple[int, int, int, int]] = {}
    for row in rows:
        interaction_id = row[0]
        if interaction_id not in deduped:
            deduped[interaction_id] = row
    result = list(deduped.values())
    elapsed = time.perf_counter() - start
    return result, elapsed


def pg_fetch_identifiers(
    conn: psycopg2.extensions.connection,
    schema: str,
    entity_ids: Sequence[int],
    id_type_id: int | None,
    use_small_identifier_column: bool = False,
) -> Dict[int, str]:
    """Return identifier values for the supplied entity ids."""
    if not entity_ids or id_type_id is None:
        return {}

    entity_list = list(dict.fromkeys(entity_ids))  # preserve order, dedupe
    if not entity_list:
        return {}

    if use_small_identifier_column:
        query = f"""
            SELECT DISTINCT ON (entity_id)
                   entity_id,
                   COALESCE(id_value_small, id_value) AS identifier
            FROM {schema}.entity_identifiers
            WHERE id_type_id = %s
              AND entity_id = ANY(%s)
            ORDER BY entity_id, (id_value_small IS NULL), id_value
        """
    else:
        query = f"""
            SELECT DISTINCT ON (entity_id)
                   entity_id,
                   id_value AS identifier
            FROM {schema}.entity_identifiers
            WHERE id_type_id = %s
              AND entity_id = ANY(%s)
            ORDER BY entity_id, id_value
        """

    with conn.cursor() as cur:
        cur.execute(query, (id_type_id, entity_list))
        return {row[0]: row[1] for row in cur.fetchall()}


def pg_top_interactions_by_evidence(
    conn: psycopg2.extensions.connection,
    id_type_id: int | None,
    limit: int = 10,
    schema: str = 'public',
    use_small_identifier_column: bool = False,
) -> tuple[List[tuple], float]:
    """PostgreSQL version of top_interactions_by_evidence."""
    query = f"""
        SELECT interaction_id, a_id, b_id, evidence_count
        FROM {schema}.interaction
        ORDER BY evidence_count DESC
        LIMIT %s
    """
    start = time.perf_counter()
    with conn.cursor() as cur:
        cur.execute(query, (limit,))
        result = cur.fetchall()
    elapsed = time.perf_counter() - start

    lookup = pg_fetch_identifiers(
        conn,
        schema,
        [item for row in result for item in row[1:3]],
        id_type_id,
        use_small_identifier_column=use_small_identifier_column,
    )

    # Enrich with identifiers
    enriched = []
    for interaction_id, a_id, b_id, evidence_count in result:
        enriched.append((
            interaction_id,
            a_id,
            lookup.get(a_id, None),
            b_id,
            lookup.get(b_id, None),
            evidence_count,
        ))
    return enriched, elapsed


def pg_summarize_interaction_evidence(
    conn: psycopg2.extensions.connection,
    interaction_ids: Sequence[int],
    limit: int = 10,
    schema: str = 'public',
) -> tuple[List[tuple], float]:
    """PostgreSQL version of summarize_interaction_evidence."""
    if not interaction_ids:
        return [], 0.0

    query = f"""
        WITH evidence_expanded AS (
            SELECT
                i.interaction_id,
                unnest(i.interaction_evidence_ids) as evidence_id
            FROM {schema}.interaction i
            WHERE i.interaction_id = ANY(%s)
        )
        SELECT
            ie.source_id,
            s.name as source_name,
            COUNT(*) as evidence_records,
            COUNT(DISTINCT ee.interaction_id) as unique_interactions
        FROM evidence_expanded ee
        JOIN {schema}.interaction_evidence ie ON ee.evidence_id = ie.id
        JOIN {schema}.source s ON ie.source_id = s.id
        GROUP BY ie.source_id, s.name
        ORDER BY evidence_records DESC
        LIMIT %s
    """
    start = time.perf_counter()
    with conn.cursor() as cur:
        cur.execute(query, (list(interaction_ids), limit))
        result = cur.fetchall()
    elapsed = time.perf_counter() - start
    return result, elapsed


def pg_membership_summary_for_entities(
    conn: psycopg2.extensions.connection,
    target_entity_ids: Sequence[int],
    id_type_id: int | None,
    limit: int = 10,
    schema: str = 'public',
    use_small_identifier_column: bool = False,
) -> tuple[List[tuple], float]:
    """PostgreSQL version of membership_summary_for_entities."""
    if not target_entity_ids:
        return [], 0.0

    query = f"""
        SELECT
            membership_id,
            entity_id,
            parent_entity_id,
            role_ids
        FROM {schema}.membership
        WHERE entity_id = ANY(%s)
        LIMIT %s
    """
    start = time.perf_counter()
    with conn.cursor() as cur:
        cur.execute(query, (list(target_entity_ids), limit))
        result = cur.fetchall()
    elapsed = time.perf_counter() - start

    parent_ids = [row[2] for row in result if row[2] is not None]
    parent_lookup = pg_fetch_identifiers(
        conn,
        schema,
        parent_ids,
        id_type_id,
        use_small_identifier_column=use_small_identifier_column,
    )

    enriched = []
    for membership_id, entity_id, parent_entity_id, role_ids in result:
        enriched.append((
            membership_id,
            entity_id,
            parent_entity_id,
            parent_lookup.get(parent_entity_id, None),
            role_ids,
        ))
    return enriched, elapsed


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=Path("databases/omnipath/output"),
        help="Directory containing the final parquet tables.",
    )
    parser.add_argument(
        "--prefix",
        default="P00",
        help="Identifier prefix to profile for autocomplete lookups.",
    )
    parser.add_argument(
        "--id-type",
        default="uniprot",
        help="Optional identifier type constraint for prefix search (use empty string to disable).",
    )
    parser.add_argument(
        "--prefix-limit",
        type=int,
        default=10,
        help="Maximum number of prefix matches to display.",
    )
    parser.add_argument(
        "--uniprot",
        default="P00533",
        help="UniProt accession to profile for interaction retrieval.",
    )
    parser.add_argument(
        "--interaction-limit",
        type=int,
        default=10,
        help="Rows to display when showing enriched interactions.",
    )
    parser.add_argument(
        "--top-interactions",
        type=int,
        default=5,
        help="Number of global high-confidence interactions to display.",
    )
    parser.add_argument(
        "--postgres-uri",
        type=str,
        help="PostgreSQL connection string for comparison (e.g., postgresql://user:pass@localhost:5432/dbname)",
    )
    parser.add_argument(
        "--postgres-schema",
        type=str,
        default="public",
        help="PostgreSQL schema name (default: public)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    root = args.root.resolve()
    id_type_code = resolve_id_type(args.id_type)
    uniprot_code = ID_TYPE_ALIASES["uniprot"]

    # Get PostgreSQL URI from args or environment
    postgres_uri = args.postgres_uri or os.getenv('DATABASE_URL')

    # Run Polars benchmarks
    print("=" * 80)
    print("POLARS BENCHMARKS")
    print("=" * 80)
    tables = load_tables(root, EXCLUDED_FILENAMES)
    metrics = QueryMetrics()
    try:
        entity_ids = tables["entity_identifiers"]
        interactions = tables["interaction_aggregate"]
    except KeyError as exc:
        raise KeyError(
            f"Required table {exc.args[0]!r} not found. Ensure {root} contains the final parquet dump."
        ) from exc

    # Get id_type_id for uniprot (we'll need this for PostgreSQL queries)
    # Note: entity_identifiers now uses id_type_id (entity_id of CV term), not id_type string
    # The entity_aggregate table doesn't have identifier info, so we need to look up from entity_identifiers
    # We'll find the id_type_id by sampling entity_identifiers for known UniProt patterns
    uniprot_id_type_id = None
    prefix_id_type_id = None

    # Find id_type_id by looking for a known UniProt identifier (e.g., the one we're searching for)
    # UniProt identifiers typically start with P, Q, O, etc.
    uniprot_sample = entity_ids.filter(pl.col("id_value") == args.uniprot).head(1)
    if not uniprot_sample.is_empty():
        uniprot_id_type_id = uniprot_sample.get_column("id_type_id")[0]
    else:
        # Fallback: find any identifier that looks like UniProt (starts with P, Q, O, etc. and is 6-10 chars)
        uniprot_sample = entity_ids.filter(
            pl.col("id_value").str.starts_with("P") &
            pl.col("id_value").str.len_chars().is_between(6, 10)
        ).head(1)
        if not uniprot_sample.is_empty():
            uniprot_id_type_id = uniprot_sample.get_column("id_type_id")[0]

    # For prefix search, if id_type_code is specified, find its id_type_id
    # Since we don't have a direct lookup, we'll use the same id_type_id as UniProt for now
    if id_type_code and id_type_code == uniprot_code:
        prefix_id_type_id = uniprot_id_type_id

    if uniprot_id_type_id is None:
        print(f"Warning: Could not find id_type_id for UniProt identifiers")

    prefix_result, prefix_time = prefix_search(
        entity_ids, prefix=args.prefix, id_type_id=prefix_id_type_id, limit=args.prefix_limit
    )
    metrics.record("prefix_search", prefix_time, prefix_result.height)
    print(
        f"Prefix search for {args.prefix!r} "
        f"({id_type_code or 'any'} id_type) returned {prefix_result.height} rows in {prefix_time:.4f}s"
    )
    if not prefix_result.is_empty():
        print(prefix_result)

    target_entities, resolution_time = metrics.time(
        "find_entity_ids",
        lambda: find_entity_ids(entity_ids, uniprot_id_type_id, args.uniprot) if uniprot_id_type_id else [],
        rows_fn=len,
    )
    print(
        f"\nResolved UniProt {args.uniprot} to {len(target_entities)} entity ids in {resolution_time:.4f}s"
    )
    interactions_result, interaction_time = interactions_for_entities(interactions, target_entities)
    metrics.record("interactions_for_entities", interaction_time, interactions_result.height)
    print(
        f"\nInteractions for UniProt {args.uniprot} "
        f"(matched {len(target_entities)} entity ids) "
        f"returned {interactions_result.height} rows in {interaction_time:.4f}s"
    )

    if args.top_interactions > 0:
        top_global, top_global_time = metrics.time(
            "top_interactions_by_evidence",
            lambda: top_interactions_by_evidence(
                interactions, entity_ids, uniprot_id_type_id, limit=args.top_interactions
            ),
            rows_fn=lambda df: df.height,
        )
        if not top_global.is_empty():
            print(
                f"\nTop {top_global.height} interactions by evidence "
                f"(computed in {top_global_time:.4f}s):"
            )
            print(top_global)

    if not interactions_result.is_empty():
        detailed, enrich_time = metrics.time(
            "enrich_interactions_with_identifiers",
            lambda: enrich_interactions_with_identifiers(
                interactions_result, entity_ids, uniprot_id_type_id
            ),
            rows_fn=lambda df: df.height,
        )
        print(
            f"\nFirst {min(args.interaction_limit, detailed.height)} enriched interactions "
            f"(computed in {enrich_time:.4f}s):"
        )
        print(detailed.head(args.interaction_limit))

        interaction_evidence = tables.get("interaction_evidence")
        sources = tables.get("source")
        if interaction_evidence is not None and sources is not None:
            evidence_summary, evidence_time = metrics.time(
                "summarize_interaction_evidence",
                lambda: summarize_interaction_evidence(
                    interactions_result, interaction_evidence, sources
                ),
                rows_fn=lambda df: df.height,
            )
            if not evidence_summary.is_empty():
                print(
                    "\nEvidence summary by source "
                    f"(computed in {evidence_time:.4f}s):"
                )
                print(evidence_summary)

        membership = tables.get("membership_aggregate")
        if membership is not None:
            membership_summary, membership_time = metrics.time(
                "membership_summary_for_entities",
                lambda: membership_summary_for_entities(
                    membership, target_entities, entity_ids, uniprot_id_type_id
                ),
                rows_fn=lambda df: df.height,
            )
            if not membership_summary.is_empty():
                print(
                    "\nMembership entries involving target entity "
                    f"(computed in {membership_time:.4f}s):"
                )
                print(membership_summary)
    else:
        print("No interactions found for supplied UniProt; skipping detail/evidence summaries.")

    metrics.print_summary()

    # Run PostgreSQL benchmarks if URI provided
    if postgres_uri and uniprot_id_type_id is not None:
        print("\n" + "=" * 80)
        print("POSTGRESQL BENCHMARKS")
        print("=" * 80)
        from urllib.parse import urlparse
        parsed = urlparse(postgres_uri)

        conn = psycopg2.connect(
            host=parsed.hostname,
            port=parsed.port or 5432,
            user=parsed.username,
            password=parsed.password,
            database=parsed.path.lstrip('/'),
        )

        try:
            pg_metrics = QueryMetrics()
            schema = args.postgres_schema
            supports_small_identifier = table_has_column(
                conn, schema, 'entity_identifiers', 'id_value_small'
            )

            # 1. Prefix search
            pg_prefix_result, pg_prefix_time = pg_prefix_search(
                conn, prefix=args.prefix, id_type_id=uniprot_id_type_id if id_type_code else None,
                limit=args.prefix_limit, schema=schema,
                use_small_identifier_column=supports_small_identifier,
            )
            pg_metrics.record("pg_prefix_search", pg_prefix_time, len(pg_prefix_result))
            print(
                f"PG Prefix search for {args.prefix!r} "
                f"returned {len(pg_prefix_result)} rows in {pg_prefix_time:.4f}s"
            )

            # 2. Find entity IDs
            start = time.perf_counter()
            pg_target_entities = pg_find_entity_ids(
                conn,
                uniprot_id_type_id,
                args.uniprot,
                schema,
                use_small_identifier_column=supports_small_identifier,
            )
            pg_resolution_time = time.perf_counter() - start
            pg_metrics.record("pg_find_entity_ids", pg_resolution_time, len(pg_target_entities))
            print(
                f"\nPG Resolved UniProt {args.uniprot} to {len(pg_target_entities)} entity ids "
                f"in {pg_resolution_time:.4f}s"
            )

            # 3. Interactions for entities
            pg_interactions_result, pg_interaction_time = pg_interactions_for_entities(
                conn, pg_target_entities, schema
            )
            pg_metrics.record("pg_interactions_for_entities", pg_interaction_time, len(pg_interactions_result))
            print(
                f"\nPG Interactions for UniProt {args.uniprot} "
                f"returned {len(pg_interactions_result)} rows in {pg_interaction_time:.4f}s"
            )

            # 4. Top interactions by evidence
            if args.top_interactions > 0:
                pg_top_global, pg_top_global_time = pg_top_interactions_by_evidence(
                    conn,
                    uniprot_id_type_id,
                    limit=args.top_interactions,
                    schema=schema,
                    use_small_identifier_column=supports_small_identifier,
                )
                pg_metrics.record("pg_top_interactions_by_evidence", pg_top_global_time, len(pg_top_global))
                print(
                    f"\nPG Top {len(pg_top_global)} interactions by evidence "
                    f"(computed in {pg_top_global_time:.4f}s)"
                )

            # 5. Evidence summary
            if pg_interactions_result:
                pg_interaction_ids = [row[0] for row in pg_interactions_result]
                pg_evidence_summary, pg_evidence_time = pg_summarize_interaction_evidence(
                    conn, pg_interaction_ids, schema=schema
                )
                pg_metrics.record("pg_summarize_interaction_evidence", pg_evidence_time, len(pg_evidence_summary))
                print(
                    f"\nPG Evidence summary by source "
                    f"(computed in {pg_evidence_time:.4f}s)"
                )

            # 6. Membership summary
            pg_membership_summary, pg_membership_time = pg_membership_summary_for_entities(
                conn,
                pg_target_entities,
                uniprot_id_type_id,
                schema=schema,
                use_small_identifier_column=supports_small_identifier,
            )
            pg_metrics.record("pg_membership_summary_for_entities", pg_membership_time, len(pg_membership_summary))
            print(
                f"\nPG Membership entries involving target entity "
                f"(computed in {pg_membership_time:.4f}s)"
            )

            pg_metrics.print_summary()

            # Print comparison
            print("\n" + "=" * 80)
            print("COMPARISON (Polars vs PostgreSQL)")
            print("=" * 80)
            polars_dict = {e.name: e.elapsed for e in metrics.entries}
            pg_dict = {e.name.replace("pg_", ""): e.elapsed for e in pg_metrics.entries}

            comparison_queries = [
                ("prefix_search", "Prefix search"),
                ("find_entity_ids", "Find entity IDs"),
                ("interactions_for_entities", "Interactions for entities"),
                ("top_interactions_by_evidence", "Top interactions by evidence"),
                ("summarize_interaction_evidence", "Summarize interaction evidence"),
                ("membership_summary_for_entities", "Membership summary"),
            ]

            for key, label in comparison_queries:
                polars_time = polars_dict.get(key, 0)
                pg_time = pg_dict.get(key, 0)
                if polars_time > 0 and pg_time > 0:
                    speedup = polars_time / pg_time
                    faster = "PostgreSQL" if speedup > 1 else "Polars"
                    ratio = speedup if speedup > 1 else 1/speedup
                    print(f"{label:40} Polars: {polars_time:7.4f}s  PG: {pg_time:7.4f}s  ({faster} {ratio:.2f}x faster)")

        finally:
            conn.close()


if __name__ == "__main__":
    main()
