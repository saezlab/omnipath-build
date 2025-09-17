"""Utilities for registering RDKit-based DuckDB UDFs."""

from __future__ import annotations

import json
import math
from typing import Any

import duckdb

__all__ = [
    'register_rdkit_udf',
]

try:
    from rdkit import Chem, RDLogger
    from rdkit.Chem import Descriptors, rdmolops, rdMolDescriptors
except ImportError as exc:  # pragma: no cover - surfaced at runtime
    raise ImportError(
        'RDKit is required for structure processing. '
        'Install the "rdkit" package to use the silver RDKit workflow.'
    ) from exc

# Silence noisy RDKit log output during property calculations.
RDLogger.DisableLog('rdApp.*')


def _sanitize_number(value: Any) -> Any:
    """Return a JSON-safe representation for numeric RDKit outputs."""
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        return float(value)
    if isinstance(value, int):
        return int(value)
    return value


def _rdkit_properties(smiles: str | None) -> str | None:
    """Compute RDKit structure properties and serialise them as JSON."""
    if not smiles:
        return None

    normalized = smiles.strip()
    if not normalized:
        return None

    mol = Chem.MolFromSmiles(normalized)
    if mol is None:
        return None

    try:
        canonical_smiles = Chem.MolToSmiles(mol, canonical=True)
        inchi = Chem.MolToInchi(mol)
        inchikey = Chem.MolToInchiKey(mol)
        formula = rdMolDescriptors.CalcMolFormula(mol)
        molecular_weight = float(Descriptors.MolWt(mol))
        exact_mass = float(Descriptors.ExactMolWt(mol))
        tpsa = float(Descriptors.TPSA(mol))
        logp = float(Descriptors.MolLogP(mol))
        hbd = int(Descriptors.NumHDonors(mol))
        hba = int(Descriptors.NumHAcceptors(mol))
        rotatable_bonds = int(Descriptors.NumRotatableBonds(mol))
        aromatic_rings = int(Descriptors.NumAromaticRings(mol))
        heavy_atoms = int(Descriptors.HeavyAtomCount(mol))
    except Exception:  # pragma: no cover - RDKit edge cases surfaced at runtime
        return None

    properties = {
        'canonical_smiles': canonical_smiles,
        'inchi': inchi,
        'inchikey': inchikey,
        'formula': formula,
        'molecular_weight': molecular_weight,
        'exact_mass': exact_mass,
        'tpsa': tpsa,
        'logp': logp,
        'hbd': hbd,
        'hba': hba,
        'rotatable_bonds': rotatable_bonds,
        'aromatic_rings': aromatic_rings,
        'heavy_atoms': heavy_atoms,
    }

    sanitized = {
        key: _sanitize_number(value) for key, value in properties.items()
    }
    return json.dumps(sanitized)


def _rdkit_canonical_smiles(smiles: str | None) -> str | None:
    """Compute canonical SMILES representation."""
    if not smiles:
        return None

    normalized = smiles.strip()
    if not normalized:
        return None

    mol = Chem.MolFromSmiles(normalized)
    if mol is None:
        return None

    try:
        return Chem.MolToSmiles(mol, canonical=True)
    except Exception:
        return None


def register_rdkit_udf(conn: duckdb.DuckDBPyConnection) -> None:
    """Expose the RDKit property calculator as a DuckDB scalar UDF."""

    conn.create_function(
        'rdkit_properties',
        _rdkit_properties,
        parameters=[duckdb.typing.VARCHAR],
        return_type=duckdb.typing.VARCHAR,
        side_effects=False,
    )

    conn.create_function(
        'rdkit_canonical_smiles',
        _rdkit_canonical_smiles,
        parameters=[duckdb.typing.VARCHAR],
        return_type=duckdb.typing.VARCHAR,
        side_effects=False,
    )
