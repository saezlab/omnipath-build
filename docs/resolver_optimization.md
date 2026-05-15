Overall: the design is **mostly in the right shape** for large-scale resolution because it is set-based, uses scoped temp tables, and avoids row-by-row Python work. But for “lots of records,” I would not call it fully efficient yet. The biggest risks are repeated large resolver joins, `md5()` calls on resolver columns, whole-table `COUNT(*)` stats, and relation reprocessing that may be much broader than the entity scope.

## Highest-impact issues

### 1. Resolver joins are probably the main bottleneck

You repeatedly join like this:

```sql
p.key_type = k.resolver_key_type
AND md5(p.key_value) = k.key_value_hash
AND p.key_value = k.key_value
```

and similarly for chemicals.

If `resolver_protein_identifier_lookup` or `resolver_chemical_identifier_lookup` is large, `md5(p.key_value)` is expensive unless you have an expression index. Better: add a stored/generated hash column to the resolver tables and join on that.

Recommended pattern:

```sql
ALTER TABLE public.resolver_protein_identifier_lookup
ADD COLUMN key_value_hash text
GENERATED ALWAYS AS (md5(key_value)) STORED;

CREATE INDEX CONCURRENTLY resolver_protein_lookup_key_hash_value_idx
ON public.resolver_protein_identifier_lookup (
  key_type,
  key_value_hash,
  key_value,
  mapping_type,
  source
)
INCLUDE (
  primary_uniprot,
  taxonomy_id
);
```

For chemicals:

```sql
ALTER TABLE public.resolver_chemical_identifier_lookup
ADD COLUMN key_value_hash text
GENERATED ALWAYS AS (md5(key_value)) STORED;

CREATE INDEX CONCURRENTLY resolver_chemical_lookup_key_hash_value_idx
ON public.resolver_chemical_identifier_lookup (
  key_type,
  key_value_hash,
  key_value,
  source
)
INCLUDE (
  standard_inchi_key,
  standard_inchi
);

CREATE INDEX CONCURRENTLY resolver_chemical_standard_inchi_idx
ON public.resolver_chemical_identifier_lookup (standard_inchi)
INCLUDE (standard_inchi_key);

CREATE INDEX CONCURRENTLY resolver_chemical_standard_inchi_key_idx
ON public.resolver_chemical_identifier_lookup (standard_inchi_key)
INCLUDE (standard_inchi);
```

Then rewrite joins as:

```sql
p.key_type = k.resolver_key_type
AND p.key_value_hash = k.key_value_hash
AND p.key_value = k.key_value
```

If you cannot alter the tables, create expression indexes instead:

```sql
CREATE INDEX CONCURRENTLY resolver_protein_lookup_expr_idx
ON public.resolver_protein_identifier_lookup (
  key_type,
  md5(key_value),
  key_value,
  mapping_type,
  source
);
```

This one change is likely to matter more than most Python-side optimization.

---

### 2. You repeat expensive protein and chemical resolver joins

The protein lookup is done in both:

```python
_create_entity_taxonomy_conflict_table()
_insert_protein_candidates()
```

The chemical lookup is done in:

```python
_insert_chemical_candidates()
_insert_standard_inchi_identity_candidates()
_insert_chemical_resolver_identifier_links()
```

For a large batch, this repeats the same large joins several times.

A better pattern is:

1. Create `_protein_resolver_match` once.
2. Use it to create taxonomy conflicts.
3. Use it to insert candidates.

For example, conceptually:

```sql
CREATE TEMP TABLE _protein_resolver_match ON COMMIT DROP AS
SELECT
  k.entity_evidence_id,
  k.entity_type,
  k.taxonomy_id AS evidence_taxonomy_id,
  p.primary_uniprot,
  NULLIF(p.taxonomy_id, '') AS resolver_taxonomy_id,
  p.source,
  k.key_type,
  p.mapping_type,
  pol.requires_taxonomy
FROM _entity_key k
JOIN public.resolver_protein_identifier_lookup p
  ON p.key_type = k.resolver_key_type
 AND p.key_value_hash = k.key_value_hash
 AND p.key_value = k.key_value
JOIN public.resolver_mapping_policy pol
  ON pol.entity_family = 'protein'
 AND pol.key_type = p.key_type
 AND COALESCE(pol.mapping_type, '') = COALESCE(p.mapping_type, '')
 AND (
      pol.resolver_source IS NULL
      OR pol.resolver_source = p.source
 )
 AND pol.action = 'accept'
WHERE k.entity_type = ANY(%s)
  AND p.primary_uniprot IS NOT NULL
  AND p.primary_uniprot <> '';
```

