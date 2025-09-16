-- Gold compound structure properties aggregated from silver compounds
-- Creates a deduplicated set of SMILES strings and computes RDKit descriptors

CREATE EXTENSION IF NOT EXISTS rdkit;

-- Create table if it doesn't exist
CREATE TABLE IF NOT EXISTS gold.compound_structures (
    structure_id BIGSERIAL PRIMARY KEY,
    input_smiles TEXT,
    mol mol,
    canonical_smiles TEXT,
    inchi TEXT,
    inchikey TEXT,
    formula TEXT,
    molecular_weight DOUBLE PRECISION,
    exact_mass DOUBLE PRECISION,
    tpsa DOUBLE PRECISION,
    logp DOUBLE PRECISION,
    hbd INTEGER,
    hba INTEGER,
    rotatable_bonds INTEGER,
    aromatic_rings INTEGER,
    heavy_atoms INTEGER,
    computed_at TIMESTAMP DEFAULT NOW()
);

-- Insert only new SMILES not already in the table
INSERT INTO gold.compound_structures (
    input_smiles,
    mol,
    canonical_smiles,
    inchi,
    inchikey,
    formula,
    molecular_weight,
    exact_mass,
    tpsa,
    logp,
    hbd,
    hba,
    rotatable_bonds,
    aromatic_rings,
    heavy_atoms,
    computed_at
)
WITH source_smiles AS (
    SELECT DISTINCT
        trim(smiles) AS input_smiles
    FROM silver.compounds
    WHERE smiles IS NOT NULL
      AND trim(smiles) <> ''
      -- Only select SMILES not already in the gold table
      AND trim(smiles) NOT IN (
          SELECT input_smiles
          FROM gold.compound_structures
          WHERE input_smiles IS NOT NULL
      )
    LIMIT 10000
),
molecule_props AS (
    SELECT
        input_smiles,
        mol_from_smiles(input_smiles) AS mol
    FROM source_smiles
),
computed_props AS (
    SELECT
        input_smiles,
        mol,
        mol_to_smiles(mol)::TEXT AS canonical_smiles,
        mol_inchi(mol)::TEXT AS inchi,
        mol_inchikey(mol)::TEXT AS inchikey,
        mol_formula(mol)::TEXT AS formula,
        mol_amw(mol) AS molecular_weight,
        mol_exactmw(mol) AS exact_mass,
        mol_tpsa(mol) AS tpsa,
        mol_logp(mol) AS logp,
        mol_hbd(mol) AS hbd,
        mol_hba(mol) AS hba,
        mol_numrotatablebonds(mol) AS rotatable_bonds,
        mol_numaromaticrings(mol) AS aromatic_rings,
        mol_numheavyatoms(mol) AS heavy_atoms
    FROM molecule_props
),
deduplicated AS (
    SELECT
        input_smiles,
        mol,
        canonical_smiles,
        inchi,
        inchikey,
        formula,
        molecular_weight,
        exact_mass,
        tpsa,
        logp,
        hbd,
        hba,
        rotatable_bonds,
        aromatic_rings,
        heavy_atoms,
        ROW_NUMBER() OVER (
            PARTITION BY COALESCE(canonical_smiles, input_smiles)
            ORDER BY CASE WHEN canonical_smiles IS NULL THEN 1 ELSE 0 END,
                     input_smiles
        ) AS rn
    FROM computed_props
)
SELECT
    input_smiles,
    mol,
    canonical_smiles,
    inchi,
    inchikey,
    formula,
    molecular_weight,
    exact_mass,
    tpsa,
    logp,
    hbd,
    hba,
    rotatable_bonds,
    aromatic_rings,
    heavy_atoms,
    NOW() AS computed_at
FROM deduplicated
WHERE rn = 1
