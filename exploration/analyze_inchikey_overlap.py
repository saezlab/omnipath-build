"""
Analyze InChIKey overlap between metabolite databases.

This script calculates how many compounds are shared between HMDB, LipidMaps,
SwissLipids, and RAMP based on InChIKey matches.
"""

import duckdb
from typing import Set, Dict

__all__ = [
    'HMDB_PATH',
    'LIPIDMAPS_PATH',
    'RAMP_PATH',
    'SWISSLIPIDS_PATH',
    'calculate_overlap',
    'get_inchikeys',
    'main',
]

# Define paths to parquet files
HMDB_PATH = "../databases/metabo/bronze/data/hmdb/compounds_for_metabo/20250904_125751.parquet"
LIPIDMAPS_PATH = "../databases/metabo/bronze/data/lipidmaps/lipidmaps_lipids/20250826_135514.parquet"
SWISSLIPIDS_PATH = "../databases/metabo/bronze/data/swisslipids/swisslipids_lipids/20250826_135637.parquet"
RAMP_PATH = "../databases/metabo/bronze/data/ramp/ramp_omnipathmetabo/20250909_101107.parquet"


def get_inchikeys(source: str, path: str, column: str = "inchikey") -> Set[str]:
    """Get all non-null InChIKeys from a source."""
    query = f"""
    SELECT DISTINCT {column}
    FROM '{path}'
    WHERE {column} IS NOT NULL
      AND {column} != ''
    """
    result = duckdb.sql(query).fetchall()
    inchikeys = {row[0] for row in result}
    print(f"{source}: {len(inchikeys):,} unique InChIKeys")
    return inchikeys


def calculate_overlap(set1: Set[str], set2: Set[str], name1: str, name2: str):
    """Calculate and print overlap statistics between two sets."""
    overlap = set1 & set2
    only_1 = set1 - set2
    only_2 = set2 - set1

    pct_1 = (len(overlap) / len(set1) * 100) if len(set1) > 0 else 0
    pct_2 = (len(overlap) / len(set2) * 100) if len(set2) > 0 else 0

    print(f"\n{name1} ∩ {name2}:")
    print(f"  Shared: {len(overlap):,} compounds")
    print(f"  Only in {name1}: {len(only_1):,} ({100 - pct_1:.1f}%)")
    print(f"  Only in {name2}: {len(only_2):,} ({100 - pct_2:.1f}%)")
    print(f"  {name1} coverage: {pct_1:.1f}%")
    print(f"  {name2} coverage: {pct_2:.1f}%")

    return overlap


