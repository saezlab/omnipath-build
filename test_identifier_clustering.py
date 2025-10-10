#!/usr/bin/env python3
"""
Test script for identifier clustering using union-find algorithm.

Goal: Create connected components of identifiers, excluding name/synonym from initial clustering
but deduplicating them within their cluster.
"""

import polars as pl
from pathlib import Path

__all__ = [
    'UnionFind',
    'cluster_identifiers',
    'main',
]


class UnionFind:
    """Simple union-find (disjoint set) data structure for clustering."""

    def __init__(self):
        self.parent = {}
        self.rank = {}

    def find(self, x):
        """Find the root of x with path compression."""
        if x not in self.parent:
            self.parent[x] = x
            self.rank[x] = 0
            return x

        if self.parent[x] != x:
            self.parent[x] = self.find(self.parent[x])  # Path compression
        return self.parent[x]

    def union(self, x, y):
        """Union the sets containing x and y."""
        root_x = self.find(x)
        root_y = self.find(y)

        if root_x == root_y:
            return

        # Union by rank
        if self.rank[root_x] < self.rank[root_y]:
            self.parent[root_x] = root_y
        elif self.rank[root_x] > self.rank[root_y]:
            self.parent[root_y] = root_x
        else:
            self.parent[root_y] = root_x
            self.rank[root_x] += 1

    def get_clusters(self):
        """Return a dict mapping each element to its cluster ID."""
        # First, ensure all paths are compressed
        for x in list(self.parent.keys()):
            self.find(x)

        # Get unique roots and assign sequential cluster IDs
        roots = sorted(set(self.parent.values()))
        root_to_cluster = {root: i + 1 for i, root in enumerate(roots)}

        # Map each element to its cluster ID
        return {x: root_to_cluster[self.parent[x]] for x in self.parent}


