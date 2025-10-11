#!/usr/bin/env python3
"""
Test script for identifier clustering using union-find algorithm.

Goal: Create connected components of identifiers from silver_entities tables,
where each row represents an entity with multiple identifiers.
Identifiers that appear together in a row are grouped, and transitively connected
through shared identifiers.
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
    Cluster identifiers from silver_entities, complex_members, and silver_interactions files.

    Args:
        data_root: Path to data directory containing silver files

    Returns:
        polars.DataFrame with columns:
            - id: auto-increment integer
            - identifier: the identifier value
            - identifier_type_namespace_name: namespace (e.g., 'OmniPath')
            - identifier_type_name: type (e.g., 'inchikey', 'uniprot', 'name')
            - entity_id: the cluster ID this identifier belongs to
    """
    print("Step 1: Reading all silver_entities files...")
    # Read all silver_entities parquet files - need to align schemas
    pattern = str(data_root / "*" / "*" / "silver" / "silver_entities.parquet")

    # Read with schema inference to handle mismatched types
    from glob import glob
    import json

    parquet_files = glob(pattern)

    if not parquet_files:
        raise FileNotFoundError(f"No silver_entities files found at {pattern}")

    # Read all files - select only the columns we need
    dfs = []
    identifier_cols = ['inchikey', 'smiles', 'lipidmaps_id', 'chebi_id', 'pubchem_cid',
                      'cas_number', 'drugbank_id', 'hmdb_id', 'kegg_id',
                      'metanetx_id', 'ramp_id', 'swisslipids_id', 'ec_number']

    for file in parquet_files:
        df = pl.read_parquet(file)
        # Select and cast identifier columns to strings, plus complex_members JSON
        select_exprs = []
        for col in identifier_cols:
            if col in df.columns:
                select_exprs.append(pl.col(col).cast(pl.String).alias(col))

        # Also include complex_members if they exist (name will be added later)
        if 'complex_members' in df.columns:
            select_exprs.append(pl.col('complex_members'))

        if select_exprs:
            df = df.select(select_exprs)
            dfs.append(df)

    silver_entities = pl.concat(dfs, how="diagonal_relaxed")

    print(f"  Total entities: {len(silver_entities):,}")
    print(f"  Columns: {silver_entities.columns}")

    # Step 2: Build union-find structure
    print("\nStep 2: Building identifier clusters using union-find...")
    uf = UnionFind()

    # Define identifier columns and their types (exclude name/synonym-like columns for now)
    identifier_columns = {
        'inchikey': 'inchikey',
        'smiles': 'smiles',
        'lipidmaps_id': 'lipidmaps_id',
        'chebi_id': 'chebi_id',
        'pubchem_cid': 'pubchem_cid',
        'cas_number': 'cas_number',
        'drugbank_id': 'drugbank_id',
        'hmdb_id': 'hmdb_id',
        'kegg_id': 'kegg_id',
        'metanetx_id': 'metanetx_id',
        'ramp_id': 'ramp_id',
        'swisslipids_id': 'swisslipids_id',
        'ec_number': 'ec_number',
    }

    # For each row, union all non-null identifiers together
    print("  Processing entities and grouping identifiers...")
    row_count = 0
    complex_member_count = 0

    for row in silver_entities.iter_rows(named=True):
        row_count += 1
        if row_count % 10000 == 0:
            print(f"    Processed {row_count:,} entities...")

        # Collect all identifiers from this row
        identifiers = []
        for col, id_type in identifier_columns.items():
            if col in row and row[col] is not None:
                value = str(row[col])
                if value and value != 'null':
                    identifiers.append((value, id_type))

        # Union all pairs of identifiers in this row
        if len(identifiers) >= 2:
            for i in range(len(identifiers) - 1):
                uf.union(identifiers[i], identifiers[i + 1])
        elif len(identifiers) == 1:
            # Just register single identifiers
            uf.find(identifiers[0])

        # Process complex_members JSON if present
        if row.get('complex_members') is not None:
            try:
                members_json = row['complex_members']
                # Handle both string JSON and already-parsed objects
                if isinstance(members_json, str):
                    members = json.loads(members_json)
                else:
                    members = members_json

                if isinstance(members, list):
                    for member in members:
                        if isinstance(member, dict):
                            member_id = member.get('member_id')
                            member_id_type = member.get('member_id_type')

                            if member_id and member_id_type:
                                # Register this member identifier
                                member_identifier = (str(member_id), str(member_id_type))
                                uf.find(member_identifier)
                                complex_member_count += 1
            except (json.JSONDecodeError, TypeError):
                # Skip malformed JSON
                pass

    print(f"  Total unique identifiers from entities: {len(uf.parent):,}")
    print(f"  Complex member identifiers found: {complex_member_count:,}")

    # Step 2b: Process silver_interactions files if they exist
    print("\nStep 2b: Processing silver_interactions files...")
    interaction_pattern = str(data_root / "*" / "*" / "silver" / "silver_interactions.parquet")
    interaction_files = glob(interaction_pattern)

    interaction_count = 0
    if interaction_files:
        print(f"  Found {len(interaction_files)} silver_interactions files")
        for file in interaction_files:
            df_int = pl.read_parquet(file)
            for row in df_int.iter_rows(named=True):
                # Entity A
                entity_a_id = row.get('entity_a_identifier')
                entity_a_type = row.get('entity_a_identifier_type')
                if entity_a_id and entity_a_type:
                    uf.find((str(entity_a_id), str(entity_a_type)))
                    interaction_count += 1

                # Entity B
                entity_b_id = row.get('entity_b_identifier')
                entity_b_type = row.get('entity_b_identifier_type')
                if entity_b_id and entity_b_type:
                    uf.find((str(entity_b_id), str(entity_b_type)))
                    interaction_count += 1
        print(f"  Interaction entity identifiers found: {interaction_count:,}")
    else:
        print(f"  No silver_interactions files found (this is expected if no interaction sources exist yet)")

    print(f"  Total unique identifiers registered: {len(uf.parent):,}")

    # Step 3: Get cluster assignments
    print("\nStep 3: Assigning cluster IDs...")
    clusters = uf.get_clusters()
    print(f"  Total clusters: {max(clusters.values()) if clusters else 0:,}")

    # Step 4: Create output dataframe
    print("\nStep 4: Creating output dataframe...")
    rows = []
    for (identifier, id_type), cluster_id in clusters.items():
        rows.append({
            "identifier": identifier,
            "identifier_type_namespace_name": "OmniPath",
            "identifier_type_name": id_type,
            "entity_id": cluster_id
        })

    result = pl.DataFrame(rows)

    # Step 5: Handle name/synonym columns
    print("\nStep 5: Processing name/synonym identifiers...")
    name_rows = []
    for row in silver_entities.iter_rows(named=True):
        # Find which cluster this entity belongs to
        entity_cluster = None
        for col, id_type in identifier_columns.items():
            if col in row and row[col] is not None:
                value = str(row[col])
                if value and value != 'null':
                    key = (value, id_type)
                    if key in clusters:
                        entity_cluster = clusters[key]
                        break

        # Add names to that cluster
        if entity_cluster and row.get('name'):
            name_rows.append({
                "identifier": row['name'],
                "identifier_type_namespace_name": "OmniPath",
                "identifier_type_name": "name",
                "entity_id": entity_cluster
            })

    if name_rows:
        name_df = pl.DataFrame(name_rows).unique(subset=["identifier", "entity_id"])
        print(f"  Name identifiers: {len(name_df):,}")
        result = pl.concat([result, name_df])

    print(f"  Final total identifiers: {len(result):,}")

    # Sort by entity_id, then by identifier for consistency
    result = result.sort(["entity_id", "identifier"])

    # Add sequential ID
    result = result.with_row_index("id", offset=1)

    return result


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
