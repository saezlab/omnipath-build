"""Build entity identifiers using graph-based equivalence detection.

This script consumes local entity identifier tables (pre-built by build_local_tables.py)
and performs global entity resolution by:
1. Loading local_entity_identifiers_*.parquet files
2. Building edges from merge-safe identifiers (InChI, InChIKey, Uniprot)
3. Using UnionFind to assign canonical entity_id across all sources
"""
from __future__ import annotations
from pathlib import Path
import logging
import polars as pl
from omnipath_build.utils.cv_term_enums import IdentifierNamespaceCv


# --------------------------------------------------------------------------- #
# Union-Find for tracking connected components
# --------------------------------------------------------------------------- #

class UnionFind:
    """Union-Find data structure for tracking connected components.

    Nodes are identified by (type_id, id_value) tuples where:
    - type_id can be either an int (when CV terms are used) or str (fallback)
    - id_value is always a str
    """

    def __init__(self):
        self.parent: dict[tuple[int | str, str], tuple[int | str, str]] = {}
        self.rank: dict[tuple[int | str, str], int] = {}
        self._num_components = 0

    def find(self, x: tuple[int | str, str]) -> tuple[int | str, str]:
        """Find the root of x with path compression."""
        if x not in self.parent:
            self.parent[x] = x
            self.rank[x] = 0
            self._num_components += 1
            return x

        if self.parent[x] != x:
            self.parent[x] = self.find(self.parent[x])
        return self.parent[x]

    def union(self, x: tuple[int | str, str], y: tuple[int | str, str]) -> bool:
        """Union two elements. Returns True if they were in different components."""
        root_x = self.find(x)
        root_y = self.find(y)

        if root_x == root_y:
            return False

        # Union by rank
        if self.rank[root_x] < self.rank[root_y]:
            self.parent[root_x] = root_y
        elif self.rank[root_x] > self.rank[root_y]:
            self.parent[root_y] = root_x
        else:
            self.parent[root_y] = root_x
            self.rank[root_x] += 1

        self._num_components -= 1
        return True

    @property
    def num_components(self) -> int:
        """Get the current number of connected components."""
        return self._num_components

    @property
    def num_nodes(self) -> int:
        """Get the total number of nodes."""
        return len(self.parent)

__all__ = [
    'MERGE_SAFE_IDENTIFIER_TYPES',
    'build_entity_identifiers',
]

logger = logging.getLogger(__name__)

# Merge-safe identifier types (used for cross-source merging)
MERGE_SAFE_IDENTIFIER_TYPES = frozenset({
    IdentifierNamespaceCv.UNIPROT.value,
    IdentifierNamespaceCv.STANDARD_INCHI.value,
    IdentifierNamespaceCv.STANDARD_INCHI_KEY.value,
})


# --------------------------------------------------------------------------- #
# Helper functions
# --------------------------------------------------------------------------- #

def _load_local_tables(local_tables_dir: Path) -> list[tuple[int, pl.DataFrame]]:
    """Load all local_entity_identifiers_*.parquet files.

    Returns:
        List of (source_id, dataframe) tuples where dataframe has columns:
        [source_id, local_entity_id, identifiers]
    """
    files = sorted(local_tables_dir.glob('local_entity_identifiers_*.parquet'))
    if not files:
        logger.warning(f"No local_entity_identifiers_*.parquet files found in {local_tables_dir}")
        return []

    result = []
    for path in files:
        df = pl.read_parquet(path)
        if len(df) == 0:
            continue
        # Extract source_id from the dataframe (all rows have the same source_id)
        source_id = df['source_id'][0]
        result.append((source_id, df))

    logger.info(f"Loaded {len(result)} local identifier tables from {local_tables_dir}")
    return result


