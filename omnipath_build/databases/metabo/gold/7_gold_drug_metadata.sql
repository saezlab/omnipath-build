-- Gold layer drug metadata table
-- Drug-specific metadata for compounds where is_drug = TRUE

-- 1) Drug-specific metadata
CREATE TABLE IF NOT EXISTS gold.drug_metadata (
  compound_id     BIGINT PRIMARY KEY REFERENCES gold.compounds(compound_id) ON DELETE CASCADE,
  approval_phase  VARCHAR(50),
  approval_year   INTEGER,
  atc_codes       TEXT[], -- Array of ATC codes
  indications     TEXT,
  created_at      TIMESTAMP DEFAULT NOW()
);

-- 2) Ingestion: Populate from silver.compounds for drug compounds
INSERT INTO gold.drug_metadata (compound_id, approval_phase, approval_year, atc_codes, indications)
SELECT DISTINCT
  gc.compound_id,
  sc.approval_phase,
  sc.approval_year,
  CASE
    WHEN sc.atc_codes IS NOT NULL AND btrim(sc.atc_codes) <> ''
    THEN string_to_array(sc.atc_codes, '|')
    ELSE NULL
  END AS atc_codes,
  sc.indications
FROM silver.compounds sc
JOIN gold.input_to_canonical itc ON btrim(sc.smiles) = itc.input_smiles
JOIN gold.compounds gc ON itc.canonical_id = gc.canonical_id
WHERE sc.smiles IS NOT NULL
  AND btrim(sc.smiles) <> ''
  AND gc.is_drug = TRUE
  AND (
    sc.approval_phase IS NOT NULL OR
    sc.approval_year IS NOT NULL OR
    sc.atc_codes IS NOT NULL OR
    sc.indications IS NOT NULL
  )
ON CONFLICT (compound_id) DO UPDATE SET
  approval_phase = COALESCE(EXCLUDED.approval_phase, gold.drug_metadata.approval_phase),
  approval_year = COALESCE(EXCLUDED.approval_year, gold.drug_metadata.approval_year),
  atc_codes = COALESCE(EXCLUDED.atc_codes, gold.drug_metadata.atc_codes),
  indications = COALESCE(EXCLUDED.indications, gold.drug_metadata.indications);

-- 3) Indexes
