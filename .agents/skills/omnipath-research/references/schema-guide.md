# OmniPath API schema guide

This guide is intentionally API-facing and only covers the resource-specific datasets returned by the current API.

## 1. Resource-specific artifact files

When using resource download endpoints, expect parquet files such as:
- `entities.parquet`
- `interactions.parquet`
- `associations.parquet`
- `annotations.parquet`
- `entity_identifiers_source.parquet`
- `entity_identifiers_resolved.parquet`

Not every resource will provide every file.
Use the workspace manifest endpoint first to see what exists for a chosen resource.

## 2. `entities.parquet`

Typical fields include:
- `entity_id`
- `entity_type`
- `entity_attributes`
- `taxonomy_id`
- `source`

Use this file to:
- define the local entity universe for a resource
- inspect entity types such as proteins, pathways, complexes, or small molecules
- join other resource files back to entity metadata

## 3. `interactions.parquet`

Typical fields include:
- `interaction_id`
- `entity_a_id`
- `entity_b_id`
- `direction`
- `sign`
- `record_attributes`
- endpoint-specific attribute fields
- `evidence`
- `source`

Use this file to:
- analyze source-specific interaction networks
- study causal direction and sign where available
- retrieve evidence-bearing interaction records for local analysis

## 4. `associations.parquet`

Typical fields include:
- `association_id`
- `parent_entity_id`
- `member_entity_id`
- `role_term_id`
- `stoichiometry`
- `evidence`
- `source`

Use this file to:
- inspect parent-member relationships
- retrieve structured memberships where a resource represents them explicitly
- analyze reaction, complex, or other hierarchical records when present

## 5. `annotations.parquet`

Typical fields include:
- `subject_type`
- `subject_id`
- `cv_term`
- `source`

Use this file to:
- inspect ontology-like annotations attached to entities or interactions
- build cohorts within one resource based on terms already present in that resource
- inspect direct term assignments exposed by a resource

## 6. Identifier tables

### `entity_identifiers_source.parquet`
Contains raw source-provided identifiers.

Typical fields include:
- `entity_id`
- `identifier`
- `identifier_type`
- `source`

### `entity_identifiers_resolved.parquet`
Contains resolved authoritative identifiers where canonicalization succeeded.

Typical fields include:
- `entity_id`
- `identifier`
- `identifier_type`
- `is_canonical`
- `source`

Important rule:
- when joining resolved entities across resource-specific datasets, use `entity_identifiers_resolved.parquet`
- prefer rows with `is_canonical = true`

Standard cross-resource join pattern:
1. filter `entity_identifiers_resolved.parquet` to `is_canonical = true`
2. join across resources on `(identifier, identifier_type)`
3. join back to `entities.parquet`, `annotations.parquet`, `interactions.parquet`, or `associations.parquet` as needed

This design already supports cross-resource analysis cleanly. Adding identifier fields directly to `entities.parquet` would only be a denormalized convenience.

## 7. Practical joining pattern

### Within one resource
- join `interactions.parquet` or `associations.parquet` back to `entities.parquet` using local `entity_id`

### Across resources
- use resolved canonical identifiers where available
- prefer `entity_identifiers_resolved.parquet` rows with `is_canonical = true`
- join across resources on `(identifier, identifier_type)`
- join back to local resource tables after the identifier join
- treat unresolved entities as potentially non-joinable across resources