def _prepare_local_entities_for_source(
    source_id: int,
    local_df: pl.DataFrame,
    merge_safe_types: frozenset[str],
) -> tuple[pl.DataFrame, pl.DataFrame] | None:
    """Prepare per-source local entities and edges from pre-built local tables.

    Args:
        source_id: Source ID (integer)
        local_df: DataFrame with columns [source_id, local_entity_id, identifiers]
                  where identifiers is list[struct{type, value}]
        merge_safe_types: Set of identifier types safe for cross-source merging

    Returns a tuple of:
    - local_ms_edges (source_id, local_entity_id, id_type, id_value) - merge-safe identifiers only
    - local_all_edges (source_id, local_entity_id, id_type, id_value) - all identifiers

    or None if the source has no identifiers.
    """
    if len(local_df) == 0:
        return None

    merge_safe_set = set(merge_safe_types)

    # Split identifiers into merge-safe and all
    expanded = (
        local_df
        .with_columns([
            pl.col('identifiers').list.eval(
                pl.when(
                    pl.element().struct.field('type').is_not_null()
                    & pl.element().struct.field('value').is_not_null()
                )
                .then(pl.element())
                .otherwise(None)
            ).list.drop_nulls().alias('all_identifiers'),
            pl.col('identifiers').list.eval(
                pl.when(
                    pl.element().struct.field('type').is_in(list(merge_safe_set))
                    & pl.element().struct.field('value').is_not_null()
                )
                .then(pl.element())
                .otherwise(None)
            ).list.drop_nulls().alias('merge_safe_identifiers'),
        ])
    )

    # Map local_entity_id -> merge-safe identifiers (flattened)
    local_ms_edges = (
        expanded
        .select(['source_id', 'local_entity_id', 'merge_safe_identifiers'])
        .filter(pl.col('merge_safe_identifiers').list.len() > 0)
        .explode('merge_safe_identifiers')
        .select([
            'source_id',
            'local_entity_id',
            pl.col('merge_safe_identifiers').struct.field('type').cast(pl.Utf8).alias('id_type'),
            pl.col('merge_safe_identifiers').struct.field('value').cast(pl.Utf8).alias('id_value'),
        ])
        .filter(pl.col('id_type').is_not_null() & pl.col('id_value').is_not_null())
        .unique()
    )

    # Map local_entity_id -> ALL identifiers
    local_all_edges = (
        expanded
        .select(['source_id', 'local_entity_id', 'all_identifiers'])
        .explode('all_identifiers')
        .select([
            'source_id',
            'local_entity_id',
            pl.col('all_identifiers').struct.field('type').cast(pl.Utf8).alias('id_type'),
            pl.col('all_identifiers').struct.field('value').cast(pl.Utf8).alias('id_value'),
        ])
        .filter(pl.col('id_type').is_not_null() & pl.col('id_value').is_not_null())
        .unique()
    )

    return local_ms_edges, local_all_edges


# --------------------------------------------------------------------------- #
# Step 2: Build edges from identifier co-occurrences
# --------------------------------------------------------------------------- #

def _build_edges_from_identifiers(
    df: pl.DataFrame,
    source_name: str,
    identifiers_col: str = 'identifiers',
    cv_id_type_mapping: pl.DataFrame | None = None,
) -> pl.DataFrame:
    """Build edges from a DataFrame of identifier lists.

    For each record, creates edges between all pairs of identifiers that co-occur.

    Args:
        df: DataFrame with columns [source_name, <identifiers_col>]
        source_name: Name of the source (for logging)
        identifiers_col: Column name containing list[struct{type,value}]
        cv_id_type_mapping: Optional CV term mapping to convert id_type to type_id

    Returns:
        DataFrame with edges [id_a, id_b, source_name]
        id_a and id_b are structs with 'type' (int or str) and 'value' (str)
    """
    if identifiers_col != 'identifiers' and identifiers_col in df.columns:
        work_df = df.rename({identifiers_col: 'identifiers'})
    else:
        work_df = df

    # For each record, create normalized rows per identifier
    edges = (
        work_df
        .with_row_index('record_id')
        .explode('identifiers')
        .select([
            'record_id',
            'source_name',
            pl.col('identifiers').struct.field('type').alias('id_type'),  # Keep original type (int or str)
            pl.col('identifiers').struct.field('value').cast(pl.Utf8).alias('id_value'),
        ])
        .filter(
            pl.col('id_type').is_not_null()
            & pl.col('id_value').is_not_null()
            & (pl.col('id_value').str.len_chars() > 0)
        )
    )

    # Join with CV mapping if provided
    if cv_id_type_mapping is not None:
        edges = (
            edges
            .join(cv_id_type_mapping, on='id_type', how='left')
            .select(['record_id', 'source_name', 'type_id', 'id_value'])
        )
        type_col = 'type_id'
    else:
        type_col = 'id_type'

    # Self-join to create all distinct pairs within each record
    edges_paired = (
        edges
        .join(edges, on=['record_id', 'source_name'], suffix='_b')
        .filter(
            (pl.col(type_col) < pl.col(f'{type_col}_b'))
            | (
                (pl.col(type_col) == pl.col(f'{type_col}_b'))
                & (pl.col('id_value') < pl.col('id_value_b'))
            )
        )
        .select([
            'source_name',
            pl.struct([
                pl.col(type_col).alias('type'),
                pl.col('id_value').alias('value'),
            ]).alias('id_a'),
            pl.struct([
                pl.col(f'{type_col}_b').alias('type'),
                pl.col('id_value_b').alias('value'),
            ]).alias('id_b'),
        ])
        .unique()
    )

    return edges_paired