Then conflict detection becomes a cheap scan of `_protein_resolver_match`, not another resolver-table join.

Do the same for chemicals with `_chemical_resolver_match`. This will also let `_insert_chemical_resolver_identifier_links()` reuse already accepted resolver mappings instead of doing its own broad lookup.

---

### 3. Final stats are surprisingly expensive

These are exact whole-table scans:

```python
entities = _count_schema_table(cur, schema, 'entity')
relations = _count_schema_table(cur, schema, 'relation')
entity_status = _status_counts(cur, schema, 'entity_evidence_resolution')
```

On large tables, `COUNT(*)` in PostgreSQL is not free. It scans the table or index. For a big graph, these summary stats can become a nontrivial part of runtime, especially if you run canonicalization frequently.

Prefer scoped stats:

```sql
SELECT r.status, COUNT(*)
FROM public.entity_evidence_resolution r
JOIN _entity_scope s
  ON s.entity_evidence_id = r.entity_evidence_id
GROUP BY r.status;
```

Or make exact global totals optional. If you only need approximate total table sizes for logging, use `pg_class.reltuples` instead of exact `COUNT(*)`.

A replacement helper could be:

```python
def _scoped_status_counts(
    cur: psycopg2.extensions.cursor,
    schema: str,
) -> dict[str, int]:
    cur.execute(
        sql.SQL(
            """
            SELECT r.status, COUNT(*)
            FROM {}.entity_evidence_resolution r
            JOIN _entity_scope s
              ON s.entity_evidence_id = r.entity_evidence_id
            GROUP BY r.status
            ORDER BY r.status
            """
        ).format(sql.Identifier(schema))
    )
    return {str(status): int(count) for status, count in cur.fetchall()}
```

---

### 4. Relation processing may be much broader than necessary

Even when `unresolved_only=True`, relation scope is created independently:

```python
_create_relation_scope(
    cur,
    schema=schema,
    source=source,
    dataset=dataset,
)
```

That means you may resolve only a small number of entity evidence rows but still delete and rebuild relation mappings for an entire source or dataset.

This can dominate runtime.

Current behavior:

```sql
SELECT re.relation_evidence_id
FROM public.relation_evidence re
WHERE re.source = %s
  AND re.dataset = %s
```

For incremental runs, consider scoping relations to affected endpoints:

```sql
CREATE TEMP TABLE _relation_scope ON COMMIT DROP AS
SELECT DISTINCT re.relation_evidence_id
FROM public.relation_evidence re
WHERE EXISTS (
    SELECT 1
    FROM _entity_scope s
    WHERE s.entity_evidence_id = re.subject_entity_evidence_id
       OR s.entity_evidence_id = re.object_entity_evidence_id
)
OR NOT EXISTS (
    SELECT 1
    FROM public.relation_evidence_relation rer
    WHERE rer.relation_evidence_id = re.relation_evidence_id
);
```

You can still keep the current broader behavior for full rebuilds, but for incremental canonicalization this should be a separate mode.

---

### 5. Delete-and-reinsert creates table bloat and WAL

These are heavy for large scopes:

```python
_delete_scoped_candidates()
_delete_scoped_relation_evidence()
```

They are defensible if mappings can disappear due to policy/resolver changes, but they generate dead tuples and WAL. For frequent large runs, this can hurt.

Options:

Use the current delete/reinsert path for full rebuilds or policy changes.

For normal incremental runs, consider:

1. Stage new candidates.
2. Upsert changed candidates.
3. Delete only stale candidates not present in the new stage.

For relation evidence links, similarly avoid deleting all scoped links if endpoints did not change.

At minimum, make sure autovacuum is keeping up on:

```text
entity_resolution_candidate
relation_evidence_relation
relation_evidence_annotation
entity_evidence_resolution
```

---

## Indexes I would verify

For entity evidence scoping:

```sql
CREATE INDEX CONCURRENTLY entity_evidence_source_dataset_id_idx
ON public.entity_evidence (source, dataset, entity_evidence_id);

CREATE INDEX CONCURRENTLY entity_evidence_dataset_id_idx
ON public.entity_evidence (dataset, entity_evidence_id);
```

For identifier expansion:

```sql
CREATE INDEX CONCURRENTLY entity_evidence_identifier_evidence_idx
ON public.entity_evidence_identifier (entity_evidence_id, identifier_id);
```

For candidate lookup:

```sql
CREATE INDEX CONCURRENTLY entity_resolution_candidate_evidence_idx
ON public.entity_resolution_candidate (entity_evidence_id);
```

If your unique constraint already starts with `entity_evidence_id`, this may already be covered.

For relation processing:

```sql
CREATE INDEX CONCURRENTLY relation_evidence_source_dataset_id_idx
ON public.relation_evidence (source, dataset, relation_evidence_id);

CREATE INDEX CONCURRENTLY relation_evidence_subject_idx
ON public.relation_evidence (subject_entity_evidence_id);

CREATE INDEX CONCURRENTLY relation_evidence_object_idx
ON public.relation_evidence (object_entity_evidence_id);

CREATE INDEX CONCURRENTLY relation_evidence_relation_evidence_idx
ON public.relation_evidence_relation (relation_evidence_id);

CREATE INDEX CONCURRENTLY relation_evidence_annotation_evidence_idx
ON public.relation_evidence_annotation (relation_evidence_id);

CREATE INDEX CONCURRENTLY annotation_relation_evidence_idx
ON public.annotation (relation_evidence_id, annotation_id);
```

For entity lookup during `_upsert_entity_resolution()`:

```sql
CREATE UNIQUE INDEX CONCURRENTLY entity_identity_idx
ON public.entity (entity_type, id_type, id_hash);
```

If you can include `id` as well, even better for collision safety:

```sql
CREATE UNIQUE INDEX CONCURRENTLY entity_identity_full_idx
ON public.entity (entity_type, id_type, id_hash, id);
```

---

## Temp-table improvements

You should analyze `_entity_scope` after populating it. You already analyze several other temp tables, but not this one.

Add:

```python
cur.execute('ANALYZE _entity_scope')
```

inside `_create_entity_scope()` after the insert.

Also, some temp indexes may not be useful. This one is suspicious:

```sql
CREATE INDEX ON _entity_resolution_stage (
  entity_type,
  id_type,
  md5(id)
)
```

Immediately after that, you mostly scan `_entity_resolution_stage` and join into the persistent `entity` table. The important index is usually on `entity`, not the temp stage. Building this temp index may cost more than it saves.

If you keep it, consider adding an `id_hash` column to `_entity_resolution_stage` instead of repeatedly computing `md5(id)`:

```sql
md5(id) AS id_hash
```

Then join with:

```sql
e.id_hash = st.id_hash
```

This is cleaner and avoids repeated hashing.

---

## CTE improvement in `_create_entity_resolution_stage()`

You scan `entity_resolution_candidate` once in `candidate_counts` and again in `ranked_candidates`.

This:

```sql
candidate_counts AS (
  SELECT
    c.entity_evidence_id,
    COUNT(*) AS candidate_count
  FROM public.entity_resolution_candidate c
  JOIN _entity_scope s
    ON s.entity_evidence_id = c.entity_evidence_id
  GROUP BY c.entity_evidence_id
),
ranked_candidates AS (
  SELECT
    c.*,
    ...
  FROM public.entity_resolution_candidate c
  JOIN _entity_scope s
    ON s.entity_evidence_id = c.entity_evidence_id
)
```

can become:

```sql
ranked_candidates AS MATERIALIZED (
  SELECT
    c.*,
    CASE
      WHEN c.key_types && %s::text[]
        OR COALESCE(c.mapping_types, ARRAY[]::text[]) && %s::text[]
        THEN 100
      WHEN c.key_types && %s::text[]
        THEN 80
      WHEN c.key_types && %s::text[]
        THEN 20
      ELSE 0
    END AS resolution_rank
  FROM public.entity_resolution_candidate c
  JOIN _entity_scope s
    ON s.entity_evidence_id = c.entity_evidence_id
),
candidate_counts AS (
  SELECT
    entity_evidence_id,
    COUNT(*) AS candidate_count
  FROM ranked_candidates
  GROUP BY entity_evidence_id
)
```

That avoids one extra scan of scoped candidates.

---

## `UNION` in chemical identifier linking is wasteful

In `_insert_chemical_resolver_identifier_links()`, you use multiple `UNION`s and then later do `SELECT DISTINCT` again.

This part:

```sql
WITH mapped AS (
  SELECT DISTINCT ...
  UNION
  SELECT DISTINCT ...
  UNION
  SELECT DISTINCT ...
)
SELECT DISTINCT ...
```

does multiple de-duplication passes.

Prefer:

```sql
WITH mapped AS (
  SELECT ...
  UNION ALL
  SELECT ...
  UNION ALL
  SELECT ...
)
SELECT DISTINCT ...
```

You already de-duplicate later, so the intermediate `UNION` sorts/hashes are probably wasted.

Also, this helper currently ignores `resolver_mapping_policy`. If policies are intended to control accepted mappings, `_insert_chemical_resolver_identifier_links()` may be adding identifiers from mappings that candidate generation would reject. That is both extra work and possibly a correctness issue.

---

## Relation uniqueness issue with nullable `relation_category`

This is important.

You insert relations with:

```sql
ON CONFLICT (
  subject_entity_id,
  predicate,
  object_entity_id,
  relation_category
)
DO NOTHING
```

But if `relation_category` can be `NULL`, a normal PostgreSQL unique index allows multiple rows where `relation_category IS NULL`.

Then this join can link one evidence row to multiple duplicate relation rows:

```sql
AND r.relation_category IS NOT DISTINCT FROM ep.relation_category
```

That can cause relation duplication and inflated mapping counts.

Best fix on PostgreSQL 15+:

```sql
CREATE UNIQUE INDEX CONCURRENTLY relation_identity_idx
ON public.relation (
  subject_entity_id,
  predicate,
  object_entity_id,
  relation_category
)
NULLS NOT DISTINCT;
```

Or make `relation_category` non-null with a default such as `'association'`.

---

## Possible correctness issue: unresolved-only status check

This condition:

```sql
r.status <> 'resolved'
```

does not match `NULL` statuses. If a resolution row exists with `status IS NULL`, it will not be included.

Use:

```sql
r.status IS DISTINCT FROM 'resolved'
```

So this block:

```sql
(
  r.entity_evidence_id IS NULL
  OR r.status <> 'resolved'
)
```

should likely become:

```sql
(
  r.entity_evidence_id IS NULL
  OR r.status IS DISTINCT FROM 'resolved'
)
```

---

## Possible correctness issue: entity status downgrade

In `_insert_entities()`:

```sql
resolution_status = CASE
  WHEN EXCLUDED.resolution_status = 'resolved'
    THEN 'resolved'
  ELSE 'unresolved'
END
```

This can downgrade an existing entity from `resolved` to `unresolved` during a later batch.

Safer:

```sql
resolution_status = CASE
  WHEN {}.entity.resolution_status = 'resolved'
    OR EXCLUDED.resolution_status = 'resolved'
    THEN 'resolved'
  ELSE 'unresolved'
END
```

Unless you intentionally want later unresolved evidence to downgrade the canonical entity, I would avoid that.

---

## Batch-level settings

For large batches with group aggregates, distincts, and temp tables, set local memory for the transaction:

```sql
SET LOCAL work_mem = '256MB';
SET LOCAL temp_buffers = '256MB';
```

Tune values based on your DB size and concurrency. Do not globally raise `work_mem` too high if many sessions run concurrently.

You could do this near the top of `canonicalize()`:

```python
cur.execute("SET LOCAL work_mem = '256MB'")
cur.execute("SET LOCAL temp_buffers = '256MB'")
```

---

## What I would measure first

Run `EXPLAIN (ANALYZE, BUFFERS)` on these query blocks first:

1. `_insert_protein_candidates()`
2. `_insert_chemical_candidates()`
3. `_insert_chemical_resolver_identifier_links()`
4. `_create_entity_resolution_stage()`
5. `_delete_scoped_relation_evidence()`
6. `_insert_relation_evidence_links()`
7. `_status_counts()` and `_count_schema_table()`

The first thing I would expect to see is sequential scans or expensive hash joins on the resolver lookup tables because of `md5(p.key_value)` / `md5(c.key_value)`.

---

## Practical priority list

I would do these first:

1. Add resolver `key_value_hash` columns or expression indexes.
2. Stop doing exact global `COUNT(*)` stats unless explicitly requested.
3. Add `ANALYZE _entity_scope`.
4. Stage protein and chemical resolver matches once and reuse them.
5. Make relation processing incremental when `unresolved_only=True`.
6. Verify relation uniqueness with nullable `relation_category`.
7. Replace chemical-linking `UNION` with `UNION ALL` plus one final `DISTINCT`.
8. Avoid downgrading existing resolved entities.
9. Add or verify relation evidence/link indexes.
10. Consider chunking large source/dataset runs to reduce long transactions, WAL, and dead tuples.

So: the approach is solid, but for very large record counts the current version likely spends too much time in repeated resolver joins, broad relation rebuilds, and exact global summary counts.
