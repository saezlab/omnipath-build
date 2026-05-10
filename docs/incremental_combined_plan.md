# Plan: Combined Outputs with Incremental Refresh via Evidence Provenance

## Problem

The current pipeline produces combined parquet files by merging per-source gold outputs, then loads them into PostgreSQL. The combine step is a full rebuild every time, even when only one source changes. We want to make the combined outputs incrementally refreshable.

## Core insight: evidence tables link raw records to combined rows

Combined outputs can be incrementally maintained if every combined row can be traced back to the raw records that contributed to it. This is achieved through evidence tables that sit between raw records and combined aggregates.

From the latest build (`data/combined/combined_build_summary.json`):

| Layer | Per-source rows | Combined rows | Reduction |
|-------|----------------|---------------|-----------|
| Entities | 483,318 | 423,712 | ~12% |
| Relations | ~7.3M | 7,286,894 | ~0.03% |
| Evidence | ~7.3M | 7,339,062 | ~0% (concatenated) |

## Proposal

**Keep the combine step.** Use stable semantic keys (`entity_key`, `relation_key`) as business identity. Use stable integer IDs (`entity_id`, `relation_id`) as primary keys for performance, joins, and bitmaps. Add evidence tables that map raw records to combined keys. Use these evidence tables to determine which combined rows need rebuilding when raw records change.

### Target architecture

```text
raw snapshots
  → fast raw-record index (bronze)
  → raw-record diff: added / removed / changed / unchanged
  → incremental silver rows
  → per-source gold parquets (entities + relations + evidence)
  → combine step (cross-source deduplication)
  → combined parquets with stable keys + stable integer IDs
  → evidence tables mapping raw_record_id → combined keys
  → PostgreSQL tables (integer PKs, text keys as business identity)
  → bitmap indexes (stable integer IDs)
```

---

## 1. Stable keys and stable integer IDs

### Entity key

```text
entity_key = hash(canonical_identifier, canonical_identifier_type, taxonomy_id)
```

`taxonomy_id` is included because the same identifier (e.g., a UniProt accession) can refer to different taxa in different sources, and those should be distinct entities.

### Entity ID

```text
entity_id = bigserial (stable integer, assigned once, never reused)
```

`entity_id` is the database primary key. It is assigned during the first time an `entity_key` is seen and remains stable across all subsequent rebuilds. Deleted entities' IDs are not reused.

### Relation key

```text
relation_key = hash(subject_entity_key, predicate, object_entity_key, relation_category)
```

Since `entity_key` includes `taxonomy_id`, the relation key implicitly includes taxon information.

### Relation ID

```text
relation_id = bigserial (stable integer, assigned once, never reused)
```

`relation_id` is the database primary key for relations. Stable across rebuilds.

### Why both key and ID?

| Concern | Text key (`entity_key`) | Integer ID (`entity_id`) |
|---------|------------------------|-------------------------|
| Identity across systems | Stable, deterministic | Opaque, system-specific |
| Storage size | ~64 bytes | 8 bytes |
| Index size | Large | Compact |
| Join performance | Slow (text comparison) | Fast (integer comparison) |
| Bitmap indexes | Not usable directly | Native support |
| Provenance tracking | Human-readable | Needs lookup |

**Best of both worlds:** `entity_key` provides stable business identity and provenance. `entity_id` provides compact storage, fast joins, and bitmap support.

---

## 2. Combined parquet schemas

### `entity.parquet`

```text
entity_id           # stable integer PK
entity_key          # stable text business key
canonical_identifier
canonical_identifier_type
taxonomy_id
identifiers
entity_type
entity_attributes
sources
```

### `entity_relation.parquet`

```text
relation_id         # stable integer PK
relation_key        # stable text business key
subject_entity_id   # references entity.entity_id
subject_entity_key  # denormalized for convenience
predicate
object_entity_id    # references entity.entity_id
object_entity_key   # denormalized for convenience
relation_category
evidence_count
sources
participant_types
```

### `entity_relation_evidence.parquet`

