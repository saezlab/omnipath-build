"""
Check why SMILES and InChIKey counts differ.
"""

import duckdb

__all__ = [
    'SWISSLIPIDS_PATH',
    'compare_smiles_inchikey',
]

SWISSLIPIDS_PATH = "../omnipath_build/databases/metabo/bronze/data/swisslipids/swisslipids_lipids/20250826_135637.parquet"

def compare_smiles_inchikey():
    """Compare SMILES and InChIKey availability."""

    print("=" * 120)
    print("Comparing SMILES vs InChIKey Availability")
    print("=" * 120)

    # Count by different criteria
    query = f"""
    SELECT
        CASE
            WHEN (smiles IS NULL OR smiles = '') THEN 'SMILES Missing'
            WHEN smiles LIKE '%[*]%' OR smiles LIKE '%[R]%' THEN 'SMILES with Wildcards'
            ELSE 'SMILES Valid'
        END as smiles_status,
        CASE
            WHEN (inchikey IS NULL OR inchikey = '' OR inchikey IN ('InChIKey=none', 'none', '-')) THEN 'InChIKey Missing'
            ELSE 'InChIKey Valid'
        END as inchikey_status,
        COUNT(*) as count
    FROM '{SWISSLIPIDS_PATH}'
    GROUP BY smiles_status, inchikey_status
    ORDER BY count DESC
    """

    result = duckdb.sql(query)
    print(f"\n{'SMILES Status':<25} {'InChIKey Status':<20} {'Count':>15}")
    print("-" * 120)
    for row in result.fetchall():
        print(f"{row[0]:<25} {row[1]:<20} {row[2]:>15,}")

    # Count distinct SMILES including wildcards
    print("\n" + "=" * 120)
    print("Distinct SMILES counts:")
    print("=" * 120)

    counts_query = f"""
    SELECT
        COUNT(*) as total_records,
        COUNT(DISTINCT smiles) as all_distinct_smiles,
        COUNT(DISTINCT CASE WHEN smiles NOT LIKE '%[*]%' AND smiles NOT LIKE '%[R]%' THEN smiles END) as distinct_non_wildcard,
        COUNT(DISTINCT CASE WHEN smiles LIKE '%[*]%' OR smiles LIKE '%[R]%' THEN smiles END) as distinct_wildcard
    FROM '{SWISSLIPIDS_PATH}'
    WHERE smiles IS NOT NULL AND smiles != ''
    """

    result = duckdb.sql(counts_query).fetchone()
    total, all_distinct, non_wildcard, wildcard = result
    print(f"\nTotal records with SMILES: {total:,}")
    print(f"All distinct SMILES (including wildcards): {all_distinct:,}")
    print(f"Distinct non-wildcard SMILES: {non_wildcard:,}")
    print(f"Distinct wildcard SMILES patterns: {wildcard:,}")

    # Count distinct InChIKeys
    print("\n" + "=" * 120)
    print("Distinct InChIKey counts:")
    print("=" * 120)

    inchikey_query = f"""
    SELECT
        COUNT(*) as total_records,
        COUNT(DISTINCT inchikey) as all_distinct_inchikeys,
        COUNT(DISTINCT CASE WHEN inchikey NOT IN ('InChIKey=none', 'none', '-', '') AND inchikey IS NOT NULL THEN inchikey END) as distinct_valid
    FROM '{SWISSLIPIDS_PATH}'
    """

    result = duckdb.sql(inchikey_query).fetchone()
    total, all_distinct, valid = result
    print(f"\nTotal records: {total:,}")
    print(f"All distinct InChIKeys (including invalid): {all_distinct:,}")
    print(f"Distinct valid InChIKeys: {valid:,}")

    # Show what the count_distinct_identifiers script is counting
    print("\n" + "=" * 120)
    print("What count_distinct_identifiers.py counts:")
    print("=" * 120)

    script_smiles_query = f"""
    SELECT COUNT(DISTINCT smiles)
    FROM '{SWISSLIPIDS_PATH}'
    WHERE smiles IS NOT NULL AND smiles != ''
    """

    script_inchikey_query = f"""
    SELECT COUNT(DISTINCT inchikey)
    FROM '{SWISSLIPIDS_PATH}'
    WHERE inchikey IS NOT NULL AND inchikey != ''
    """

    smiles_count = duckdb.sql(script_smiles_query).fetchone()[0]
    inchikey_count = duckdb.sql(script_inchikey_query).fetchone()[0]

    print(f"\nDistinct SMILES (non-null, non-empty): {smiles_count:,}")
    print(f"  This includes wildcard SMILES patterns!")
    print(f"\nDistinct InChIKeys (non-null, non-empty): {inchikey_count:,}")
    print(f"  This includes 'InChIKey=none', 'none', '-' as distinct values!")

    # Show breakdown
    print("\n" + "=" * 120)
    print("The difference explained:")
    print("=" * 120)

    print(f"""
The script counts:
  - SMILES: {smiles_count:,} distinct values
    = {non_wildcard:,} valid structures + {wildcard:,} wildcard patterns

  - InChIKeys: {inchikey_count:,} distinct values
    = {valid:,} valid InChIKeys + a few invalid placeholder values

The 185k records with wildcard SMILES have ~{wildcard:,} distinct SMILES patterns,
but they mostly share the same few invalid InChIKey values ('InChIKey=none', 'none', etc.)
So SMILES shows more "diversity" in the wildcards than InChIKey does.
    """)

    print("=" * 120)


if __name__ == "__main__":
    compare_smiles_inchikey()
