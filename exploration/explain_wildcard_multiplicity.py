"""
Explain why 185k records have only 390 distinct wildcard SMILES patterns.
"""

import duckdb

__all__ = [
    'SWISSLIPIDS_PATH',
    'explain_multiplicity',
]

SWISSLIPIDS_PATH = "../omnipath_build/databases/metabo/bronze/data/swisslipids/swisslipids_lipids/20250826_135637.parquet"

def explain_multiplicity():
    """Show why multiple records share the same wildcard SMILES."""

    print("=" * 120)
    print("Why 185k records have only 390 wildcard SMILES patterns")
    print("=" * 120)

    # Show top wildcard patterns and how many records use each
    print("\nTop 10 wildcard SMILES patterns by record count:")
    print("-" * 120)

    pattern_query = f"""
    SELECT
        smiles,
        COUNT(*) as record_count,
        MIN(name) as example_name_1,
        MAX(name) as example_name_2,
        COUNT(DISTINCT name) as distinct_names,
        COUNT(DISTINCT id) as distinct_ids,
        COUNT(DISTINCT formula) as distinct_formulas
    FROM '{SWISSLIPIDS_PATH}'
    WHERE smiles LIKE '%[*]%' OR smiles LIKE '%[R]%'
    GROUP BY smiles
    ORDER BY record_count DESC
    LIMIT 10
    """

    result = duckdb.sql(pattern_query)
    for idx, row in enumerate(result.fetchall(), 1):
        smiles, count, name1, name2, distinct_names, distinct_ids, distinct_formulas = row
        print(f"\nPattern #{idx}:")
        print(f"  SMILES: {smiles}")
        print(f"  Used in {count:,} records")
        print(f"  Distinct IDs: {distinct_ids:,}")
        print(f"  Distinct names: {distinct_names:,}")
        print(f"  Distinct formulas: {distinct_formulas:,}")
        print(f"  Example 1: {name1}")
        if name1 != name2:
            print(f"  Example 2: {name2}")

    # Look at specific examples for one pattern
    print("\n" + "=" * 120)
    print("Detailed look at records sharing the same wildcard SMILES pattern:")
    print("=" * 120)

    examples_query = f"""
    WITH top_pattern AS (
        SELECT smiles
        FROM '{SWISSLIPIDS_PATH}'
        WHERE smiles LIKE '%[*]%' OR smiles LIKE '%[R]%'
        GROUP BY smiles
        ORDER BY COUNT(*) DESC
        LIMIT 1
    )
    SELECT
        id,
        name,
        abbreviation,
        level,
        formula,
        smiles
    FROM '{SWISSLIPIDS_PATH}'
    WHERE smiles IN (SELECT smiles FROM top_pattern)
    LIMIT 15
    """

    result = duckdb.sql(examples_query)
    print(f"\n{'ID':<20} {'Name':<50} {'Abbreviation':<20} {'Formula':<15}")
    print("-" * 120)
    for row in result.fetchall():
        id_, name, abbrev, level, formula, smiles = row
        name_short = name[:47] + "..." if len(name) > 50 else name
        abbrev_short = (abbrev[:17] + "...") if abbrev and len(abbrev) > 20 else (abbrev or "")
        print(f"{id_:<20} {name_short:<50} {abbrev_short:<20} {formula:<15}")

    # Show what varies between records with same SMILES
    print("\n" + "=" * 120)
    print("What differs between records with the same wildcard SMILES?")
    print("=" * 120)

    variance_query = f"""
    WITH pattern_records AS (
        SELECT
            smiles,
            COUNT(*) as count
        FROM '{SWISSLIPIDS_PATH}'
        WHERE smiles LIKE '%[*]%' OR smiles LIKE '%[R]%'
        GROUP BY smiles
        HAVING COUNT(*) > 100
        LIMIT 1
    )
    SELECT
        COUNT(DISTINCT id) as distinct_ids,
        COUNT(DISTINCT name) as distinct_names,
        COUNT(DISTINCT abbreviation) as distinct_abbreviations,
        COUNT(DISTINCT level) as distinct_levels,
        COUNT(DISTINCT formula) as distinct_formulas,
        COUNT(DISTINCT lipid_class) as distinct_lipid_classes,
        COUNT(DISTINCT exact_mass) as distinct_masses,
        COUNT(*) as total_records
    FROM '{SWISSLIPIDS_PATH}'
    WHERE smiles IN (SELECT smiles FROM pattern_records)
    """

    result = duckdb.sql(variance_query).fetchone()
    print(f"\nFor the most common wildcard pattern:")
    print(f"  Total records: {result[7]:,}")
    print(f"  Distinct IDs: {result[0]:,}")
    print(f"  Distinct names: {result[1]:,}")
    print(f"  Distinct abbreviations: {result[2]:,}")
    print(f"  Distinct levels: {result[3]:,}")
    print(f"  Distinct formulas: {result[4]:,}")
    print(f"  Distinct lipid classes: {result[5]:,}")
    print(f"  Distinct exact masses: {result[6]:,}")

    print("\n" + "=" * 120)
    print("EXPLANATION:")
    print("=" * 120)
    print("""
Each wildcard SMILES represents a GENERIC LIPID STRUCTURE TEMPLATE.

The same template is reused for many different lipids that share the same
backbone but differ in:
  - Chain lengths (e.g., 10:0, 12:0, 14:0, 16:0, 18:0...)
  - Double bonds (e.g., 16:0, 16:1, 16:2...)
  - Stereochemistry at different positions
  - Different head groups or modifications

For example, the pattern [O-]P([O-])(=O)OCC(CO[*])O[*] represents:
  - A phosphate group attached to a glycerol backbone
  - Where [*] placeholders indicate variable fatty acid chains

This ONE pattern can represent HUNDREDS of specific lipids like:
  - LPA(10:0), LPA(12:0), LPA(14:0), etc. (different chain lengths)
  - Each with a unique SwissLipids ID, name, formula, and mass

So: 185,043 records / 390 patterns ≈ 475 records per pattern on average
    """)

    print("=" * 120)


if __name__ == "__main__":
    explain_multiplicity()