```text
relation_evidence_id  # stable integer PK (per-combined-build)
relation_id           # references entity_relation.relation_id
relation_key          # denormalized for convenience
source                # source name
raw_record_id         # from bronze/silver provenance
record_attributes
subject_attributes
object_attributes
evidence
```

---

## 3. Evidence tables

Evidence tables provide the provenance bridge from raw records to combined aggregates.

### `entity_evidence.parquet` (new, per-source)

Per-source, deduplicated by entity. One row per (source, entity_key). Tracks which raw records contributed to each entity.

```text
source              # source name
entity_key          # the combined entity key
raw_record_ids      # array of raw_record_ids that contributed to this entity
entity_type         # from this source
taxonomy_id         # from this source
identifiers         # identifiers from this source
entity_attributes   # attributes from this source
```

**Why deduplicated per source:** The same entity (e.g., protein P12345) can appear in many raw records within a source (e.g., as a participant in 100 interactions). We don't need 100 entity_evidence rows — we need one row that says "this entity in this source was built from these raw records." When any of those raw records changes, we rebuild the combined entity.

**Why arrays of raw_record_ids:** Since entity deduplication happens early (by fingerprint and canonical ID), multiple raw records collapse to one entity. The array captures all contributors.

### `entity_relation_evidence.parquet` (per-raw-record)

Per-raw-record. One row per raw record that produced a relation.

```text
source              # source name
relation_key        # the combined relation key
raw_record_id       # single raw_record_id
record_attributes
subject_attributes
object_attributes
evidence
```

**Why per raw record:** Each raw record contributes distinct evidence (PMID, method, score, etc.). Even if two raw records produce the same relation_key, their evidence payloads differ. We need per-raw-record granularity to correctly rebuild the combined relation when any raw record changes.

### Evidence table lifecycle

1. **Per-source gold build** emits `entity_evidence.parquet` and `entity_relation_evidence.parquet`.
2. **Combine step** concatenates per-source evidence tables into combined evidence tables (no deduplication needed — just concatenate). Assigns stable `entity_id`s and `relation_id`s.
3. **PostgreSQL loader** loads combined evidence tables alongside combined entity and relation tables.

---

## 4. Incremental update algorithm

For a source update:

```text
1. Fetch/store new raw snapshot.
2. Run fast pre-parse → raw-record diff (added / removed / changed / unchanged).
3. Build silver (incremental, only added/removed/changed records).
4. Build gold parquets for this source only.
   - entity_evidence.parquet with raw_record_ids array per entity
   - entity_relation_evidence.parquet with raw_record_id per row
5. Determine affected combined keys:
   a. Affected entity_keys = all entity_keys in old or new entity_evidence for source X.
   b. Affected relation_keys = all relation_keys in old or new relation_evidence for source X.
6. Run targeted combine:
   a. For affected entity_keys:
      - Read per-source entity.parquet for ALL sources.
      - Group by entity_key.
      - Recompute merged row for affected keys.
      - Keep unchanged rows from previous combined output.
   b. For affected relation_keys:
      - Read per-source entity_relation.parquet for ALL sources.
      - Group by relation_key.
      - Recompute merged row for affected keys.
      - Keep unchanged rows from previous combined output.
   c. For affected relation_keys:
      - Concatenate per-source relation_evidence for affected keys.
      - Reassign relation_evidence_id.
      - Keep unchanged evidence rows from previous combined output.
7. Write combined parquets.
8. In PostgreSQL, apply diffs transactionally:
   a. DELETE FROM entity_relation_evidence WHERE relation_key IN (affected_relation_keys);
   b. DELETE FROM entity_relation WHERE relation_key IN (affected_relation_keys);
   c. DELETE FROM entity WHERE entity_key IN (affected_entity_keys);
   d. INSERT/UPDATE new entity rows. ON CONFLICT (entity_key) DO UPDATE.
      - Existing entity_id is preserved.
      - New entity_keys get new entity_ids.
   e. INSERT/UPDATE new relation rows. ON CONFLICT (relation_key) DO UPDATE.
      - Existing relation_id is preserved.
      - New relation_keys get new relation_ids.
   f. INSERT new evidence rows.
   g. UPDATE entity_evidence for source X (delete old, insert new).
   h. COMMIT.
9. Refresh bitmap indexes for affected entity_ids / relation_ids.
10. Write build/update manifest.
```

