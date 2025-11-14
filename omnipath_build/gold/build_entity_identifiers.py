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
from pypath.internals.cv_terms import IdentifierNamespaceCv


# --------------------------------------------------------------------------- #
# Union-Find for tracking connected components (integer-indexed, optimized)
# --------------------------------------------------------------------------- #


class UnionFind:
    """Union-Find data structure for tracking connected components.

    Nodes are identified externally by (id_type, id_value) tuples where:
    - id_type is a CV term accession string (e.g., "MI:0326", "OM:0204")
    - id_value is always a str

    Internally, nodes are mapped to integer indices for performance.
    """

    def __init__(self) -> None:
        # parent[i] is the parent index of node i
        self.parent: list[int] = []
        # rank[i] is the rank of the tree rooted at i
        self.rank: list[int] = []
        # mapping from (id_type, id_value) -> int index
        self._index: dict[tuple[str, str], int] = {}
        # number of connected components
        self._num_components: int = 0

    # --------------------------- internal helpers --------------------------- #

    def _get_index(self, node: tuple[str, str]) -> int:
        """Get or create an integer index for a node."""
        idx = self._index.get(node)
        if idx is None:
            idx = len(self.parent)
            self._index[node] = idx
            # new node is its own parent
            self.parent.append(idx)
            self.rank.append(0)
            self._num_components += 1
        return idx

    # ------------------------------ public API ----------------------------- #

    def find(self, node: tuple[str, str]) -> int:
        """Find the root index of node with path compression."""
        i = self._get_index(node)
        # iterative path compression
        while self.parent[i] != i:
            self.parent[i] = self.parent[self.parent[i]]
            i = self.parent[i]
        return i

    def union(self, x: tuple[str, str], y: tuple[str, str]) -> bool:
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
    'MERGE_UNSAFE_IDENTIFIER_TYPES',
    'build_entity_identifiers',
]

logger = logging.getLogger(__name__)

# Merge-unsafe identifier types (excluded from cross-source merging)
# These are ambiguous identifiers that should NOT be used for entity resolution
MERGE_UNSAFE_IDENTIFIER_TYPES = frozenset({
    IdentifierNamespaceCv.CHEBI.value,
    IdentifierNamespaceCv.GENE_NAME_PRIMARY.value,
    IdentifierNamespaceCv.GENE_NAME_SYNONYM.value,
    IdentifierNamespaceCv.NAME.value,
    IdentifierNamespaceCv.SYNONYM.value,
    IdentifierNamespaceCv.SYSTEMATIC_NAME.value,
    IdentifierNamespaceCv.ABBREVIATED_NAME.value,
    IdentifierNamespaceCv.IUPAC_NAME.value,
    IdentifierNamespaceCv.IUPAC_TRADITIONAL_NAME.value,
    IdentifierNamespaceCv.MOLECULAR_FORMULA.value,
    IdentifierNamespaceCv.SMILES.value,
    IdentifierNamespaceCv.INN.value,
})


# --------------------------------------------------------------------------- #
# Helper functions
# --------------------------------------------------------------------------- #


def _load_local_tables(local_tables_dir: Path) -> list[tuple[int, pl.DataFrame]]:
    """Load all local_entity_identifier_*.parquet files (flattened format).

    Returns:
        List of (source_id, dataframe) tuples where dataframe has columns:
        [source_id, local_entity_id, type_id, identifier, local_entity_identifier_id]
    """
    files = sorted(local_tables_dir.glob('local_entity_identifier_*.parquet'))
    if not files:
        logger.warning(f"No local_entity_identifier_*.parquet files found in {local_tables_dir}")
        return []

    result: list[tuple[int, pl.DataFrame]] = []
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
    merge_unsafe_types: frozenset[str],
) -> tuple[pl.DataFrame, pl.DataFrame] | None:
    """Prepare per-source local entities and edges from pre-built local tables.

    Args:
        source_id: Source ID (integer)
        local_df: DataFrame with columns [source_id, local_entity_id, type_id, identifier, local_entity_identifier_id]
                  (flattened format from build_local_tables.py)
        merge_unsafe_types: Set of identifier types to EXCLUDE from cross-source merging (blacklist)

    Returns a tuple of:
    - local_ms_edges (source_id, local_entity_id, id_type, id_value) - merge-safe identifiers only
    - local_all_edges (source_id, local_entity_id, id_type, id_value) - all identifiers

    or None if the source has no identifiers.
    """
    if len(local_df) == 0:
        return None

    unsafe_list = list(merge_unsafe_types)

    # Rename columns to match expected format
    # Input: [source_id, local_entity_id, type_id, identifier, local_entity_identifier_id]
    # Output: [source_id, local_entity_id, id_type, id_value]
    local_all_edges = (
        local_df
        .select([
            'source_id',
            'local_entity_id',
            pl.col('type_id').cast(pl.Utf8).alias('id_type'),
            pl.col('identifier').cast(pl.Utf8).alias('id_value'),
        ])
        .filter(pl.col('id_type').is_not_null() & pl.col('id_value').is_not_null())
        .unique()
    )

    # Filter OUT merge-unsafe identifiers (blacklist approach)
    local_ms_edges = (
        local_all_edges
        .filter(~pl.col('id_type').is_in(unsafe_list))
    )

    return local_ms_edges, local_all_edges


