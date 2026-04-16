# Reduced-size schema changes

This document summarizes the size-optimized schema variant for exported parquet artifacts.

## Summary

Main goals:
- replace repeated long string identifiers with integer surrogate keys
- remove duplicated canonical identifiers
- drop constant, empty, and all-null columns
- move evidence and identifier data closer to the level where it belongs

## File-level changes

### `entity.parquet`

New columns:
- `entity_pk`: integer surrogate key
- `canonical_identifier`: canonical entity identifier
- `canonical_identifier_type`: type of canonical identifier
- `identifiers`: `list<struct<identifier, identifier_type>>` containing non-canonical identifiers

Kept:
- `entity_type`
- `taxonomy_id`
- `entity_attributes`
- `sources`

Notes:
- canonical identifiers are stored only here
- non-canonical identifiers are folded into the entity row instead of being repeated in a separate identifier table

### `interaction.parquet`

New columns:
- `interaction_pk`: integer surrogate key
- `entity_a_pk`: integer foreign key to `entity.entity_pk`
- `entity_b_pk`: integer foreign key to `entity.entity_pk`

Kept:
- `evidence_count`
- `direction` 
- `sign` 
- `sources` 
Removed:
- string `interaction_id`
- `entity_a_id`
- `entity_a_id_type`
- `entity_b_id`
- `entity_b_id_type`


### `interaction_evidence.parquet`

New columns:
- `interaction_pk`: integer foreign key to `interaction.interaction_pk`

Kept:
- `record_attributes`
- `evidence`
- `direction` 
- `sign` 
- `entity_a_attributes` 
- `entity_b_attributes`
- `source`


Removed:
- `interaction_id`
- `entity_a_id`
- `entity_a_id_type`
- `entity_b_id`
- `entity_b_id_type`

## Removed file

### `entity_identifiers.parquet`

This file is eliminated in the reduced-size variant.

Its contents are folded into `entity.parquet`:
- the canonical identifier is kept as scalar columns on the entity row
- non-canonical identifiers are stored in nested `identifiers`

## Expected impact

The biggest savings come from:
- replacing repeated long string foreign keys with integer keys
- removing repeated entity identifiers from `interaction_evidence.parquet`
- eliminating canonical identifier duplication
- dropping constant and empty columns