**Why no row hashes:** The incremental decision is driven by raw-record diffs, not by comparing row content. We find affected combined keys by looking up which keys had evidence from the changed source. Then we recompute those rows from the current per-source data. Whether the recomputed row is identical to the old one doesn't matter — we just apply it.

### Example: entity with multiple raw records

Suppose combined entity E (protein P12345, human) has evidence from:
- Source corum: `raw_record_ids = ["raw_1", "raw_2", "raw_3"]`
- Source uniprot: `raw_record_ids = ["raw_4"]`

Now source corum updates and `raw_1` changes:

1. `affected_entity_keys` includes E because corum's `entity_evidence` has `entity_key = E`.
2. We rebuild E by reading ALL current per-source `entity.parquet` rows for `entity_key = E`.
3. The recomputed E uses the current state of corum's entity row (updated) and uniprot's entity row (unchanged).
4. If corum deleted all its raw records for E, E is rebuilt from uniprot only.
5. If ALL sources delete their evidence for E, E is deleted from the combined output.
6. `entity_id` for E stays the same throughout (if E still exists).

### Example: relation with multiple raw records

Suppose combined relation R has evidence from:
- Source intact: `raw_record_id = "raw_5"` (PMID 12345)
- Source intact: `raw_record_id = "raw_6"` (PMID 67890)

Now source intact updates and `raw_5` changes:

1. `affected_relation_keys` includes R because intact's `relation_evidence` has `relation_key = R`.
2. We rebuild R by reading ALL current per-source `entity_relation.parquet` rows for `relation_key = R`.
3. We rebuild R's evidence by reading ALL current per-source `relation_evidence` rows for `relation_key = R`.
4. The new R has `evidence_count = 2` and evidence rows for `raw_5` (updated) and `raw_6` (unchanged).
5. `relation_id` for R stays the same.

---

## 5. Combine step changes

The combine step currently does a full rebuild. We change it to support targeted recomputation while assigning stable integer IDs.

### Input

- Per-source gold directories (unchanged)
- Previous combined directory (to reuse unchanged rows and preserve IDs)
- Affected `entity_key` set (from evidence tables)
- Affected `relation_key` set (from evidence tables)

### Algorithm

```python
def build_combined_parquets_incremental(
    gold_root,
    previous_combined_dir,
    affected_entity_keys,
    affected_relation_keys,
    output_dir,
):
    # Load previous combined state
    previous_entities = pl.read_parquet(previous_combined_dir / 'entity.parquet')
    previous_relations = pl.read_parquet(previous_combined_dir / 'entity_relation.parquet')
    previous_evidence = pl.read_parquet(previous_combined_dir / 'entity_relation_evidence.parquet')
    
    # Entities: keep unaffected, recompute affected
    unchanged_entities = previous_entities.filter(
        ~pl.col('entity_key').is_in(affected_entity_keys)
    )
    recomputed_entities = recompute_entities(gold_root, affected_entity_keys)
    # Preserve entity_id for recomputed keys that existed before
    id_map = dict(zip(previous_entities['entity_key'], previous_entities['entity_id']))
    new_entities = pl.concat([unchanged_entities, recomputed_entities])
    
    # Relations: keep unaffected, recompute affected
    unchanged_relations = previous_relations.filter(
        ~pl.col('relation_key').is_in(affected_relation_keys)
    )
    recomputed_relations = recompute_relations(gold_root, affected_relation_keys)
    new_relations = pl.concat([unchanged_relations, recomputed_relations])
    
    # Evidence: keep unaffected, recompute affected
    unchanged_evidence = previous_evidence.filter(
        ~pl.col('relation_key').is_in(affected_relation_keys)
    )
    recomputed_evidence = recompute_relation_evidence(gold_root, affected_relation_keys)
    # Remap relation_evidence_id for recomputed evidence
    new_evidence = pl.concat([unchanged_evidence, recomputed_evidence])
    
    # Write outputs
    new_entities.write_parquet(output_dir / 'entity.parquet')
    new_relations.write_parquet(output_dir / 'entity_relation.parquet')
    new_evidence.write_parquet(output_dir / 'entity_relation_evidence.parquet')
```

