#!/usr/bin/env python3
"""
Build Compounds - Compute chemical properties from SMILES for compound entities.

This module augments the entity_identifier table by computing molecular properties
from SMILES strings using RDKit. It creates a compound table with properties like:
- Molecular formula
- Molecular weight and exact mass
- Topological polar surface area (TPSA)
- LogP (partition coefficient)
- Hydrogen bond donors/acceptors
- Rotatable bonds
- Aromatic rings
- Heavy atoms

The compound table links to entities via entity_id.
"""

import polars as pl
from pathlib import Path
from typing import Optional
import logging

try:
    from rdkit import Chem
    from rdkit.Chem import Descriptors, Lipinski, rdMolDescriptors
    RDKIT_AVAILABLE = True
except ImportError:
    RDKIT_AVAILABLE = False

try:
    from tqdm import tqdm
    TQDM_AVAILABLE = True
except ImportError:
    TQDM_AVAILABLE = False

logger = logging.getLogger(__name__)

# SMILES identifier type
SMILES_IDENTIFIER_TYPE = "smiles"

__all__ = [
    'RDKIT_AVAILABLE',
    'SMILES_IDENTIFIER_TYPE',
    'TQDM_AVAILABLE',
    'build_compounds',
]


def _compute_compound_properties(smiles: str) -> Optional[dict]:
    """
    Compute molecular properties from a SMILES string.

    Args:
        smiles: SMILES string to parse

    Returns:
        Dictionary with computed properties, or None if SMILES is invalid
    """
    if not RDKIT_AVAILABLE:
        return None

    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        return None

    try:
        return {
            "formula": rdMolDescriptors.CalcMolFormula(mol),
            "molecular_weight": float(Descriptors.MolWt(mol)),
            "exact_mass": float(Descriptors.ExactMolWt(mol)),
            "tpsa": float(rdMolDescriptors.CalcTPSA(mol)),
            "logp": float(Descriptors.MolLogP(mol)),
            "hbd": int(Lipinski.NumHDonors(mol)),
            "hba": int(Lipinski.NumHAcceptors(mol)),
            "rotatable_bonds": int(Lipinski.NumRotatableBonds(mol)),
            "aromatic_rings": int(rdMolDescriptors.CalcNumAromaticRings(mol)),
            "heavy_atoms": int(mol.GetNumHeavyAtoms()),
        }
    except Exception as e:
        logger.debug(f"Failed to compute properties for SMILES '{smiles}': {e}")
        return None


