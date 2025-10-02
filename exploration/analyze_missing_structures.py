"""
Analyze why SwissLipids records have missing SMILES/InChIKeys.
"""

import duckdb

__all__ = [
    'SWISSLIPIDS_PATH',
    'analyze_missing_structures',
]

SWISSLIPIDS_PATH = "../omnipath_build/databases/metabo/bronze/data/swisslipids/swisslipids_lipids/20250826_135637.parquet"

def analyze_missing_structures():
    """Investigate records with missing structural information."""

    print("=" * 120)
    print("Analysis of Missing Structural Information in SwissLipids")
    print("=" * 120)

    # Count records by InChIKey status
    print("\nInChIKey Status:")
    print("-" * 120)

    status_query = f"""
    SELECT
        CASE
            WHEN inchikey IS NULL THEN 'NULL'
            WHEN inchikey = '' THEN 'Empty String'
            WHEN inchikey = 'none' THEN 'none'
            WHEN inchikey = 'InChIKey=none' THEN 'InChIKey=none'
            WHEN inchikey = '-' THEN 'Dash'
            ELSE 'Valid'
        END as status,
        COUNT(*) as count
    FROM '{SWISSLIPIDS_PATH}'
    GROUP BY status
    ORDER BY count DESC
    """

    result = duckdb.sql(status_query)
    for row in result.fetchall():
        status, count = row
        print(f"  {status:<20} {count:>10,}")

    # Look at examples of records with InChIKey=none
    print("\n" + "=" * 120)
    print("Examples of records with 'InChIKey=none':")
    print("=" * 120)

    examples_query = f"""
    SELECT
        id,
        name,
        abbreviation,
        level,
        lipid_class,
        parent,
        components,
        smiles,
        inchi,
        inchikey,
        formula
    FROM '{SWISSLIPIDS_PATH}'
    WHERE inchikey = 'InChIKey=none'
    LIMIT 10
    """

    examples = duckdb.sql(examples_query)
    df = examples.to_df()

    for idx, row in df.iterrows():
        print(f"\nRecord {idx + 1}:")
        print(f"  ID: {row['id']}")
        print(f"  Name: {row['name']}")
        print(f"  Abbreviation: {row['abbreviation']}")
        print(f"  Level: {row['level']}")
        print(f"  Lipid Class: {row['lipid_class']}")
        print(f"  Parent: {row['parent']}")
        print(f"  Components: {row['components']}")
        print(f"  Formula: {row['formula']}")
        smiles_preview = str(row['smiles'])[:100] if row['smiles'] else 'None'
        print(f"  SMILES: {smiles_preview}")
        inchi_preview = str(row['inchi'])[:100] if row['inchi'] else 'None'
        print(f"  InChI: {inchi_preview}")
        print(f"  InChIKey: {row['inchikey']}")

    # Check if SMILES contain wildcards
    print("\n" + "=" * 120)
    print("Checking for wildcards/placeholders in SMILES:")
    print("=" * 120)

    wildcard_query = f"""
    SELECT
        COUNT(*) as total_with_wildcards,
        COUNT(DISTINCT smiles) as distinct_smiles_with_wildcards
    FROM '{SWISSLIPIDS_PATH}'
    WHERE smiles LIKE '%[*]%'
       OR smiles LIKE '%[R]%'
    """

    result = duckdb.sql(wildcard_query)
    total_wildcards, distinct_wildcards = result.fetchone()
    print(f"\nRecords with wildcards [*] or [R] in SMILES: {total_wildcards:,}")
    print(f"Distinct SMILES patterns with wildcards: {distinct_wildcards:,}")

    # Check distribution by level
    print("\n" + "=" * 120)
    print("Distribution of missing InChIKeys by lipid level:")
    print("=" * 120)

    level_query = f"""
    SELECT
        level,
        COUNT(*) as total,
        SUM(CASE WHEN inchikey IN ('InChIKey=none', 'none', '-', '') OR inchikey IS NULL THEN 1 ELSE 0 END) as missing,
        ROUND(100.0 * SUM(CASE WHEN inchikey IN ('InChIKey=none', 'none', '-', '') OR inchikey IS NULL THEN 1 ELSE 0 END) / COUNT(*), 1) as pct_missing
    FROM '{SWISSLIPIDS_PATH}'
    GROUP BY level
    ORDER BY total DESC
    """

    result = duckdb.sql(level_query)
    print(f"\n{'Level':<30} {'Total':>10} {'Missing':>10} {'% Missing':>12}")
    print("-" * 120)
    for row in result.fetchall():
        level, total, missing, pct = row
        print(f"{level:<30} {total:>10,} {missing:>10,} {pct:>11.1f}%")

    # Examples of records with valid vs invalid InChIKeys
    print("\n" + "=" * 120)
    print("Comparison: Records with valid InChIKeys:")
    print("=" * 120)

    valid_query = f"""
    SELECT
        id,
        name,
        level,
        lipid_class,
        smiles,
        inchikey
    FROM '{SWISSLIPIDS_PATH}'
    WHERE inchikey NOT IN ('InChIKey=none', 'none', '-', '')
      AND inchikey IS NOT NULL
    LIMIT 5
    """

    valid = duckdb.sql(valid_query)
    for idx, row in enumerate(valid.fetchall(), 1):
        print(f"\nValid Record {idx}:")
        print(f"  ID: {row[0]}")
        print(f"  Name: {row[1]}")
        print(f"  Level: {row[2]}")
        print(f"  Lipid Class: {row[3]}")
        smiles_preview = str(row[4])[:80] if row[4] else 'None'
        print(f"  SMILES: {smiles_preview}...")
        print(f"  InChIKey: {row[5]}")

    print("\n" + "=" * 120)


if __name__ == "__main__":
    analyze_missing_structures()
