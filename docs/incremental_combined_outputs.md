# Incremental Combined Outputs

## Overview

Combined warehouse parquet files (entity, relation, evidence) can now be incrementally refreshed. Instead of rebuilding everything when one source changes, the system identifies affected rows via evidence tables and only recomputes those.

**Key design decisions:**
- `entity_key` = deterministic hash of `(canonical_identifier, type, taxonomy_id)` is the stable business identity.
- `entity_id` = stable `bigserial` integer for performance, joins, and bitmaps.
- Same pattern for relations: `relation_key` (text) + `relation_id` (integer).
- Evidence tables bridge raw records to combined rows. No row hashes needed.
- Combine output is always written to `data/combined/latest/` with optional immutable monthly snapshots.

---

## 1. Stable keys and stable integer IDs

| | Text key (`entity_key`) | Integer ID (`entity_id`) |
|---|---|---|
| Identity | Stable, deterministic across systems | Opaque, system-specific |
| Storage | ~64 bytes | 8 bytes |
| Joins | Slow (text comparison) | Fast (integer comparison) |
| Bitmap indexes | Not usable directly | Native support |

Both are stored: `entity_key` for provenance and identity, `entity_id` for compact storage and fast joins.

**Entity key:**
```
entity_key = hash(canonical_identifier, canonical_identifier_type, taxonomy_id)
```

**Relation key:**
```
relation_key = hash(subject_entity_key, predicate, object_entity_key, relation_category)
```

---

## 2. Parquet schemas

### Per-source gold

**`entities/entity.parquet`**
```
entity_pk                   # per-source integer
entity_key                  # stable hash
canonical_identifier
canonical_identifier_type
identifiers
entity_type
taxonomy_id
entity_attributes
sources
```

**`entities/entity_evidence.parquet`** (new)
```
source
entity_key
raw_record_ids              # array of raw_record_ids that contributed
entity_type
taxonomy_id
identifiers
entity_attributes
```

**`relations/entity_relation.parquet`**
```
relation_pk                 # per-source integer
relation_key                # stable hash
subject_entity_pk
subject_entity_key          # denormalized
predicate
object_entity_pk
object_entity_key           # denormalized
relation_category
evidence_count
sources
```

**`relations/entity_relation_evidence.parquet`**
```
source
relation_evidence_pk        # per-source integer
relation_pk
relation_key                # denormalized
raw_record_id               # single raw record id
record_attributes
subject_attributes
object_attributes
evidence
```

### Combined

**`entity.parquet`**
```
entity_id           # stable integer PK
entity_key          # stable text business key
canonical_identifier
canonical_identifier_type
identifiers
entity_type
taxonomy_id
entity_attributes
sources
```

**`entity_relation.parquet`**
```
relation_id         # stable integer PK
relation_key        # stable text business key
subject_entity_id
subject_entity_key
predicate
object_entity_id
object_entity_key
relation_category
participant_types
evidence_count
sources
```

**`entity_relation_evidence.parquet`**
```
relation_evidence_id  # stable integer PK
relation_id
relation_key
source
raw_record_id
record_attributes
subject_attributes
object_attributes
evidence
```

**`entity_evidence.parquet`**
```
source
entity_key
raw_record_ids
entity_type
taxonomy_id
identifiers
entity_attributes
```

---

## 3. Evidence tables

Evidence tables are the provenance bridge from raw records to combined aggregates.

### `entity_evidence.parquet` (per-source)

Per-source, deduplicated by entity. One row per `(source, entity_key)`. Tracks which raw records contributed to each entity.

```
source              # source name
entity_key          # the combined entity key
raw_record_ids      # array of raw_record_ids that contributed to this entity
entity_type         # from this source
taxonomy_id         # from this source
identifiers         # identifiers from this source
entity_attributes   # attributes from this source
```

Why deduplicated per source: the same entity can appear in many raw records within a source (e.g., as a participant in 100 interactions). One row with an array of `raw_record_ids` is sufficient. When any raw record in the array changes, the entity is recomputed.

### `entity_relation_evidence.parquet` (per-raw-record)

Per-raw-record. One row per raw record that produced a relation.

```
source              # source name
relation_key        # the combined relation key
raw_record_id       # single raw_record_id
record_attributes
subject_attributes
object_attributes
evidence
```

Why per raw record: each raw record contributes distinct evidence (PMID, method, score, etc.). Even if two raw records produce the same `relation_key`, their evidence payloads differ. Per-raw-record granularity is needed to correctly rebuild the combined relation when any raw record changes.

---

## 4. Combine

The combine step has only one mode. It updates the DuckDB state store and then
exports `latest/`. On an empty state it bootstraps from the available gold
outputs; otherwise affected keys drive a targeted update. There is no separate
full-build engine or CLI mode.

### Update flow

```
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
   a. For affected entity_keys: read ALL per-source entity.parquet, group by entity_key, recompute merged row. Keep unchanged rows from previous combined output.
   b. For affected relation_keys: read ALL per-source entity_relation.parquet, group by relation_key, recompute merged row. Keep unchanged rows.
   c. For affected relation_keys: concatenate per-source relation_evidence, reassign relation_evidence_id. Keep unchanged evidence rows.
7. Write combined parquets directly to latest/.
8. In PostgreSQL, apply diffs transactionally.
9. Refresh bitmap indexes for affected entity_ids / relation_ids.
10. Append entry to build_manifest.jsonl.
```

### CLI