The `recompute_*` functions read per-source parquet files, filter to the affected keys, and apply the same merge logic as the full combine. IDs for keys that existed in the previous combined output are preserved.

---

## 6. PostgreSQL layer

### Base tables (integer PKs, text keys as business identity)

```sql
CREATE TABLE entity (
    entity_id           bigserial PRIMARY KEY,
    entity_key          text NOT NULL UNIQUE,
    canonical_identifier text NOT NULL,
    canonical_identifier_type text NOT NULL,
    taxonomy_id         text,
    entity_type         text,
    entity_attributes   jsonb,
    identifiers         jsonb,
    sources             jsonb
);

CREATE TABLE entity_relation (
    relation_id         bigserial PRIMARY KEY,
    relation_key        text NOT NULL UNIQUE,
    subject_entity_id   bigint NOT NULL REFERENCES entity(entity_id),
    subject_entity_key  text NOT NULL,
    predicate           text NOT NULL,
    object_entity_id    bigint NOT NULL REFERENCES entity(entity_id),
    object_entity_key   text NOT NULL,
    relation_category   text NOT NULL,
    evidence_count      bigint NOT NULL DEFAULT 0,
    sources             jsonb,
    participant_types   jsonb
);

CREATE TABLE entity_relation_evidence (
    relation_evidence_id bigserial PRIMARY KEY,
    relation_id         bigint NOT NULL REFERENCES entity_relation(relation_id),
    relation_key        text NOT NULL,
    source              text NOT NULL,
    raw_record_id       text,
    record_attributes   jsonb,
    subject_attributes  jsonb,
    object_attributes  jsonb,
    evidence            jsonb
);
```

### Entity evidence table

```sql
CREATE TABLE entity_evidence (
    source              text NOT NULL,
    entity_key          text NOT NULL REFERENCES entity(entity_key),
    raw_record_ids      jsonb,   -- array of raw_record_ids
    entity_type         text,
    taxonomy_id         text,
    identifiers         jsonb,
    entity_attributes   jsonb,
    PRIMARY KEY (source, entity_key)
);
```

### Indexes

```sql
-- For looking up affected keys by source
CREATE INDEX entity_evidence_source_idx ON entity_evidence(source);
CREATE INDEX relation_evidence_source_idx ON entity_relation_evidence(source);

-- For looking up evidence by relation_key
CREATE INDEX relation_evidence_relation_key_idx ON entity_relation_evidence(relation_key);

-- For entity lookups by canonical ID
CREATE INDEX entity_canonical_idx ON entity(canonical_identifier, canonical_identifier_type);

-- For relation lookups
CREATE INDEX entity_relation_subject_idx ON entity_relation(subject_entity_id);
CREATE INDEX entity_relation_object_idx ON entity_relation(object_entity_id);
```

### Incremental PostgreSQL apply

```sql
BEGIN;

-- Delete old evidence for affected relations
DELETE FROM entity_relation_evidence WHERE relation_key = ANY(affected_relation_keys);

-- Delete old relations for affected keys
DELETE FROM entity_relation WHERE relation_key = ANY(affected_relation_keys);

-- Delete old entities for affected keys
DELETE FROM entity WHERE entity_key = ANY(affected_entity_keys);

-- Insert/update new entities (preserving entity_id for existing keys)
INSERT INTO entity (entity_id, entity_key, canonical_identifier, ...)
VALUES (...)
ON CONFLICT (entity_key) DO UPDATE SET
    canonical_identifier = EXCLUDED.canonical_identifier,
    entity_attributes = EXCLUDED.entity_attributes,
    ...;

-- Insert/update new relations (preserving relation_id for existing keys)
INSERT INTO entity_relation (relation_id, relation_key, subject_entity_id, ...)
VALUES (...)
ON CONFLICT (relation_key) DO UPDATE SET
    evidence_count = EXCLUDED.evidence_count,
    sources = EXCLUDED.sources,
    ...;

-- Insert new evidence
INSERT INTO entity_relation_evidence (relation_id, relation_key, source, raw_record_id, ...)
VALUES (...);

-- Update entity_evidence for the changed source
DELETE FROM entity_evidence WHERE source = 'X';
INSERT INTO entity_evidence (source, entity_key, raw_record_ids, ...)
VALUES (...);

COMMIT;
```

