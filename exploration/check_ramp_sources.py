"""
Check which original sources contribute to records with multiple SMILES per InChIKey.
"""

import duckdb

__all__ = [
    'RAMP_PATH',
    'check_sources_for_duplicates',
]

RAMP_PATH = "../omnipath_build/databases/metabo/bronze/data/ramp/ramp_omnipathmetabo/20250909_101107.parquet"

def check_sources_for_duplicates():
    """Identify sources contributing to multiple SMILES per InChIKey."""

    print("=" * 120)
    print("RAMP: Source Analysis for Multiple SMILES per InChIKey")
    print("=" * 120)

    # First, identify InChIKeys with multiple SMILES
    print("\nIdentifying InChIKeys with multiple SMILES...")

    # Check which sources contribute to the problematic records
    sources_query = f"""
    WITH multi_smiles_inchikeys AS (
        SELECT inchi_key
        FROM '{RAMP_PATH}'
        WHERE inchi_key IS NOT NULL AND inchi_key != ''
          AND iso_smiles IS NOT NULL AND iso_smiles != ''
          AND iso_smiles NOT LIKE '%[*]%'
          AND iso_smiles NOT LIKE '%[R]%'
        GROUP BY inchi_key
        HAVING COUNT(DISTINCT iso_smiles) > 1
    )
    SELECT
        chem_data_source,
        COUNT(*) as record_count,
        COUNT(DISTINCT inchi_key) as distinct_inchikeys,
        COUNT(DISTINCT iso_smiles) as distinct_smiles
    FROM '{RAMP_PATH}'
    WHERE inchi_key IN (SELECT inchi_key FROM multi_smiles_inchikeys)
    GROUP BY chem_data_source
    ORDER BY record_count DESC
    """

    result = duckdb.sql(sources_query)
    print("\nSources contributing to records with multiple SMILES per InChIKey:")
    print("-" * 120)
    print(f"{'Source':<30} {'Records':>15} {'Distinct InChIKeys':>20} {'Distinct SMILES':>20}")
    print("-" * 120)

    for row in result.fetchall():
        source, count, inchikeys, smiles = row
        print(f"{source:<30} {count:>15,} {inchikeys:>20,} {smiles:>20,}")

    # Get total for comparison
    total_query = f"""
    WITH multi_smiles_inchikeys AS (
        SELECT inchi_key
        FROM '{RAMP_PATH}'
        WHERE inchi_key IS NOT NULL AND inchi_key != ''
          AND iso_smiles IS NOT NULL AND iso_smiles != ''
          AND iso_smiles NOT LIKE '%[*]%'
          AND iso_smiles NOT LIKE '%[R]%'
        GROUP BY inchi_key
        HAVING COUNT(DISTINCT iso_smiles) > 1
    )
    SELECT COUNT(*) as total_records
    FROM '{RAMP_PATH}'
    WHERE inchi_key IN (SELECT inchi_key FROM multi_smiles_inchikeys)
    """

    total = duckdb.sql(total_query).fetchone()[0]
    print("-" * 120)
    print(f"{'TOTAL':<30} {total:>15,}")

    # Compare to overall source distribution
    print("\n" + "=" * 120)
    print("Overall source distribution in RAMP (for context):")
    print("-" * 120)

    overall_query = f"""
    SELECT
        chem_data_source,
        COUNT(*) as record_count,
        COUNT(DISTINCT inchi_key) as distinct_inchikeys,
        COUNT(DISTINCT iso_smiles) as distinct_smiles
    FROM '{RAMP_PATH}'
    GROUP BY chem_data_source
    ORDER BY record_count DESC
    """

    result = duckdb.sql(overall_query)
    print(f"{'Source':<30} {'Total Records':>15} {'Distinct InChIKeys':>20} {'Distinct SMILES':>20}")
    print("-" * 120)

    for row in result.fetchall():
        source, count, inchikeys, smiles = row
        print(f"{source:<30} {count:>15,} {inchikeys:>20,} {smiles:>20,}")

    # Show specific examples from each source
    print("\n" + "=" * 120)
    print("Examples of multi-SMILES records by source:")
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
        LIMIT 5
    ),
    example_records AS (
        SELECT
            chem_data_source,
            inchi_key,
            iso_smiles,
            common_name,
            ramp_id
        FROM '{RAMP_PATH}'
        WHERE inchi_key IN (SELECT inchi_key FROM multi_smiles_inchikeys)
    )
    SELECT
        chem_data_source,
        inchi_key,
        COUNT(DISTINCT iso_smiles) as smiles_variants,
        STRING_AGG(DISTINCT iso_smiles, ' | ') as smiles_list,
        MIN(common_name) as example_name
    FROM example_records
    GROUP BY chem_data_source, inchi_key
    ORDER BY chem_data_source, smiles_variants DESC
    LIMIT 20
    """

    result = duckdb.sql(examples_query)
    current_source = None
    for row in result.fetchall():
        source, inchikey, variants, smiles_list, name = row
        if source != current_source:
            current_source = source
            print(f"\n{source}:")
            print("-" * 120)

        smiles_preview = smiles_list[:150] if len(smiles_list) > 150 else smiles_list
        print(f"  InChIKey: {inchikey}")
        print(f"    Name: {name}")
        print(f"    SMILES variants: {variants}")
        print(f"    SMILES: {smiles_preview}...")
        print()

    # Check if same InChIKey comes from multiple sources with different SMILES
    print("=" * 120)
    print("Do different sources provide different SMILES for the same InChIKey?")
    print("=" * 120)

    cross_source_query = f"""
    WITH multi_smiles_inchikeys AS (
        SELECT inchi_key
        FROM '{RAMP_PATH}'
        WHERE inchi_key IS NOT NULL AND inchi_key != ''
          AND iso_smiles IS NOT NULL AND iso_smiles != ''
          AND iso_smiles NOT LIKE '%[*]%'
        GROUP BY inchi_key
        HAVING COUNT(DISTINCT iso_smiles) > 1
        LIMIT 10
    )
    SELECT
        inchi_key,
        COUNT(DISTINCT chem_data_source) as num_sources,
        COUNT(DISTINCT iso_smiles) as num_smiles,
        STRING_AGG(DISTINCT chem_data_source, ', ') as sources,
        MIN(common_name) as name
    FROM '{RAMP_PATH}'
    WHERE inchi_key IN (SELECT inchi_key FROM multi_smiles_inchikeys)
    GROUP BY inchi_key
    ORDER BY num_sources DESC, num_smiles DESC
    LIMIT 10
    """

    result = duckdb.sql(cross_source_query)
    print(f"\n{'InChIKey':<35} {'Sources':>10} {'SMILES':>10} {'Source List':<40}")
    print("-" * 120)
    for row in result.fetchall():
        inchikey, num_sources, num_smiles, sources, name = row
        sources_short = sources[:37] + "..." if len(sources) > 40 else sources
        print(f"{inchikey:<35} {num_sources:>10} {num_smiles:>10} {sources_short:<40}")
        print(f"  Name: {name}")

    print("\n" + "=" * 120)


if __name__ == "__main__":
    check_sources_for_duplicates()
