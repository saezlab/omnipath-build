-- Stage 3: create canonical compound structures deduplicated on canonical SMILES

CREATE EXTENSION IF NOT EXISTS rdkit;

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
WITH pending_mappings AS (
    SELECT
        m.input_smiles,
        m.canonical_smiles,
        m.mol
    FROM gold.compound_smiles_map m
    LEFT JOIN gold.compound_structures s
        ON s.canonical_smiles = m.canonical_smiles
    WHERE s.canonical_smiles IS NULL
      AND m.canonical_smiles IS NOT NULL
      AND m.mol IS NOT NULL
    LIMIT 10000
),
canonical_groups AS (
    SELECT
        canonical_smiles,
        MIN(input_smiles) AS representative_input_smiles
    FROM pending_mappings
    GROUP BY canonical_smiles
),
representative_molecules AS (
    SELECT
        g.representative_input_smiles AS input_smiles,
        p.canonical_smiles,
        p.mol
    FROM canonical_groups g
    JOIN pending_mappings p
        ON p.canonical_smiles = g.canonical_smiles
       AND p.input_smiles = g.representative_input_smiles
),
computed_props AS (
    SELECT
        input_smiles,
        mol,
        canonical_smiles,
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
    FROM representative_molecules
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
FROM computed_props;
