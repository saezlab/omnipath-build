"""
Analyze ID coverage across metabolite databases in bronze layer.

This script provides an overview of what percentage of compounds have each type of ID
across HMDB, LipidMaps, SwissLipids, and RAMP datasets.
"""

import duckdb

__all__ = [
    'HMDB_PATH',
    'LIPIDMAPS_PATH',
    'RAMP_PATH',
    'SWISSLIPIDS_PATH',
    'analyze_hmdb',
    'analyze_lipidmaps',
    'analyze_ramp',
    'analyze_swisslipids',
    'main',
]

# Define paths to parquet files
HMDB_PATH = "../databases/metabo/bronze/data/hmdb/compounds_for_metabo/20250904_125751.parquet"
LIPIDMAPS_PATH = "../databases/metabo/bronze/data/lipidmaps/lipidmaps_lipids/20250826_135514.parquet"
SWISSLIPIDS_PATH = "../databases/metabo/bronze/data/swisslipids/swisslipids_lipids/20250826_135637.parquet"
RAMP_PATH = "../databases/metabo/bronze/data/ramp/ramp_omnipathmetabo/20250909_101107.parquet"


def analyze_hmdb():
    """Analyze ID coverage in HMDB dataset."""
    query = f"""
    SELECT
      'HMDB' as source,
      COUNT(*) as total_compounds,
      ROUND(100.0 * COUNT(chebi_id) FILTER (WHERE chebi_id IS NOT NULL AND chebi_id != '') / COUNT(*), 2) as chebi_pct,
      ROUND(100.0 * COUNT(pubchem_compound_id) FILTER (WHERE pubchem_compound_id IS NOT NULL AND pubchem_compound_id != '') / COUNT(*), 2) as pubchem_pct,
      ROUND(100.0 * COUNT(kegg_id) FILTER (WHERE kegg_id IS NOT NULL AND kegg_id != '') / COUNT(*), 2) as kegg_pct,
      ROUND(100.0 * COUNT(drugbank_id) FILTER (WHERE drugbank_id IS NOT NULL AND drugbank_id != '') / COUNT(*), 2) as drugbank_pct,
      ROUND(100.0 * COUNT(cas_registry_number) FILTER (WHERE cas_registry_number IS NOT NULL AND cas_registry_number != '') / COUNT(*), 2) as cas_pct,
      ROUND(100.0 * COUNT(accession) FILTER (WHERE accession IS NOT NULL AND accession != '') / COUNT(*), 2) as hmdb_pct,
      ROUND(100.0 * COUNT(inchikey) FILTER (WHERE inchikey IS NOT NULL AND inchikey != '') / COUNT(*), 2) as inchikey_pct,
      ROUND(100.0 * COUNT(smiles) FILTER (WHERE smiles IS NOT NULL AND smiles != '') / COUNT(*), 2) as smiles_pct
    FROM '{HMDB_PATH}'
    """
    return duckdb.sql(query).df()


def analyze_lipidmaps():
    """Analyze ID coverage in LipidMaps dataset."""
    query = f"""
    SELECT
      'LipidMaps' as source,
      COUNT(*) as total_compounds,
      ROUND(100.0 * COUNT(chebi) FILTER (WHERE chebi IS NOT NULL AND chebi != '') / COUNT(*), 2) as chebi_pct,
      ROUND(100.0 * COUNT(pubchem) FILTER (WHERE pubchem IS NOT NULL AND pubchem != '') / COUNT(*), 2) as pubchem_pct,
      ROUND(100.0 * COUNT(id) FILTER (WHERE id IS NOT NULL AND id != '') / COUNT(*), 2) as lipidmaps_pct,
      ROUND(100.0 * COUNT(inchikey) FILTER (WHERE inchikey IS NOT NULL AND inchikey != '') / COUNT(*), 2) as inchikey_pct,
      ROUND(100.0 * COUNT(smiles) FILTER (WHERE smiles IS NOT NULL AND smiles != '') / COUNT(*), 2) as smiles_pct
    FROM '{LIPIDMAPS_PATH}'
    """
    return duckdb.sql(query).df()


