# Ontology Entity Model Frontend Handoff

The build pipeline no longer exposes ontology terms as a separate warehouse table. Ontology terms are now normal rows in `entity`.

## New Model

Ontology term rows are identified by:

```sql
entity_type = 'OM:0012:Cv Term'
canonical_identifier_type = 'OM:0204:Cv Term Accession'
```

Relevant fields:

- `canonical_identifier`: ontology accession, e.g. `GO:0008150`, `HP:0000118`, `MI:0001`, `OM:0012`
- `sources`: resource IDs, e.g. `go`, `hpo`, `psi_mi`, `omnipath_ontology`
- `entity_attributes`: includes label/name, definition, synonyms, and `ontology_prefix`

Definitions are stored as:

```text
term = 'OM:0801:Definition'
```

Labels and synonyms are also present as entity identifiers and attributes:

```text
OM:0202:Name
OM:0203:Synonym
```

## Removed/Changed Artifacts

Removed as final/public outputs:

- `ontology_term.parquet`
- PostgreSQL table `ontology_term`

Changed:

- `relation_annotation_term.term_id` is replaced by `relation_annotation_term.term_entity_pk`
- `relation_annotation_term.term_entity_pk` references `entity.entity_pk`

## Counts And Filters

Ontology prefix counts should be computed from CV-term entities, not `ontology_term`.

Example:

```sql
SELECT
  lower(split_part(canonical_identifier, ':', 1)) AS ontology_prefix,
  count(*) AS term_count
FROM entity
WHERE entity_type = 'OM:0012:Cv Term'
  AND canonical_identifier_type = 'OM:0204:Cv Term Accession'
GROUP BY 1
ORDER BY 1;
```

Scoped annotation filters should continue to use bitmap tables or annotation relations, but term identity is now `term_entity_pk`.

Entity annotation facts:

```sql
entity_relation.relation_category = 'annotation'
entity_relation.object_entity_pk = ontology term entity_pk
```

Relation annotation facts:

```sql
relation_annotation_term.term_entity_pk = ontology term entity_pk
```

## Resources

Ontology resources are now first-class resource records with `resource_kind = 'ontology'`.

Expected ontology resource IDs:

- `go`
- `hpo`
- `psi_mi`
- `omnipath_ontology`

Use `resources.resource_id` as the stable key and `resources.resource_name` for display.

## Frontend Migration Notes

- Replace reads from `ontology_term` with reads from `entity` filtered to CV-term rows.
- Replace joins on `term_id` with joins on `term_entity_pk` where using `relation_annotation_term`.
- For display, derive accession from `entity.canonical_identifier`.
- Extract label/definition/synonyms from `entity.identifiers` and/or `entity_attributes`.
- For resource icons, use `resources.resource_kind = 'ontology'`.
