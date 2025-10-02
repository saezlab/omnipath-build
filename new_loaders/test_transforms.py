#!/usr/bin/env python3
"""Test script to verify DuckDB transformation functions work with HMDB data."""

import duckdb
from pathlib import Path

# Find the HMDB bronze parquet file
bronze_path = Path(__file__).parent.parent / "omnipath_build" / "databases" / "metabo" / "bronze" / "data" / "hmdb" / "compounds_for_metabo"

print("Looking for HMDB bronze data...")
print(f"Path: {bronze_path}")

if not bronze_path.exists():
    print("❌ Bronze data path doesn't exist!")
    print("Please run bronze loader first to download HMDB data")
    exit(1)

# Find latest parquet file
parquet_files = list(bronze_path.glob("*.parquet"))
if not parquet_files:
    print("❌ No parquet files found!")
    exit(1)

latest_parquet = sorted(parquet_files)[-1]
print(f"✓ Found bronze data: {latest_parquet}")

# Load transformation functions
transform_sql_path = Path(__file__).parent.parent / "omnipath_build" / "databases" / "metabo" / "silver" / "transformation_functions.sql"
print(f"\nLoading transformations from: {transform_sql_path}")

with open(transform_sql_path) as f:
    transform_sql = f.read()

# Create DuckDB connection and load functions
conn = duckdb.connect(":memory:")
print("✓ Created DuckDB connection")

# Execute transformation functions
conn.execute(transform_sql)
print("✓ Loaded transformation functions")

# Test reading the parquet
result = conn.execute(f"SELECT COUNT(*) FROM '{latest_parquet}'").fetchone()
print(f"✓ Bronze file contains {result[0]:,} rows")

# Test a few key functions with sample data
print("\n=== Testing Transformation Functions ===")

# Test 1: build_identifier_list_hmdb
print("\n1. Testing build_identifier_list_hmdb:")
query = f"""
SELECT
    accession,
    chebi_id,
    pubchem_compound_id,
    build_identifier_list_hmdb(
        chebi_id,
        pubchem_compound_id,
        kegg_id,
        drugbank_id,
        cas_registry_number,
        inchikey
    ) as identifier_list
FROM '{latest_parquet}'
WHERE chebi_id IS NOT NULL
LIMIT 3
"""
results = conn.execute(query).fetchall()
for row in results:
    print(f"  {row[0]}: {row[3]}")

# Test 2: to_json for synonyms
print("\n2. Testing to_json for synonyms:")
query = f"""
SELECT
    accession,
    synonyms,
    to_json(synonyms) as name_variants
FROM '{latest_parquet}'
WHERE synonyms IS NOT NULL
LIMIT 3
"""
results = conn.execute(query).fetchall()
for row in results:
    print(f"  {row[0]}: {len(str(row[2]))} chars of JSON")

# Test 3: Full transformation like in HMDB config
print("\n3. Testing full HMDB silver transformation:")
query = f"""
SELECT
    'compound' as entity_type,
    accession as identifier,
    'hmdb' as identifier_type,
    build_identifier_list_hmdb(
        chebi_id,
        pubchem_compound_id,
        kegg_id,
        drugbank_id,
        cas_registry_number,
        inchikey
    ) as additional_identifiers,
    COALESCE(iupac_name, traditional_iupac) as name,
    to_json(synonyms) as name_variants,
    chemical_formula as compound_formula,
    smiles as compound_smiles,
    inchi as compound_inchi,
    average_molecular_weight as molecular_weight,
    monisotopic_molecular_weight as exact_mass,
    true as is_valid,
    'hmdb_compounds_for_metabo' as processed_by,
    metadata_row_number as bronze_record_id,
    CURRENT_TIMESTAMP as created_at,
    CURRENT_TIMESTAMP as updated_at
FROM '{latest_parquet}'
LIMIT 5
"""

results = conn.execute(query).fetchall()
print(f"  ✓ Transformed {len(results)} rows successfully")
print(f"  Sample output (first row):")
print(f"    - entity_type: {results[0][0]}")
print(f"    - identifier: {results[0][1]}")
print(f"    - name: {results[0][4][:50] if results[0][4] else 'NULL'}...")
print(f"    - has additional_identifiers: {results[0][3] is not None}")

print("\n✅ All transformation tests passed!")

conn.close()
