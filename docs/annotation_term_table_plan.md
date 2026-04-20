# Annotation term table + ontology artifact plan

## Goal

Support annotation browse/search with:

- exact or partial accession search
- label search
- optional definition search
- ranking by annotated entity count

while keeping entity identifier lookup separate and exact-only.

---

## Agreed direction

## 1. Keep identifier lookup simple

Entity identifier resolution remains:

- exact-match only on `entity_identifier.identifier`

Keep only the minimal secondary indexes needed for that behavior:

- `entity_identifier(entity_pk)` btree
- `entity_identifier(identifier)` hash

No prefix/fuzzy/trigram identifier search.

---

## 2. Keep raw annotation facts separate

`entity_annotation` should remain the fact table:

- `entity_pk`
- `cv_term`
- `sources`

`cv_term` should stay as the canonical raw accession, e.g.:

- `GO:0002250`
- `HP:0001250`
- `MI:0217`


---

## 3. Add a real annotation term metadata table

Add a persistent table for searchable annotation term metadata.

Suggested name:

- `annotation_term`

Suggested columns:

- `accession text primary key`
- `ontology_id text null`
- `label text null`
- `namespace text null`
- `definition text null`

This table is the source for annotation term search metadata.

---

## 4. Keep counts in a separate materialized view

Keep annotation counts separate from metadata and align naming with existing count materialized views.

Suggested MV name:

- `entity_annotation_counts`

Suggested columns:

- `accession text`
- `annotated_entity_count bigint`

Definition:

- built from `entity_annotation`
- grouped by `cv_term`
- `COUNT(DISTINCT entity_pk)`

This replaces the current ad hoc `entity_annotation_search` materialized view.

---

## 5. Frontend/backend query model

Annotation browse/search should query a join of:

- `annotation_term`
- `entity_annotation_counts`

This joined result should expose frontend-ready fields:

- `accession`
- `label`
- `namespace`
- `definition`
- `annotated_entity_count`

Possible implementation:

- keep `annotation_term` as a table
- keep `entity_annotation_counts` as an MV
- expose a simple SQL view for joined browse/search results if helpful

---

## 6. Search indexing for annotation terms

On `annotation_term` add:

- unique index on `accession`
- trigram index on `label`
- trigram index on `accession` if substring accession search is desired
- optional trigram index on `definition`

On `entity_annotation_counts` add:

- unique index on `accession`
- browse/sort index on `(annotated_entity_count DESC, accession)`

---

## 7. Ontology artifacts in build outputs

Add a dedicated build output directory for ontology source files.

Suggested path:

- `data_v2/ontologies/`

or

- `data_v2/controlled_vocabularies/`

Contents:

- local/computed OBO files we generate
- downloaded ontology OBO files we support, e.g. Gene Ontology

This directory becomes the shared source of truth for ontology files.

---

## 8. How ontology files are used

The ontology artifact directory should be used in two places:

### Build / Postgres load
Use these OBO files to populate `annotation_term` from distinct `cv_term` accessions.

### API service
Simplify the ontology API service so it loads ontology files directly from this shared ontology artifact directory instead of maintaining a separate ontology copy/cache model.

---

## 9. Why this design

This separates concerns cleanly:

- `entity_annotation` = facts
- `annotation_term` = ontology metadata
- `entity_annotation_counts` = derived counts
- ontology artifact directory = shared ontology file source

It also avoids overloading raw annotation rows with display/search metadata.

---

## 10. Next implementation steps

1. Add ontology artifact output directory to the build.
2. Ensure supported OBO files are written/copied there.
3. Add `annotation_term` table to PostgreSQL load.
4. Populate `annotation_term` from supported ontology OBO files.
5. Rename/replace `entity_annotation_search` with `entity_annotation_counts`.
6. Add indexes for annotation metadata search.