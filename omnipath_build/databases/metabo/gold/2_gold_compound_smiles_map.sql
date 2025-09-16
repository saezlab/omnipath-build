-- Stage 2: compute canonical SMILES and store mapping from input to canonical

CREATE EXTENSION IF NOT EXISTS rdkit;

CREATE TABLE IF NOT EXISTS gold.compound_smiles_map (
    input_smiles TEXT PRIMARY KEY REFERENCES gold.compound_input_smiles(input_smiles),
    canonical_smiles TEXT,
    mol mol,
    computed_at TIMESTAMP DEFAULT NOW()
);

WITH pending_inputs AS (
    SELECT i.input_smiles
    FROM gold.compound_input_smiles i
    LEFT JOIN gold.compound_smiles_map m
        ON m.input_smiles = i.input_smiles
    WHERE m.input_smiles IS NULL
    LIMIT 10000
),
molecule_data AS (
    SELECT
        input_smiles,
        mol_from_smiles(input_smiles) AS mol
    FROM pending_inputs
),
normalized AS (
    SELECT
        input_smiles,
        mol,
        CASE
            WHEN mol IS NOT NULL THEN mol_to_smiles(mol)::TEXT
            ELSE NULL
        END AS canonical_smiles
    FROM molecule_data
)
INSERT INTO gold.compound_smiles_map (
    input_smiles,
    canonical_smiles,
    mol,
    computed_at
)
SELECT
    input_smiles,
    canonical_smiles,
    mol,
    NOW()
FROM normalized;