# --------------------------------------------------------------------------- #
# Utilities to convert union-find to mapping
# --------------------------------------------------------------------------- #


def _union_find_to_safe_clusters(uf: UnionFind) -> pl.DataFrame:
    """Convert UnionFind state into a mapping of (id_type, id_value) -> entity_id.

    Returns DataFrame columns: [type_id, id_value, entity_id]
    - type_id is a CV term accession string (e.g., "MI:0326")
    - id_value is the identifier value
    - entity_id is a sequential integer starting at 1
    """
    # Compute roots for each index (find() also compresses paths)
    index_to_root: dict[int, int] = {}
    for node, idx in uf._index.items():
        root = uf.find(node)
        index_to_root[idx] = root

    # Map unique roots to sequential ints
    unique_roots = sorted(set(index_to_root.values()))
    root_to_entity: dict[int, int] = {root: i + 1 for i, root in enumerate(unique_roots)}

    rows = [
        {
            'type_id': t,
            'id_value': v,
            'entity_id': root_to_entity[index_to_root[idx]],
        }
        for (t, v), idx in uf._index.items()
    ]
    return pl.DataFrame(rows)


# --------------------------------------------------------------------------- #
# Public entry point (full unified table with provenance)
# --------------------------------------------------------------------------- #


def build_entity_identifiers(
    local_tables_dir: Path,
    merge_unsafe_types: frozenset[str] = MERGE_UNSAFE_IDENTIFIER_TYPES,
) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    """Build unified identifier tables with canonical entity IDs from local tables.

    Args:
        local_tables_dir: Directory containing local_entity_identifier_*.parquet files
        merge_unsafe_types: Set of identifier types (accession strings) to EXCLUDE from cross-source merging (blacklist)

    Returns a tuple of:
      - record_to_global: (source_id, local_entity_id) -> entity_id
      - entity_identifiers: (id, entity_id, type_id, identifier)
      - entity_identifier_resource: (id, entity_identifier_id, source_entity_id)
                                    where source_entity_id is the entity_id of the source (from local context)
    """
    local_tables_dir = Path(local_tables_dir)
    sources_data = _load_local_tables(local_tables_dir)

    if not sources_data:
        logger.warning("No local tables found, returning empty results")
        return pl.DataFrame(), pl.DataFrame(), pl.DataFrame()

    union_find = UnionFind()
    all_local_ms_edges: list[pl.DataFrame] = []
    all_local_all_edges: list[pl.DataFrame] = []

    num_sources = len(sources_data)
    sources_processed = 0

    for source_id, local_df in sources_data:
        logger.info("=" * 80)
        logger.info(f"Processing source [{sources_processed + 1}/{num_sources}]: source_id={source_id}")
        logger.info("=" * 80)

        prepared = _prepare_local_entities_for_source(source_id, local_df, merge_unsafe_types)
        if prepared is None:
            logger.warning(f"  No identifiers found in source {source_id}, skipping")
            sources_processed += 1
            continue
        local_ms_edges, local_all_edges = prepared

        # Local entity stats
        n_local = local_df.select('local_entity_id').n_unique()
        n_with_ms = len(local_ms_edges.select('local_entity_id').unique())
        n_without_ms = n_local - n_with_ms
        logger.info(f"  Local entities: {n_local:,} (with MS: {n_with_ms:,}, without MS: {n_without_ms:,})")

        # Build identifier lists per local entity
        # Note: id_type is now an accession string (e.g., "MI:0326")
        type_col = 'id_type'
        ms_edges_with_lists = (
            local_ms_edges
            .group_by(['source_id', 'local_entity_id'])
            .agg([
                pl.struct(
                    type=pl.col(type_col),
                    value=pl.col('id_value'),
                ).alias('identifiers')
            ])
        )

        # Deduplicate identical identifier sets:
        # we only need the unique 'identifiers' values to drive unions;
        # the actual local_entity_ids sharing those sets don't change the UF result.
        id_sets = ms_edges_with_lists.select('identifiers').unique()

        n_unique_id_sets = len(id_sets)
        n_total_entities = len(ms_edges_with_lists)
        if n_unique_id_sets < n_total_entities:
            logger.info(
                f"  Deduplicated identifier sets: "
                f"{n_total_entities:,} entities -> {n_unique_id_sets:,} unique sets"
            )

        # Register all merge-safe nodes
        ms_nodes = local_ms_edges.select([type_col, 'id_value']).unique()
        for t, v in ms_nodes.iter_rows():
            # Just ensure node is known to UF
            union_find.find((t, v))

        # Connect identifiers within each unique identifier set using Union-Find directly
        components_before = union_find.num_components

        for row in id_sets.iter_rows(named=True):
            id_structs = row['identifiers']
            if not id_structs or len(id_structs) < 2:
                continue

            # id_structs is a list of dicts: {'type': ..., 'value': ...}
            base_struct = id_structs[0]
            base = (base_struct['type'], str(base_struct['value']))

            for s in id_structs[1:]:
                other = (s['type'], str(s['value']))
                union_find.union(base, other)

        components_after = union_find.num_components
        logger.info(f"  Merge-safe nodes: {len(ms_nodes):,}")
        logger.info(f"  Entities after UF: {components_after:,} (merged {components_before - components_after:,})")

        all_local_ms_edges.append(local_ms_edges)
        all_local_all_edges.append(local_all_edges)
        sources_processed += 1

    # Build safe cluster mapping (type_id/id_type, id_value) -> entity_id
    safe_clusters = _union_find_to_safe_clusters(union_find)

    if not all_local_ms_edges or not all_local_all_edges:
        logger.warning("No edges collected, returning empty results")
        return pl.DataFrame(), pl.DataFrame(), pl.DataFrame()

    local_ms_edges_all = pl.concat(all_local_ms_edges, how='diagonal_relaxed')
    local_all_edges_all = pl.concat(all_local_all_edges, how='diagonal_relaxed')

    # Map local entities to global entity IDs via merge-safe identifiers
    # Note: Both edges and safe_clusters use id_type (accession string)
    type_col = 'id_type'
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

    # Propagate entity_id to all identifiers with source provenance
    identifiers_with_sources = (
        local_all_edges_all
        .join(
            record_to_global.select(['source_id', 'local_entity_id', 'entity_id']),
            on=['source_id', 'local_entity_id'],
            how='inner'
        )
        .select(['entity_id', type_col, 'id_value', 'source_id'])
        .unique()  # Remove duplicates
    )

    # Build entity_identifier table (unique identifiers without source info)
    entity_identifiers = (
        identifiers_with_sources
        .select(['entity_id', type_col, 'id_value'])
        .unique()
        .sort(['entity_id', type_col, 'id_value'])
        .with_row_index('id', offset=1)
        # Rename columns to match schema
        .rename({type_col: 'type_id', 'id_value': 'identifier'})
    )

    # Build source_id → source_entity_id mapping
    # Sources always have local_entity_id = 1 (guaranteed by build_local_tables.py)
    source_mapping = (
        record_to_global
        .filter(pl.col('local_entity_id') == 1)
        .select(['source_id', pl.col('entity_id').alias('source_entity_id')])
    )
    logger.info(f"Built source mapping: {len(source_mapping):,} sources")

    # Build entity_identifier_resource table (identifier → source provenance)
    entity_identifier_resource = (
        identifiers_with_sources
        .join(
            entity_identifiers.rename({'type_id': type_col, 'identifier': 'id_value'}),
            on=['entity_id', type_col, 'id_value'],
            how='inner'
        )
        .join(source_mapping, on='source_id', how='inner')
        .select([pl.col('id').alias('entity_identifier_id'), 'source_entity_id'])
        .unique()
        .sort(['entity_identifier_id', 'source_entity_id'])
        .with_row_index('id', offset=1)
    )

    logger.info(
        "Final results: %s local->global mappings, %s identifier records, %s identifier-resource links",
        f"{len(record_to_global):,}",
        f"{len(entity_identifiers):,}",
        f"{len(entity_identifier_resource):,}",
    )
    return record_to_global, entity_identifiers, entity_identifier_resource