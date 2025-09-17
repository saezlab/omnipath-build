-- Gold layer compound classifications table
-- Normalized classifications parsed from pipe-delimited classes column

-- 1) Normalized compound classifications
CREATE TABLE IF NOT EXISTS gold.compound_classifications (
  classification_id   BIGSERIAL PRIMARY KEY,
  compound_id         BIGINT NOT NULL REFERENCES gold.compounds(compound_id) ON DELETE CASCADE,
  classification_system TEXT NOT NULL,
  classification_path TEXT[] NOT NULL, -- Array of classification levels
  created_at          TIMESTAMP DEFAULT NOW(),

  UNIQUE(compound_id, classification_system, classification_path)
);

-- 2) Ingestion: Parse pipe-delimited classes from silver.compounds
-- Each class entry is separated by |, and within each system levels are separated by $
INSERT INTO gold.compound_classifications (compound_id, classification_system, classification_path)
SELECT DISTINCT
  gc.compound_id,
  SPLIT_PART(class_part, '$', 1) AS classification_system,
  string_to_array(
    regexp_replace(class_part, '^[^$]+\$', ''), -- Remove system name and first $
    '$'
  ) AS classification_path
FROM silver.compounds sc
JOIN gold.input_to_canonical itc ON btrim(sc.smiles) = itc.input_smiles
JOIN gold.compounds gc ON itc.canonical_id = gc.canonical_id
CROSS JOIN LATERAL unnest(string_to_array(sc.classes, '|')) AS class_part
WHERE sc.smiles IS NOT NULL
  AND btrim(sc.smiles) <> ''
  AND sc.classes IS NOT NULL
  AND btrim(sc.classes) <> ''
  AND class_part IS NOT NULL
  AND btrim(class_part) <> ''
  AND SPLIT_PART(class_part, '$', 1) <> ''
  AND array_length(string_to_array(regexp_replace(class_part, '^[^$]+\$', ''), '$'), 1) > 0
ON CONFLICT (compound_id, classification_system, classification_path) DO NOTHING;

-- 3) Indexes
