# Postgres combined schema plan

## Assumed input artifact model

We assume the revised exported source-specific artifacts have the following shapes.

### `entities.parquet`
- `entity_id`
- `entity_id_type`
- `entity_type`
- `entity_attributes`
- `taxonomy_id`
- `source`

### `entity_identifiers.parquet`
- `entity_id`
- `entity_id_type`
- `identifier`
- `identifier_type`
- `is_canonical`
- `sources`

### `interactions.parquet`
- `interaction_id`
- `entity_a_id`
- `entity_a_id_type`
- `entity_b_id`
- `entity_b_id_type`
- `direction`
- `sign`
- `record_attributes`
- `entity_a_attributes`
- `entity_b_attributes`
- `evidence`
- `source`

And similarly for associations and annotations.

---

## Core implication

The exported `(entity_id, entity_id_type)` pair is already the warehouse entity key.

Because of that, Postgres no longer needs:
- a source-local entity table keyed by numeric IDs
- a source-entity to canonical-entity mapping table

The database can treat `(entity_id_type, entity_id)` as the primary entity key directly.

---

## Recommended base tables

## 1. `entity`

Main global entity table.

Suggested columns:
- `entity_id text not null`
- `entity_id_type text not null`
- `entity_type text null`
- `taxonomy_id text null`
- `entity_attributes jsonb null`
- `sources text[] not null`

Primary key:
- `(entity_id_type, entity_id)`

Notes:
- this is the deduplicated global entity universe
- if the same entity appears in multiple resources, it is still one row here
- `sources` is the aggregated list of contributing resources
- even though `entities.parquet` still has scalar `source`, the warehouse entity table should store aggregated `sources`

Suggested DDL:

```sql
create table entity (
  entity_id text not null,
  entity_id_type text not null,
  entity_type text,
  taxonomy_id text,
  entity_attributes jsonb,
  sources text[] not null default '{}',
  primary key (entity_id_type, entity_id)
);
```

---

## 2. `entity_identifier`

Unified identifier table matching the new artifact design.

Suggested columns:
- `entity_id text not null`
- `entity_id_type text not null`
- `identifier text not null`
- `identifier_type text not null`
- `is_canonical boolean not null`
- `sources text[] not null`

Foreign key:
- `(entity_id_type, entity_id)` references `entity(entity_id_type, entity_id)`

Primary key:
- `(entity_id_type, entity_id, identifier_type, identifier)`

Notes:
- this replaces the old split source/resolved identifier tables
- `sources` means provenance of the identifier assertion
- array-based provenance is fine for MVP

Suggested DDL:

```sql
create table entity_identifier (
  entity_id text not null,
  entity_id_type text not null,
  identifier text not null,
  identifier_type text not null,
  is_canonical boolean not null,
  sources text[] not null default '{}',
  primary key (entity_id_type, entity_id, identifier_type, identifier),
  foreign key (entity_id_type, entity_id)
    references entity (entity_id_type, entity_id)
);
```

---

## 3. `interaction_evidence`

Store source-specific interaction rows as-is.

Suggested columns:
- `source text not null`
- `interaction_id bigint not null`
- `entity_a_id text not null`
- `entity_a_id_type text not null`
- `entity_b_id text not null`
- `entity_b_id_type text not null`
- `direction bigint null`
- `sign bigint null`
- `record_attributes jsonb null`
- `entity_a_attributes jsonb null`
- `entity_b_attributes jsonb null`
- `evidence jsonb null`

Primary key:
- `(source, interaction_id)`

Foreign keys:
- `(entity_a_id_type, entity_a_id)` -> `entity`
- `(entity_b_id_type, entity_b_id)` -> `entity`

Notes:
- these rows are the provenance-bearing source assertions
- they should remain uncollapsed in the base table
- aggregation should happen in a derived layer, not here

Suggested DDL:

```sql
create table interaction_evidence (
  source text not null,
  interaction_id bigint not null,
  entity_a_id text not null,
  entity_a_id_type text not null,
  entity_b_id text not null,
  entity_b_id_type text not null,
  direction bigint,
  sign bigint,
  record_attributes jsonb,
  entity_a_attributes jsonb,
  entity_b_attributes jsonb,
  evidence jsonb,
  primary key (source, interaction_id),
  foreign key (entity_a_id_type, entity_a_id)
    references entity (entity_id_type, entity_id),
  foreign key (entity_b_id_type, entity_b_id)
    references entity (entity_id_type, entity_id)
);
```

---

## 4. `association_evidence`

Same pattern as interactions.

Suggested columns:
- `source text not null`
- `association_id bigint not null`
- `parent_entity_id text not null`
- `parent_entity_id_type text not null`
- `member_entity_id text not null`
- `member_entity_id_type text not null`
- `role_term_id text null`
- `stoichiometry text null`
- `record_attributes jsonb null`
- `parent_attributes jsonb null`
- `member_attributes jsonb null`
- `evidence jsonb null`

