# PostgreSQL Search Migration Plan

Date: 2026-03-25

## Goal

Replace the current Meilisearch-based search stack with PostgreSQL, while only normalizing the biggest storage/memory wins.

Key decisions:

1. **No full-text search required** for the new system.
2. **Prefix search on identifiers is sufficient** for entity lookup.
3. **Normalize ontology term arrays** out of the large search documents.
4. Keep the main entity / interaction / association records as **lean denormalized summary tables**.
5. Keep evidence out of the hot search path initially:
   - either in dedicated PostgreSQL evidence tables, or
   - continue serving it from parquet sidecars during migration.

---

## Reference implementation note

For identifier prefix search, the old PostgreSQL implementation in `../omnipath-next` is the right model:

- query pattern: `identifier_value ILIKE query || '%'`
- index pattern: `btree (identifier_value text_pattern_ops)`
- optional trigram index for fuzzy fallback, if ever needed later

Relevant files:

- `../omnipath-next/src/db/queries.ts`
- `../omnipath-next/src/db/drizzle/schema.ts`
- `../omnipath-next/db_build/uniprot_simple_loader.py`

---

# 1. Current outputs and proposed PostgreSQL schema

## 1.1 `entity_identifier.parquet`

## Current schema

Observed columns:

- `id`
- `entity_key`
- `type_id`
- `identifier`

Example:

```json
{
  "id": 1,
  "entity_key": "C:CPX:CPX-1072",
  "type_id": "OM:0007",
  "identifier": "SIGNOR-C41"
}
```

## Role in the new system

This becomes the **main text-search entrypoint**.

We do not need full-text search over entity summaries if prefix search here is enough.

## Proposed PostgreSQL table

### `search.entity_identifier`

```sql
create table search.entity_identifier (
  id bigint primary key,
  entity_id text not null,
  type_id text not null,
  tax_id text,
  identifier_value text not null,
  identifier_value_norm text not null
);
```

### indexes

```sql
create index entity_identifier_entity_idx
  on search.entity_identifier (entity_id);

create index entity_identifier_type_idx
  on search.entity_identifier (type_id);

create index entity_identifier_tax_id_idx
  on search.entity_identifier (tax_id);

create index entity_identifier_value_prefix_idx
  on search.entity_identifier (identifier_value_norm text_pattern_ops);

create index entity_identifier_tax_prefix_idx
  on search.entity_identifier (tax_id, identifier_value_norm text_pattern_ops);
```

## Notes

- `entity_key` becomes `entity_id` in PostgreSQL-facing search tables.
- `identifier_value_norm` should be a normalized form used for prefix matching.
- `tax_id` should live directly on the identifier table.
  - This matches the old PostgreSQL implementation in `../omnipath-next`, where identifier search accepted an optional `taxonId` and applied it directly in the prefix-search query.
  - For the new schema, I recommend adding the composite index `(tax_id, identifier_value_norm text_pattern_ops)` so species-scoped prefix search is efficient.
- Optional later:
  - trigram index for fuzzy matching
  - source provenance side table if needed in search APIs

### Optional provenance table

```sql
create table search.entity_identifier_source (
  entity_identifier_id bigint not null references search.entity_identifier(id),
  source_ref text not null,
  primary key (entity_identifier_id, source_ref)
);
```

---

## 1.2 `search_entities.parquet`

## Current schema

Observed columns:

- `entity_id`
- `entity_type`
- `names`
- `synonyms`
- `gene_symbols`
- `descriptions`
- `ncbi_tax_id`
- `ontology_terms`
- `num_interactions`
- `sources`
- `identifiers`

Example:

```json
{
  "entity_id": "C:CPX:CPX-1072",
  "entity_type": "complex:MI:0314",
  "names": ["DICER1/hAgo2/PRKRA"],
  "synonyms": [],
  "gene_symbols": [],
  "descriptions": [],
  "ncbi_tax_id": "9606",
  "ontology_terms": [],
  "num_interactions": 1,
  "sources": ["SIGNOR:OM:1152"],
  "identifiers": [
    {"key": "complexportal:OM:0105", "value": "CPX-1072"},
    {"key": "signor:OM:0007", "value": "SIGNOR-C41"}
  ]
}
```