```bash
# 1. Compute affected keys by comparing old vs new evidence
cat affected_entities.json
# ["abc123...", "def456..."]

cat affected_relations.json
# ["ghi789...", "jkl012..."]

# 2. Run combine (incremental update of latest/)
uv run python -m omnipath_build.gold.combine \
  --gold-root data/gold \
  --output-dir data/combined \
  --affected-entities affected_entities.json \
  --affected-relations affected_relations.json

# 3. Force a fresh pipeline bootstrap
rm -rf data/combined/latest
uv run python -m omnipath_build.gold.combine \
  --gold-root data/gold \
  --output-dir data/combined
```

---

## 6. PostgreSQL schema

### Base tables

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

CREATE TABLE entity_evidence (
    source              text NOT NULL,
    entity_key          text NOT NULL REFERENCES entity(entity_key),
    raw_record_ids      jsonb,
    entity_type         text,
    taxonomy_id         text,
    identifiers         jsonb,
    entity_attributes   jsonb,
    PRIMARY KEY (source, entity_key)
);
```

### Full load

```bash
uv run python -c "
from omnipath_build.cli.commands import main
import sys
sys.exit(main([
    'postgres',
    '--output-dir', 'data/combined',
    '--postgres-uri', 'postgresql://user:pass@host:5432/db',
    '--schema', 'public',
    '--drop-existing',
]))
"
```

### Incremental load

```bash
uv run python -c "
from omnipath_build.cli.commands import main
import sys
sys.exit(main([
    'postgres',
    '--output-dir', 'data/combined',
    '--postgres-uri', 'postgresql://user:pass@host:5432/db',
    '--schema', 'public',
    '--affected-entities', 'affected_entities.json',
    '--affected-relations', 'affected_relations.json',
    '--changed-source', 'connectomedb',
]))
"
```

The incremental loader:
1. Queries affected `entity_id`s and `relation_id`s from the database.
2. DELETEs in dependency order: `relation_annotation_term` → `entity_relation_evidence` → `entity_relation` → `entity_identifier` → `entity` → `entity_evidence`.
3. COPYs filtered parquet rows for affected keys only.
4. Rebuilds `relation_annotation_term` via SQL `INSERT...SELECT` from evidence.
5. Recreates materialized views.

---

## 7. Bitmap indexes

Bitmap tables use the PostgreSQL `roaringbitmap` extension and reference stable integer IDs directly.

| Table | Description |
|---|---|
| `facet_entity_bitmap` | `entity_type`, `taxonomy_id`, `source`, `ontology_id` → entity bitmaps |
| `facet_relation_bitmap` | `predicate`, `participant_type`, `source` → relation bitmaps |
| `annotation_term_entity_bitmap` | ontology term → set of annotated entity IDs |
| `annotation_term_relation_bitmap` | ontology term → set of relation IDs |

During bootstrap, bitmap tables are created and populated from the loaded base
tables. During delta updates, affected entity/relation IDs are removed from and
added back to the bitmap tables.

---

## 8. Versioning and rollback

Combined outputs always use a single mutable working directory with optional immutable monthly snapshots:

```
data/combined/
  latest/         # mutable working directory
  2024-01/        # frozen monthly snapshot
  2024-02/        # frozen monthly snapshot
  2024-03/        # frozen monthly snapshot
```

**How it works:**

1. **Every run writes to `latest/` directly.** Previous state is read from `latest/`, affected rows are recomputed, and the result is written back to the same directory. A `build_manifest.jsonl` (append-only) records each run's timestamp, mode, changed source, and row counts.

2. **Monthly freeze:** Pass `--freeze-monthly` on any update (typically the first update of a new month).
   - After writing `latest/`, the system copies the entire directory to `YYYY-MM/` (e.g. `2024-04/`).
   - The monthly snapshot is immutable.
   - Downstream consumers who need reproducibility can pin to a specific month.

3. **Rollback granularity:**
   - **Intra-month:** Reverse entries in `build_manifest.jsonl` or re-run from the last monthly snapshot.
   - **Inter-month:** Point a symlink or loader config to any `YYYY-MM/` directory.
   - **Fresh bootstrap:** Delete `latest/` and rerun the pipeline, or run the
     standalone combine command without affected-key files.

**Why this is safe:**

- Parquet files are replaced atomically (write to temp, `mv` into place). Readers with the file already open continue to see the old file descriptor.
- The `latest/` directory is only mutated by one orchestrator process at a time.
- If an update corrupts `latest/`, it can always be reconstructed from the last monthly snapshot + re-running the pipeline.

**CLI examples:**

```bash
# Daily update (directly modifies latest/)
uv run python -m omnipath_build.gold.combine \
  --gold-root data/gold \
  --output-dir data/combined \
  --affected-entities affected_entities.json \
  --affected-relations affected_relations.json

# First update of the month: update + freeze monthly snapshot
uv run python -m omnipath_build.gold.combine \
  --gold-root data/gold \
  --output-dir data/combined \
  --freeze-monthly \
  --affected-entities affected_entities.json \
  --affected-relations affected_relations.json

# Full rebuild (delete latest/ first)
rm -rf data/combined/latest
uv run python -m omnipath_build.gold.combine \
  --gold-root data/gold \
  --output-dir data/combined
```

---

## 9. Summary

```
raw-record diff
  → evidence tables map source+raw_record_id → combined keys
  → identify affected entity_keys and relation_keys
  → targeted combine: recompute only affected combined rows
  → apply diff to PostgreSQL (delete/insert/update for affected keys only)
  → rebuild bitmaps using stable integer IDs
```

**What is kept from the old system:** the combine step itself, cross-source deduplication logic, and the overall pipeline shape.

**What is new:** stable keys, stable integer IDs, evidence tables, mutable `latest/` output with monthly snapshots, targeted recomputation, and incremental PostgreSQL loading.
