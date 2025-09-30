"""
Test UniChem API to see what IDs can be retrieved for molecules with only InChIKey.

This script samples molecules that have InChIKey but are missing other IDs,
then queries UniChem to see what cross-references are available.
"""

import duckdb
import requests
import time
from typing import Dict, List, Optional

__all__ = [
    'HMDB_PATH',
    'LIPIDMAPS_PATH',
    'RAMP_PATH',
    'SWISSLIPIDS_PATH',
    'UNICHEM_BASE',
    'analyze_unichem_results',
    'get_hmdb_sample',
    'get_lipidmaps_sample',
    'get_ramp_sample',
    'get_swisslipids_sample',
    'main',
    'query_unichem',
]

# Define paths to parquet files
HMDB_PATH = "omnipath_build/databases/metabo/bronze/data/hmdb/compounds_for_metabo/20250904_125751.parquet"
LIPIDMAPS_PATH = "omnipath_build/databases/metabo/bronze/data/lipidmaps/lipidmaps_lipids/20250826_135514.parquet"
SWISSLIPIDS_PATH = "omnipath_build/databases/metabo/bronze/data/swisslipids/swisslipids_lipids/20250826_135637.parquet"
RAMP_PATH = "omnipath_build/databases/metabo/bronze/data/ramp/ramp_omnipathmetabo/20250909_101107.parquet"

# UniChem API base URL
UNICHEM_BASE = "https://www.ebi.ac.uk/unichem/rest"


def query_unichem(inchikey: str) -> Optional[Dict]:
    """Query UniChem for cross-references using InChIKey.

    Returns dict with available sources or None if not found.
    """
    try:
        # Use the compound search endpoint
        url = f"{UNICHEM_BASE}/inchikey/{inchikey}"
        response = requests.get(url, timeout=10)

        if response.status_code == 200:
            data = response.json()
            # Check if it's an error response
            if isinstance(data, dict) and 'error' in data:
                return None
            return data
        elif response.status_code == 404:
            return None
        else:
            print(f"  Error {response.status_code} for {inchikey}")
            return None
    except Exception as e:
        print(f"  Exception querying {inchikey}: {e}")
        return None


def get_hmdb_sample():
    """Get HMDB molecules with InChIKey but missing ChEBI and PubChem."""
    query = f"""
    SELECT
      accession as hmdb_id,
      inchikey,
      synonyms,
      chebi_id,
      pubchem_compound_id,
      kegg_id,
      drugbank_id
    FROM '{HMDB_PATH}'
    WHERE inchikey IS NOT NULL
      AND inchikey != ''
      AND (chebi_id IS NULL OR chebi_id = '')
      AND (pubchem_compound_id IS NULL OR pubchem_compound_id = '')
    LIMIT 20
    """
    return duckdb.sql(query).df()


def get_lipidmaps_sample():
    """Get LipidMaps molecules with InChIKey but missing ChEBI and PubChem."""
    query = f"""
    SELECT
      id as lipidmaps_id,
      inchikey,
      name,
      chebi,
      pubchem
    FROM '{LIPIDMAPS_PATH}'
    WHERE inchikey IS NOT NULL
      AND inchikey != ''
      AND (chebi IS NULL OR chebi = '')
      AND (pubchem IS NULL OR pubchem = '')
    LIMIT 20
    """
    return duckdb.sql(query).df()


def get_swisslipids_sample():
    """Get SwissLipids molecules with InChIKey but missing ChEBI and HMDB."""
    query = f"""
    SELECT
      id as swisslipids_id,
      inchikey,
      lipidmaps,
      hmdb,
      chebi
    FROM '{SWISSLIPIDS_PATH}'
    WHERE inchikey IS NOT NULL
      AND inchikey != ''
      AND (chebi IS NULL OR chebi = '')
      AND (hmdb IS NULL OR hmdb = '')
    LIMIT 20
    """
    return duckdb.sql(query).df()


def get_ramp_sample():
    """Get RAMP molecules with InChIKey but limited source IDs."""
    query = f"""
    SELECT
      ramp_id,
      common_name,
      inchi_key,
      sources
    FROM '{RAMP_PATH}'
    WHERE inchi_key IS NOT NULL
      AND inchi_key != ''
      AND (sources NOT LIKE '%chebi:%' OR sources NOT LIKE '%pubchem:%')
    LIMIT 20
    """
    return duckdb.sql(query).df()


def analyze_unichem_results(results: Dict) -> Dict[str, List[str]]:
    """Parse UniChem results and organize by source database."""
    sources = {}

    if not results:
        return sources

    # Check if error response
    if isinstance(results, dict) and 'error' in results:
        return sources

    # UniChem returns a list of entries, each with src_id and src_compound_id
    for entry in results:
        src_id = entry.get('src_id')
        src_compound_id = entry.get('src_compound_id')

        # Map src_id to database names (common ones)
        src_map = {
            '1': 'chembl',
            '2': 'drugbank',
            '3': 'pdb',
            '4': 'gtopdb',
            '5': 'pubchem_dotf',
            '6': 'kegg_ligand',
            '7': 'chebi',
            '8': 'nih_ncc',
            '9': 'zinc',
            '10': 'emolecules',
            '11': 'ibm',
            '12': 'atlas',
            '13': 'patents',
            '14': 'fdasrs',
            '15': 'surechembl',
            '17': 'pharmgkb',
            '18': 'hmdb',
            '20': 'selleck',
            '21': 'pubchem_tpharma',
            '22': 'pubchem',
            '23': 'mcule',
            '24': 'nmrshiftdb2',
            '25': 'lincs',
            '26': 'actor',
            '27': 'recon',
            '28': 'molport',
            '31': 'lipidmaps',
            '32': 'swisslipids',
            '34': 'metabolights',
            '37': 'brenda',
        }

        src_name = src_map.get(str(src_id), f'source_{src_id}')

        if src_name not in sources:
            sources[src_name] = []
        sources[src_name].append(src_compound_id)

    return sources