def build_compounds(
    output_dir: Path,
    entity_identifiers: Optional[pl.DataFrame] = None,
    compound_limit: Optional[int] = None,
    use_cache: bool = True,
    cache_dir: Optional[Path] = None,
) -> pl.DataFrame:
    """
    Build compound table with computed molecular properties.

    This function:
    1. Reads SMILES identifiers from entity_identifier table
    2. Computes molecular properties using RDKit
    3. Creates a compound table linked to entity_id
    4. Uses caching to avoid recomputing properties

    Args:
        output_dir: Path to output directory for gold tables
        entity_identifiers: Optional pre-loaded entity_identifier table
        compound_limit: Optional limit on number of compounds to process
        use_cache: Whether to use cached compound properties
        cache_dir: Optional directory for caching computed properties

    Returns:
        DataFrame with compound properties
    """
    if not RDKIT_AVAILABLE:
        print("⚠️  RDKit not available - skipping compound property computation")
        print("   Install with: pip install rdkit")
        return pl.DataFrame(schema={
            "id": pl.UInt32,
            "entity_id": pl.Int64,
            "formula": pl.Utf8,
            "molecular_weight": pl.Float64,
            "exact_mass": pl.Float64,
            "tpsa": pl.Float64,
            "logp": pl.Float64,
            "hbd": pl.Int32,
            "hba": pl.Int32,
            "rotatable_bonds": pl.Int32,
            "aromatic_rings": pl.Int32,
            "heavy_atoms": pl.Int32,
        })

    print("\nStep 1: Loading entity identifiers...")
    if entity_identifiers is None:
        entity_id_path = output_dir / "entity_identifier.parquet"
        if not entity_id_path.exists():
            print(f"  ⚠️  entity_identifier table not found at {entity_id_path}")
            return pl.DataFrame()
        entity_identifiers = pl.read_parquet(entity_id_path)

    print(f"  Loaded {len(entity_identifiers):,} entity identifiers")

    print("\nStep 2: Extracting SMILES identifiers...")

    # Filter for SMILES identifier type
    # These are stored with identifier_type_name = 'smiles'
    smiles_identifiers = entity_identifiers.filter(
        pl.col('identifier_type_name') == SMILES_IDENTIFIER_TYPE
    ).select([
        'entity_id',
        pl.col('identifier').alias('smiles'),
    ])

    if len(smiles_identifiers) == 0:
        print("  ⚠️  No SMILES identifiers found in entity_identifier table")
        print(f"     (Looking for identifier_type_name = '{SMILES_IDENTIFIER_TYPE}')")
        return pl.DataFrame()

    # Deduplicate by entity_id (keep first SMILES per entity)
    smiles_with_entities = smiles_identifiers.unique(subset=['entity_id'], keep='first')

    print(f"  Found {len(smiles_with_entities):,} entities with SMILES")

    # Apply limit if specified
    if compound_limit is not None and len(smiles_with_entities) > compound_limit:
        print(f"\n  ⚠️  Limiting to first {compound_limit:,} compounds (out of {len(smiles_with_entities):,})")
        smiles_with_entities = smiles_with_entities.head(compound_limit)

    # Setup cache
    if cache_dir is None:
        cache_dir = output_dir / "compound_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / "compound_properties.parquet"

    # Load cache if available
    cached_props = {}
    if use_cache and cache_path.exists():
        print(f"\nStep 5: Loading cached compound properties...")
        try:
            cached_df = pl.read_parquet(cache_path)
            for row in cached_df.iter_rows(named=True):
                cached_props[row['entity_id']] = row
            print(f"  Loaded {len(cached_props):,} cached compound properties")
        except Exception as e:
            print(f"  ⚠️  Failed to load cache: {e}")

    print(f"\nStep 6: Computing molecular properties...")

    # Filter out cached entities
    to_compute = smiles_with_entities.filter(
        ~pl.col('entity_id').is_in(list(cached_props.keys()))
    )

    if len(to_compute) > 0:
        print(f"  Computing properties for {len(to_compute):,} new compounds...")
    else:
        print(f"  All compounds already cached!")

    new_props = []
    failed = 0

    # Setup progress bar
    items = to_compute.iter_rows(named=True)
    if TQDM_AVAILABLE:
        items = tqdm(
            list(items),
            desc="  Processing compounds",
            unit="compound"
        )

    for row in items:
        entity_id = row['entity_id']
        smiles = row['smiles']

        props = _compute_compound_properties(smiles)
        if props is None:
            failed += 1
            continue

        props['entity_id'] = entity_id
        new_props.append(props)

    if failed > 0:
        print(f"  ⚠️  Failed to compute properties for {failed:,} compounds (invalid SMILES)")

    # Save new computations to cache
    if new_props and use_cache:
        print(f"\nStep 7: Saving {len(new_props):,} new computations to cache...")
        new_cache_df = pl.DataFrame(new_props)

        if cached_props:
            # Merge with existing cache
            old_cache_df = pl.DataFrame(list(cached_props.values()))
            combined_cache = pl.concat([old_cache_df, new_cache_df])
        else:
            combined_cache = new_cache_df

        combined_cache.write_parquet(cache_path)

    # Combine cached and new properties
    all_props = list(cached_props.values()) + new_props

    if not all_props:
        print("  ⚠️  No valid compound properties computed")
        return pl.DataFrame()

    # Create final DataFrame
    compound_df = pl.DataFrame(all_props).with_columns([
        pl.lit(None).cast(pl.UInt32).alias('id')  # Placeholder, will be set below
    ]).select([
        'entity_id',
        'formula',
        'molecular_weight',
        'exact_mass',
        'tpsa',
        'logp',
        'hbd',
        'hba',
        'rotatable_bonds',
        'aromatic_rings',
        'heavy_atoms',
    ]).sort('entity_id')

    # Add sequential IDs
    compound_df = compound_df.with_row_count(name='id', offset=1).with_columns([
        pl.col('id').cast(pl.UInt32)
    ]).select([
        'id',
        'entity_id',
        'formula',
        'molecular_weight',
        'exact_mass',
        'tpsa',
        'logp',
        'hbd',
        'hba',
        'rotatable_bonds',
        'aromatic_rings',
        'heavy_atoms',
    ])

    print(f"\n✓ Computed properties for {len(compound_df):,} compounds")
    print(f"  ({len(cached_props):,} from cache, {len(new_props):,} newly computed)")

    return compound_df
