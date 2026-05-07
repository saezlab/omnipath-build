# Columnar processing design notes

## Question

Can the input pipeline move from row/object mapping to fully columnar processing?

Short answer: yes, but it should be treated as a change in the **raw → silver contract**, not as a blanket replacement of parsers with `polars.scan_csv`. The durable boundary should be columnar parquet. Exotic formats can first be normalized into a tabular parquet intermediate, then mapped to canonical silver tables columnarly.

## Current design from first principles

The current flow is roughly:

```text
raw resource parser
  -> yields Python rows or Entity objects
  -> declarative EntityBuilder maps row -> nested SilverEntity
  -> SilverTableWriter flattens SilverEntity -> canonical silver parquet tables
  -> gold/id-resolver operate on parquet with Polars
```

### Why this design exists

1. **Uniform semantic model across heterogeneous resources**

   Sources include TSV, CSV, zip/tar archives, SQLite, XML, RDF/OWL, SDF, RDA, OBO and ad-hoc APIs. A row/object mapper provides one semantic target:

   - entity type
   - identifiers
   - annotations
   - memberships
   - membership annotations

   This keeps source modules declarative even when raw formats are not columnar.

2. **Streaming-friendly ingestion**

   A parser can yield one record at a time. This is important for large XML, TSV, compressed archives and remote data. The writer buffers only batches before writing parquet.

3. **Expressive mapping DSL**

   `FieldConfig`, `Column`, `CV`, `EntityBuilder`, `MembersFromList` support:

   - regex extraction
   - delimiter splitting
   - per-cell mapping
   - callable selectors
   - per-row deduplication
   - index-aligned member lists
   - nested memberships

   This is easy to reason about in Python and works for many weird source formats.

4. **Clean downstream physical schema**

   The current silver tables are already the useful columnar boundary:

   ```text
   entity_occurrence.parquet
   entity_identifier.parquet
   entity_annotation.parquet
   membership.parquet
   membership_annotation.parquet
   resource.parquet
   ```

   Gold canonicalization already consumes these with Polars and avoids rebuilding nested entities.

5. **Failure isolation and debuggability**

   Per-source parsers and row-level validation make it easier to locate malformed input records, invalid identifiers and source-specific quirks.

### Main cost of the current design

The expensive part is raw → silver for large tabular sources:

```text
columnar file -> Python dict rows -> Python objects -> flattened parquet rows
```

This creates avoidable overhead:

- Python object allocation per row, identifier, annotation and member
- repeated regex/string parsing in Python
- per-row caches and dedupe sets
- large delimited member lists represented as Python strings/lists
- poor CPU cache locality compared with vectorized array operations

For simple large TSV/CSV sources, the semantic mapping is declarative but the execution engine is row-oriented.

## Important distinction: parser columnarity vs mapper columnarity

Using `polars.scan_csv` in a parser helps only when the parser itself performs heavy tabular work: filtering, joins, grouping, projection.

But if the result is immediately converted back to Python dicts for `EntityBuilder`, the pipeline is still row/object-bound.

The larger architectural improvement is a columnar mapper that writes canonical silver tables directly:

```text
raw file(s)
  -> LazyFrame/DataFrame intermediate
  -> vectorized mapping expressions
  -> silver parquet tables
```

## Proposed principle

Every source should pass through a **source-normalized tabular parquet layer** before semantic mapping, except sources that are already trivial row streams.

```text
raw native format
  -> source-normalized parquet dataset
  -> canonical silver tables
  -> gold/id-resolver
```

For normal CSV/TSV/SQLite this intermediate can be virtual/lazy. For exotic formats it is materialized.

Examples:

```text
CSV/TSV/gz       -> scan_csv LazyFrame -> silver tables
SQLite           -> SQL query or parquet export -> silver tables
XML/SDF/RDF/RDA  -> parser emits normalized parquet -> silver tables
FooDB tar CSVs   -> extracted/scan_csv tables -> joined aggregate parquet -> silver tables
```

## Alternative routes

### Route A: Keep current design, optimize individual parsers

Use Polars/DuckDB only inside heavy parsers, then yield dict rows to current `EntityBuilder`.

Pros:

- minimal architecture change
- low risk
- source-by-source migration
- useful for sources with heavy joins/group-bys, e.g. FooDB

Cons:

- still pays Python row/object mapping cost
- Polars can increase memory if `.collect()` produces large string/list aggregates
- no global improvement for BindingDB/IntAct/STITCH-style row-heavy mappings

Best for:

- quick fixes
- weird parsers with obvious pandas/list bottlenecks

### Route B: Add a columnar execution backend for the existing mapping DSL

Keep `EntityBuilder` as the semantic declaration, but compile vectorizable parts to Polars expressions.

Conceptually:

```python
EntityBuilder(...).compile_polars(raw_lf).write_silver_tables(...)
```

The compiler would translate:

- constant CV term -> `pl.lit(...)`
- `f("column")` -> `pl.col("column")`
- regex extract -> `str.extract`
- delimiter split -> `str.split` + `explode`
- mapping dict -> `replace`/join lookup
- annotations/identifiers -> long tables
- `MembersFromList` -> positional list explosion

Fallback to row mode when unsupported:

- arbitrary callable selectors
- arbitrary Python transforms
- non-tabular row shapes
- complex nested source-specific logic

Pros:

- preserves source declarations
- incremental adoption
- direct canonical silver output
- best path for large tabular resources

