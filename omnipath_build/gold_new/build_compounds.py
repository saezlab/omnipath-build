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
- Molfile (Mol block format for RDKit compatibility)

The compound table links to entities via entity_id.
"""

import polars as pl
from pathlib import Path
from typing import Optional
import logging
import shutil

from omnipath_build.utils.cv_term_enums import IdentifierNamespaceCv

try:
    from rdkit import Chem, DataStructs
    from rdkit.Chem import Descriptors, Lipinski, rdMolDescriptors, rdFingerprintGenerator
    from rdkit.Chem.MolStandardize import rdMolStandardize
    MORGAN_GENERATOR = rdFingerprintGenerator.GetMorganGenerator(radius=2, fpSize=2048)
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
        # Compute Morgan fingerprint (radius=2, 2048 bits)
        morgan_fp = MORGAN_GENERATOR.GetFingerprint(mol)

        # Convert to binary format for PostgreSQL RDKit cartridge
        # Use DataStructs.BitVectToBinaryText() for compatibility with bfp_from_binary_text()
        morgan_fp_bytes = DataStructs.BitVectToBinaryText(morgan_fp)

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
            "molfile": Chem.MolToMolBlock(mol),
            "morgan_fp": morgan_fp_bytes,
        }
    except Exception as e:
        logger.debug(f"Failed to compute properties for {structure_type} '{structure}': {e}")
        return None


def build_compounds(
    entity_identifiers: pl.DataFrame,
    compound_limit: Optional[int] = None,
    use_cache: bool = True,
    cache_dir: Optional[Path] = None,
    chunk_size: int = 10000,
) -> pl.DataFrame:
    """
    Build compound table with computed molecular properties.

    This function:
    1. Extracts chemical structure identifiers (InChI or SMILES) from entity_identifiers table
    2. Prefers Standard InChI over SMILES (avoids duplicate computation)
    3. Computes molecular properties using RDKit
    4. Creates a compound table linked to entity_id
    5. Uses existing compound.parquet file as cache to avoid recomputing properties

    Args:
        entity_identifiers: DataFrame from build_entity_identifiers output
                           (columns: entity_id, id_type, id_value, sources)
        compound_limit: Optional limit on number of compounds to process
        use_cache: Whether to use existing compound.parquet as cache
        cache_dir: Optional directory where compound.parquet is located (defaults to cwd)
        chunk_size: Number of compounds to process in each chunk (default: 10000)

    Returns:
        DataFrame with compound properties (columns: id, entity_id, formula,
        molecular_weight, exact_mass, tpsa, logp, hbd, hba, rotatable_bonds,
        aromatic_rings, heavy_atoms, molfile)
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
            "molfile": pl.Utf8,
            "morgan_fp": pl.Binary,
        })

    print("\nStep 1: Looking up chemical structure identifier types...")
    print("  (Using id_type accession strings from entity_identifiers)")

    # Get identifier type accession strings
    standard_inchi_type = IdentifierNamespaceCv.STANDARD_INCHI.value
    smiles_type = IdentifierNamespaceCv.SMILES.value

    print(f"  Standard InChI type: {standard_inchi_type}")
    print(f"  SMILES type: {smiles_type}")

    print("\nStep 2: Extracting chemical structure identifiers...")
    print("  Priority: Standard InChI (preferred) > SMILES (fallback)")

    structure_parts = []

    # Get entities with Standard InChI (preferred)
    inchi_entities = entity_identifiers.filter(
        pl.col('id_type') == standard_inchi_type
    ).select([
        'entity_id',
        pl.col('id_value').alias('structure'),
        pl.lit('inchi').alias('structure_type'),
    ])
    if len(inchi_entities) > 0:
        structure_parts.append(inchi_entities)
        print(f"  Found {len(inchi_entities):,} entities with Standard InChI")

    # Get entities with SMILES (fallback)
    smiles_entities = entity_identifiers.filter(
        pl.col('id_type') == smiles_type
    ).select([
        'entity_id',
        pl.col('id_value').alias('structure'),
        pl.lit('smiles').alias('structure_type'),
    ])
    if len(smiles_entities) > 0:
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

    # Setup cache - use the output compound.parquet file itself as cache
    if cache_dir is None:
        cache_dir = Path.cwd()
    cache_dir = Path(cache_dir)
    cache_path = cache_dir / "compound.parquet"

    # Load existing compound table if available (create structure -> properties lookup)
    cached_structures = set()
    if use_cache and cache_path.exists():
        print(f"\nStep 3: Loading existing compound table...")
        try:
            cached_df = pl.read_parquet(cache_path)
            # Join with structure_identifiers to get structure strings
            cached_with_structure = cached_df.join(
                structure_identifiers.select(['entity_id', 'structure']),
                on='entity_id',
                how='inner'
            )
            # Only store the structure strings (not full properties) to save memory
            cached_structures = set(cached_with_structure.select('structure').to_series())
            print(f"  Found {len(cached_structures):,} cached structures")
            del cached_df, cached_with_structure  # Free memory
        except Exception as e:
            print(f"  ⚠️  Failed to load existing compounds: {e}")

    print(f"\nStep 4: Computing molecular properties (chunked processing)...")

    # Filter out cached structures
    to_compute = structure_identifiers.filter(
        ~pl.col('structure').is_in(list(cached_structures))
    )

    if len(to_compute) > 0:
        print(f"  Computing properties for {len(to_compute):,} new compounds...")
        print(f"  Processing in chunks of {chunk_size:,}")
    else:
        print(f"  All compounds already cached!")

    # Process in chunks and write to separate parquet files
    temp_dir = cache_dir / "compound_temp_chunks"
    temp_dir.mkdir(exist_ok=True)

    failed = 0
    num_new = 0
    total_chunks = (len(to_compute) + chunk_size - 1) // chunk_size
    chunk_files = []

    for chunk_idx in range(total_chunks):
        start_idx = chunk_idx * chunk_size
        end_idx = min(start_idx + chunk_size, len(to_compute))
        chunk_df = to_compute.slice(start_idx, end_idx - start_idx)

        chunk_props = []

        # Setup progress bar for this chunk
        items = chunk_df.iter_rows(named=True)
        if TQDM_AVAILABLE:
            items = tqdm(
                list(items),
                desc=f"  Chunk {chunk_idx + 1}/{total_chunks}",
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
            chunk_props.append(props)

        if chunk_props:
            # Create DataFrame for this chunk
            chunk_result = pl.DataFrame(chunk_props).select([
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
                'molfile',
                'morgan_fp',
            ])

            # Write each chunk to a separate file (no reading back!)
            chunk_file = temp_dir / f"chunk_{chunk_idx:06d}.parquet"
            chunk_result.write_parquet(chunk_file)
            chunk_files.append(chunk_file)

            num_new += len(chunk_props)
            del chunk_props, chunk_result  # Free memory

    if failed > 0:
        print(f"  ⚠️  Failed to compute properties for {failed:,} compounds (invalid structures)")

    # Combine cached and new compounds
    result_parts = []

    # Load cached compounds if they exist
    if use_cache and cache_path.exists() and len(cached_structures) > 0:
        cached_df = pl.read_parquet(cache_path)
        # Only keep cached compounds that are in our current structure_identifiers
        cached_df = cached_df.join(
            structure_identifiers.select('entity_id'),
            on='entity_id',
            how='inner'
        )
        result_parts.append(cached_df)
        num_cached = len(cached_df)
    else:
        num_cached = 0

    # Load new compounds if any were computed
    if num_new > 0 and chunk_files:
        # Read all chunk files using Polars' efficient multi-file read
        new_df = pl.read_parquet(chunk_files)
        result_parts.append(new_df)

        # Clean up chunk files and directory
        shutil.rmtree(temp_dir)

    if not result_parts:
        print("  ⚠️  No valid compound properties computed")
        return pl.DataFrame()

    # Combine all parts
    compound_df = pl.concat(result_parts, how='diagonal_relaxed').unique(
        subset=['entity_id']
    ).sort('entity_id')

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
        'molfile',
        'morgan_fp',
    ])

    print(f"\n✓ Computed properties for {len(compound_df):,} compounds")
    print(f"  ({num_cached:,} from cache, {num_new:,} newly computed)")

    return compound_df