def cluster_identifiers(data_root: Path):
    """
    Cluster identifiers from pass1 entity_identifier files.

    Args:
        data_root: Path to data directory containing pass1 files

    Returns:
        polars.DataFrame with columns:
            - id: auto-increment integer
            - identifier: the identifier value
            - identifier_type_namespace_name: namespace (e.g., 'OmniPath')
            - identifier_type_name: type (e.g., 'inchikey', 'uniprot', 'name')
            - entity_id: the cluster ID this identifier belongs to
    """
    print("Step 1: Reading all entity_identifier pass1 files...")
    # Read all entity_identifier parquet files
    pattern = str(data_root / "*" / "*" / "gold" / "entity_identifier.parquet")
    all_identifiers = pl.read_parquet(pattern)

    print(f"  Total identifiers: {len(all_identifiers):,}")
    print(f"  Columns: {all_identifiers.columns}")

    # Step 2: Separate identifiers into clustering vs. name/synonym
    print("\nStep 2: Separating clustering identifiers from name/synonym...")
    clustering_df = all_identifiers.filter(
        ~pl.col("identifier_type_name").is_in(["name", "synonym"])
    )
    name_synonym_df = all_identifiers.filter(
        pl.col("identifier_type_name").is_in(["name", "synonym"])
    )

    print(f"  Clustering identifiers: {len(clustering_df):,}")
    print(f"  Name/synonym identifiers: {len(name_synonym_df):,}")

    # Step 3: Build union-find structure
    print("\nStep 3: Building identifier clusters using union-find...")
    uf = UnionFind()

    # Create a unique key for each identifier: (identifier, identifier_type_name)
    # Group by entity_deduplication to find connected components
    grouped = (
        clustering_df
        .group_by(["entity_deduplication_identifier", "entity_deduplication_identifier_type"])
        .agg([
            pl.col("identifier"),
            pl.col("identifier_type_name")
        ])
    )

    # Union all identifiers that share the same entity_deduplication_identifier
    for row in grouped.iter_rows(named=True):
        identifiers = row["identifier"]
        types = row["identifier_type_name"]

        if len(identifiers) < 2:
            # Single identifier, just register it
            if len(identifiers) == 1:
                key = (identifiers[0], types[0])
                uf.find(key)
            continue

        # Union all pairs
        keys = [(id_val, type_val) for id_val, type_val in zip(identifiers, types)]
        for i in range(len(keys) - 1):
            uf.union(keys[i], keys[i + 1])

    print(f"  Total unique identifier keys: {len(uf.parent):,}")

    # Step 4: Get cluster assignments
    print("\nStep 4: Assigning cluster IDs...")
    clusters = uf.get_clusters()

    print(f"  Total clusters: {max(clusters.values()):,}")

    # Create a mapping dataframe
    cluster_mapping = pl.DataFrame([
        {
            "identifier": key[0],
            "identifier_type_name": key[1],
            "entity_id": cluster_id
        }
        for key, cluster_id in clusters.items()
    ])

    # Join back to get full data with cluster IDs
    clustered_df = (
        clustering_df
        .select(["identifier", "identifier_type_namespace_name", "identifier_type_name",
                 "entity_deduplication_identifier", "entity_deduplication_identifier_type"])
        .join(
            cluster_mapping,
            on=["identifier", "identifier_type_name"],
            how="left"
        )
        .select(["identifier", "identifier_type_namespace_name", "identifier_type_name", "entity_id"])
    )

    # Step 5: Handle name/synonym identifiers
    print("\nStep 5: Processing name/synonym identifiers...")

    # For name/synonym, map them to the cluster of their original entity_dedup_id
    # Build a lookup: (entity_dedup_id, entity_dedup_type) -> entity_id (cluster)
    dedup_to_cluster = (
        clustering_df
        .select(["identifier", "identifier_type_name",
                 "entity_deduplication_identifier", "entity_deduplication_identifier_type"])
        .join(cluster_mapping, on=["identifier", "identifier_type_name"], how="left")
        .select([
            pl.col("entity_deduplication_identifier").alias("dedup_id"),
            pl.col("entity_deduplication_identifier_type").alias("dedup_type"),
            "entity_id"
        ])
        .unique(subset=["dedup_id", "dedup_type"])
    )

    # Map name/synonym to clusters
    name_synonym_with_cluster = (
        name_synonym_df
        .join(
            dedup_to_cluster,
            left_on=["entity_deduplication_identifier", "entity_deduplication_identifier_type"],
            right_on=["dedup_id", "dedup_type"],
            how="left"
        )
        .select(["identifier", "identifier_type_namespace_name", "identifier_type_name", "entity_id"])
    )

    print(f"  Name/synonym identifiers mapped to clusters: {len(name_synonym_with_cluster):,}")

    # Deduplicate name/synonym within their cluster
    name_synonym_deduped = name_synonym_with_cluster.unique(
        subset=["identifier", "identifier_type_name", "entity_id"]
    )

    print(f"  Name/synonym after deduplication: {len(name_synonym_deduped):,}")

    # Step 6: Combine all results
    print("\nStep 6: Combining all results...")
    final_result = pl.concat([
        clustered_df,
        name_synonym_deduped
    ])

    print(f"  Final total identifiers: {len(final_result):,}")

    # Sort by entity_id, then by identifier for consistency
    final_result = final_result.sort(["entity_id", "identifier"])

    # Add sequential ID
    final_result = final_result.with_row_index("id", offset=1)

    return final_result


def main():
    """Test the clustering on actual data."""
    import sys
    from pathlib import Path

    # Default to current working directory
    data_root = Path("/Users/jschaul/Code/omnipath_build/databases/omnipath/data")

    if not data_root.exists():
        print(f"Error: Data root not found: {data_root}")
        sys.exit(1)

    print(f"Using data root: {data_root}")
    print("=" * 70)

    # Run clustering
    result = cluster_identifiers(data_root)

    print("\n" + "=" * 70)
    print("Clustering complete!")
    print("=" * 70)
    print(f"\nFinal shape: {result.shape}")
    print(f"\nColumn names: {result.columns}")
    print("\nSample of results:")
    print(result.head(10))

    # Show some statistics
    print("\n" + "=" * 70)
    print("Statistics:")
    print("=" * 70)

    entity_counts = result.group_by("entity_id").agg(
        pl.len().alias("identifier_count")
    ).sort("identifier_count", descending=True)

    print(f"\nTop entities (clusters) by identifier count:")
    print(entity_counts.head(10))

    type_counts = result.group_by("identifier_type_name").agg(
        pl.len().alias("count")
    ).sort("count", descending=True)

    print(f"\nIdentifier type distribution:")
    print(type_counts)

    # Show number of unique clusters
    print(f"\nTotal unique entity_id (clusters): {result['entity_id'].n_unique():,}")

    # Optionally save to parquet
    output_path = Path("test_clustered_entity_identifier.parquet")
    result.write_parquet(output_path)
    print(f"\nResults saved to: {output_path}")


if __name__ == "__main__":
    main()
