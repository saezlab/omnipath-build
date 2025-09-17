-- Gold layer compound-source database linking table
-- Many-to-many relationship between compounds and source databases

-- 1) Many-to-many linking compounds to source databases
CREATE TABLE IF NOT EXISTS gold.compound_source_databases (
  compound_id BIGINT REFERENCES gold.compounds(compound_id) ON DELETE CASCADE,
  database_id INTEGER REFERENCES gold.source_databases(database_id),
  created_at  TIMESTAMP DEFAULT NOW(),

  PRIMARY KEY (compound_id, database_id)
);

-- 2) Ingestion: Parse source_database from silver.compounds and link to compounds
INSERT INTO gold.compound_source_databases (compound_id, database_id)
SELECT DISTINCT
  gc.compound_id,
  gsd.database_id
FROM silver.compounds sc
JOIN gold.input_to_canonical itc ON btrim(sc.smiles) = itc.input_smiles
JOIN gold.compounds gc ON itc.canonical_id = gc.canonical_id
JOIN gold.source_databases gsd ON LOWER(sc.source_database) = LOWER(gsd.database_name)
WHERE sc.smiles IS NOT NULL
  AND btrim(sc.smiles) <> ''
  AND sc.source_database IS NOT NULL
  AND btrim(sc.source_database) <> ''
ON CONFLICT (compound_id, database_id) DO NOTHING;

-- 3) Indexes
