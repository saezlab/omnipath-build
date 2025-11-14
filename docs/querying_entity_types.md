# Querying Entity Types in OmniPath Gold Tables

## Overview

The OmniPath gold tables use controlled vocabulary (CV) terms to represent entity types. The `entity_type_id` column in the `entity.parquet` table is not a simple enum - it's an `entity_id` that references a CV term entity stored in the database.

## Understanding the Data Model

1. **Entity Types are Entities**: Entity types (like "PROTEIN", "LIPID", etc.) are themselves stored as entities in the database
2. **CV Terms**: These entity type entities have a special identifier of type `OM:0204` (CV_TERM_ACCESSION)
3. **Names**: Each entity type also has a human-readable name stored with type `OM:0202` (NAME)
4. **Accessions**: Each entity type has an accession string (e.g., "MI:0326" for protein, "OM:0013" for INTERACTION)
5. **Mapping**: The `entity.entity_type_id` → `entity_identifier.entity_id` → `entity_identifier.identifier` gives you both the CV accession and name

## Query Pattern: Get Entity Counts by Type Name

### Optimized SQL Query (Recommended)

The `entity_identifier` table has ~22 million rows, so it's important to avoid multiple scans with `IN` clauses. This optimized query scans the table only once:

```sql
WITH
-- Get the type_id for CV_TERM_ACCESSION and NAME once
special_types AS (
    SELECT
        MAX(CASE WHEN identifier = 'OM:0204' THEN entity_id END) as cv_term_type_id,
        MAX(CASE WHEN identifier = 'OM:0202' THEN entity_id END) as name_type_id
    FROM 'databases/omnipath/output/entity_identifier.parquet'
    WHERE identifier IN ('OM:0204', 'OM:0202')
),
-- Get all entity identifiers in one scan
all_identifiers AS (
    SELECT
        entity_id,
        type_id,
        identifier
    FROM 'databases/omnipath/output/entity_identifier.parquet'
),
-- Find CV term accessions
cv_accessions AS (
    SELECT
        ai.entity_id,
        ai.identifier as accession
    FROM all_identifiers ai
    CROSS JOIN special_types st
    WHERE ai.type_id = st.cv_term_type_id
),
-- Find names
cv_names AS (
    SELECT
        ai.entity_id,
        ai.identifier as name
    FROM all_identifiers ai
    CROSS JOIN special_types st
    WHERE ai.type_id = st.name_type_id
),
-- Combine accession and name for each entity type
entity_type_info AS (
    SELECT
        ca.entity_id,
        ca.accession,
        cn.name
    FROM cv_accessions ca
    LEFT JOIN cv_names cn ON ca.entity_id = cn.entity_id
),
-- Count entities by type
entity_counts AS (
    SELECT
        entity_type_id,
        COUNT(*) as count
    FROM 'databases/omnipath/output/entity.parquet'
    GROUP BY entity_type_id
)
SELECT
    eti.name,
    eti.accession,
    ec.entity_type_id,
    ec.count
FROM entity_counts ec
LEFT JOIN entity_type_info eti ON ec.entity_type_id = eti.entity_id
ORDER BY ec.count DESC
```

### Using DuckDB CLI

```bash
duckdb -c "
WITH
special_types AS (
    SELECT
        MAX(CASE WHEN identifier = 'OM:0204' THEN entity_id END) as cv_term_type_id,
        MAX(CASE WHEN identifier = 'OM:0202' THEN entity_id END) as name_type_id
    FROM 'databases/omnipath/output/entity_identifier.parquet'
    WHERE identifier IN ('OM:0204', 'OM:0202')
),
all_identifiers AS (
    SELECT entity_id, type_id, identifier
    FROM 'databases/omnipath/output/entity_identifier.parquet'
),
cv_accessions AS (
    SELECT ai.entity_id, ai.identifier as accession
    FROM all_identifiers ai
    CROSS JOIN special_types st
    WHERE ai.type_id = st.cv_term_type_id
),
cv_names AS (
    SELECT ai.entity_id, ai.identifier as name
    FROM all_identifiers ai
    CROSS JOIN special_types st
    WHERE ai.type_id = st.name_type_id
),
entity_type_info AS (
    SELECT ca.entity_id, ca.accession, cn.name
    FROM cv_accessions ca
    LEFT JOIN cv_names cn ON ca.entity_id = cn.entity_id
),
entity_counts AS (
    SELECT entity_type_id, COUNT(*) as count
    FROM 'databases/omnipath/output/entity.parquet'
    GROUP BY entity_type_id
)
SELECT eti.name, eti.accession, ec.entity_type_id, ec.count
FROM entity_counts ec
LEFT JOIN entity_type_info eti ON ec.entity_type_id = eti.entity_id
ORDER BY ec.count DESC
"
```

## Key Points

1. **Performance**: The `entity_identifier` table has ~22 million rows. Avoid using `IN (SELECT ...)` subqueries as they cause multiple full table scans.
2. **Single Scan Strategy**: The optimized query uses CTEs to scan the large table once, filtering for both CV term accessions (`OM:0204`) and names (`OM:0202`) in parallel.
3. **CV Term Identifiers**:
   - `OM:0204` - CV_TERM_ACCESSION identifier type (stores accessions like "MI:0326", "OM:0013")
   - `OM:0202` - NAME identifier type (stores human-readable names like "protein", "INTERACTION")
4. **Accession Formats**:
   - `MI:XXXX` - PSI-MI standard terms
   - `OM:XXXX` - OmniPath custom terms

## Why This Approach is Fast

The slow approach (using `IN` with subqueries):
- Scans `entity_identifier` to find `OM:0204` entity
- Scans again to find all CV term accessions
- Scans again to find `OM:0202` entity
- Scans again to find all names
- **Result**: 4+ full table scans of 22M rows

The fast approach (using CTEs):
- Single scan to find both `OM:0204` and `OM:0202` entities
- Single scan of all identifiers, filtering in parallel for both types
- **Result**: 2 table scans instead of 4+

## Reference: Common Entity Types

Entity types from the database (as of last query):

| Name | Accession | Count |
|------|-----------|-------|
| INTERACTION | OM:0013 | 3,566,080 |
| LIPID | OM:0011 | 696,799 |
| small molecule | MI:0328 | 221,536 |
| protein | MI:0326 | 90,712 |
| CV_TERM | OM:0012 | 50,743 |
| SYNTHETIC_ORGANIC | OM:0020 | 2,626 |
| PEPTIDE | OM:0024 | 1,117 |
| protein complex | MI:0315 | 250 |

See `pypath/pypath/internals/cv_terms/entity_types.py` for the full EntityTypeCv enum definition.
