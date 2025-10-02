"""
Analyze duplicate entries in SwissLipids data.
"""

import duckdb

__all__ = [
    'SWISSLIPIDS_PATH',
    'analyze_duplicates',
]

SWISSLIPIDS_PATH = "../omnipath_build/databases/metabo/bronze/data/swisslipids/swisslipids_lipids/20250826_135637.parquet"

def analyze_duplicates():
    """Investigate why SwissLipids has many duplicate entries."""

    # Check for duplicate InChIKeys
    print("=" * 120)
    print("Top 10 InChIKeys with most duplicates:")
    print("=" * 120)

    query = f"""
    SELECT
        inchikey,
        COUNT(*) as count,
        COUNT(DISTINCT smiles) as distinct_smiles,
        STRING_AGG(DISTINCT smiles, ' | ') as smiles_examples
    FROM '{SWISSLIPIDS_PATH}'
    WHERE inchikey IS NOT NULL AND inchikey != ''
    GROUP BY inchikey
    HAVING COUNT(*) > 1
    ORDER BY count DESC
    LIMIT 10
    """

    result = duckdb.sql(query)
    for row in result.fetchall():
        inchikey, count, distinct_smiles, smiles = row
        print(f"\nInChIKey: {inchikey}")
        print(f"  Record Count: {count}")
        print(f"  Distinct SMILES: {distinct_smiles}")
        if len(smiles) > 150:
            print(f"  SMILES: {smiles[:150]}...")
        else:
            print(f"  SMILES: {smiles}")

    print("\n" + "=" * 120)
    print("Checking what differs between duplicate records...")
    print("=" * 120)

    # Get column names
    columns_query = f"DESCRIBE SELECT * FROM '{SWISSLIPIDS_PATH}'"
    columns = [row[0] for row in duckdb.sql(columns_query).fetchall()]
    print(f"\nColumns in dataset: {', '.join(columns)}")

    # Look at a specific example of duplicates
    print("\n" + "=" * 120)
    print("Example: All records for a duplicate InChIKey")
    print("=" * 120)

    example_query = f"""
    WITH duplicate_keys AS (
        SELECT inchikey
        FROM '{SWISSLIPIDS_PATH}'
        WHERE inchikey IS NOT NULL AND inchikey != ''
        GROUP BY inchikey
        HAVING COUNT(*) > 1
        LIMIT 1
    )
    SELECT *
    FROM '{SWISSLIPIDS_PATH}'
    WHERE inchikey IN (SELECT inchikey FROM duplicate_keys)
    LIMIT 5
    """

    example = duckdb.sql(example_query)
    print(example.to_df().to_string())

    # Count duplicates by different criteria
    print("\n" + "=" * 120)
    print("Duplicate Statistics:")
    print("=" * 120)

    total_records = duckdb.sql(f"SELECT COUNT(*) FROM '{SWISSLIPIDS_PATH}'").fetchone()[0]

    inchikey_dups = duckdb.sql(f"""
        SELECT COUNT(*) FROM (
            SELECT inchikey
            FROM '{SWISSLIPIDS_PATH}'
            WHERE inchikey IS NOT NULL AND inchikey != ''
            GROUP BY inchikey
            HAVING COUNT(*) > 1
        )
    """).fetchone()[0]

    smiles_dups = duckdb.sql(f"""
        SELECT COUNT(*) FROM (
            SELECT smiles
            FROM '{SWISSLIPIDS_PATH}'
            WHERE smiles IS NOT NULL AND smiles != ''
            GROUP BY smiles
            HAVING COUNT(*) > 1
        )
    """).fetchone()[0]

    print(f"\nTotal records: {total_records:,}")
    print(f"InChIKeys with duplicates: {inchikey_dups:,}")
    print(f"SMILES with duplicates: {smiles_dups:,}")


if __name__ == "__main__":
    analyze_duplicates()
