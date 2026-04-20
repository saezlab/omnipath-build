# PostgreSQL combined schema index review

Reviewed file: `omnipath_build/postgres_combined.py`

## Executive summary

The current schema is already fairly light, but there are a few places where the index set can be simplified without losing the most important access paths.

My main conclusion:

- keep all **primary key / unique indexes**
- keep the **materialized view indexes**
- keep a strong index for **identifier lookup** on `entity_identifier`
- consider keeping `entity_identifier(entity_pk)` if entity-to-identifiers lookups are common
- the weakest / easiest-to-drop indexes are the raw-table **`sources` GIN indexes**, `entity.taxonomy_id`, and the standalone `entity_identifier(identifier_type)` index
- the current **hash index on `entity_identifier(identifier)`** is the one I would most strongly replace or remove

If you want a simplified but still practical set, I would reduce the explicit base-table indexes to:

- `entity_identifier(identifier, identifier_type)` using **btree**
- `entity_identifier(entity_pk)`
- optionally `entity_annotation(cv_term)`
- optionally `interaction_annotation(cv_term)`

and keep the current materialized view indexes.

---

## 1. Index inventory

## Implicit indexes from primary keys

These are automatically created by PostgreSQL and should be considered part of the baseline:

- `entity(entity_pk)`
- `entity_identifier(id)`
- `interaction_evidence(source, interaction_pk)`
- `interaction(interaction_pk)`
- `association_evidence(source, association_pk)`
- `association(association_pk)`
- `entity_annotation(entity_pk, cv_term)`
- `interaction_annotation(interaction_pk, cv_term)`

These are all reasonable and should stay.

## Explicit secondary indexes in `create_secondary_indexes`

Defined in `omnipath_build/postgres_combined.py`:

- `entity_taxonomy_idx` on `entity(taxonomy_id)`
- `entity_sources_gin_idx` on `entity using gin(sources)`
- `entity_identifier_type_idx` on `entity_identifier(identifier_type)`
- `entity_identifier_entity_pk_idx` on `entity_identifier(entity_pk)`
- `entity_identifier_value_hash_idx` on `entity_identifier using hash(identifier)`
- `interaction_sources_gin_idx` on `interaction using gin(sources)`
- `association_sources_gin_idx` on `association using gin(sources)`
- `entity_annotation_cv_term_idx` on `entity_annotation(cv_term)`
- `interaction_annotation_cv_term_idx` on `interaction_annotation(cv_term)`

## Explicit indexes on materialized views

Also defined in `postgres_combined.py`:

- `entity_summary_pk_idx` on `entity_summary(entity_pk)` unique
- `entity_filter_counts_key_value_idx` on `entity_filter_counts(filter_key, filter_value)` unique
- `entity_filter_counts_key_count_idx` on `entity_filter_counts(filter_key, doc_count desc, filter_value)`
- `interaction_filter_counts_key_value_idx` on `interaction_filter_counts(filter_key, filter_value)` unique
- `interaction_filter_counts_key_count_idx` on `interaction_filter_counts(filter_key, doc_count desc, filter_value)`

These all look justified.

---

## 2. How the current code actually uses these tables

From `postgres_combined.py` itself, the important derived queries are:

- `entity_summary`
  - groups `entity_identifier` by `entity_pk`
  - groups `entity_annotation` by `entity_pk`
  - counts interactions by `entity_a_pk` / `entity_b_pk`
- `entity_filter_counts`
  - scans `entity`
  - unnests `entity.sources`
- `interaction_filter_counts`
  - scans `interaction`
  - joins `interaction.entity_a_pk` and `interaction.entity_b_pk` to `entity.entity_pk`

Important implication:

- the current **raw-table `sources` GIN indexes do not help these materialized view builds**, because the code is not doing `WHERE sources @> ...` or overlap predicates here; it is scanning and unnesting
- the current `taxonomy_id` index also does not help any of the derived object builds in this file
- the most obviously useful current secondary index is on `entity_identifier`, because identifiers are the most likely direct lookup path in downstream applications