def main():
    """Analyze InChIKey overlap across all databases."""
    print("=" * 80)
    print("InChIKey Overlap Analysis - Metabolite Databases")
    print("=" * 80)

    print("\n1. Loading InChIKeys from all sources...")
    print("-" * 80)

    hmdb_keys = get_inchikeys("HMDB", HMDB_PATH)
    lipidmaps_keys = get_inchikeys("LipidMaps", LIPIDMAPS_PATH)
    swisslipids_keys = get_inchikeys("SwissLipids", SWISSLIPIDS_PATH)
    ramp_keys = get_inchikeys("RAMP", RAMP_PATH, column="inchi_key")

    total_unique = len(hmdb_keys | lipidmaps_keys | swisslipids_keys | ramp_keys)
    print(f"\nTotal unique InChIKeys across all sources: {total_unique:,}")

    print("\n" + "=" * 80)
    print("2. Pairwise Overlaps")
    print("=" * 80)

    # HMDB vs others
    calculate_overlap(hmdb_keys, lipidmaps_keys, "HMDB", "LipidMaps")
    calculate_overlap(hmdb_keys, swisslipids_keys, "HMDB", "SwissLipids")
    calculate_overlap(hmdb_keys, ramp_keys, "HMDB", "RAMP")

    # LipidMaps vs others
    calculate_overlap(lipidmaps_keys, swisslipids_keys, "LipidMaps", "SwissLipids")
    calculate_overlap(lipidmaps_keys, ramp_keys, "LipidMaps", "RAMP")

    # SwissLipids vs RAMP
    calculate_overlap(swisslipids_keys, ramp_keys, "SwissLipids", "RAMP")

    print("\n" + "=" * 80)
    print("3. Multi-way Overlaps")
    print("=" * 80)

    # All four
    all_four = hmdb_keys & lipidmaps_keys & swisslipids_keys & ramp_keys
    print(f"\nPresent in all 4 databases: {len(all_four):,}")

    # Any three
    hmdb_lipid_swiss = (hmdb_keys & lipidmaps_keys & swisslipids_keys) - ramp_keys
    hmdb_lipid_ramp = (hmdb_keys & lipidmaps_keys & ramp_keys) - swisslipids_keys
    hmdb_swiss_ramp = (hmdb_keys & swisslipids_keys & ramp_keys) - lipidmaps_keys
    lipid_swiss_ramp = (lipidmaps_keys & swisslipids_keys & ramp_keys) - hmdb_keys

    print(f"Present in exactly 3 databases:")
    print(f"  HMDB + LipidMaps + SwissLipids (not RAMP): {len(hmdb_lipid_swiss):,}")
    print(f"  HMDB + LipidMaps + RAMP (not SwissLipids): {len(hmdb_lipid_ramp):,}")
    print(f"  HMDB + SwissLipids + RAMP (not LipidMaps): {len(hmdb_swiss_ramp):,}")
    print(f"  LipidMaps + SwissLipids + RAMP (not HMDB): {len(lipid_swiss_ramp):,}")

    # Unique to each
    hmdb_only = hmdb_keys - lipidmaps_keys - swisslipids_keys - ramp_keys
    lipid_only = lipidmaps_keys - hmdb_keys - swisslipids_keys - ramp_keys
    swiss_only = swisslipids_keys - hmdb_keys - lipidmaps_keys - ramp_keys
    ramp_only = ramp_keys - hmdb_keys - lipidmaps_keys - swisslipids_keys

    print(f"\nUnique to each database:")
    print(f"  Only in HMDB: {len(hmdb_only):,} ({len(hmdb_only)/len(hmdb_keys)*100:.1f}%)")
    print(f"  Only in LipidMaps: {len(lipid_only):,} ({len(lipid_only)/len(lipidmaps_keys)*100:.1f}%)")
    print(f"  Only in SwissLipids: {len(swiss_only):,} ({len(swiss_only)/len(swisslipids_keys)*100:.1f}%)")
    print(f"  Only in RAMP: {len(ramp_only):,} ({len(ramp_only)/len(ramp_keys)*100:.1f}%)")

    print("\n" + "=" * 80)
    print("4. Venn Diagram Summary")
    print("=" * 80)
    print(f"""
    Total unique compounds: {total_unique:,}

    Database sizes:
    - HMDB:        {len(hmdb_keys):>8,} ({len(hmdb_keys)/total_unique*100:>5.1f}% of total)
    - LipidMaps:   {len(lipidmaps_keys):>8,} ({len(lipidmaps_keys)/total_unique*100:>5.1f}% of total)
    - SwissLipids: {len(swisslipids_keys):>8,} ({len(swisslipids_keys)/total_unique*100:>5.1f}% of total)
    - RAMP:        {len(ramp_keys):>8,} ({len(ramp_keys)/total_unique*100:>5.1f}% of total)

    Coverage:
    - In 4 databases: {len(all_four):>8,} ({len(all_four)/total_unique*100:>5.1f}%)
    - In 3 databases: {len(hmdb_lipid_swiss) + len(hmdb_lipid_ramp) + len(hmdb_swiss_ramp) + len(lipid_swiss_ramp):>8,} ({(len(hmdb_lipid_swiss) + len(hmdb_lipid_ramp) + len(hmdb_swiss_ramp) + len(lipid_swiss_ramp))/total_unique*100:>5.1f}%)
    - In 1 database:  {len(hmdb_only) + len(lipid_only) + len(swiss_only) + len(ramp_only):>8,} ({(len(hmdb_only) + len(lipid_only) + len(swiss_only) + len(ramp_only))/total_unique*100:>5.1f}%)
    """)

    print("=" * 80)


if __name__ == "__main__":
    main()
