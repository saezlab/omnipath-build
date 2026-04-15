# Schema inventory

This document lists the proposed canonical table/file names and column sets for the aligned source-specific parquet artifacts and the combined Postgres warehouse.

---

## Grain conventions

### Evidence-grain tables
One row per source assertion / source record.

### Aggregate tables
One row per normalized semantic object after collapsing evidence rows.

---

## Source-specific parquet artifacts

These are the proposed file-level schemas for each source output directory.

## 1. `entities.parquet`

Grain:
- one row per deduplicated entity in the source-specific package
- keyed by exported semantic entity identity

Columns:
- `entity_id text not null`
- `entity_id_type text not null`
- `entity_type text null`
- `taxonomy_id text null`
- `entity_attributes json/list null`
- `sources text[] not null`

Key:
- `(entity_id_type, entity_id)`

Notes:
- in a source-specific package, `sources` will usually be a single-element array
- this should already be deduplicated at entity grain

---

## 2. `entity_identifiers.parquet`

Grain:
- one row per identifier assertion for an entity

Columns:
- `entity_id text not null`
- `entity_id_type text not null`
- `identifier text not null`
- `identifier_type text not null`
- `is_canonical boolean not null`
- `sources text[] not null`

Key:
- `(entity_id_type, entity_id, identifier_type, identifier)`

Notes:
- `sources` captures provenance of the identifier assertion
- canonical resolver-added identifiers also live here

---

## 3. `interaction_evidence.parquet`

Grain:
- one row per source interaction assertion

Columns:
- `source text not null`
- `interaction_id bigint not null`
- `entity_a_id text not null`
- `entity_a_id_type text not null`
- `entity_b_id text not null`
- `entity_b_id_type text not null`
- `direction bigint null`
- `sign bigint null`
- `record_attributes json/list null`
- `entity_a_attributes json/list null`
- `entity_b_attributes json/list null`
- `evidence json/list null`

Key:
- `(source, interaction_id)`

Notes:
- this is the provenance-bearing base interaction file
- this should not contain aggregate-only fields like `evidence_count`

---

## 4. `interaction.parquet`

Grain:
- one row per normalized aggregated interaction identity within the source-specific package

Columns:
- `interaction_id text not null`
- `entity_a_id text not null`
- `entity_a_id_type text not null`
- `entity_b_id text not null`
- `entity_b_id_type text not null`
- `direction bigint null`
- `sign bigint null`
- `evidence_count bigint not null`
- `sources text[] not null`

Key:
- `interaction_id`

Grouping identity:
- `entity_a_id`
- `entity_a_id_type`
- `entity_b_id`
- `entity_b_id_type`
- `direction`
- `sign`

Notes:
- `interaction_id` should be a stable hash or otherwise deterministic ID derived from the normalized interaction identity
- undirected interactions should normalize endpoint ordering before aggregation
- directed interactions should preserve endpoint order

---

## 5. `association_evidence.parquet`

Grain:
- one row per source association assertion

Columns:
- `source text not null`
- `association_id bigint not null`
- `parent_entity_id text not null`
- `parent_entity_id_type text not null`
- `member_entity_id text not null`
- `member_entity_id_type text not null`
- `role_term_id text null`
- `stoichiometry text null`
- `record_attributes json/list null`
- `parent_attributes json/list null`
- `member_attributes json/list null`
- `evidence json/list null`

Key:
- `(source, association_id)`

Notes:
- this is the provenance-bearing base association file

---

## 6. `association.parquet`

Grain:
- one row per normalized aggregated association identity within the source-specific package

Columns:
- `association_id text not null`
- `parent_entity_id text not null`
- `parent_entity_id_type text not null`
- `member_entity_id text not null`
- `member_entity_id_type text not null`
- `role_term_id text null`
- `stoichiometry text null`
- `sources text[] not null`

Key:
- `association_id`

Grouping identity:
- `parent_entity_id`
- `parent_entity_id_type`
- `member_entity_id`
- `member_entity_id_type`
- `role_term_id`
- `stoichiometry`

Notes:
- this is the compact semantic association layer
- it intentionally keeps only the most important columns from `association_evidence`
- `association_id` should be a stable hash or otherwise deterministic ID derived from the normalized association identity

---

## 7. `entity_annotation.parquet`

Grain:
- one row per entity-level annotation term

Columns:
- `entity_id text not null`
- `entity_id_type text not null`
- `cv_term text not null`
- `sources text[] not null`

Key:
- `(entity_id_type, entity_id, cv_term)`

Notes:
- this stores semantic entity annotations directly
- provenance is captured as aggregated `sources`, not separate evidence rows

---

## 8. `interaction_annotation.parquet`

Grain:
- one row per interaction-level annotation term

Columns:
- `interaction_id text not null`
- `cv_term text not null`
- `sources text[] not null`

Key:
- `(interaction_id, cv_term)`