Cons:

- compiler complexity
- not every DSL feature is vectorizable
- must exactly match current row semantics for dedupe, null handling and placeholder handling
- careful testing required

Best for:

- BindingDB
- IntAct
- STITCH
- UniProt-like TSVs
- CellPhoneDB/ConnectomeDB/MRCLinksDB/simple CSV resources

### Route C: Introduce explicit source-normalized schemas plus mapping specs

Instead of compiling the existing DSL, define each source in two layers:

1. raw parser writes normalized parquet tables
2. a declarative columnar mapping spec maps those tables to silver

Example:

```text
stitch.links.parquet
stitch.actions.parquet
  -> columnar join/filter/dedup
  -> silver tables
```

Pros:

- cleanest columnar architecture
- easier to optimize joins and multi-table inputs
- natural for source-specific normalized artifacts
- excellent for debugging and reuse

Cons:

- more boilerplate per source
- duplicates some existing `EntityBuilder` semantics unless shared carefully
- larger migration

Best for:

- complex multi-file tabular sources
- sources where normalized intermediate has independent value
- sources with expensive parser work that should be cached

### Route D: Use DuckDB as the physical execution engine

Represent raw/extracted/intermediate data as parquet/CSV and use DuckDB SQL to produce canonical silver tables.

Pros:

- excellent larger-than-memory joins/group-bys
- good for huge CSV/parquet and SQLite-like workloads
- SQL can express many mapping operations directly
- easier spill-to-disk story than Polars for some workloads

Cons:

- separate expression language from Python DSL
- regex/list/member semantics need care
- Python UDFs would reduce benefits

Best for:

- STITCH joins
- BindingDB/IntAct filtering/projection
- global combine-like operations
- sources exceeding RAM in Polars eager collect

## What “fully columnar” means here

A truly columnar raw → silver path should produce the five silver tables directly, not nested `Entity` objects.

### Entity occurrences

One row per parent/member occurrence:

```text
occurrence_id
parent_occurrence_id
entity_role
entity_type
source
dataset
row_number
```

Columnar generation:

- parent occurrence IDs from row index
- member occurrence IDs from exploded member index
- stable IDs from dataset + row/member index

### Identifiers

Long table:

```text
occurrence_id
identifier_type
identifier
source
dataset
```

Columnar generation:

- one expression per CV identifier
- concatenate vertical outputs
- filter null/blank/placeholder
- unique by occurrence/type/value

### Annotations

Same idea:

```text
occurrence_id
term
value
unit
source
dataset
```

### Memberships

For member lists:

```text
membership_id
parent_occurrence_id
member_occurrence_id
```

Columnar generation requires preserving member index alignment. This is the hardest but feasible part:

- split each member field to list
- normalize blanks to nulls/placeholders
- create positional index per list element
- join/explode all member identifier/annotation expressions on `(row_number, member_index)`
- emit member occurrence only if member has at least one identifier

## Recommended path

### Phase 1: Preserve current silver contract

Do not change gold or id-resolver first. They already benefit from the canonical silver parquet tables.

Keep this invariant:

```text
All source ingestion methods must produce the same silver tables.
```

### Phase 2: Add optional source-normalized parquet cache

Add a conventional location:

```text
<source silver dir>/_raw_parquet/<dataset>/<table>.parquet
```

or a staging-only equivalent.

For exotic formats, parsers write these tables first. For CSV/TSV, this can be skipped if `scan_csv` is cheap and deterministic.

### Phase 3: Add `ColumnarSilverWriter` / `ColumnarEntityMapper`

A new backend should accept LazyFrames/DataFrames and write canonical silver tables.

Start with a small subset of mapping features:

- constant terms
- direct column values
- regex extract
- delimiter split/explode
- dict mapping
- simple annotations
- simple member pairs

Unsupported constructs fall back to row mode.

### Phase 4: Migrate high-impact sources

Order should be based on size and row/object overhead:

1. STITCH: multi-file join/filter; good DuckDB/Polars candidate
2. BindingDB: huge TSV, many identifiers
3. IntAct: huge MITAB TSV, many identifier regex extractions
4. UniProt-like TSVs if build time is high
5. FooDB: already partly columnar; can remove final dict/entity object path later

Do not prioritize small/simple sources.

## Design guardrails

1. **Columnar path must be semantically equivalent**

   For each migrated source, compare silver table row counts and sampled records against row mode.

2. **Keep row mode permanently**

   Some source formats and mappings are too irregular to justify vectorization.

3. **Avoid nested parquet as the main interface**

   The canonical five silver tables are better than nested entity rows for downstream work.

4. **Prefer source-local columnarization**

   Do not force every parser to become Polars. Normalize only where it reduces memory/time.

5. **Use DuckDB when spilling matters**

   Polars is fast, but large string group-bys/list aggregations can still peak high in memory. DuckDB may be better for very large joins and aggregations.

## Conclusion

The current design is reasonable: it separates messy source parsing from a clean semantic entity model, then writes a columnar silver contract consumed by Polars gold steps.

The main inefficiency is not that gold is non-columnar; it is that large tabular sources pass through Python rows and nested objects before reaching silver parquet.

The best evolution is therefore not “replace parsers with Polars” but:

```text
keep canonical silver tables
add optional source-normalized parquet intermediates
add a columnar mapper backend for vectorizable EntityBuilder patterns
migrate only high-impact tabular sources
keep row/object mode as fallback
```