## Current pain point

The main repeated payload worth normalizing here is:

- `ontology_terms`

The remaining fields are acceptable to keep denormalized in a summary table.

## Proposed PostgreSQL table

### `search.entity`

```sql
create table search.entity (
  entity_id text primary key,
  entity_type text not null,
  primary_name text,
  names text[] not null default '{}',
  synonyms text[] not null default '{}',
  gene_symbols text[] not null default '{}',
  descriptions text[] not null default '{}',
  ncbi_tax_id text,
  num_interactions integer not null default 0,
  sources text[] not null default '{}',
  identifiers jsonb not null default '[]'::jsonb
);
```

### indexes

```sql
create index entity_type_idx on search.entity (entity_type);
create index entity_tax_idx on search.entity (ncbi_tax_id);
create index entity_sources_gin_idx on search.entity using gin (sources);
```

## Proposed normalization

### `search.ontology_term`

```sql
create table search.ontology_term (
  term_id text primary key,
  ontology_prefix text not null,
  label text
);
```

### `search.entity_ontology_term`

```sql
create table search.entity_ontology_term (
  entity_id text not null references search.entity(entity_id) on delete cascade,
  term_id text not null references search.ontology_term(term_id),
  primary key (entity_id, term_id)
);
```

### indexes

```sql
create index entity_ontology_term_term_idx
  on search.entity_ontology_term (term_id);

create index entity_ontology_term_entity_idx
  on search.entity_ontology_term (entity_id);
```

## Notes

- Entity search should start from `search.entity_identifier`, not from `search.entity`.
- `search.entity` is primarily for:
  - result rendering
  - exact filtering
  - exporting
  - follow-up lookups after identifier prefix search

---

## 1.3 `search_interactions.parquet`

## Current schema

Observed columns:

- `interaction_id`
- `interaction_key`
- `member_a_id`
- `member_b_id`
- `member_types`
- `interaction_type`
- `is_directed`
- `sign`
- `evidence`
- `sources`
- `interaction_annotation_terms`
- `participant_annotation_terms`
- `evidence_count`

Example:

```json
{
  "interaction_id": 1,
  "interaction_key": "C:CPX:CPX-1072-X:SIGNOR:SIGNOR-PH95|d|-1",
  "member_a_id": "C:CPX:CPX-1072",
  "member_b_id": "X:SIGNOR:SIGNOR-PH95",
  "member_types": ["complex:MI:0314", "phenotype:MI:2261"],
  "interaction_type": "complex:MI:0314|phenotype:MI:2261",
  "is_directed": true,
  "sign": -1,
  "evidence": [...],
  "sources": ["SIGNOR:OM:1152"],
  "interaction_annotation_terms": ["MI:0217", "MI:0364", "MI:2240"],
  "participant_annotation_terms": [],
  "evidence_count": 1
}
```

## Current pain points

The biggest normalization wins are:

- `interaction_annotation_terms`
- `participant_annotation_terms`

Potential secondary win:

- `evidence`

## Proposed PostgreSQL table

### `search.interaction`

```sql
create table search.interaction (
  interaction_id bigint primary key,
  interaction_key text not null unique,
  member_a_id text not null,
  member_b_id text not null,
  interaction_type text not null,
  member_types text[] not null default '{}',
  is_directed boolean not null,
  sign smallint not null,
  evidence_count integer not null default 0,
  sources text[] not null default '{}'
);
```

### indexes

```sql
create index interaction_member_a_idx on search.interaction (member_a_id);
create index interaction_member_b_idx on search.interaction (member_b_id);
create index interaction_type_idx on search.interaction (interaction_type);
create index interaction_directed_idx on search.interaction (is_directed);
create index interaction_sign_idx on search.interaction (sign);
create index interaction_sources_gin_idx on search.interaction using gin (sources);
```

## Proposed normalization

### interaction-level terms

