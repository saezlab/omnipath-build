"""
HMDB to silver transformation using Polars.

Clean, readable transformation using the select_entities builder.
"""
import polars as pl
from pathlib import Path
import time

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from cleaning_functions import (
    normalize_inchikey,
    normalize_inchi,
    normalize_smiles,
    normalize_id,
    clean_synonyms,
)
from silver_builders import select_entities

__all__ = [
    'clean_hmdb_dataframe',
    'map_hmdb_to_silver',
    'transform_hmdb_to_silver',
]


# ============================================================================
# PHASE 1: CLEANING
# ============================================================================

def clean_hmdb_dataframe(df: pl.DataFrame) -> pl.DataFrame:
    """
    Clean HMDB bronze dataframe using Polars expressions.

    Args:
        df: Bronze dataframe

    Returns:
        Cleaned dataframe
    """
    print("  → Normalizing identifiers...")
    cleaned = df.select([
        # Identifiers
        pl.col('accession').map_elements(lambda x: normalize_id(x, 'HMDB'), return_dtype=pl.Utf8).alias('accession'),
        pl.col('chebi_id'),
        pl.col('pubchem_compound_id'),
        pl.col('kegg_id'),
        pl.col('drugbank_id'),
        pl.col('cas_registry_number'),

        # Structural identifiers
        pl.col('inchikey').map_elements(normalize_inchikey, return_dtype=pl.Utf8).alias('inchikey'),
        pl.col('inchi').map_elements(normalize_inchi, return_dtype=pl.Utf8).alias('inchi'),
        pl.col('smiles').map_elements(normalize_smiles, return_dtype=pl.Utf8).alias('smiles'),

        # Names
        pl.col('traditional_iupac'),
        pl.col('iupac_name'),
        pl.col('synonyms').map_elements(clean_synonyms, return_dtype=pl.List(pl.Utf8)).alias('synonyms'),

        # Properties
        pl.col('monisotopic_molecular_weight'),
        pl.col('average_molecular_weight'),
        pl.col('chemical_formula'),

        # References (keep as-is, can be strings or ints)
        pl.col('general_references'),
    ])

    return cleaned


# ============================================================================
# PHASE 2: MAPPING
# ============================================================================

def map_hmdb_to_silver(cleaned_df: pl.DataFrame) -> pl.DataFrame:
    """
    Map cleaned HMDB dataframe to silver entities schema.

    Uses the declarative select_entities builder for clean, readable code.

    Args:
        cleaned_df: Cleaned dataframe

    Returns:
        Dataframe with silver entity records
    """
    return select_entities(
        cleaned_df,
        # Required fields
        source='hmdb',
        entity_type='compound',
        accession='accession',

        # Structural identifiers
        inchikey='inchikey',
        inchi='inchi',
        smiles='smiles',

        # Names
        name='traditional_iupac',
        synonyms='synonyms',

        # Cross-references
        cross_references={
            'chebi': 'chebi_id',
            'pubchem_compound': 'pubchem_compound_id',
            'kegg_compound': 'kegg_id',
            'drugbank': 'drugbank_id',
            'cas': 'cas_registry_number',
        },

        # Annotations
        annotations={
            'monoisotopic_molecular_weight': 'monisotopic_molecular_weight',
            'average_molecular_weight': 'average_molecular_weight',
            'chemical_formula': 'chemical_formula',
            'iupac_name': 'iupac_name',
        },

        # References
        references='general_references',
    )


# ============================================================================
# MAIN PIPELINE
# ============================================================================

def transform_hmdb_to_silver(bronze_path: str, silver_path: str) -> None:
    """
    Complete pipeline: bronze → clean → silver using Polars.

    Args:
        bronze_path: Path to bronze parquet file
        silver_path: Path to output silver parquet file
    """
    start_time = time.time()

    print("=" * 70)
    print("HMDB → Silver Transformation (Polars)")
    print("=" * 70)

    # Load bronze
    print(f"\n📥 Loading bronze from {Path(bronze_path).name}...")
    load_start = time.time()
    bronze_df = pl.read_parquet(bronze_path)
    load_time = time.time() - load_start
    print(f"✓ Loaded {len(bronze_df):,} rows in {load_time:.1f}s")

    # Phase 1: Clean
    print(f"\n🧹 Phase 1: Cleaning...")
    clean_start = time.time()
    cleaned_df = clean_hmdb_dataframe(bronze_df)
    clean_time = time.time() - clean_start
    print(f"✓ Cleaned {len(cleaned_df):,} rows in {clean_time:.1f}s")
    print(f"  ({len(cleaned_df) / clean_time:,.0f} rows/sec)")

    # Phase 2: Map to silver
    print(f"\n🗺️  Phase 2: Mapping to silver schema...")
    map_start = time.time()
    silver_df = map_hmdb_to_silver(cleaned_df)
    map_time = time.time() - map_start
    print(f"✓ Mapped {len(silver_df):,} entities in {map_time:.1f}s")
    print(f"  ({len(silver_df) / map_time:,.0f} rows/sec)")

    # Save
    print(f"\n💾 Saving to {Path(silver_path).name}...")
    save_start = time.time()
    Path(silver_path).parent.mkdir(parents=True, exist_ok=True)
    silver_df.write_parquet(silver_path, compression='snappy')

    file_size_mb = Path(silver_path).stat().st_size / (1024 * 1024)
    save_time = time.time() - save_start
    print(f"✓ Saved {file_size_mb:.1f} MB in {save_time:.1f}s")

    # Summary
    total_time = time.time() - start_time
    print(f"\n{'='*70}")
    print(f"✅ Complete!")
    print(f"{'='*70}")
    print(f"  Total time:    {total_time:.1f}s")
    print(f"  Throughput:    {len(silver_df) / total_time:,.0f} rows/sec")
    print(f"  Output size:   {file_size_mb:.1f} MB")
    print(f"  Output:        {silver_path}")
    print(f"{'='*70}\n")


if __name__ == '__main__':
    bronze_path = "/Users/jschaul/Code/omnipath_build/databases/omnipath/data/hmdb/compounds_for_metabo/bronze/latest.parquet"
    silver_path = "/Users/jschaul/Code/omnipath_build/databases/omnipath/data/hmdb/compounds_for_metabo/silver/entities.parquet"

    transform_hmdb_to_silver(bronze_path, silver_path)