---

## 7. Bitmap indexes

Bitmap indexes use the PostgreSQL `roaringbitmap` extension, which requires 32-bit integer IDs. Since we have stable integer IDs, bitmaps reference them directly.

### Bitmap queries

```sql
-- facet_entity_bitmap
SELECT 'entity_type', entity_type, rb_build_agg(entity_id::integer), COUNT(*)::integer
FROM entity
GROUP BY entity_type;

-- facet_relation_bitmap
SELECT 'predicate', predicate, relation_category, rb_build_agg(relation_id::integer), COUNT(*)::integer
FROM entity_relation
GROUP BY predicate, relation_category;

-- annotation_term_entity_bitmap
SELECT er.object_entity_id AS term_entity_id,
       rb_build_agg(er.subject_entity_id::integer),
       COUNT(*)::integer
FROM entity_relation er
JOIN entity term ON term.entity_id = er.object_entity_id
WHERE er.relation_category = 'association'
  AND term.entity_type = 'OM:0012:Cv Term'
GROUP BY er.object_entity_id;
```

### Incremental bitmap refresh

For affected entity_ids and relation_ids:
1. Rebuild bitmap rows only for facets/terms that include affected entity_ids/relation_ids.
2. Or, since bitmap tables are small (~100-1000 rows), rebuild them entirely.

The integer IDs are stable across rebuilds, so bitmaps don't need dynamic reassignment.

---

## 8. Relation annotation terms

The `relation_annotation_term` table links ontology terms to relations. It is built from combined `entity_relation_evidence` and `entity` tables.

### Schema

```sql
CREATE TABLE relation_annotation_term (
    relation_id         bigint NOT NULL REFERENCES entity_relation(relation_id),
    relation_key        text NOT NULL,
    relation_evidence_id bigint,
    source              text,
    scope               text,
    term_entity_id      bigint NOT NULL REFERENCES entity(entity_id),
    PRIMARY KEY (relation_id, source, scope, term_entity_id)
);
```

### Incremental rebuild

```sql
BEGIN;

-- Delete old annotations for affected relations
DELETE FROM relation_annotation_term WHERE relation_key = ANY(affected_relation_keys);

-- Rebuild annotations for affected relations from evidence
INSERT INTO relation_annotation_term (relation_id, relation_key, relation_evidence_id, source, scope, term_entity_id)
SELECT DISTINCT
    e.relation_id,
    e.relation_key,
    e.relation_evidence_id,
    e.source,
    'record' AS scope,
    term.entity_id AS term_entity_id
FROM entity_relation_evidence e
JOIN entity term ON term.canonical_identifier = (e.record_attributes->>'term_id')
WHERE e.relation_key = ANY(affected_relation_keys)
  AND term.entity_type = 'OM:0012:Cv Term';

COMMIT;
```

The link to `relation_evidence` (and thereby to `raw_record_id`) is preserved through `relation_evidence_id`.

---

## 9. Answers to open questions

### 1. Entity evidence granularity: deduplicated per source or one row per raw record?

**Answer: Deduplicated per (source, entity_key) with `raw_record_ids` array.**

