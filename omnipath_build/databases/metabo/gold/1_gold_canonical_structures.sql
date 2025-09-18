CREATE EXTENSION IF NOT EXISTS rdkit;

-- 1) One row per canonical structure (all computed props live here)
CREATE TABLE IF NOT EXISTS gold.canonical_structures (
  canonical_id     BIGSERIAL PRIMARY KEY,
  canonical_smiles TEXT UNIQUE NOT NULL,
  mol              mol NOT NULL,
  inchi            TEXT,
  inchikey         TEXT,
  formula          TEXT,
  molecular_weight DOUBLE PRECISION,
  exact_mass       DOUBLE PRECISION,
  tpsa             DOUBLE PRECISION,
  logp             DOUBLE PRECISION,
  hbd              INTEGER,
  hba              INTEGER,
  rotatable_bonds  INTEGER,
  aromatic_rings   INTEGER,
  heavy_atoms      INTEGER,
  morgan_fp        bfp,
  computed_at      TIMESTAMP DEFAULT NOW()
);

-- 2) Map each input SMILES exactly once to its canonical
CREATE TABLE IF NOT EXISTS gold.input_to_canonical (
  input_smiles   TEXT PRIMARY KEY,
  canonical_id   BIGINT NOT NULL REFERENCES gold.canonical_structures(canonical_id),
  inserted_at    TIMESTAMP DEFAULT NOW()
);

CREATE OR REPLACE FUNCTION gold.ingest_smiles(p_input TEXT)
RETURNS void
LANGUAGE plpgsql
AS $$
DECLARE
  v_trimmed       TEXT;
  v_mol           mol;
  v_canonical     TEXT;
  v_canonical_id  BIGINT;
BEGIN
  -- normalize
  v_trimmed := btrim(p_input);
  IF v_trimmed IS NULL OR v_trimmed = '' THEN
    RETURN;
  END IF;

  -- already mapped?
  PERFORM 1 FROM gold.input_to_canonical WHERE input_smiles = v_trimmed;
  IF FOUND THEN
    RETURN;
  END IF;

  -- build molecule; skip invalid
  v_mol := mol_from_smiles(v_trimmed);
  IF v_mol IS NULL THEN
    RETURN;
  END IF;

  -- canonicalize
  v_canonical := mol_to_smiles(v_mol)::TEXT;

  -- ensure canonical row exists and get its id
  INSERT INTO gold.canonical_structures (
    canonical_smiles, mol, inchi, inchikey, formula,
    molecular_weight, exact_mass, tpsa, logp,
    hbd, hba, rotatable_bonds, aromatic_rings, heavy_atoms, morgan_fp, computed_at
  )
  VALUES (
    v_canonical,
    v_mol,
    mol_inchi(v_mol)::TEXT,
    mol_inchikey(v_mol)::TEXT,
    mol_formula(v_mol)::TEXT,
    mol_amw(v_mol),
    mol_exactmw(v_mol),
    mol_tpsa(v_mol),
    mol_logp(v_mol),
    mol_hbd(v_mol),
    mol_hba(v_mol),
    mol_numrotatablebonds(v_mol),
    mol_numaromaticrings(v_mol),
    mol_numheavyatoms(v_mol),
    morganbv_fp(v_mol),
    NOW()
  )
  ON CONFLICT (canonical_smiles)
  DO UPDATE SET canonical_smiles = EXCLUDED.canonical_smiles
  RETURNING canonical_id INTO v_canonical_id;

  -- map input → canonical_id
  INSERT INTO gold.input_to_canonical (input_smiles, canonical_id, inserted_at)
  VALUES (v_trimmed, v_canonical_id, NOW())
  ON CONFLICT (input_smiles) DO NOTHING;

END;
$$;

-- Ingest at most 10,000 distinct, trimmed SMILES
WITH candidate_smiles AS (
  SELECT input_smiles
  FROM (
    SELECT DISTINCT btrim(smiles) AS input_smiles
    FROM silver.compounds
    WHERE smiles IS NOT NULL
      AND btrim(smiles) <> ''
  ) d
  -- ORDER BY input_smiles           -- deterministic slice; remove or change if desired
  -- LIMIT 50000
)
SELECT gold.ingest_smiles(input_smiles)
FROM candidate_smiles;

CREATE INDEX IF NOT EXISTS idx_itc_canonical_id ON gold.input_to_canonical(canonical_id);