def analyze_swisslipids():
    """Analyze ID coverage in SwissLipids dataset."""
    query = f"""
    SELECT
      'SwissLipids' as source,
      COUNT(*) as total_compounds,
      ROUND(100.0 * COUNT(chebi) FILTER (WHERE chebi IS NOT NULL AND chebi != '') / COUNT(*), 2) as chebi_pct,
      ROUND(100.0 * COUNT(lipidmaps) FILTER (WHERE lipidmaps IS NOT NULL AND lipidmaps != '') / COUNT(*), 2) as lipidmaps_pct,
      ROUND(100.0 * COUNT(hmdb) FILTER (WHERE hmdb IS NOT NULL AND hmdb != '') / COUNT(*), 2) as hmdb_pct,
      ROUND(100.0 * COUNT(metanetx) FILTER (WHERE metanetx IS NOT NULL AND metanetx != '') / COUNT(*), 2) as metanetx_pct,
      ROUND(100.0 * COUNT(id) FILTER (WHERE id IS NOT NULL AND id != '') / COUNT(*), 2) as swisslipids_pct,
      ROUND(100.0 * COUNT(inchikey) FILTER (WHERE inchikey IS NOT NULL AND inchikey != '') / COUNT(*), 2) as inchikey_pct,
      ROUND(100.0 * COUNT(smiles) FILTER (WHERE smiles IS NOT NULL AND smiles != '') / COUNT(*), 2) as smiles_pct
    FROM '{SWISSLIPIDS_PATH}'
    """
    return duckdb.sql(query).df()


def analyze_ramp():
    """Analyze ID coverage in RAMP dataset.

    Note: RAMP stores cross-references in the 'sources' column as comma-separated values
    with prefixes like 'hmdb:', 'chebi:', 'kegg:', 'pubchem:', 'CAS:', etc.
    """
    query = f"""
    SELECT
      'RAMP' as source,
      COUNT(*) as total_compounds,
      ROUND(100.0 * COUNT(inchi_key) FILTER (WHERE inchi_key IS NOT NULL AND inchi_key != '') / COUNT(*), 2) as inchikey_pct,
      ROUND(100.0 * COUNT(iso_smiles) FILTER (WHERE iso_smiles IS NOT NULL AND iso_smiles != '') / COUNT(*), 2) as smiles_pct,
      ROUND(100.0 * COUNT(chem_source_id) FILTER (WHERE chem_source_id IS NOT NULL AND chem_source_id != '') / COUNT(*), 2) as source_id_pct,
      ROUND(100.0 * COUNT(*) FILTER (WHERE sources LIKE '%hmdb:%') / COUNT(*), 2) as hmdb_pct,
      ROUND(100.0 * COUNT(*) FILTER (WHERE sources LIKE '%chebi:%') / COUNT(*), 2) as chebi_pct,
      ROUND(100.0 * COUNT(*) FILTER (WHERE sources LIKE '%kegg:%') / COUNT(*), 2) as kegg_pct,
      ROUND(100.0 * COUNT(*) FILTER (WHERE sources LIKE '%pubchem:%') / COUNT(*), 2) as pubchem_pct,
      ROUND(100.0 * COUNT(*) FILTER (WHERE sources LIKE '%CAS:%') / COUNT(*), 2) as cas_pct,
      ROUND(100.0 * COUNT(*) FILTER (WHERE sources LIKE '%chemspider:%') / COUNT(*), 2) as chemspider_pct,
      ROUND(100.0 * COUNT(*) FILTER (WHERE sources LIKE '%wikidata:%') / COUNT(*), 2) as wikidata_pct
    FROM '{RAMP_PATH}'
    """
    return duckdb.sql(query).df()


def main():
    """Run analysis on all datasets and print results."""
    print("=" * 80)
    print("ID Coverage Analysis - Metabolite Databases")
    print("=" * 80)

    print("\nHMDB Dataset:")
    print("-" * 80)
    hmdb_df = analyze_hmdb()
    print(hmdb_df.to_string(index=False))

    print("\n\nLipidMaps Dataset:")
    print("-" * 80)
    lipidmaps_df = analyze_lipidmaps()
    print(lipidmaps_df.to_string(index=False))

    print("\n\nSwissLipids Dataset:")
    print("-" * 80)
    swisslipids_df = analyze_swisslipids()
    print(swisslipids_df.to_string(index=False))

    print("\n\nRAMP Dataset:")
    print("-" * 80)
    ramp_df = analyze_ramp()
    print(ramp_df.to_string(index=False))

    print("\n" + "=" * 80)


if __name__ == "__main__":
    main()