Primary key:
- `(source, association_id)`

Foreign keys:
- parent and member endpoint pairs reference `entity`

---

## 5. `entity_annotation_evidence`

Entity-targeting annotations should be stored separately.

Suggested columns:
- `source text not null`
- `entity_id text not null`
- `entity_id_type text not null`
- `cv_term text not null`

Foreign key:
- `(entity_id_type, entity_id)` -> `entity`

Notes:
- keeping entity annotations separate keeps the model clean
- this avoids mixing entity and interaction subjects in one evidence table

---

## 6. `interaction_annotation_evidence`

Interaction-targeting annotations should be stored in a separate source-specific evidence table, parallel to `entity_annotation_evidence`.

Suggested columns:
- `source text not null`
- `interaction_id bigint not null`
- `cv_term text not null`

Foreign key:
- `(source, interaction_id)` -> `interaction_evidence(source, interaction_id)`

Notes:
- this points to source-specific interaction evidence rows only
- this keeps row-level annotation provenance explicit
- aggregated interaction annotations should be produced in a derived materialized view

---

## Materialized views

## 1. `mv_interaction`

Aggregate source-specific interaction evidence rows into one deduplicated interaction layer with a stable aggregated interaction ID.

### Group by
- normalized interaction identity based on:
  - `entity_a_id`
  - `entity_a_id_type`
  - `entity_b_id`
  - `entity_b_id_type`
  - `direction`
  - `sign`

### Aggregate
- `evidence_count`
- `sources text[]`
- optional summarized evidence statistics

Suggested columns:
- `interaction_id`
- `entity_a_id`
- `entity_a_id_type`
- `entity_b_id`
- `entity_b_id_type`
- `direction`
- `sign`
- `evidence_count bigint`
- `sources text[]`

Important:
- for undirected rows, endpoint ordering should likely be normalized before aggregation
- for directed rows, endpoint order should be preserved
- `interaction_id` should be a stable aggregated interaction ID derived from the normalized interaction identity

---

## 2. `mv_entity_summary`

Optional but useful summary view.

Possible inputs:
- `entity`
- `entity_identifier`
- interactions, associations, annotations

Potential columns:
- `entity_id`
- `entity_id_type`
- `entity_type`
- `taxonomy_id`
- `sources`
- `identifier_count`
- `interaction_count`
- `annotation_count`

This is not required for correctness but would be useful for browsing and APIs.

---

## 3. `mv_interaction_annotation`

Aggregate source-specific interaction annotation evidence onto the deduplicated interaction layer.

Inputs:
- `interaction_annotation_evidence`
- `interaction_evidence`
- `mv_interaction`

Group by:
- aggregated `interaction_id`
- `cv_term`

Aggregate:
- `sources text[]`

Suggested columns:
- `interaction_id`
- `cv_term`
- `sources text[]`

Notes:
- this view should map source-specific interaction evidence rows onto the stable aggregated `interaction_id` exposed by `mv_interaction`
- this gives per-term provenance at the aggregated interaction level
- if needed later, it can also gain support counts

---

## Ingestion flow

### Phase 1
Load and upsert global entities:
- from all `entities.parquet`
- dedupe on `(entity_id_type, entity_id)`
- merge `sources`

### Phase 2
Load and upsert `entity_identifier`:
- dedupe on `(entity_id_type, entity_id, identifier_type, identifier)`
- merge `sources`
- preserve `is_canonical`

### Phase 3
Load `interaction_evidence`
- rows can directly foreign-key to `entity`

### Phase 4
Load `association_evidence`, `entity_annotation_evidence`, and `interaction_annotation_evidence`

### Phase 5
Refresh materialized views:
- `mv_interaction`
- optional `mv_entity_summary`
- `mv_interaction_annotation`

---

## Why this model is simpler

Because the semantic entity identity has already been decided upstream, the database no longer needs to answer:
- what is the local source entity?
- how does it map to a canonical entity?
- which canonical entity should this endpoint collapse to?

That complexity disappears.

The database only needs to:
- store global entities
- store identifier assertions
- store source-specific evidence rows
- build derived aggregate views

---

## Recommended core warehouse objects

### Base tables
- `entity`
- `entity_identifier`
- `interaction_evidence`
- `association_evidence`
- `entity_annotation_evidence`
- `interaction_annotation_evidence`

### Derived objects
- `mv_interaction`
- optional `mv_entity_summary`
- `mv_interaction_annotation`

---

## Naming note

Avoid `canonical_entity` here.

Under this model, the main entity table already represents the semantic identity space directly, so the simplest and clearest name is just:
- `entity`

and:
- `entity_identifier`
