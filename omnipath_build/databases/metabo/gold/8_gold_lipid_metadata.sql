-- Gold layer lipid metadata table
-- Lipid-specific metadata for compounds where is_lipid = TRUE

-- 1) Lipid-specific metadata
CREATE TABLE IF NOT EXISTS gold.lipid_metadata (
  compound_id                 BIGINT PRIMARY KEY REFERENCES gold.compounds(compound_id) ON DELETE CASCADE,
  lipid_classification_level  VARCHAR(100),
  lipid_parent               VARCHAR(255),
  lipid_components           TEXT,
  created_at                 TIMESTAMP DEFAULT NOW()
);

-- 2) Ingestion: Populate from silver.compounds for lipid compounds
INSERT INTO gold.lipid_metadata (compound_id, lipid_classification_level, lipid_parent, lipid_components)
SELECT
  gc.compound_id,
  (array_agg(sc.lipid_classification_level ORDER BY sc.lipid_classification_level) FILTER (WHERE sc.lipid_classification_level IS NOT NULL))[1] AS lipid_classification_level,
  (array_agg(sc.lipid_parent ORDER BY sc.lipid_parent) FILTER (WHERE sc.lipid_parent IS NOT NULL))[1] AS lipid_parent,
  string_agg(DISTINCT sc.lipid_components, ' | ' ORDER BY sc.lipid_components) FILTER (WHERE sc.lipid_components IS NOT NULL) AS lipid_components
FROM silver.compounds sc
JOIN gold.input_to_canonical itc ON btrim(sc.smiles) = itc.input_smiles
JOIN gold.compounds gc ON itc.canonical_id = gc.canonical_id
WHERE sc.smiles IS NOT NULL
  AND btrim(sc.smiles) <> ''
  AND gc.is_lipid = TRUE
  AND (
    sc.lipid_classification_level IS NOT NULL OR
    sc.lipid_parent IS NOT NULL OR
    sc.lipid_components IS NOT NULL
  )
GROUP BY gc.compound_id
ON CONFLICT (compound_id) DO UPDATE SET
  lipid_classification_level = COALESCE(EXCLUDED.lipid_classification_level, gold.lipid_metadata.lipid_classification_level),
  lipid_parent = COALESCE(EXCLUDED.lipid_parent, gold.lipid_metadata.lipid_parent),
  lipid_components = COALESCE(EXCLUDED.lipid_components, gold.lipid_metadata.lipid_components);

-- 3) Indexes
