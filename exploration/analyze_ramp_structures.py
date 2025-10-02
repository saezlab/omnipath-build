"""
Analyze RAMP data for missing SMILES/InChIKeys and wildcard patterns.
"""

import duckdb

__all__ = [
    'RAMP_PATH',
    'analyze_ramp',
]

RAMP_PATH = "../omnipath_build/databases/metabo/bronze/data/ramp/ramp_omnipathmetabo/20250909_101107.parquet"

def analyze_ramp():
    """Investigate RAMP structural information."""

    print("=" * 120)
    print("Analysis of RAMP Structural Information")
    print("=" * 120)

    # Basic counts
    print("\nBasic Statistics:")
    print("-" * 120)

    basic_query = f"""
    SELECT
        COUNT(*) as total_records,
        COUNT(DISTINCT iso_smiles) as distinct_smiles_all,
        COUNT(DISTINCT CASE WHEN iso_smiles IS NOT NULL AND iso_smiles != '' THEN iso_smiles END) as distinct_smiles_valid,
        COUNT(DISTINCT inchi_key) as distinct_inchikey_all,
        COUNT(DISTINCT CASE WHEN inchi_key IS NOT NULL AND inchi_key != '' THEN inchi_key END) as distinct_inchikey_valid
    FROM '{RAMP_PATH}'
    """

    result = duckdb.sql(basic_query).fetchone()
    print(f"Total records: {result[0]:,}")
    print(f"Distinct SMILES (all): {result[1]:,}")
    print(f"Distinct SMILES (non-null, non-empty): {result[2]:,}")
    print(f"Distinct InChIKeys (all): {result[3]:,}")
    print(f"Distinct InChIKeys (non-null, non-empty): {result[4]:,}")

    # Status breakdown
    print("\n" + "=" * 120)
    print("SMILES vs InChIKey Status:")
    print("=" * 120)

    status_query = f"""
    SELECT
        CASE
            WHEN (iso_smiles IS NULL OR iso_smiles = '') THEN 'SMILES Missing'
            WHEN iso_smiles LIKE '%[*]%' OR iso_smiles LIKE '%[R]%' THEN 'SMILES with Wildcards'
            ELSE 'SMILES Valid'
        END as smiles_status,
        CASE
            WHEN (inchi_key IS NULL OR inchi_key = '') THEN 'InChIKey Missing'
            ELSE 'InChIKey Valid'
        END as inchikey_status,
        COUNT(*) as count
    FROM '{RAMP_PATH}'
    GROUP BY smiles_status, inchikey_status
    ORDER BY count DESC
    """

    result = duckdb.sql(status_query)
    print(f"\n{'SMILES Status':<25} {'InChIKey Status':<20} {'Count':>15}")
    print("-" * 120)
    for row in result.fetchall():
        print(f"{row[0]:<25} {row[1]:<20} {row[2]:>15,}")

    # Check for wildcards
    print("\n" + "=" * 120)
    print("Wildcard Analysis:")
    print("=" * 120)

    wildcard_query = f"""
    SELECT
        COUNT(*) as total_with_wildcards,
        COUNT(DISTINCT iso_smiles) as distinct_smiles_with_wildcards
    FROM '{RAMP_PATH}'
    WHERE iso_smiles LIKE '%[*]%' OR iso_smiles LIKE '%[R]%'
    """

    result = duckdb.sql(wildcard_query).fetchone()
    if result[0] > 0:
        print(f"\nRecords with wildcards [*] or [R] in SMILES: {result[0]:,}")
        print(f"Distinct SMILES patterns with wildcards: {result[1]:,}")
    else:
        print("\nNo wildcard patterns found in RAMP SMILES.")

    # Examples of missing structures
    print("\n" + "=" * 120)
    print("Examples of records with missing SMILES:")
    print("=" * 120)

    missing_smiles_query = f"""
    SELECT
        ramp_id,
        common_name,
        iso_smiles,
        inchi_key
    FROM '{RAMP_PATH}'
    WHERE iso_smiles IS NULL OR iso_smiles = ''
    LIMIT 10
    """

    result = duckdb.sql(missing_smiles_query)
    for idx, row in enumerate(result.fetchall(), 1):
        print(f"\nRecord {idx}:")
        print(f"  RAMP ID: {row[0]}")
        print(f"  Name: {row[1]}")
        print(f"  SMILES: {row[2]}")
        print(f"  InChIKey: {row[3]}")

    # Examples with missing InChIKeys
    print("\n" + "=" * 120)
    print("Examples of records with missing InChIKeys:")
    print("=" * 120)

    missing_inchikey_query = f"""
    SELECT
        ramp_id,
        common_name,
        iso_smiles,
        inchi_key
    FROM '{RAMP_PATH}'
    WHERE inchi_key IS NULL OR inchi_key = ''
    LIMIT 10
    """

    result = duckdb.sql(missing_inchikey_query)
    for idx, row in enumerate(result.fetchall(), 1):
        print(f"\nRecord {idx}:")
        print(f"  RAMP ID: {row[0]}")
        print(f"  Name: {row[1]}")
        smiles_preview = str(row[2])[:80] if row[2] else 'None'
        print(f"  SMILES: {smiles_preview}")
        print(f"  InChIKey: {row[3]}")

    # Check columns available
    print("\n" + "=" * 120)
    print("Available columns in RAMP:")
    print("=" * 120)

    columns_query = f"DESCRIBE SELECT * FROM '{RAMP_PATH}'"
    columns = duckdb.sql(columns_query)
    print("\nColumns:")
    for row in columns.fetchall():
        print(f"  - {row[0]} ({row[1]})")

    print("\n" + "=" * 120)


if __name__ == "__main__":
    analyze_ramp()
