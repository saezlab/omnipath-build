"""
Count distinct SMILES and InChIKeys per metabolite database source.

This script analyzes each database to count how many unique SMILES and InChIKeys
they contain, helping identify data quality and coverage metrics.
"""

import duckdb
from typing import Dict, Tuple

__all__ = [
    'HMDB_PATH',
    'LIPIDMAPS_PATH',
    'RAMP_PATH',
    'SWISSLIPIDS_PATH',
    'count_identifiers',
    'main',
]

# Define paths to parquet files
HMDB_PATH = "../omnipath_build/databases/metabo/bronze/data/hmdb/compounds_for_metabo/20250904_125751.parquet"
LIPIDMAPS_PATH = "../omnipath_build/databases/metabo/bronze/data/lipidmaps/lipidmaps_lipids/20250826_135514.parquet"
SWISSLIPIDS_PATH = "../omnipath_build/databases/metabo/bronze/data/swisslipids/swisslipids_lipids/20250826_135637.parquet"
RAMP_PATH = "../omnipath_build/databases/metabo/bronze/data/ramp/ramp_omnipathmetabo/20250909_101107.parquet"


def count_identifiers(
    source: str,
    path: str,
    smiles_col: str = "smiles",
    inchikey_col: str = "inchikey"
) -> Tuple[int, int, int]:
    """
    Count distinct SMILES and InChIKeys in a source.

    Returns:
        Tuple of (total_records, distinct_smiles, distinct_inchikeys)
    """
    # Count total records
    total_query = f"SELECT COUNT(*) FROM '{path}'"
    total = duckdb.sql(total_query).fetchone()[0]

    # Count distinct SMILES (non-null, non-empty)
    smiles_query = f"""
    SELECT COUNT(DISTINCT {smiles_col})
    FROM '{path}'
    WHERE {smiles_col} IS NOT NULL
      AND {smiles_col} != ''
    """
    distinct_smiles = duckdb.sql(smiles_query).fetchone()[0]

    # Count distinct InChIKeys (non-null, non-empty)
    inchikey_query = f"""
    SELECT COUNT(DISTINCT {inchikey_col})
    FROM '{path}'
    WHERE {inchikey_col} IS NOT NULL
      AND {inchikey_col} != ''
    """
    distinct_inchikeys = duckdb.sql(inchikey_query).fetchone()[0]

    return total, distinct_smiles, distinct_inchikeys


def main():
    """Analyze identifier counts across all databases."""
    print("=" * 80)
    print("Distinct SMILES and InChIKey Count Analysis")
    print("=" * 80)
    print()

    results = {}

    # HMDB
    print("Analyzing HMDB...")
    results['HMDB'] = count_identifiers("HMDB", HMDB_PATH)

    # LipidMaps
    print("Analyzing LipidMaps...")
    results['LipidMaps'] = count_identifiers("LipidMaps", LIPIDMAPS_PATH)

    # SwissLipids
    print("Analyzing SwissLipids...")
    results['SwissLipids'] = count_identifiers("SwissLipids", SWISSLIPIDS_PATH)

    # RAMP (different column names)
    print("Analyzing RAMP...")
    results['RAMP'] = count_identifiers("RAMP", RAMP_PATH,
                                       smiles_col="iso_smiles",
                                       inchikey_col="inchi_key")

    print()
    print("=" * 80)
    print("Summary")
    print("=" * 80)
    print()
    print(f"{'Database':<15} {'Total Records':>15} {'Distinct SMILES':>20} {'Distinct InChIKeys':>20}")
    print("-" * 80)

    for source, (total, smiles, inchikeys) in results.items():
        print(f"{source:<15} {total:>15,} {smiles:>20,} {inchikeys:>20,}")

    print()
    print("=" * 80)
    print("Coverage Rates")
    print("=" * 80)
    print()
    print(f"{'Database':<15} {'SMILES Coverage':>20} {'InChIKey Coverage':>20}")
    print("-" * 80)

    for source, (total, smiles, inchikeys) in results.items():
        smiles_pct = (smiles / total * 100) if total > 0 else 0
        inchikey_pct = (inchikeys / total * 100) if total > 0 else 0
        print(f"{source:<15} {smiles_pct:>19.1f}% {inchikey_pct:>19.1f}%")

    print()
    print("=" * 80)


if __name__ == "__main__":
    main()
