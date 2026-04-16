# OmniPath API schema guide

API-facing summary of the resource-specific datasets returned by the current API.

## Common artifact files

- `entity.parquet`
- `entity_identifiers.parquet`
- `interaction.parquet`
- `interaction_evidence.parquet`
- `association.parquet`
- `association_evidence.parquet`
- `entity_annotation.parquet`
- `interaction_annotation.parquet`

Not every resource ships every file.

## File roles

### `entity.parquet`
Canonical entity table for the resource.
Typical fields: `entity_id`, `entity_id_type`, `entity_type`, `entity_attributes`, `taxonomy_id`, `sources`.
Use it to inspect the resource's entity universe and metadata.

### `entity_identifiers.parquet`
Alternate identifiers and synonyms.
Typical fields: `entity_id`, `entity_id_type`, `identifier`, `identifier_type`, `is_canonical`, `sources`.
Use it for ID translation only, not as the default cross-resource join surface.

### `interaction.parquet`
Compact semantic interaction table.
Typical fields: `interaction_id`, `entity_a_id`, `entity_a_id_type`, `entity_b_id`, `entity_b_id_type`, `direction`, `sign`, `evidence_count`, `sources`.

### `interaction_evidence.parquet`
Provenance-bearing interaction assertions.
Typical fields: `source`, `interaction_id`, `entity_a_id`, `entity_a_id_type`, `entity_b_id`, `entity_b_id_type`, `direction`, `sign`, `record_attributes`, `evidence`.

### `association.parquet`
Compact parent-member relationships.
Typical fields: `association_id`, `parent_entity_id`, `parent_entity_id_type`, `member_entity_id`, `member_entity_id_type`, `role_term_id`, `stoichiometry`, `sources`.

### `association_evidence.parquet`
Provenance-bearing parent-member assertions.
Typical fields: `source`, `association_id`, `parent_entity_id`, `parent_entity_id_type`, `member_entity_id`, `member_entity_id_type`, `role_term_id`, `stoichiometry`, `evidence`.

### `entity_annotation.parquet`
Entity-level ontology-like annotations.
Typical fields: `entity_id`, `entity_id_type`, `cv_term`, `sources`.
`cv_term` may contain ontology IDs, pathway IDs, or other controlled-vocabulary terms depending on the resource.

### `interaction_annotation.parquet`
Interaction-level ontology-like annotations.
Typical fields: `interaction_id`, `cv_term`, `sources`.
`cv_term` may contain ontology IDs, pathway IDs, or other controlled-vocabulary terms depending on the resource.

## Join pattern

- use the canonical identifier columns already present in each public table for joins
- entity-level joins: `(entity_id, entity_id_type)`
- interaction tables carry canonical endpoint columns: `(entity_a_id, entity_a_id_type)` and `(entity_b_id, entity_b_id_type)`
- association tables carry canonical parent/member columns: `(parent_entity_id, parent_entity_id_type)` and `(member_entity_id, member_entity_id_type)`
- use `entity_identifiers.parquet` only to map alternate or user-supplied IDs to canonical ones
