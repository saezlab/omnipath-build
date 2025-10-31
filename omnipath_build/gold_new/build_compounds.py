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

from omnipath_build.utils.cv_term_enums import IdentifierNamespaceCv

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

__all__ = [
    'RDKIT_AVAILABLE',
    'TQDM_AVAILABLE',
    'build_compounds',
]


def _compute_compound_properties(structure: str, structure_type: str) -> Optional[dict]:
    """
    Compute molecular properties from a chemical structure string.

    Args:
        structure: Chemical structure string (SMILES or InChI)
        structure_type: Type of structure ('smiles' or 'inchi')

    Returns:
        Dictionary with computed properties, or None if structure is invalid
    """
    if not RDKIT_AVAILABLE:
        return None

    # Parse structure based on type
    if structure_type == 'inchi':
        mol = Chem.MolFromInchi(structure)
    else:  # smiles
        mol = Chem.MolFromSmiles(structure)

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
        logger.debug(f"Failed to compute properties for {structure_type} '{structure}': {e}")
        return None


def build_compounds(
    entity_identifiers: pl.DataFrame,
    cv_term_df: pl.DataFrame,
    compound_limit: Optional[int] = None,
    use_cache: bool = True,
    cache_dir: Optional[Path] = None,
) -> pl.DataFrame:
    """
    Build compound table with computed molecular properties.

    This function:
    1. Extracts chemical structure identifiers (InChI or SMILES) from entity_identifiers table
    2. Prefers Standard InChI over SMILES (avoids duplicate computation)
    3. Computes molecular properties using RDKit
    4. Creates a compound table linked to entity_id
    5. Uses caching to avoid recomputing properties

    Args:
        entity_identifiers: DataFrame from build_entity_identifiers output
                           (columns: entity_id, type_id, id_value, sources)
        cv_term_df: CV terms DataFrame for looking up identifier type_ids
        compound_limit: Optional limit on number of compounds to process
        use_cache: Whether to use cached compound properties
        cache_dir: Optional directory for caching computed properties

    Returns:
        DataFrame with compound properties (columns: id, entity_id, formula,
        molecular_weight, exact_mass, tpsa, logp, hbd, hba, rotatable_bonds,
        aromatic_rings, heavy_atoms)
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

    print("\nStep 1: Looking up chemical structure identifier types...")

    # Look up type_ids for chemical structure identifiers
    standard_inchi_type_id = cv_term_df.filter(
        pl.col('accession') == IdentifierNamespaceCv.STANDARD_INCHI.value
    ).select('id').to_series()

    smiles_type_id = cv_term_df.filter(
        pl.col('accession') == IdentifierNamespaceCv.SMILES.value
    ).select('id').to_series()

    if len(standard_inchi_type_id) == 0 and len(smiles_type_id) == 0:
        print("  ⚠️  No STANDARD_INCHI or SMILES CV terms found")
        return pl.DataFrame()

    standard_inchi_type_id = standard_inchi_type_id[0] if len(standard_inchi_type_id) > 0 else None
    smiles_type_id = smiles_type_id[0] if len(smiles_type_id) > 0 else None

    print(f"  Standard InChI type_id: {standard_inchi_type_id}")
    print(f"  SMILES type_id: {smiles_type_id}")

    print("\nStep 2: Extracting chemical structure identifiers...")
    print("  Priority: Standard InChI (preferred) > SMILES (fallback)")

    structure_parts = []

    # Get entities with Standard InChI (preferred)
    if standard_inchi_type_id is not None:
        inchi_entities = entity_identifiers.filter(
            pl.col('type_id') == standard_inchi_type_id
        ).select([
            'entity_id',
            pl.col('id_value').alias('structure'),
            pl.lit('inchi').alias('structure_type'),
        ])
        structure_parts.append(inchi_entities)
        print(f"  Found {len(inchi_entities):,} entities with Standard InChI")

    # Get entities with SMILES (fallback)
    if smiles_type_id is not None:
        smiles_entities = entity_identifiers.filter(
            pl.col('type_id') == smiles_type_id
        ).select([
            'entity_id',
            pl.col('id_value').alias('structure'),
            pl.lit('smiles').alias('structure_type'),
        ])
        structure_parts.append(smiles_entities)
        print(f"  Found {len(smiles_entities):,} entities with SMILES")

    if not structure_parts:
        print("  ⚠️  No chemical structure identifiers found")
        return pl.DataFrame()

    # Combine and deduplicate (prefer InChI: keep='first' and InChI is added first)
    structure_identifiers = pl.concat(structure_parts, how='diagonal_relaxed')
    structure_identifiers = structure_identifiers.unique(subset=['entity_id'], keep='first')

    print(f"  Total entities with structures (after deduplication): {len(structure_identifiers):,}")

    # Count how many of each type we're using
    type_counts = structure_identifiers.group_by('structure_type').agg(pl.count().alias('count'))
    for row in type_counts.iter_rows(named=True):
        print(f"    - {row['structure_type']}: {row['count']:,}")

    # Apply limit if specified
    if compound_limit is not None and len(structure_identifiers) > compound_limit:
        print(f"\n  ⚠️  Limiting to first {compound_limit:,} compounds (out of {len(structure_identifiers):,})")
        structure_identifiers = structure_identifiers.head(compound_limit)

    # Setup cache
    if cache_dir is None:
        cache_dir = Path.cwd() / "compound_cache"
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / "compound_properties.parquet"

    # Load cache if available (keyed by structure string)
    cached_props = {}
    if use_cache and cache_path.exists():
        print(f"\nStep 3: Loading cached compound properties...")
        try:
            cached_df = pl.read_parquet(cache_path)
            for row in cached_df.iter_rows(named=True):
                cached_props[row['structure']] = row
            print(f"  Loaded {len(cached_props):,} cached compound properties")
        except Exception as e:
            print(f"  ⚠️  Failed to load cache: {e}")

    print(f"\nStep 4: Computing molecular properties...")

    # Filter out cached structures
    to_compute = structure_identifiers.filter(
        ~pl.col('structure').is_in(list(cached_props.keys()))
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
        structure = row['structure']
        structure_type = row['structure_type']

        props = _compute_compound_properties(structure, structure_type)
        if props is None:
            failed += 1
            continue

        props['entity_id'] = entity_id
        props['structure'] = structure
        new_props.append(props)

    if failed > 0:
        print(f"  ⚠️  Failed to compute properties for {failed:,} compounds (invalid structures)")

    # Save new computations to cache
    if new_props and use_cache:
        print(f"\nStep 5: Saving {len(new_props):,} new computations to cache...")
        new_cache_df = pl.DataFrame(new_props)

        if cached_props:
            # Merge with existing cache
            old_cache_df = pl.DataFrame(list(cached_props.values()))
            combined_cache = pl.concat([old_cache_df, new_cache_df], how='diagonal_relaxed')
        else:
            combined_cache = new_cache_df

        combined_cache.write_parquet(cache_path)

    # Build entity_id -> cached properties lookup
    entity_to_structure = {
        row['entity_id']: row['structure']
        for row in structure_identifiers.iter_rows(named=True)
    }

    # Combine cached and new properties
    all_props = []
    for entity_id, structure in entity_to_structure.items():
        if structure in cached_props:
            props = dict(cached_props[structure])
            props['entity_id'] = entity_id
            all_props.append(props)

    # Add newly computed properties
    all_props.extend(new_props)

    if not all_props:
        print("  ⚠️  No valid compound properties computed")
        return pl.DataFrame()

    # Create final DataFrame
    compound_df = pl.DataFrame(all_props).select([
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
    ]).unique(subset=['entity_id']).sort('entity_id')

    # Add sequential IDs
    compound_df = compound_df.with_row_index(name='id', offset=1).with_columns([
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

    num_cached = sum(1 for _, struct in entity_to_structure.items() if struct in cached_props)
    num_new = len(new_props)

    print(f"\n✓ Computed properties for {len(compound_df):,} compounds")
    print(f"  ({num_cached:,} from cache, {num_new:,} newly computed)")

    return compound_df
