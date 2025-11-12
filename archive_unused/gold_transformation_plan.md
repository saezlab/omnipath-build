# Gold Table Transformation Plan

## Overview
Three-phase pipeline for building gold tables with foreign key resolution:
1. **Pass 1**: Extract from sources to individual parquet files (parallel)
2. **Deduplication**: Combine and deduplicate pass1 files
3. **Pass 2**: Resolve foreign keys and create final tables

## Phase 1: Source Extraction (Parallel)

```python
# Can run in parallel - no dependencies between tables or sources
for table_name, table_def in gold_tables.items():
    for source in sources:
        # Create schema with main + temp columns
        columns = table_def["columns"]["main"] + table_def["columns"]["temp"]

        # Extract data using temp columns (string-based lookups)
        # Use DuckDB to query silver tables and transform
        conn.execute(
            f"""
            COPY (
                SELECT {build_column_list(columns)}
                FROM {source}_silver_tables
                WHERE ...
            ) TO 'gold_tables/{table_name}_pass1_{source}.parquet'
        """
        )
```

**Output**: `{table}_pass1_{source}.parquet` files
- Contains both main columns and temp columns
- No FK resolution yet (uses temp columns for lookups)
- One file per table per source

## Phase 2: Deduplication

```python
# Process tables independently (can be parallel)
for table_name, table_def in gold_tables.items():
    # Read all pass1 files for this table using DuckDB
    conn.execute(
        f"""
        COPY (
            SELECT DISTINCT ON ({table_def["constraints"]["pass1"]}) *
            FROM read_parquet('gold_tables/{table_name}_pass1_*.parquet')
            ORDER BY {priority_columns}  -- source priority or timestamp
        ) TO 'gold_tables/{table_name}_deduped.parquet'
    """
    )
```

**Output**: `{table}_deduped.parquet` files
- Combined data from all sources
- Deduplicated based on natural keys
- Still contains temp columns (needed for pass2)

## Phase 3: Foreign Key Resolution (Sequential)

```python
# Must respect table dependencies (topological order)
# Tables with no FKs first, then tables that depend on them
for table_name in topological_order(gold_tables):
    table_def = gold_tables[table_name]

    # Build FK join clauses
    joins = []
    select_cols = []

    for fk_def in table_def["foreign_keys"]:
        # Example: fk("namespace_id", "cv_namespace.name = namespace_name")
        target_table = extract_table_from_fk(fk_def["link"])
        join_condition = parse_join_condition(fk_def["link"])

        joins.append(
            f"""
            LEFT JOIN read_parquet('gold_tables/{target_table}_deduped.parquet') AS {target_table}
            ON {join_condition}
        """
        )

        select_cols.append(f"{target_table}.id AS {fk_def['id']}")

    # Get main columns (excluding temp columns)
    main_cols = [col for col in table_def["columns"]["main"]]
    all_select_cols = main_cols + select_cols

    # Resolve FKs and write final table using DuckDB
    conn.execute(
        f"""
        COPY (
            SELECT {', '.join(all_select_cols)}
            FROM read_parquet('gold_tables/{table_name}_deduped.parquet') AS main
            {' '.join(joins)}
        ) TO 'gold_tables/{table_name}.parquet'
    """
    )
```

**Output**: `{table}.parquet` files
- Final gold tables with resolved FKs
- No temp columns
- Ready for FK constraints in database

## Incremental Updates

To add a new source:

```python
# 1. Run pass1 for new source only
for table_name in gold_tables.keys():
    conn.execute(
        f"""
        COPY (
            SELECT {build_column_list(columns)}
            FROM {new_source}_silver_tables
            WHERE ...
        ) TO 'gold_tables/{table_name}_pass1_{new_source}.parquet'
    """
    )

# 2. Rerun deduplication (picks up new files automatically via glob pattern)
run_deduplication_phase()

# 3. Rerun pass2 (uses updated deduped files)
run_pass2_phase()
```

## File Structure

```
gold_tables/
├── cv_term_pass1_uniprot.parquet       # Pass1: individual sources
├── cv_term_pass1_pfam.parquet
├── cv_term_pass1_hgnc.parquet
├── cv_term_deduped.parquet             # After dedup
├── cv_term.parquet                     # Final (pass2)
│
├── cv_namespace_pass1_uniprot.parquet
├── cv_namespace_deduped.parquet
├── cv_namespace.parquet
│
└── ...
```

## Benefits

1. **Parallelization**: Pass1 sources can run concurrently
2. **Incremental**: Add new sources without reprocessing existing ones
3. **Debugging**: Inspect pass1, deduped, and final files separately
4. **Reprocessing**: Change dedup/FK logic without re-extracting
5. **Data lineage**: Clear provenance from source → pass1 → deduped → final
