"""
Check if multiple SMILES map to the same InChIKey in RAMP data.
"""

import duckdb

__all__ = [
    'RAMP_PATH',
    'check_smiles_to_inchikey_mapping',
]

RAMP_PATH = "../omnipath_build/databases/metabo/bronze/data/ramp/ramp_omnipathmetabo/20250909_101107.parquet"

def check_smiles_to_inchikey_mapping():
    """Investigate if multiple SMILES share the same InChIKey."""

    print("=" * 120)
    print("RAMP: Checking SMILES to InChIKey Mapping")
    print("=" * 120)

    # Count how many InChIKeys have multiple SMILES
    print("\nInChIKeys with multiple SMILES:")
    print("-" * 120)

    multi_smiles_query = f"""
    SELECT
        inchi_key,
        COUNT(DISTINCT iso_smiles) as distinct_smiles_count,
        COUNT(*) as record_count,
        STRING_AGG(DISTINCT iso_smiles, ' | ') as smiles_list
    FROM '{RAMP_PATH}'
    WHERE inchi_key IS NOT NULL AND inchi_key != ''
      AND iso_smiles IS NOT NULL AND iso_smiles != ''
      AND iso_smiles NOT LIKE '%[*]%'
      AND iso_smiles NOT LIKE '%[R]%'
    GROUP BY inchi_key
    HAVING COUNT(DISTINCT iso_smiles) > 1
    ORDER BY distinct_smiles_count DESC, record_count DESC
    LIMIT 20
    """

    result = duckdb.sql(multi_smiles_query)
    rows = result.fetchall()

    if len(rows) == 0:
        print("\nNo InChIKeys found with multiple SMILES!")
        print("This suggests a different reason for the SMILES/InChIKey count difference.")
    else:
        print(f"\nFound {len(rows)} InChIKeys (showing top 20) with multiple SMILES:")
        print()
        for idx, row in enumerate(rows, 1):
            inchikey, smiles_count, record_count, smiles_list = row
            print(f"\n{idx}. InChIKey: {inchikey}")
            print(f"   Distinct SMILES: {smiles_count}")
            print(f"   Total records: {record_count}")
            if len(smiles_list) > 200:
                print(f"   SMILES: {smiles_list[:200]}...")
            else:
                print(f"   SMILES: {smiles_list}")

    # Get total count of such InChIKeys
    count_query = f"""
    SELECT COUNT(*) as inchikeys_with_multiple_smiles
    FROM (
        SELECT inchi_key
        FROM '{RAMP_PATH}'
        WHERE inchi_key IS NOT NULL AND inchi_key != ''
          AND iso_smiles IS NOT NULL AND iso_smiles != ''
          AND iso_smiles NOT LIKE '%[*]%'
          AND iso_smiles NOT LIKE '%[R]%'
        GROUP BY inchi_key
        HAVING COUNT(DISTINCT iso_smiles) > 1
    )
    """

    total_multi = duckdb.sql(count_query).fetchone()[0]
    print(f"\n" + "=" * 120)
    print(f"Total InChIKeys with multiple SMILES: {total_multi:,}")

    # Calculate the "redundancy" - how many extra SMILES exist
    redundancy_query = f"""
    SELECT
        SUM(distinct_smiles_count - 1) as extra_smiles
    FROM (
        SELECT
            inchi_key,
            COUNT(DISTINCT iso_smiles) as distinct_smiles_count
        FROM '{RAMP_PATH}'
        WHERE inchi_key IS NOT NULL AND inchi_key != ''
          AND iso_smiles IS NOT NULL AND iso_smiles != ''
          AND iso_smiles NOT LIKE '%[*]%'
          AND iso_smiles NOT LIKE '%[R]%'
        GROUP BY inchi_key
        HAVING COUNT(DISTINCT iso_smiles) > 1
    )
    """

    extra_smiles = duckdb.sql(redundancy_query).fetchone()[0]
    print(f"Extra SMILES (beyond 1 per InChIKey): {extra_smiles:,}")

    # Count SMILES without InChIKeys
    print("\n" + "=" * 120)
    print("SMILES without valid InChIKeys:")
    print("-" * 120)

    no_inchikey_query = f"""
    SELECT COUNT(DISTINCT iso_smiles) as distinct_smiles_no_inchikey
    FROM '{RAMP_PATH}'
    WHERE (inchi_key IS NULL OR inchi_key = '')
      AND iso_smiles IS NOT NULL
      AND iso_smiles != ''
      AND iso_smiles NOT LIKE '%[*]%'
      AND iso_smiles NOT LIKE '%[R]%'
    """

    no_inchikey = duckdb.sql(no_inchikey_query).fetchone()[0]
    print(f"\nDistinct SMILES without valid InChIKey: {no_inchikey:,}")

    # Final accounting
    print("\n" + "=" * 120)
    print("Accounting for the difference:")
    print("=" * 120)

    # Get the actual counts
    total_query = f"""
    SELECT
        COUNT(DISTINCT iso_smiles) as distinct_smiles,
        COUNT(DISTINCT inchi_key) as distinct_inchikeys
    FROM '{RAMP_PATH}'
    WHERE iso_smiles IS NOT NULL AND iso_smiles != ''
      AND iso_smiles NOT LIKE '%[*]%' AND iso_smiles NOT LIKE '%[R]%'
      AND inchi_key IS NOT NULL AND inchi_key != ''
    """

    distinct_smiles, distinct_inchikeys = duckdb.sql(total_query).fetchone()

    print(f"""
Distinct non-wildcard SMILES with valid InChIKey: {distinct_smiles:,}
Distinct valid InChIKeys: {distinct_inchikeys:,}
Difference: {distinct_smiles - distinct_inchikeys:,}

This difference of {distinct_smiles - distinct_inchikeys:,} comes from:
  - {extra_smiles:,} extra SMILES due to multiple SMILES per InChIKey
    (e.g., stereoisomers, tautomers, different notations)

Expected if hypothesis is correct: ~{distinct_smiles - distinct_inchikeys:,}
Actual extra SMILES found: {extra_smiles:,}

Match: {'YES - hypothesis confirmed!' if abs(extra_smiles - (distinct_smiles - distinct_inchikeys)) < 100 else 'NO - other factors involved'}
    """)

    # Show examples of why SMILES differ for same InChIKey
    print("=" * 120)
    print("Examples of different SMILES for same InChIKey:")
    print("=" * 120)

    examples_query = f"""
    WITH multi_smiles_inchikeys AS (
        SELECT inchi_key
        FROM '{RAMP_PATH}'
        WHERE inchi_key IS NOT NULL AND inchi_key != ''
          AND iso_smiles IS NOT NULL AND iso_smiles != ''
          AND iso_smiles NOT LIKE '%[*]%'
        GROUP BY inchi_key
        HAVING COUNT(DISTINCT iso_smiles) > 1
        LIMIT 3
    )
    SELECT
        t.inchi_key,
        t.iso_smiles,
        t.common_name,
        t.ramp_id
    FROM '{RAMP_PATH}' t
    WHERE t.inchi_key IN (SELECT inchi_key FROM multi_smiles_inchikeys)
    ORDER BY t.inchi_key, t.iso_smiles
    """

    result = duckdb.sql(examples_query)
    current_inchikey = None
    example_num = 0
    for row in result.fetchall():
        inchikey, smiles, name, ramp_id = row
        if inchikey != current_inchikey:
            example_num += 1
            current_inchikey = inchikey
            print(f"\nExample {example_num}: InChIKey = {inchikey}")
            variant_num = 0
        variant_num += 1
        smiles_preview = smiles[:80] if smiles else 'None'
        print(f"  Variant {variant_num}:")
        print(f"    SMILES: {smiles_preview}...")
        print(f"    Name: {name}")
        print(f"    ID: {ramp_id}")

    print("\n" + "=" * 120)


if __name__ == "__main__":
    check_smiles_to_inchikey_mapping()