Notes:
- this stores semantic interaction annotations directly
- provenance is captured as aggregated `sources`, not separate evidence rows
- source interaction annotations are mapped onto aggregated `interaction_id`

---

## 9. Optional `entity_summary.parquet`

Grain:
- one row per entity summary

Columns:
- `entity_id text not null`
- `entity_id_type text not null`
- `entity_type text null`
- `taxonomy_id text null`
- `sources text[] not null`
- `identifier_count bigint not null`
- `interaction_count bigint not null`
- `annotation_count bigint not null`

Key:
- `(entity_id_type, entity_id)`

Notes:
- optional convenience artifact
- not required for correctness

---

## Combined Postgres warehouse objects

The warehouse should use the same logical schema names wherever possible.

Naming decision:
- use the same object names as the parquet artifacts where possible
- do not keep `_mv` in the public derived object names
- derived warehouse objects should therefore be named `interaction`, `association`, `interaction_annotation`, and `entity_summary`

## Base tables

## 1. `entity`

Columns:
- `entity_id text not null`
- `entity_id_type text not null`
- `entity_type text null`
- `taxonomy_id text null`
- `entity_attributes jsonb null`
- `sources text[] not null`

Primary key:
- `(entity_id_type, entity_id)`

---

## 2. `entity_identifier`

Columns:
- `entity_id text not null`
- `entity_id_type text not null`
- `identifier text not null`
- `identifier_type text not null`
- `is_canonical boolean not null`
- `sources text[] not null`

Primary key:
- `(entity_id_type, entity_id, identifier_type, identifier)`

Foreign key:
- `(entity_id_type, entity_id)` -> `entity`

---

## 3. `interaction_evidence`

Columns:
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

---

## 4. `interaction`

Recommended form:
- materialized view or derived table

Columns:
- `interaction_id text not null`
- `entity_a_id text not null`
- `entity_a_id_type text not null`
- `entity_b_id text not null`
- `entity_b_id_type text not null`
- `direction bigint null`
- `sign bigint null`
- `evidence_count bigint not null`
- `sources text[] not null`

Primary key:
- `interaction_id`

Derived from:
- `interaction_evidence`

---

## 5. `association_evidence`

Columns:
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
- parent and member endpoint pairs -> `entity`

---

## 6. `association`

Recommended form:
- materialized view or derived table

Columns:
- `association_id text not null`
- `parent_entity_id text not null`
- `parent_entity_id_type text not null`
- `member_entity_id text not null`
- `member_entity_id_type text not null`
- `role_term_id text null`
- `stoichiometry text null`
- `sources text[] not null`

Primary key:
- `association_id`

Derived from:
- `association_evidence`

Notes:
- this is the compact semantic association layer
- it intentionally keeps only the most important columns from `association_evidence`

---

## 7. `entity_annotation`

Columns:
- `entity_id text not null`
- `entity_id_type text not null`
- `cv_term text not null`
- `sources text[] not null`

Primary key:
- `(entity_id_type, entity_id, cv_term)`

Foreign key:
- `(entity_id_type, entity_id)` -> `entity`

---

## 8. `interaction_annotation`

Recommended form:
- materialized view or derived table

Columns:
- `interaction_id text not null`
- `cv_term text not null`
- `sources text[] not null`

Primary key:
- `(interaction_id, cv_term)`

Derived from:
- source interaction annotations mapped onto `interaction`
- `interaction_evidence`
- `interaction`

---

## 9. Optional `entity_summary`

Recommended form:
- materialized view or derived table

Columns:
- `entity_id text not null`
- `entity_id_type text not null`
- `entity_type text null`
- `taxonomy_id text null`
- `sources text[] not null`
- `identifier_count bigint not null`
- `interaction_count bigint not null`
- `annotation_count bigint not null`

Primary key:
- `(entity_id_type, entity_id)`

---

## `resources.parquet` counting conventions

`resources.parquet` should report aggregate semantic counts for the public datasets, not evidence-row counts.

Recommended count semantics:
- `entity_count` -> rows in `entities.parquet`
- `interaction_count` -> rows in `interaction.parquet`
- `association_count` -> rows in `association.parquet`
- `annotation_count` -> entity-level plus interaction-level annotation rows
  - i.e. `entity_annotation.parquet` + `interaction_annotation.parquet`
- `identifier_count` -> rows in `entity_identifiers.parquet`

If needed later, evidence-grain counts can be added as separate fields such as:
- `interaction_evidence_count`
- `association_evidence_count`

## Naming decisions captured here

Preferred names:
- `entity`
- `entity_identifier`
- `interaction_evidence`
- `interaction`
- `association_evidence`
- `association`
- `entity_annotation`
- `interaction_annotation`
- optional `entity_summary`

Avoid:
- mixed-grain `annotations`
- aggregate interaction rows stored in `interaction_evidence`
- aggregate-only columns added redundantly onto evidence rows