---

## 3. Assessment of each explicit index

## `entity_taxonomy_idx` on `entity(taxonomy_id)`

### Value
Moderate to low.

### Why
This is only useful if you frequently filter entities directly by taxonomy, e.g.:

```sql
where taxonomy_id = '9606'
```

There is no evidence in this file that the derived objects depend on it.

### Recommendation
**Drop unless direct taxonomy filtering on `entity` is a common API/query path.**

If most user-facing filtering happens through precomputed views or through entity identifiers, this index is probably not essential.

---

## `entity_sources_gin_idx` on `entity using gin(sources)`

### Value
Low to moderate.

### Why
A GIN array index is useful for queries like:

```sql
where sources @> array['Reactome']
```

or overlap checks.

But in the current code:

- filter counts are computed by full scan + `unnest(sources)`
- there is no direct evidence here of heavy raw-table filtering on `entity.sources`

GIN indexes also have noticeably higher write/build cost than simple btree indexes.

### Recommendation
**Good candidate to drop** if the intended query path is through the materialized filter-count objects rather than raw table filtering.

---

## `entity_identifier_type_idx` on `entity_identifier(identifier_type)`

### Value
Low.

### Why
A standalone index on `identifier_type` is usually only helpful if queries commonly ask for:

```sql
where identifier_type = 'uniprot'
```

across a large table.

In practice, identifier lookup usually starts from the identifier value itself, often together with type.

A type-only index is often too low-selectivity to be one of the most vital indexes.

### Recommendation
**Drop** in a simplified design.

If you need type-aware lookup, a composite btree like `(identifier, identifier_type)` is much more useful.

---

## `entity_identifier_entity_pk_idx` on `entity_identifier(entity_pk)`

### Value
High.

### Why
This supports:

- fetching all identifiers for an entity
- joins / aggregations by `entity_pk`
- the `entity_summary` materialized view's identifier-count aggregation

Even if PostgreSQL may still choose a sequential scan for full-table aggregation, this index is a sensible and inexpensive access path for entity-centric lookups.

### Recommendation
**Keep.**

This is one of the clearer "vital" non-PK indexes in the schema.

---

## `entity_identifier_value_hash_idx` on `entity_identifier using hash(identifier)`

### Value
Questionable.

### Why
Hash indexes in PostgreSQL are much narrower in usefulness than btree indexes:

- equality only
- cannot help with ordering
- cannot support prefix/pattern use cases
- cannot be extended as flexibly into composite access paths

For an identifier table, a **btree** index is almost always the safer and more generally useful choice.

### Recommendation
**Replace or remove.**

Preferred replacement:

```sql
create index ... on entity_identifier (identifier, identifier_type)
```

If you want maximum simplicity, even just:

```sql
create index ... on entity_identifier (identifier)
```

would usually be more broadly useful than a hash index.

This is the single index I would most strongly change.

---

## `interaction_sources_gin_idx` on `interaction using gin(sources)`

### Value
Low to moderate.

### Why
Same reasoning as for `entity.sources`.

Useful only if downstream queries directly filter aggregated interactions by source membership on the raw table.

The materialized filter-count view does not benefit from it.

### Recommendation
**Drop unless raw interaction table source filtering is a known hot path.**

---

## `association_sources_gin_idx` on `association using gin(sources)`

### Value
Low to moderate.

### Why
Same tradeoff as above.

There is no derived-object logic in this file that clearly benefits from it.

### Recommendation
**Drop unless raw association source filtering is known to be common.**

---

## `entity_annotation_cv_term_idx` on `entity_annotation(cv_term)`

### Value
Moderate.

### Why
The primary key on `(entity_pk, cv_term)` already supports entity-first access.

This extra index helps the reverse direction:

- find entities by annotation term
- compute term-based slices efficiently

If annotation-term search/faceting is part of the product, this is useful.

### Recommendation
**Keep if annotation-term filtering exists; otherwise optional.**

