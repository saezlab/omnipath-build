-- Gold layer compound identifiers table
-- Normalized identifiers parsed from pipe-delimited ids column

-- 1) Normalized compound identifiers
CREATE TABLE IF NOT EXISTS gold.compound_identifiers (
  identifier_id   BIGSERIAL PRIMARY KEY,
  compound_id     BIGINT NOT NULL REFERENCES gold.compounds(compound_id) ON DELETE CASCADE,
  identifier_type VARCHAR(100) NOT NULL,
  identifier_value TEXT NOT NULL,
  created_at      TIMESTAMP DEFAULT NOW(),

  UNIQUE(compound_id, identifier_type, identifier_value)
);

-- 2) Ingestion: Parse pipe-delimited ids from silver.compounds
INSERT INTO gold.compound_identifiers (compound_id, identifier_type, identifier_value)
WITH expanded AS (
  SELECT
    gc.compound_id,
    lower(btrim(SPLIT_PART(id_part, ':', 1))) AS identifier_type,
    btrim(SPLIT_PART(id_part, ':', 2)) AS identifier_value,
    lower(btrim(SPLIT_PART(id_part, ':', 2))) AS identifier_value_norm
  FROM silver.compounds sc
  JOIN gold.input_to_canonical itc ON btrim(sc.smiles) = itc.input_smiles
  JOIN gold.compounds gc ON itc.canonical_id = gc.canonical_id
  CROSS JOIN LATERAL unnest(string_to_array(sc.ids, '|')) AS id_part
  WHERE sc.smiles IS NOT NULL
    AND btrim(sc.smiles) <> ''
    AND sc.ids IS NOT NULL
    AND btrim(sc.ids) <> ''
    AND id_part IS NOT NULL
    AND btrim(id_part) <> ''
    AND SPLIT_PART(id_part, ':', 1) <> ''
    AND SPLIT_PART(id_part, ':', 2) <> ''
), scored AS (
  SELECT
    compound_id,
    identifier_type,
    identifier_value,
    identifier_value_norm,
    MAX(CASE WHEN identifier_type = 'name' THEN 1 ELSE 0 END) OVER (
      PARTITION BY compound_id, identifier_value_norm
    ) AS has_name,
    ROW_NUMBER() OVER (
      PARTITION BY compound_id, identifier_type, identifier_value_norm
      ORDER BY identifier_value
    ) AS type_value_rank
  FROM expanded
  WHERE identifier_type IS NOT NULL
    AND identifier_type <> ''
    AND identifier_value IS NOT NULL
    AND identifier_value <> ''
)
SELECT
  compound_id,
  identifier_type,
  identifier_value
FROM scored
WHERE type_value_rank = 1
  AND (identifier_type <> 'synonym' OR has_name = 0)
ON CONFLICT (compound_id, identifier_type, identifier_value) DO NOTHING;
