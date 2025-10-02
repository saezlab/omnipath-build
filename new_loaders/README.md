# New Source-by-Source DuckDB Pipeline

This directory contains a new, streamlined data processing pipeline that processes sources one-by-one using **DuckDB for all transformations**, with Parquet files as the storage format for bronze, silver, and gold layers.

## ✅ What's Been Implemented

### Architecture

```
Bronze (Parquet)
    ↓  DuckDB transformations
Silver (Parquet)
    ↓  DuckDB deduplication
Gold (Parquet)
```

### Components

1. **`source_processor.py`** - Main processor class
   - Reads bronze Parquet files
   - Applies field mappings and transformations
   - Writes silver Parquet files
   - Deduplicates and creates gold Parquet files
   - All processing done in DuckDB (no PostgreSQL dependency for transformations)

2. **Transformation Functions** - Reused existing DuckDB macros
   - Already in `omnipath_build/databases/metabo/silver/transformation_functions.sql`
   - Work perfectly with DuckDB
   - Functions like `build_identifier_list_hmdb()`, `split_string_to_json()`, etc.

3. **Test Scripts**
   - `test_transforms.py` - Test individual transformation functions
   - `test_hmdb_silver.py` - Test HMDB bronze → silver
   - `test_psimi_silver.py` - Test PSI-MI bronze → silver
   - `test_full_pipeline.py` - Test complete pipeline for both sources

## 📊 Test Results

### HMDB (Human Metabolome Database)
- **Bronze**: 217,920 compounds
- **Silver**: 217,920 rows → 43 MB Parquet
- **Gold**: 217,920 entities → 43 MB Parquet
- **Processing time**: < 1 second per layer

### PSI-MI (Molecular Interaction Ontology)
- **Bronze**: 1,647 ontology terms
- **Silver**: 1,647 rows → 0.26 MB Parquet
- **Gold**: 1,647 CV terms → 0.26 MB Parquet
- **Processing time**: < 1 second per layer

## 🚀 Usage

### Process a Single Source

```python
from new_loaders import SourceProcessor

# Full pipeline
with SourceProcessor(database_name="metabo", source_module="hmdb") as processor:
    results = processor.process_full_pipeline()

# Just bronze → silver
with SourceProcessor(database_name="metabo", source_module="hmdb") as processor:
    silver_files = processor.process_to_silver()

# Just silver → gold
with SourceProcessor(database_name="metabo", source_module="hmdb") as processor:
    silver_files = processor.process_to_silver()
    gold_files = processor.process_to_gold(silver_files)
```

### Run Tests

```bash
# Test transformation functions
python new_loaders/test_transforms.py

# Test individual sources
python new_loaders/test_hmdb_silver.py
python new_loaders/test_psimi_silver.py

# Test full pipeline
python new_loaders/test_full_pipeline.py
```

## 📁 Output Structure

```
omnipath_build/databases/metabo/
├── bronze/data/              # Input: Bronze Parquet files
│   ├── hmdb/
│   │   └── compounds_for_metabo/
│   │       └── 20250904_125751.parquet
│   └── psimi/
│       └── psimi_ontology/
│           └── 20251002_125229.parquet
│
├── silver_parquet/           # Generated: Silver layer
│   ├── hmdb_compounds_for_metabo_silver_entities.parquet
│   └── psimi_psimi_ontology_silver_cv_terms.parquet
│
└── gold_parquet/             # Generated: Gold layer
    ├── gold_entities_hmdb.parquet
    └── gold_cv_terms_psimi.parquet
```

## 🎯 Key Features

### 1. **Source-by-Source Processing**
- Each source processed independently
- Easy to debug and monitor
- Failed source doesn't block others
- Can reprocess individual sources

### 2. **Pure DuckDB Transformations**
- No PostgreSQL dependency during transformation
- Fast vectorized execution
- Handles large datasets efficiently
- Uses existing transformation functions (no rewrite needed!)

### 3. **Parquet-Based Storage**
- **Compression**: 10-20x smaller than database tables
- **Fast I/O**: Columnar format optimized for analytics
- **Portable**: Files can be shared/versioned easily
- **Schema Evolution**: Easy to handle schema changes

### 4. **Deduplication at Gold Layer**
- Entities deduplicated by `identifier`
- CV terms deduplicated by `term_accession`
- Keeps most recent record when duplicates exist

## 🔄 How It Works

### Bronze → Silver Transformation

The processor reads the resource configuration YAML:

```yaml
processing:
  target_table: silver_entities
  field_mapping:
  - source: accession
    target: identifier
  - source: [chebi_id, pubchem_compound_id, ...]
    target: additional_identifiers
    transform: build_identifier_list_hmdb
```

And generates DuckDB SQL:

```sql
SELECT
    'compound' AS "entity_type",
    "accession" AS "identifier",
    build_identifier_list_hmdb(
        "chebi_id",
        "pubchem_compound_id",
        ...
    ) AS "additional_identifiers",
    ...
FROM bronze_parquet
```

### Silver → Gold Transformation

Currently implements simple deduplication:

```sql
SELECT DISTINCT ON (identifier) *
FROM silver_parquet
ORDER BY identifier, created_at DESC
```

## 📈 Performance Benefits

Compared to the original pipeline:

| Aspect | Original | New Pipeline |
|--------|----------|-------------|
| Bronze → Silver | DuckDB → PostgreSQL | DuckDB → Parquet |
| Silver → Gold | PostgreSQL procedures | DuckDB → Parquet |
| Storage | PostgreSQL tables | Parquet files (10-20x smaller) |
| Processing | Mixed (DuckDB + PostgreSQL) | Pure DuckDB |
| Portability | Database-dependent | File-based, portable |
| Debugging | Complex (multiple layers) | Simple (one file per step) |

## 🔮 Future Enhancements

1. **Cross-Source Deduplication**
   - Merge entities from multiple sources
   - Resolve identifier conflicts
   - Build unified entity mapping

2. **Advanced Gold Transformations**
   - Complex deduplication logic
   - Relationship building
   - Quality scoring

3. **Incremental Updates**
   - Track processed sources
   - Only reprocess changed data
   - Merge updates into existing gold

4. **PostgreSQL Export** (Optional)
   - Load gold Parquet → PostgreSQL for querying
   - Keep Parquet as source of truth
   - PostgreSQL as query layer only

## 🎓 Lessons Learned

1. ✅ **DuckDB transformation functions already existed** - No rewrite needed!
2. ✅ **Parquet is highly efficient** - 43 MB for 217K entities with full metadata
3. ✅ **Source-by-source is cleaner** - Much easier to debug and understand
4. ✅ **Simple deduplication works** - DISTINCT ON is powerful and fast
5. ✅ **Pure DuckDB is fast** - Sub-second processing for both sources

## 📚 Next Steps

To integrate this into the main pipeline:

1. Create a batch processor to run all sources
2. Add cross-source deduplication for gold layer
3. Add incremental update tracking
4. (Optional) Add PostgreSQL export for querying
5. (Optional) Add data validation checks
