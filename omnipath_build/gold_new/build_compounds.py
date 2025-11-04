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
- Canonical SMILES and InChI (where available)

The compound table links to entities via an entity_compound bridge table.
"""

import polars as pl
from pathlib import Path
from typing import Optional
import logging
from multiprocessing import Pool, cpu_count

from omnipath_build.utils.cv_term_enums import IdentifierNamespaceCv

try:
    from rdkit import Chem, RDLogger
    from rdkit.Chem import Descriptors, Lipinski, rdMolDescriptors
    from rdkit.Chem.MolStandardize import rdMolStandardize
    # Disable RDKit warnings and info messages
    RDLogger.DisableLog('rdApp.*')
    RDKIT_AVAILABLE = True
except ImportError:
    RDKIT_AVAILABLE = False

try:
    from tqdm import tqdm
    TQDM_AVAILABLE = True
except ImportError:
    TQDM_AVAILABLE = False

logger = logging.getLogger(__name__)
# Enable logging to see errors
logging.basicConfig(level=logging.ERROR)

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
    try:
        if structure_type == 'inchi':
            mol = Chem.MolFromInchi(structure)
        else:  # smiles
            mol = Chem.MolFromSmiles(structure)
    except Exception as e:
        logger.error(f"Exception parsing {structure_type}: {e}")
        return None

    if mol is None:
        return None

    try:
        # Standardize the molecule to ensure consistent fingerprints/descriptors
        cleaned_mol = rdMolStandardize.Cleanup(mol)
        if cleaned_mol is None:
            # Cleanup failed, use original molecule
            cleaned_mol = mol
        mol = cleaned_mol

        # Canonical SMILES serves as a stable structure representation
        canonical_smiles = Chem.MolToSmiles(mol, canonical=True)

        # Compute optional InChI (may fail if RDKit built without InChI support)
        try:
            inchi = Chem.MolToInchi(mol)
        except Exception as e:
            logger.debug(f"Failed to compute InChI: {e}")
            inchi = None

        # Prefer InChIKey for deduplication when available, fall back to canonical SMILES
        try:
            structure_key = Chem.MolToInchiKey(mol)
        except Exception as e:
            logger.debug(f"Failed to compute InChIKey, using canonical SMILES: {e}")
            structure_key = canonical_smiles or structure

        return {
            "structure_key": structure_key,
            "canonical_smiles": canonical_smiles,
            "inchi": inchi,
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
        import traceback
        logger.error(f"Failed to compute properties for {structure_type} '{structure[:100]}...': {e}")
        logger.error(f"Traceback: {traceback.format_exc()}")
        return None


def _process_batch(batch_data: list[tuple[int, str, str]]) -> tuple[list[tuple[int, str]], dict[str, dict], int, list[str]]:
    """
    Process a batch of structures and compute their properties.

    Args:
        batch_data: List of (entity_id, structure, structure_type) tuples

    Returns:
        Tuple of (entity_structure_pairs, new_compounds, failed_count, sample_errors)
    """
    # Verify RDKit is available in worker process
    if not RDKIT_AVAILABLE:
        return [], {}, len(batch_data), ["RDKit not available in worker process"]

    entity_structure_pairs = []
    new_compounds = {}
    failed = 0
    sample_errors = []

    for entity_id, structure, structure_type in batch_data:
        props = _compute_compound_properties(structure, structure_type)
        if props is None:
            failed += 1
            # Collect first few errors for debugging
            if len(sample_errors) < 5:
                sample_errors.append(f"{structure_type}: {structure[:50]}...")
            continue

        structure_key = props['structure_key']
        entity_structure_pairs.append((entity_id, structure_key))

        if structure_key not in new_compounds:
            new_compounds[structure_key] = props

    return entity_structure_pairs, new_compounds, failed, sample_errors


def build_compounds(
    entity_identifiers: pl.DataFrame,
    compound_limit: Optional[int] = None,
    use_cache: bool = True,
    cache_dir: Optional[Path] = None,
    chunk_size: int = 10000,
    num_workers: Optional[int] = None,
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """
    Build compound table with computed molecular properties.

    This function:
    1. Extracts chemical structure identifiers (InChI or SMILES) from entity_identifiers table
    2. Prefers Standard InChI over SMILES (avoids duplicate computation)
    3. Computes molecular properties using RDKit
    4. Creates a deduplicated compound table and an entity_compound join table
    5. Uses existing compound.parquet file as cache to avoid recomputing properties

    Args:
        entity_identifiers: DataFrame from build_entity_identifiers output
                           (columns: entity_id, id_type, id_value, sources)
        compound_limit: Optional limit on number of compounds to process
        use_cache: Whether to use existing compound.parquet as cache
        cache_dir: Optional directory where compound.parquet is located (defaults to cwd)
        chunk_size: Number of compounds to process in each chunk (default: 10000)
        num_workers: Number of worker processes for parallel processing (default: CPU count)

    Returns:
        Tuple of:
            - DataFrame with unique compound properties (columns: id, structure_key,
              canonical_smiles, inchi, molecular descriptors)
            - DataFrame mapping entity_id to compound_id
    """
    empty_compound_schema = {
        "id": pl.UInt32,
        "structure_key": pl.Utf8,
        "canonical_smiles": pl.Utf8,
        "inchi": pl.Utf8,
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
    }
    empty_entity_compound_schema = {
        "entity_id": pl.Int64,
        "compound_id": pl.UInt32,
    }

    def _empty_tables() -> tuple[pl.DataFrame, pl.DataFrame]:
        return (
            pl.DataFrame(schema=empty_compound_schema),
            pl.DataFrame(schema=empty_entity_compound_schema),
        )

    if not RDKIT_AVAILABLE:
        print("⚠️  RDKit not available - skipping compound property computation")
        print("   Install with: pip install rdkit")
        return _empty_tables()

    print("\nStep 1: Looking up chemical structure identifier types...")
    print("  (Resolving CV term accessions to entity IDs)")

    # Get identifier type accession strings
    standard_inchi_accession = IdentifierNamespaceCv.STANDARD_INCHI.value
    smiles_accession = IdentifierNamespaceCv.SMILES.value

    print(f"  Standard InChI accession: {standard_inchi_accession}")
    print(f"  SMILES accession: {smiles_accession}")

    # Resolve CV term accessions to entity IDs
    # CV terms have id_type = "OM:0204" (CV_TERM_ACCESSION)

    # Find the entity_id for Standard InChI
    standard_inchi_id = (
        entity_identifiers
        .filter(
            (pl.col('id_value') == standard_inchi_accession) &
            (pl.col('id_type_id').is_not_null())
        )
        .select(pl.col('entity_id').first())
    )
    if len(standard_inchi_id) == 0:
        print(f"  ⚠️  Could not find entity_id for {standard_inchi_accession}")
        standard_inchi_id = None
    else:
        standard_inchi_id = standard_inchi_id.item()
        print(f"  Standard InChI entity_id: {standard_inchi_id}")

    # Find the entity_id for SMILES
    smiles_id = (
        entity_identifiers
        .filter(
            (pl.col('id_value') == smiles_accession) &
            (pl.col('id_type_id').is_not_null())
        )
        .select(pl.col('entity_id').first())
    )
    if len(smiles_id) == 0:
        print(f"  ⚠️  Could not find entity_id for {smiles_accession}")
        smiles_id = None
    else:
        smiles_id = smiles_id.item()
        print(f"  SMILES entity_id: {smiles_id}")

    print("\nStep 2: Extracting chemical structure identifiers...")
    print("  Priority: Standard InChI (preferred) > SMILES (fallback)")

    structure_parts = []

    # Get entities with Standard InChI (preferred)
    if standard_inchi_id is not None:
        inchi_entities = entity_identifiers.filter(
            pl.col('id_type_id') == standard_inchi_id
        ).select([
            'entity_id',
            pl.col('id_value').alias('structure'),
            pl.lit('inchi').alias('structure_type'),
        ])
        if len(inchi_entities) > 0:
            structure_parts.append(inchi_entities)
            print(f"  Found {len(inchi_entities):,} entities with Standard InChI")

    # Get entities with SMILES (fallback)
    if smiles_id is not None:
        smiles_entities = entity_identifiers.filter(
            pl.col('id_type_id') == smiles_id
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
        return _empty_tables()

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

    # Setup cache - stored unique compounds keyed by standardized structure
    if cache_dir is None:
        cache_dir = Path.cwd()
    cache_dir = Path(cache_dir)
    cache_path = cache_dir / "compound.parquet"
    # Load existing compounds if available (structure_key -> id mapping)
    cached_compounds_df: Optional[pl.DataFrame] = None
    cached_structure_to_id: dict[str, int] = {}
    if use_cache and cache_path.exists():
        print(f"\nStep 3: Loading existing compound table cache...")
        try:
            cached_compounds_df = pl.read_parquet(cache_path)

            if 'structure_key' not in cached_compounds_df.columns or 'id' not in cached_compounds_df.columns:
                print("  ⚠️  Existing cache missing structure_key/id columns; ignoring cached compounds")
                cached_compounds_df = None
            else:
                cached_structure_to_id = dict(zip(
                    cached_compounds_df['structure_key'].to_list(),
                    cached_compounds_df['id'].to_list(),
                ))
                print(f"  Loaded {len(cached_structure_to_id):,} cached compounds")
        except Exception as e:
            print(f"  ⚠️  Failed to load existing compounds cache: {e}")
            cached_compounds_df = None
            cached_structure_to_id = {}

    print(f"\nStep 4: Computing molecular properties (parallel processing)...")

    if len(structure_identifiers) == 0:
        print("  ⚠️  No structures to process")
        return _empty_tables()

    # Quick test with first structure to ensure RDKit is working
    test_row = structure_identifiers.head(1).row(0, named=True)
    test_props = _compute_compound_properties(test_row['structure'], test_row['structure_type'])
    if test_props is None:
        print(f"  ⚠️  Failed to process test structure - RDKit may not be working properly")
        return _empty_tables()

    # Determine number of workers
    if num_workers is None:
        num_workers = min(4,cpu_count()-1)

    print(f"\n  Processing {len(structure_identifiers):,} entity structures")
    print(f"  Using {num_workers} worker processes with chunk size {chunk_size:,}")

    # Prepare data for parallel processing
    # Convert to list of tuples: (entity_id, structure, structure_type)
    all_data = [
        (row['entity_id'], row['structure'], row['structure_type'])
        for row in structure_identifiers.iter_rows(named=True)
    ]

    # Split into chunks for parallel processing
    total_chunks = (len(all_data) + chunk_size - 1) // chunk_size
    chunks = [
        all_data[i * chunk_size:(i + 1) * chunk_size]
        for i in range(total_chunks)
    ]

    # Process chunks in parallel
    failed = 0
    entity_structure_pairs: list[tuple[int, str]] = []
    new_compounds: dict[str, dict] = {}

    with Pool(processes=num_workers) as pool:
        # Submit all chunks to the pool
        if TQDM_AVAILABLE:
            results = list(tqdm(
                pool.imap(_process_batch, chunks),
                total=len(chunks),
                desc="  Processing chunks",
                unit="chunk"
            ))
        else:
            results = pool.map(_process_batch, chunks)

    # Merge results from all chunks
    all_sample_errors = []
    for pairs, compounds, fail_count, sample_errors in results:
        entity_structure_pairs.extend(pairs)
        failed += fail_count
        all_sample_errors.extend(sample_errors)

        # Merge new compounds, avoiding duplicates
        for structure_key, props in compounds.items():
            if structure_key not in cached_structure_to_id and structure_key not in new_compounds:
                new_compounds[structure_key] = props

    if failed > 0:
        print(f"  ⚠️  Failed to compute properties for {failed:,} compounds (invalid structures)")
        if all_sample_errors:
            print(f"  Sample of failed structures (first 10):")
            for err in all_sample_errors[:10]:
                print(f"    - {err}")

    print(f"  Successfully processed {len(entity_structure_pairs):,} structures")

    if not entity_structure_pairs:
        print("  ⚠️  No valid structures processed")
        return _empty_tables()

    used_structure_keys = {pair[1] for pair in entity_structure_pairs}

    compound_parts = []
    num_cached = 0

    if cached_compounds_df is not None and len(cached_compounds_df) > 0:
        cached_compounds_df = cached_compounds_df.filter(
            pl.col('structure_key').is_in(list(used_structure_keys))
        )
        if len(cached_compounds_df) > 0:
            compound_parts.append(cached_compounds_df)
            num_cached = len(cached_compounds_df)
            cached_structure_to_id = dict(zip(
                cached_compounds_df['structure_key'].to_list(),
                cached_compounds_df['id'].to_list(),
            ))
        else:
            cached_structure_to_id = {}
    else:
        cached_structure_to_id = {}

    # Assign IDs to newly discovered compounds
    new_rows = []
    next_id = (max(cached_structure_to_id.values()) if cached_structure_to_id else 0) + 1
    for structure_key, props in new_compounds.items():
        row = dict(props)
        row['id'] = next_id
        new_rows.append(row)
        cached_structure_to_id[structure_key] = next_id
        next_id += 1

    if new_rows:
        new_compounds_df = pl.DataFrame(new_rows).select([
            'id',
            'structure_key',
            'canonical_smiles',
            'inchi',
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
        compound_parts.append(new_compounds_df)
        num_new = len(new_rows)
    else:
        num_new = 0

    if not compound_parts:
        print("  ⚠️  No valid compound properties computed")
        compound_df = pl.DataFrame(schema=empty_compound_schema)
    else:
        compound_df = pl.concat(compound_parts, how='diagonal_relaxed').sort('id').with_columns([
            pl.col('id').cast(pl.UInt32),
        ])

    # Build entity-compound join table
    entity_compound_rows = []
    missing_keys = 0
    for entity_id, structure_key in entity_structure_pairs:
        compound_id = cached_structure_to_id.get(structure_key)
        if compound_id is None:
            missing_keys += 1
            continue
        entity_compound_rows.append({
            "entity_id": entity_id,
            "compound_id": compound_id,
        })

    if missing_keys > 0:
        print(f"  ⚠️  Missing compound IDs for {missing_keys:,} entity mappings")

    if entity_compound_rows:
        entity_compound_df = pl.DataFrame(entity_compound_rows).with_columns([
            pl.col('compound_id').cast(pl.UInt32),
        ]).sort('entity_id')
    else:
        entity_compound_df = pl.DataFrame(schema=empty_entity_compound_schema)

    print(f"\n✓ Structures processed: {len(entity_structure_pairs):,}")
    print(f"  - Compounds reused from cache: {num_cached:,}")
    print(f"  - Compounds newly computed: {num_new:,}")
    print(f"  - Total unique compounds: {len(compound_df):,}")

    return compound_df, entity_compound_df
