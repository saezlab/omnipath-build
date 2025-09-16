-- Stage 1: capture unique input SMILES strings before any RDKit processing

CREATE TABLE IF NOT EXISTS gold.compound_input_smiles (
    input_smiles TEXT PRIMARY KEY,
    inserted_at TIMESTAMP DEFAULT NOW()
);

WITH candidate_smiles AS (
    SELECT DISTINCT trim(smiles) AS input_smiles
    FROM silver.compounds
    WHERE smiles IS NOT NULL
      AND trim(smiles) <> ''
),
new_smiles AS (
    SELECT c.input_smiles
    FROM candidate_smiles c
    WHERE NOT EXISTS (
        SELECT 1
        FROM gold.compound_input_smiles existing
        WHERE existing.input_smiles = c.input_smiles
    )
    LIMIT 10000
)
INSERT INTO gold.compound_input_smiles (input_smiles)
SELECT input_smiles
FROM new_smiles;