```sql
create table search.interaction_annotation_term (
  interaction_id bigint not null references search.interaction(interaction_id) on delete cascade,
  term_id text not null references search.ontology_term(term_id),
  primary key (interaction_id, term_id)
);
```

### participant-level terms

```sql
create table search.interaction_participant_term (
  interaction_id bigint not null references search.interaction(interaction_id) on delete cascade,
  entity_id text not null,
  role text,
  term_id text not null references search.ontology_term(term_id),
  primary key (interaction_id, entity_id, term_id)
);
```

### indexes

```sql
create index interaction_annotation_term_term_idx
  on search.interaction_annotation_term (term_id);

create index interaction_participant_term_term_idx
  on search.interaction_participant_term (term_id);

create index interaction_participant_term_entity_idx
  on search.interaction_participant_term (entity_id);

create index interaction_participant_term_interaction_idx
  on search.interaction_participant_term (interaction_id);
```

## Evidence handling

Recommendation for the initial migration:

- keep `evidence` **out of the hot search table**
- either:
  - continue serving from parquet, or
  - move to a dedicated side table

### optional table

```sql
create table search.interaction_evidence (
  interaction_id bigint not null references search.interaction(interaction_id) on delete cascade,
  evidence_serial integer not null,
  source text not null,
  interaction_annotations jsonb not null default '[]'::jsonb,
  member_a_annotations jsonb not null default '[]'::jsonb,
  member_b_annotations jsonb not null default '[]'::jsonb,
  primary key (interaction_id, evidence_serial)
);
```

---

## 1.4 `search_associations.parquet`

## Current schema

Observed columns:

- `association_id`
- `association_key`
- `parent_entity_id`
- `parent_entity_type`
- `member_entity_id`
- `member_entity_type`
- `sources`
- `evidence`
- `association_annotation_terms`

Example:

```json
{
  "association_id": 1,
  "association_key": "C:CPX:CPX-1072_P:UP:A0A6Q8PFV4",
  "parent_entity_id": "C:CPX:CPX-1072",
  "parent_entity_type": "complex:MI:0314",
  "member_entity_id": "P:UP:A0A6Q8PFV4",
  "member_entity_type": "protein:MI:0326",
  "sources": ["SIGNOR:OM:1152"],
  "evidence": [{"evidence_serial": 1, "source": "SIGNOR:OM:1152", "annotations": []}],
  "association_annotation_terms": []
}
```

## Current pain point

The biggest normalization win here is:

- `association_annotation_terms`

Evidence separation is optional but sensible.

## Proposed PostgreSQL table

### `search.association`

```sql
create table search.association (
  association_id bigint primary key,
  association_key text not null unique,
  parent_entity_id text not null,
  parent_entity_type text not null,
  member_entity_id text not null,
  member_entity_type text not null,
  sources text[] not null default '{}',
  evidence_count integer not null default 0
);
```

### indexes

```sql
create index association_parent_id_idx on search.association (parent_entity_id);
create index association_member_id_idx on search.association (member_entity_id);
create index association_parent_type_idx on search.association (parent_entity_type);
create index association_member_type_idx on search.association (member_entity_type);
create index association_sources_gin_idx on search.association using gin (sources);
```

## Proposed normalization

### `search.association_annotation_term`

```sql
create table search.association_annotation_term (
  association_id bigint not null references search.association(association_id) on delete cascade,
  term_id text not null references search.ontology_term(term_id),
  primary key (association_id, term_id)
);
```

### indexes

```sql
create index association_annotation_term_term_idx
  on search.association_annotation_term (term_id);

create index association_annotation_term_assoc_idx
  on search.association_annotation_term (association_id);
```

## optional evidence table

```sql
create table search.association_evidence (
  association_id bigint not null references search.association(association_id) on delete cascade,
  evidence_serial integer not null,
  source text not null,
  annotations jsonb not null default '[]'::jsonb,
  primary key (association_id, evidence_serial)
);
```

---

# 2. Summary of what is normalized vs kept flat

## Normalize now

These are the biggest wins and should be the first things moved out of document-like rows:

- entity `ontology_terms`
- interaction `interaction_annotation_terms`
- interaction `participant_annotation_terms`
- association `association_annotation_terms`

