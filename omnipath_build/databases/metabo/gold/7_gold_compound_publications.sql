-- Gold layer compound publications table
-- Normalized PMIDs parsed from pipe-delimited pmids column

-- 1) Normalized compound publications
CREATE TABLE IF NOT EXISTS gold.compound_publications (
  compound_id BIGINT NOT NULL REFERENCES gold.compounds(compound_id) ON DELETE CASCADE,
  pmid        BIGINT NOT NULL,
  created_at  TIMESTAMP DEFAULT NOW(),

  PRIMARY KEY (compound_id, pmid)
);

-- 2) Ingestion: Parse pipe-delimited pmids from silver.compounds
INSERT INTO gold.compound_publications (compound_id, pmid)
SELECT DISTINCT
  gc.compound_id,
  pmid_part::BIGINT
FROM silver.compounds sc
JOIN gold.input_to_canonical itc ON btrim(sc.smiles) = itc.input_smiles
JOIN gold.compounds gc ON itc.canonical_id = gc.canonical_id
CROSS JOIN LATERAL unnest(string_to_array(sc.pmids, '|')) AS pmid_part
WHERE sc.smiles IS NOT NULL
  AND btrim(sc.smiles) <> ''
  AND sc.pmids IS NOT NULL
  AND btrim(sc.pmids) <> ''
  AND pmid_part IS NOT NULL
  AND btrim(pmid_part) <> ''
  AND pmid_part ~ '^\d+$' -- Only numeric values
ON CONFLICT (compound_id, pmid) DO NOTHING;

-- 3) Indexes
