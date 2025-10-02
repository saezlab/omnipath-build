📋 STEP-BY-STEP PLAN: Source-by-Source DuckDB Processing
Phase 1: Architecture Design & Setup
Current State Analysis:
HMDB: Produces silver_entities (compounds with molecular properties)
PSI-MI: Produces silver_cv_terms (controlled vocabulary ontology)
Both use custom transformation functions
Gold layer has complex PostgreSQL stored procedures
Step 1: Create New Directory Structure
omnipath_build/
├── new_loaders/              # New DuckDB-only loaders
│   ├── __init__.py
│   ├── source_processor.py   # Main orchestrator
│   └── duckdb_transforms.py  # DuckDB transformation functions
├── databases/metabo/
│   ├── bronze/data/          # Existing: Parquet files
│   ├── silver_parquet/       # NEW: Intermediate silver Parquet
│   └── gold_parquet/         # NEW: Final gold Parquet (optional)
Step 2: Design Source-by-Source Processing Flow
For each source (HMDB, PSI-MI):
1. Bronze Parquet (exists)
   ↓
2. Apply Silver Transformations (DuckDB)
   ↓ 
3. Write Silver Parquet
   ↓
4. Apply Gold Transformations (DuckDB)
   ↓
5. Upsert to PostgreSQL Gold (minimal writes)
Step 3: Convert Silver Transformation Functions to DuckDB
Current functions to convert:
build_identifier_list_hmdb() - PostgreSQL function
coalesce() - Standard, should work
to_json() - Convert to DuckDB's JSON syntax
parse_bracketed_list() - Custom function
split_string_to_json() - Custom function
Action: Create DuckDB equivalents in transformation_functions.sql for DuckDB
Step 4: Implement Core DuckDB Processor
Features:
Load bronze Parquet
Apply field mappings
Execute transformations
Write silver Parquet per source
Merge/deduplicate across sources
Write to gold Parquet
Final upsert to PostgreSQL
Step 5: Convert Gold Layer Logic to DuckDB
Complex parts from 2_entities.sql:
CV term caching → Use DuckDB temp tables
Entity deduplication → GROUP BY in DuckDB
JSONB operations → DuckDB's JSON functions
Upserts → Generate MERGE statements or bulk writes
Foreign key resolution → JOIN operations
Step 6: Handle State & Incremental Processing
Tracking:
Which sources have been processed
Version/timestamp of each source
Conflict resolution between sources
Rollback capability
Step 7: PostgreSQL Write Strategy
Options:
Bulk COPY: Generate CSV → COPY command
Batch INSERT: Generate SQL with 1000s of rows
DuckDB → PostgreSQL: Use DuckDB's PostgreSQL extension
🎯 DETAILED IMPLEMENTATION PLAN
Task Breakdown:
A. Setup (1-2 hours)
Create new_loaders/ directory
Create databases/metabo/silver_parquet/ directory
Install DuckDB JSON extension if needed
B. DuckDB Transformation Functions (2-3 hours)
Convert build_identifier_list_hmdb() to DuckDB macro
Convert parse_bracketed_list() to DuckDB macro
Convert split_string_to_json() to DuckDB macro
Test each function with sample data
C. Source Processor Implementation (4-6 hours)
Create SourceProcessor class
Implement bronze → silver pipeline
Read bronze Parquet
Apply field mappings
Execute transformations
Write silver Parquet
Add validation/error handling
Add progress logging
D. Gold Layer Processing (6-8 hours)
Analyze PostgreSQL gold functions
Convert to DuckDB equivalents:
CV term lookup/caching
Entity deduplication logic
Identifier merging
Type-specific table population
Implement incremental merge strategy
E. PostgreSQL Writer (2-3 hours)
Implement bulk write from DuckDB
Handle conflicts (upsert vs merge)
Transaction management
Rollback capability
F. Testing (4-6 hours)
Test HMDB processing end-to-end
Test PSI-MI processing end-to-end
Verify data integrity
Compare with original pipeline results
Performance benchmarking
G. Documentation & CLI (2-3 hours)
Add command-line interface
Document new workflow
Migration guide from old system
📊 Key Design Decisions Needed:
Decision 1: Gold Layer Storage
Option A: Keep PostgreSQL for gold (hybrid)
Option B: Gold as Parquet, PostgreSQL as query layer only
Recommendation: Start with Option A (easier migration)
Decision 2: Deduplication Strategy
Per-source silver: Each source gets own silver Parquet
Merged silver: One deduplicated silver Parquet across all sources
Recommendation: Per-source (simpler, allows re-processing)
Decision 3: Incremental Updates
Full rebuild: Re-process all sources each time
Incremental: Track what changed, merge updates
Recommendation: Start with full rebuild (simpler), add incremental later
🚀 Minimal Viable Implementation (MVP)
To prove the concept, let's start with:
HMDB only (simpler - just entities, no interactions)
Bronze → Silver Parquet (DuckDB transformations)
Silver Parquet → PostgreSQL Gold (bulk insert, skip complex dedup for now)
Verify data matches original pipeline
MVP Scope (~8-12 hours total):
Single source processor for HMDB
DuckDB transformations for HMDB fields
Direct write to PostgreSQL gold (simplified)
Manual verification
📝 Next Steps - What Should We Implement First?
I recommend we start with:
Create the transformation functions for DuckDB (2-3 hours)
This is foundational and self-contained
Easy to test independently
Build simple source processor for HMDB (3-4 hours)
Bronze → Silver Parquet only
No gold layer yet
Validate transformations work
Then add PostgreSQL gold write (2-3 hours)
Simplified entity insertion
Skip complex deduplication initially
