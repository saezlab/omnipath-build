import sys
import logging
import time
from pathlib import Path
import duckdb
sys.path.insert(0, str(Path.cwd()))

logging.basicConfig(level=logging.WARNING)

# Create 1000 sample
silver_file = Path('/Users/jschaul/Code/omnipath_build/omnipath_build/databases/metabo/silver_parquet/hmdb_compounds_for_metabo_silver_entities.parquet')
sample_file = Path('/tmp/hmdb_sample_1000_profile.parquet')

conn = duckdb.connect(':memory:')
print('Creating 1,000 row sample...')
conn.execute(f"""
    COPY (
        SELECT * FROM '{silver_file}'
        LIMIT 1000
    ) TO '{sample_file}' (FORMAT PARQUET)
""")
conn.close()

# Manual profiling
from new_loaders.gold_parquet_builder import GoldParquetBuilder

output_dir = Path('/tmp/test_gold_hmdb_1000_profile')
output_dir.mkdir(parents=True, exist_ok=True)

t0 = time.time()
builder = GoldParquetBuilder('hmdb', output_dir)
print(f'⏱️  Schema creation: {time.time()-t0:.3f}s')

t1 = time.time()
result = builder.conn.execute(f"SELECT * FROM '{sample_file}' LIMIT 0")
available_columns = {desc[0] for desc in result.description}
print(f'⏱️  Column detection: {time.time()-t1:.3f}s')

t2 = time.time()
temp_table = 'temp_silver_hmdb'
builder.conn.execute(f"""
    CREATE OR REPLACE TEMP VIEW {temp_table} AS
    SELECT
        entity_type, identifier, identifier_type,
        compound_formula, compound_smiles, compound_inchi,
        molecular_weight, exact_mass, NULL as tpsa, NULL as logp,
        NULL as hbd, NULL as hba, NULL as rotatable_bonds, NULL as aromatic_rings, NULL as heavy_atoms,
        CASE
            WHEN compound_inchi IS NOT NULL AND TRIM(compound_inchi) != ''
                THEN compound_inchi
            ELSE identifier
        END as canonical_id
    FROM '{sample_file}'
    WHERE is_valid = TRUE
""")
total_count = builder.conn.execute(f'SELECT COUNT(*) FROM {temp_table}').fetchone()[0]
print(f'⏱️  Temp view creation: {time.time()-t2:.3f}s ({total_count} rows)')

t3 = time.time()
builder.conn.execute(f"""
    CREATE TEMP TABLE unique_entities AS
    SELECT DISTINCT ON (canonical_id) *
    FROM {temp_table}
    ORDER BY canonical_id
""")
unique_count = builder.conn.execute('SELECT COUNT(*) FROM unique_entities').fetchone()[0]
print(f'⏱️  Deduplication: {time.time()-t3:.3f}s ({unique_count} unique)')

t4 = time.time()
source_id = builder._ensure_source('hmdb')
provenance_id = builder._ensure_provenance(source_id)
compound_cv_term_id = builder._get_or_create_cv_term('entity_type', 'compound')
next_entity_id = builder.conn.execute("SELECT nextval('seq_entity')").fetchone()[0]
builder.conn.execute(f"""
    INSERT INTO entity (id, cv_term_id, created_at)
    SELECT
        {next_entity_id} + ROW_NUMBER() OVER (ORDER BY canonical_id) - 1 as id,
        {compound_cv_term_id} as cv_term_id,
        NOW() as created_at
    FROM unique_entities
""")
print(f'⏱️  Insert entities: {time.time()-t4:.3f}s')

t5 = time.time()
start_entity_id = builder.conn.execute('SELECT COALESCE(MAX(id), 0) - COUNT(*) + 1 FROM entity').fetchone()[0]
builder.conn.execute(f"""
    CREATE TEMP TABLE canonical_mapping AS
    SELECT
        *,
        {start_entity_id} + ROW_NUMBER() OVER (ORDER BY canonical_id) - 1 as entity_id
    FROM unique_entities
""")
print(f'⏱️  Create mapping: {time.time()-t5:.3f}s')

t6 = time.time()
id_types = builder.conn.execute('SELECT DISTINCT identifier_type FROM unique_entities WHERE identifier_type IS NOT NULL').fetchall()
for (id_type,) in id_types:
    builder._get_or_create_cv_term('identifier_type', id_type)
print(f'⏱️  CV term creation: {time.time()-t6:.3f}s ({len(id_types)} types)')

builder.close()
print(f'\n⏱️  TOTAL: {time.time()-t0:.3f}s')
print(f'Estimated for 217,920 rows: {(time.time()-t0) * 217.92:.1f}s = {(time.time()-t0) * 217.92 / 60:.1f}m')