Among the "non-vital" indexes, this is more defensible than the `sources` GIN indexes.

---

## `interaction_annotation_cv_term_idx` on `interaction_annotation(cv_term)`

### Value
Moderate.

### Why
Same pattern as `entity_annotation_cv_term_idx`.

The PK supports interaction-first access; this index supports term-first access.

### Recommendation
**Keep if interaction annotation term filtering exists; otherwise optional.**

---

## 4. Materialized view indexes

These should stay.

## `entity_summary_pk_idx`

Needed for fast lookup by entity PK on the summary view, and generally cheap/appropriate.

## `entity_filter_counts_*`

Both indexes are well aligned with likely access patterns:

- exact row lookup by `(filter_key, filter_value)`
- listing values for a key ordered by count

These are small and useful.

## `interaction_filter_counts_*`

Same reasoning as above.

### Recommendation
**Keep all materialized-view indexes as-is.**

These are likely low-cost and high-value.

---

## 5. Simplification options

## Option A: conservative simplification

Keep:

- all PK / unique indexes
- all materialized-view indexes
- `entity_identifier(entity_pk)`
- replace hash index with `entity_identifier(identifier, identifier_type)` btree
- `entity_annotation(cv_term)`
- `interaction_annotation(cv_term)`

Drop:

- `entity(taxonomy_id)`
- all three raw-table `sources` GIN indexes
- standalone `entity_identifier(identifier_type)`

This is my preferred option.

---

## Option B: aggressive simplification

Keep:

- all PK / unique indexes
- all materialized-view indexes
- `entity_identifier(identifier, identifier_type)` btree
- `entity_identifier(entity_pk)`

Drop everything else in `create_secondary_indexes`.

This is the smallest set I would still call practical.

---

## Option C: ultra-minimal

Keep only:

- PK / unique indexes
- materialized-view indexes
- one identifier lookup index on `entity_identifier(identifier, identifier_type)`

This is the absolute minimum I'd be comfortable with, but I would expect some entity-to-identifier and annotation-term queries to slow down.

---

## 6. Recommended final set

If the goal is to simplify while preserving the most vital indexes, I recommend this target set.

## Base tables

Keep:

- implicit PK/unique indexes
- `entity_identifier_lookup_idx` on `entity_identifier(identifier, identifier_type)` using **btree**
- `entity_identifier_entity_pk_idx` on `entity_identifier(entity_pk)`
- optionally `entity_annotation_cv_term_idx`
- optionally `interaction_annotation_cv_term_idx`

Drop:

- `entity_taxonomy_idx`
- `entity_sources_gin_idx`
- `entity_identifier_type_idx`
- `entity_identifier_value_hash_idx`
- `interaction_sources_gin_idx`
- `association_sources_gin_idx`

## Materialized views

Keep all existing MV indexes.

---

## 7. Important note: one missing category of indexes

Separate from simplification, there is one thing worth noticing:

- `interaction(entity_a_pk)` / `interaction(entity_b_pk)` are **not** indexed
- `association(parent_entity_pk)` / `association(member_entity_pk)` are **not** indexed
- evidence tables also do not have secondary indexes on their foreign-key columns

That may be completely fine today.

But if future query patterns become entity-centric, these might matter more than several of the current optional indexes.

So my summary is:

- several current indexes can safely be removed
- if performance issues later appear, the first new indexes I'd consider are probably endpoint foreign-key indexes, not the current `sources` GIN indexes

---

## Bottom line

The current indexing is not excessive, but it is not fully focused on the highest-value access paths.

If you want to simplify while keeping the most important performance support:

1. keep PK / unique indexes
2. keep materialized-view indexes
3. keep `entity_identifier(entity_pk)`
4. replace the hash index with a btree identifier lookup index
5. make annotation `cv_term` indexes optional based on real usage
6. remove the raw-table `sources` GIN indexes and likely `entity(taxonomy_id)`

If you want, I can next turn this review into a concrete patch to `create_secondary_indexes()` in `omnipath_build/postgres_combined.py`.