## Keep flat for now

These should remain denormalized in the main PostgreSQL summary tables:

- `entity_type`
- `interaction_type`
- `member_types`
- `is_directed`
- `sign`
- `num_interactions`
- `evidence_count`
- `names`
- `synonyms`
- `gene_symbols`
- `descriptions`
- `sources`
- `identifiers`

## Optional later

These can be revisited later if still too heavy:

- evidence payloads
- sources as join tables/dim tables

---

# 3. Query model in PostgreSQL

## 3.1 Entity lookup

Entity search becomes a two-step process:

1. prefix search in `search.entity_identifier`
2. fetch matching entity rows from `search.entity`
3. optionally filter by ontology terms via `search.entity_ontology_term`

Example:

```sql
select entity_id, identifier_value, type_id, tax_id
from search.entity_identifier
where identifier_value_norm like upper($1) || '%'
order by
  case when identifier_value_norm = upper($1) then 1 else 2 end,
  identifier_value_norm
limit 20;
```

Species-scoped variant:

```sql
select entity_id, identifier_value, type_id, tax_id
from search.entity_identifier
where tax_id = $2
  and identifier_value_norm like upper($1) || '%'
order by
  case when identifier_value_norm = upper($1) then 1 else 2 end,
  identifier_value_norm
limit 20;
```

Then:

```sql
select e.*
from search.entity e
where e.entity_id = any($1::text[]);
```

Ontology filter:

```sql
select e.*
from search.entity e
where exists (
  select 1
  from search.entity_ontology_term t
  where t.entity_id = e.entity_id
    and t.term_id = 'GO:0003677'
);
```

---

## 3.2 Interaction filtering

Main table holds the cheap/high-value summary fields.
Term filters go through `exists` clauses.

Example:

```sql
select i.*
from search.interaction i
where i.member_a_id = $1
   or i.member_b_id = $1;
```

Interaction annotation term filter:

```sql
select i.*
from search.interaction i
where exists (
  select 1
  from search.interaction_annotation_term t
  where t.interaction_id = i.interaction_id
    and t.term_id = 'MI:0217'
);
```

Participant term filter:

```sql
select i.*
from search.interaction i
where exists (
  select 1
  from search.interaction_participant_term t
  where t.interaction_id = i.interaction_id
    and t.term_id = 'GO:0003677'
);
```

---

## 3.3 Association filtering

Example:

```sql
select a.*
from search.association a
where a.parent_entity_id = $1;
```

Association annotation term filter:

```sql
select a.*
from search.association a
where exists (
  select 1
  from search.association_annotation_term t
  where t.association_id = a.association_id
    and t.term_id = 'OM:0501'
);
```

---

# 4. Migration phases

## Phase 1: define PostgreSQL schema

Create a new `search` schema with:

- `search.entity_identifier`
- `search.entity`
- `search.interaction`
- `search.association`
- `search.ontology_term`
- `search.entity_ontology_term`
- `search.interaction_annotation_term`
- `search.interaction_participant_term`
- `search.association_annotation_term`

Optional:

- `search.entity_identifier_source`
- `search.interaction_evidence`
- `search.association_evidence`

---

## Phase 2: update build pipeline outputs

Add a PostgreSQL load/export path from the existing parquet build outputs.

Initial load sources:

- `entity_identifier.parquet`
- `search_entities.parquet`
- `search_interactions.parquet`
- `search_associations.parquet`

### transformation rules

- `entity_key` -> `entity_id`
- populate `search.entity_identifier.tax_id` from the entity taxon assignment used for search filtering
  - this should be the same species/taxonomy signal currently exposed via `search_entities.ncbi_tax_id`
  - if an identifier belongs to an entity with no taxon, keep `tax_id = null`
- move repeated ontology arrays into join tables
- populate `search.ontology_term` with canonical unique term IDs
- preserve current summary fields in main tables

### tax_id population plan for `search.entity_identifier`

Recommended build rule:

1. build or read the entity-level taxonomy mapping first
   - source of truth should be the same logic currently used to populate `search_entities.ncbi_tax_id`
