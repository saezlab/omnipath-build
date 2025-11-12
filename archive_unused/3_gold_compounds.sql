-- Gold layer main compounds table
-- Links canonical structures to compound metadata

-- 1) Main compounds table - lightweight linking table
CREATE TABLE IF NOT EXISTS gold.compounds (
  compound_id   BIGSERIAL PRIMARY KEY,
  canonical_id  BIGINT REFERENCES gold.canonical_structures(canonical_id),
  is_drug       BOOLEAN DEFAULT FALSE,
  is_lipid      BOOLEAN DEFAULT FALSE,
  is_metabolite BOOLEAN DEFAULT FALSE,
  loaded_at     TIMESTAMP DEFAULT NOW(),

  UNIQUE(canonical_id) -- One compound record per canonical structure
);

-- 2) Ingestion: Create compounds from silver.compounds that have canonical structures
INSERT INTO gold.compounds (canonical_id, is_drug, is_lipid, is_metabolite)
SELECT
  itc.canonical_id,
  bool_or(sc.is_drug) AS is_drug,
  bool_or(sc.is_lipid) AS is_lipid,
  bool_or(sc.is_metabolite) AS is_metabolite
FROM silver.compounds sc
JOIN gold.input_to_canonical itc ON btrim(sc.smiles) = itc.input_smiles
WHERE sc.smiles IS NOT NULL
  AND btrim(sc.smiles) <> ''
GROUP BY itc.canonical_id
ON CONFLICT (canonical_id) DO UPDATE SET
  is_drug = EXCLUDED.is_drug OR gold.compounds.is_drug,
  is_lipid = EXCLUDED.is_lipid OR gold.compounds.is_lipid,
  is_metabolite = EXCLUDED.is_metabolite OR gold.compounds.is_metabolite;

-- 3) Indexes