Rationale:
- In the gold build, entities are already deduplicated by fingerprint and canonical ID. Multiple silver occurrences collapse to one entity row. The per-raw-record attributes are merged with "first non-null" or similar logic, so per-raw-record detail is already lost at the entity level.
- One row per (source, entity_key) is the natural granularity because it mirrors the per-source `entity.parquet` output. The `raw_record_ids` array captures all contributing raw records without storage bloat.
- If we stored one row per raw record, we'd have ~759K rows for a source like UniProt (vs ~66K deduplicated entities), with 90% redundant data (same entity_key, same identifiers, same type).
- The array of `raw_record_ids` is sufficient for targeted refresh: when any raw record in the array changes, we recompute the entity.

### 2. ID persistence: are gaps in entity_id/relation_id acceptable?

**Answer: Yes, absolutely.**

Rationale:
- `bigserial` supports up to 9.2 quintillion values. With 424K entities and modest churn, we'll never exhaust it.
- Roaringbitmaps handle sparse integer sets efficiently — gaps don't hurt performance or storage.
- Client caching benefits from stable IDs. If ID 42 always refers to protein P12345 (human), clients can safely cache bitmap results, facet counts, etc.
- The only minor downside is slightly less dense integer space, but this is irrelevant at our scale.

### 3. Relation evidence ID stability: do clients rely on stable evidence IDs?

**Answer: No, and we don't need them to be stable.**

Rationale:
- `relation_evidence_id` is primarily a local row identifier within the evidence table. External queries filter by `relation_id`, `source`, or `raw_record_id`, not by `relation_evidence_id`.
- `relation_annotation_term` references `relation_evidence_id`, but it is rebuilt during each incremental update for affected relations. The annotation build reads fresh evidence rows and gets their new IDs.
- For incremental updates, the cleanest approach is: `DELETE WHERE relation_key IN (affected) + INSERT new evidence rows`. New IDs are assigned automatically by `bigserial`. Unchanged evidence rows keep their old IDs.
- If we ever need to preserve IDs for unchanged evidence, we can do so by keeping unaffected evidence rows and only deleting/inserting for affected relation_keys. The new evidence rows for affected keys get new IDs, which is fine.

### 4. Combine reuse: how to manage the previous combined directory?

**Answer: Use the existing versioning pattern from the silver layer.**

Rationale:
- The silver layer already uses `data/silver/<source>/<version>/` with a `latest` symlink. We can adopt the same pattern: `data/combined/<version>/` with a `latest` symlink.
- The incremental combine step:
  1. Reads previous combined from `data/combined/<latest>/`
  2. Computes new version ID (timestamp or monotonic integer)
  3. Writes new combined to `data/combined/<new_version>/`
  4. Atomically updates `latest` symlink
- This gives us rollback capability: if an incremental combine corrupts data, we can revert to the previous version and do a full rebuild.
- For the PostgreSQL loader, it always reads from `data/combined/latest/`.
- Old versions can be garbage-collected after N generations (configurable retention policy).

### 5. Entity identifier dedup: can it be expressed cleanly for targeted recomputation?

**Answer: Yes, trivially.**

Rationale:
- The current combine logic for identifiers is a pure function: explode list of structs → deduplicate by (type, value) → relist.
- When recomputing an affected `entity_key`, we read all per-source `entity.parquet` rows for that key and apply the same logic. It's a local operation on a small set of rows.
- In SQL, this is straightforward:
  ```sql
  WITH exploded AS (
      SELECT entity_key, identifier, identifier_type
      FROM entity_evidence,
      LATERAL jsonb_to_recordset(identifiers) AS x(identifier text, identifier_type text)
      WHERE entity_key = ?
  )
  SELECT entity_key, 
         jsonb_agg(DISTINCT jsonb_build_object('identifier', identifier, 'identifier_type', identifier_type))
  FROM exploded
  GROUP BY entity_key;
  ```
- The same applies to `entity_attributes`: explode, deduplicate by (term, value), relist. All deterministic and local to the affected key.

---

## 10. Why this is simpler than the contribution-layer plan