def _merge_edges_into_graph(
    global_graph: pl.DataFrame | None,
    new_edges: pl.DataFrame,
    union_find: UnionFind,
    nodes_to_register: pl.DataFrame | None = None,
) -> pl.DataFrame:
    """Merge new edges into the global graph and update connected components.

    Args:
        global_graph: Current global graph (or None if first source)
        new_edges: New edges to merge [source_name, id_a, id_b]
        union_find: UnionFind structure for tracking connected components
        nodes_to_register: Optional DataFrame with columns [id_type, id_value] to ensure singletons are registered

    Returns:
        Updated global graph [id_a, id_b, sources]
    """
    # Register singleton nodes (if provided)
    if nodes_to_register is not None and len(nodes_to_register) > 0:
        for t, v in nodes_to_register.iter_rows():
            union_find.find((t, v))

    # Update union-find with new edges
    for row in new_edges.iter_rows(named=True):
        id_a = (row['id_a']['type'], row['id_a']['value'])
        id_b = (row['id_b']['type'], row['id_b']['value'])
        union_find.union(id_a, id_b)

    if global_graph is None:
        # First source: just group the edges
        return (
            new_edges
            .group_by(['id_a', 'id_b'])
            .agg([
                pl.col('source_name').unique().alias('sources')
            ])
        )

    # Combine with existing graph
    # First, expand existing graph back to edge format
    existing_edges = (
        global_graph
        .explode('sources')
        .select([
            pl.col('sources').alias('source_name'),
            'id_a',
            'id_b',
        ])
    )

    # Combine old and new edges
    combined = pl.concat([existing_edges, new_edges], how='diagonal_relaxed')

    # Re-aggregate
    merged = (
        combined
        .group_by(['id_a', 'id_b'])
        .agg([
            pl.col('source_name').unique().alias('sources')
        ])
    )

    return merged


# --------------------------------------------------------------------------- #
# Utilities to convert union-find to mapping
# --------------------------------------------------------------------------- #

def _union_find_to_safe_clusters(uf: UnionFind) -> pl.DataFrame:
    """Convert UnionFind state into a mapping of (type_id,id_value) -> entity_id.

    Returns DataFrame columns: [type_id, id_value, entity_id]
    entity_id is a sequential integer starting at 1.
    type_id can be either int (when CV terms used) or str (fallback).
    """
    # Ensure path compression
    roots: dict[tuple[int | str, str], tuple[int | str, str]] = {}
    for node in list(uf.parent.keys()):
        roots[node] = uf.find(node)

    # Map unique roots to sequential ints
    unique_roots = sorted(set(roots.values()))
    root_to_entity = {root: i + 1 for i, root in enumerate(unique_roots)}

    rows = [
        {
            'type_id': t,
            'id_value': v,
            'entity_id': root_to_entity[roots[(t, v)]],
        }
        for (t, v) in roots.keys()
    ]
    return pl.DataFrame(rows)