def main():
    """Run UniChem enrichment test."""
    print("=" * 80)
    print("UniChem Enrichment Test")
    print("=" * 80)

    # Test HMDB
    print("\n\nHMDB - Molecules with only InChIKey (missing ChEBI & PubChem):")
    print("-" * 80)
    hmdb_df = get_hmdb_sample()
    print(f"Found {len(hmdb_df)} samples\n")

    enrichable_count = 0
    for idx, row in hmdb_df.iterrows():
        if idx >= 20:  # Test first 20
            break

        synonyms = row['synonyms'][0] if row['synonyms'] is not None and len(row['synonyms']) > 0 else 'Unknown'
        print(f"\n{idx+1}. {synonyms} ({row['hmdb_id']})")
        print(f"   InChIKey: {row['inchikey']}")

        results = query_unichem(row['inchikey'])
        if results:
            sources = analyze_unichem_results(results)
            print(f"   ✓ Found {len(sources)} source(s) in UniChem:")
            for src_name, ids in sources.items():
                print(f"     - {src_name}: {', '.join(ids[:3])}" +
                      (f" (+{len(ids)-3} more)" if len(ids) > 3 else ""))

            # Check if we found useful IDs
            if 'chebi' in sources or 'pubchem' in sources or 'kegg_ligand' in sources:
                enrichable_count += 1
        else:
            print("   ✗ Not found in UniChem")

        time.sleep(0.5)  # Be nice to the API

    print(f"\n{'='*80}")
    print(f"Summary: {enrichable_count}/20 compounds could be enriched with useful IDs")
    print(f"{'='*80}")

    # Test LipidMaps
    print("\n\nLipidMaps - Molecules with only InChIKey (missing ChEBI & PubChem):")
    print("-" * 80)
    lipidmaps_df = get_lipidmaps_sample()
    print(f"Found {len(lipidmaps_df)} samples\n")

    enrichable_count = 0
    for idx, row in lipidmaps_df.iterrows():
        if idx >= 20:  # Test first 20
            break

        print(f"\n{idx+1}. {row['name']} ({row['lipidmaps_id']})")
        print(f"   InChIKey: {row['inchikey']}")

        results = query_unichem(row['inchikey'])
        if results:
            sources = analyze_unichem_results(results)
            print(f"   ✓ Found {len(sources)} source(s) in UniChem:")
            for src_name, ids in sources.items():
                print(f"     - {src_name}: {', '.join(ids[:3])}" +
                      (f" (+{len(ids)-3} more)" if len(ids) > 3 else ""))

            if 'chebi' in sources or 'pubchem' in sources or 'hmdb' in sources:
                enrichable_count += 1
        else:
            print("   ✗ Not found in UniChem")

        time.sleep(0.5)

    print(f"\n{'='*80}")
    print(f"Summary: {enrichable_count}/20 compounds could be enriched with useful IDs")
    print(f"{'='*80}")

    # Test RAMP
    print("\n\nRAMP - Molecules with only InChIKey (missing common IDs in sources):")
    print("-" * 80)
    ramp_df = get_ramp_sample()
    print(f"Found {len(ramp_df)} samples\n")

    enrichable_count = 0
    for idx, row in ramp_df.iterrows():
        if idx >= 20:  # Test first 20
            break

        print(f"\n{idx+1}. {row['common_name'] or 'Unknown'} ({row['ramp_id']})")
        print(f"   InChIKey: {row['inchi_key']}")
        print(f"   Current sources: {row['sources'][:100]}..." if row['sources'] and len(row['sources']) > 100 else f"   Current sources: {row['sources']}")

        results = query_unichem(row['inchi_key'])
        if results:
            sources = analyze_unichem_results(results)
            print(f"   ✓ Found {len(sources)} source(s) in UniChem:")
            for src_name, ids in sources.items():
                print(f"     - {src_name}: {', '.join(ids[:3])}" +
                      (f" (+{len(ids)-3} more)" if len(ids) > 3 else ""))

            if 'chebi' in sources or 'pubchem' in sources or 'hmdb' in sources or 'kegg_ligand' in sources:
                enrichable_count += 1
        else:
            print("   ✗ Not found in UniChem")

        time.sleep(0.5)

    print(f"\n{'='*80}")
    print(f"Summary: {enrichable_count}/20 compounds could be enriched with useful IDs")
    print(f"{'='*80}")


if __name__ == "__main__":
    main()