| Aspect | Contribution-layer plan | Evidence-based incremental |
|--------|------------------------|---------------------------|
| **Combine step** | Removed | **Kept, but targeted** |
| **Contribution tables** | Required | **Replaced by evidence tables** |
| **Raw-record provenance** | Only in contribution layer | **In evidence tables** |
| **Incremental logic** | Diff raw → silver → contribution → combined → PostgreSQL | **Diff raw → find affected keys via evidence → recompute affected combined rows → apply diff** |
| **Cross-source dedup** | In PostgreSQL aggregate tables | **In combine step (unchanged)** |
| **Primary keys** | Integer PKs + stable keys | **Stable integer IDs for performance + stable text keys for identity** |
| **Bitmap integers** | Dynamic reassignment each build | **Stable across rebuilds** |
| **Row hashes** | Required for diffing | **Not needed** |
| **Schema complexity** | New base tables + aggregate tables + contribution tables | **Current schema + evidence tables** |

---

## 11. Migration path

### Phase 1: Add stable keys and IDs to per-source and combined gold
- Add `entity_key = hash(canonical_identifier, canonical_identifier_type, taxonomy_id)` to per-source and combined `entity.parquet`.
- Add `relation_key = hash(subject_entity_key, predicate, object_entity_key, relation_category)` to per-source and combined `entity_relation.parquet`.
- Add `entity_id` and `relation_id` as stable bigserial IDs to combined outputs.
- Update `combine.py` to emit stable keys and assign IDs.

### Phase 2: Add evidence tables
- Create `entity_evidence.parquet` per source (deduplicated by entity_key, raw_record_ids array).
- Add `raw_record_id` to per-source `entity_relation_evidence.parquet`.
- Update `build_entities.py` to emit `entity_evidence.parquet`.
- Update `build_relations.py` to add `raw_record_id` to evidence.
- Combine step concatenates per-source evidence into combined evidence tables and assigns IDs.

### Phase 3: Update PostgreSQL schema
- Change `entity` PK to `entity_id bigserial` with `entity_key text UNIQUE`.
- Change `entity_relation` PK to `relation_id bigserial` with `relation_key text UNIQUE`.
- Add `entity_evidence` table.
- Update `entity_relation_evidence` to use `relation_id` FK.
- Add `entity_id`/`relation_id` columns to existing tables for transition.

### Phase 4: Targeted combine
- Modify `combine.py` to accept affected key sets and only recompute those.
- Preserve IDs for keys that existed in previous combined output.
- Reuse previous combined rows for unaffected keys.
- Write previous combined state to a versioned directory for incremental reuse.

### Phase 5: Incremental PostgreSQL loader
- Add `--postgres-mode incremental`.
- Implement transactional diff/apply for affected keys only.
- Preserve existing IDs via `ON CONFLICT DO UPDATE`.
- Add targeted bitmap/MV refresh using stable integer IDs.

### Phase 6: Clean up
- Remove old integer PK assignment logic (sequential `row_number()`).
- Remove old combined outputs format.
- Verify bitmap indexes use stable IDs.

---

## Summary

The incremental design keeps combined outputs but makes them refreshable via evidence provenance:

```text
raw-record diff
  → evidence tables map source+raw_record_id → combined keys
  → identify affected entity_keys and relation_keys
  → targeted combine: recompute only affected combined rows
  → apply diff to PostgreSQL (delete/insert/update for affected keys only)
  → rebuild bitmaps using stable integer IDs
```

**Key decisions:**
- `entity_key` = `hash(canonical_identifier, canonical_identifier_type, taxonomy_id)` is the stable business identity.
- `entity_id` is a stable bigserial integer for performance, joins, and bitmaps.
- Same pattern for relations: `relation_key` (text) + `relation_id` (integer).
- `entity_evidence` is deduplicated per (source, entity_key) with `raw_record_ids` array.
- `relation_evidence` remains per-raw-record with single `raw_record_id`.
- No row hashes needed — incremental decisions are driven by raw-record diffs.
- Bitmap integers are stable across rebuilds.
- Combine output is versioned like silver outputs.
- Targeted recomputation of identifiers and attributes is trivially local to affected keys.