# --------------------------------------------------------------------------- #
# Public entry point (full unified table with provenance)
# --------------------------------------------------------------------------- #

def build_entity_identifiers(
    local_tables_dir: Path,
    merge_safe_types: frozenset[str] = MERGE_SAFE_IDENTIFIER_TYPES,
    cv_term_df: pl.DataFrame | None = None,
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Build unified identifier tables with canonical entity IDs from local tables.

    Args:
        local_tables_dir: Directory containing local_entity_identifiers_*.parquet files
        merge_safe_types: Set of identifier types safe for cross-source merging
        cv_term_df: Optional DataFrame with CV terms (columns: 'id', 'accession')
                    If provided, type_id (integer) will be used for identifier types

    Returns a tuple of:
      - record_to_global: (source_id, local_entity_id) -> entity_id
      - final_identifiers: (entity_id, type_id, id_value) with sources provenance
    """
    local_tables_dir = Path(local_tables_dir)
    sources_data = _load_local_tables(local_tables_dir)

    if not sources_data:
        logger.warning("No local tables found, returning empty results")
        return pl.DataFrame(), pl.DataFrame()

    # Prepare CV term mapping if provided
    cv_id_type_mapping: pl.DataFrame | None = None
    if cv_term_df is not None:
        cv_id_type_mapping = cv_term_df.select([
            pl.col('accession').alias('id_type'),
            pl.col('id').alias('type_id')
        ])
        logger.info(f"Using CV term mapping with {len(cv_id_type_mapping)} identifier types")

    union_find = UnionFind()
    all_local_ms_edges: list[pl.DataFrame] = []
    all_local_all_edges: list[pl.DataFrame] = []

    num_sources = len(sources_data)
    sources_processed = 0

    for source_id, local_df in sources_data:
        logger.info("=" * 80)
        logger.info(f"Processing source [{sources_processed + 1}/{num_sources}]: source_id={source_id}")
        logger.info("=" * 80)

        prepared = _prepare_local_entities_for_source(source_id, local_df, merge_safe_types)
        if prepared is None:
            logger.warning(f"  No identifiers found in source {source_id}, skipping")
            sources_processed += 1
            continue
        local_ms_edges, local_all_edges = prepared

        # Join with CV terms to replace id_type with type_id (if mapping provided)
        if cv_id_type_mapping is not None:
            local_ms_edges = (
                local_ms_edges
                .join(cv_id_type_mapping, on='id_type', how='left')
                .select(['source_id', 'local_entity_id', 'type_id', 'id_value'])
            )
            local_all_edges = (
                local_all_edges
                .join(cv_id_type_mapping, on='id_type', how='left')
                .select(['source_id', 'local_entity_id', 'type_id', 'id_value'])
            )

        # Local entity stats
        n_local = local_df.select('local_entity_id').n_unique()
        n_with_ms = len(local_ms_edges.select('local_entity_id').unique())
        n_without_ms = n_local - n_with_ms
        logger.info(f"  Local entities: {n_local:,} (with MS: {n_with_ms:,}, without MS: {n_without_ms:,})")

        # Build edges from merge-safe identifiers
        # Optimization: Group by unique identifier sets to avoid processing duplicates
        type_col = 'type_id' if cv_id_type_mapping is not None else 'id_type'

        # First, create identifier lists per local entity
        ms_edges_with_lists = (
            local_ms_edges
            .group_by(['source_id', 'local_entity_id'])
            .agg([
                pl.struct(
                    type=pl.col(type_col),
                    value=pl.col('id_value')
                ).alias('identifiers')
            ])
        )

        # Group by unique identifier sets and collect all local_entity_ids that share them
        ms_edges_grouped = (
            ms_edges_with_lists
            .group_by('identifiers')
            .agg([
                pl.col('local_entity_id').alias('local_entity_ids'),
                pl.col('source_id').first().alias('source_id')  # All have same source_id
            ])
            .with_columns([
                pl.lit(f"source_{source_id}").alias('source_name')  # Temporary for edge builder
            ])
        )

        n_unique_id_sets = len(ms_edges_grouped)
        n_total_entities = len(ms_edges_with_lists)
        if n_unique_id_sets < n_total_entities:
            logger.info(f"  Deduplicated identifier sets: {n_total_entities:,} entities -> {n_unique_id_sets:,} unique sets")

        # Register all merge-safe nodes
        ms_nodes = local_ms_edges.select([type_col, 'id_value']).unique()
        for t, v in ms_nodes.iter_rows():
            union_find.find((t, v))

        # Build edges within each unique identifier set (connect all merge-safe identifiers that co-occur)
        edges = _build_edges_from_identifiers(
            ms_edges_grouped,
            f"source_{source_id}",
            identifiers_col='identifiers',
            cv_id_type_mapping=None,  # Already mapped above if needed
        )

        components_before = union_find.num_components
        _merge_edges_into_graph(global_graph=None, new_edges=edges, union_find=union_find, nodes_to_register=None)
        components_after = union_find.num_components
        logger.info(f"  Merge-safe nodes: {len(ms_nodes):,}, edges: {len(edges):,}")
        logger.info(f"  Entities after UF: {components_after:,} (merged {components_before - components_after:,})")

        all_local_ms_edges.append(local_ms_edges)
        all_local_all_edges.append(local_all_edges)
        sources_processed += 1

    # Build safe cluster mapping (type_id/id_type, id_value) -> entity_id
    safe_clusters = _union_find_to_safe_clusters(union_find)

    if not all_local_ms_edges or not all_local_all_edges:
        logger.warning("No edges collected, returning empty results")
        return pl.DataFrame(), pl.DataFrame()

    local_ms_edges_all = pl.concat(all_local_ms_edges, how='diagonal_relaxed')
    local_all_edges_all = pl.concat(all_local_all_edges, how='diagonal_relaxed')

    # Map local entities to global entity IDs via merge-safe identifiers
    type_col = 'type_id' if cv_id_type_mapping is not None else 'id_type'
    join_cols = [type_col, 'id_value']
    record_to_global = (
        local_ms_edges_all
        .join(safe_clusters.rename({'type_id': type_col}), on=join_cols, how='inner')
        .group_by(['source_id', 'local_entity_id'])
        .agg([
            pl.col('entity_id').first().alias('entity_id'),
        ])
    )

    # Include local entities with no merge-safe IDs as first-class entities
    all_local_entities = local_all_edges_all.select(['source_id', 'local_entity_id']).unique()
    unresolved = all_local_entities.join(
        record_to_global.select(['source_id', 'local_entity_id']),
        on=['source_id', 'local_entity_id'],
        how='anti'
    )

    # Assign new entity IDs after the max existing entity_id
    max_ent = 0
    if len(safe_clusters) > 0:
        max_val = safe_clusters.select(pl.col('entity_id').max()).item()
        max_ent = int(max_val) if max_val is not None else 0

    if len(unresolved) > 0:
        unresolved = (
            unresolved
            .with_row_index('row_idx')
            .with_columns([
                (pl.col('row_idx') + max_ent + 1).cast(pl.Int64).alias('entity_id'),
            ])
            .drop('row_idx')
        )
        record_to_global = pl.concat([record_to_global, unresolved], how='diagonal_relaxed')
        logger.info(f"Added unresolved local entities (no merge-safe IDs): {len(unresolved):,}")

    # Propagate entity_id to all identifiers and aggregate provenance
    final_identifiers = (
        local_all_edges_all
        .join(
            record_to_global.select(['source_id', 'local_entity_id', 'entity_id']),
            on=['source_id', 'local_entity_id'],
            how='inner'
        )
        .group_by(['entity_id', type_col, 'id_value'])
        .agg([
            pl.col('source_id').unique().sort().alias('sources')
        ])
        .sort(['entity_id', type_col, 'id_value'])
    )

    logger.info(f"Final results: {len(record_to_global):,} local->global mappings, {len(final_identifiers):,} identifier records")
    return record_to_global, final_identifiers