2. load `entity_identifier.parquet`
3. left join identifiers on entity taxonomy by `entity_id`
4. write the resulting `tax_id` onto every identifier row for that entity

Conceptually:

```text
entity_identifier(id, entity_id, type_id, identifier)
  +
entity_taxonomy(entity_id, tax_id)
  ->
search.entity_identifier(id, entity_id, type_id, tax_id, identifier_value, identifier_value_norm)
```

Practical options:

#### Option A: derive from `search_entities.parquet`

This is the simplest migration path.

- build `search_entities.parquet` as today
- read `entity_id` + `ncbi_tax_id`
- join onto `entity_identifier.parquet`
- load into PostgreSQL

Pros:
- simplest implementation
- guaranteed consistency with current entity search filtering

Cons:
- `entity_identifier` loading depends on the `search_entities` build artifact

#### Option B: derive directly from global tables

This is the cleaner long-term approach.

- derive an `entity_id -> tax_id` mapping from the same global-table logic currently used by `build_search_entities.py`
- join that mapping directly when populating `search.entity_identifier`

Pros:
- cleaner dependency graph
- avoids depending on a downstream search parquet to populate another search table

Cons:
- slightly more implementation work

### edge cases

- entities without taxonomy annotations -> `tax_id = null`
- entities with a single taxonomy annotation -> use that tax ID directly
- entities with multiple taxonomy annotations:
  - if this occurs, keep behavior aligned with the current `search_entities.ncbi_tax_id` logic
  - do **not** invent new resolution rules in the identifier loader
  - the identifier table should mirror entity search semantics exactly

---

## Phase 3: switch search APIs in `../omnipath-present`

Replace the Meilisearch-based query layer with PostgreSQL-backed endpoints.

Main targets:

- entity identifier search -> PostgreSQL prefix query
- entity details/search results -> PostgreSQL summary tables
- interaction filtering -> PostgreSQL summary + join tables
- association filtering -> PostgreSQL summary + join tables

Frontend goal:

- keep payload shape as similar as possible to current search responses
- minimize React/UI changes during backend migration

---

## Phase 4: evidence cutover

Initial option:

- keep lazy evidence fetches reading from parquet

Later option:

- move evidence into PostgreSQL evidence tables
- switch existing evidence endpoints to PostgreSQL-backed lookups

This should be done only after the primary search path is stable.

---

## Phase 5: remove Meilisearch

Once PostgreSQL-backed search is validated:

- remove Meilisearch import step from `omnipath_build`
- remove Meilisearch container from `../omnipath-present/docker-compose.yaml`
- remove Meilisearch-specific query/filter code from `next-omnipath`

---

# 5. Validation checklist

## Build-side

- [ ] PostgreSQL tables receive complete data from current parquet outputs
- [ ] ontology term join tables reproduce current filter behavior
- [ ] entity identifier prefix search returns expected matches

## API-side

- [ ] entity lookup works from identifier prefix search
- [ ] entity filters work with normalized ontology joins
- [ ] interaction filters work with normalized term joins
- [ ] association filters work with normalized term joins
- [ ] evidence endpoints still return the expected payloads

## UI-side

- [ ] no user-visible regression in entity lookup
- [ ] interaction filtering behavior matches current UI
- [ ] association filtering behavior matches current UI
- [ ] ontology label rendering still works via ontology API / term metadata

## Infra-side

- [ ] Meilisearch container can be removed
- [ ] PostgreSQL index sizes and memory footprint are measured
- [ ] end-to-end latency is acceptable for common queries

---

# 6. Final recommendation

The recommended PostgreSQL design is:

1. use **`entity_identifier` as the only search-text entrypoint**
2. use **lean summary tables** for entities, interactions, and associations
3. normalize only the biggest repeated payloads:
   - ontology term arrays
4. keep evidence outside the hot path initially

This gives a much better fit for the actual workload than Meilisearch, because the workload is mostly:

- identifier prefix lookup
- exact filtering
- faceting/counting
- graph/entity neighborhood exploration

rather than broad full-text relevance search.
