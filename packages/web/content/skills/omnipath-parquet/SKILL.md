---
name: omnipath-parquet
description: Lists versioned OmniPath Parquet resources, downloads their published tables, and explains the normalized data model. Use when discovering datasets, downloading resource files, or analyzing downloaded OmniPath Parquet data.
---

# OmniPath Parquet Resources

This serving stack publishes one set of normalized Parquet tables per resource version. Use the HTTP API to list datasets and download files. Do not look for Postgres tables or retired zip-of-gold archives.

## Base URL

Hosted API:

```text
https://omnipath-metabo-dev.schaul.click/api
```

Local development:

```text
http://127.0.0.1:8085
```

From the Svelte app, the same routes are public under `/api` (for example `http://127.0.0.1:5173/api`).

## Layout

```text
resources/<resource>/<version>/
├── entity.parquet
├── entity_identifier.parquet
├── entity_annotation.parquet
├── entity_evidence.parquet
├── relation.parquet
├── relation_annotation.parquet
├── relation_evidence.parquet
└── evidence_payloads.parquet
```

The default `release=latest` selects the highest numeric version of each resource.
Use `GET /releases` to discover published OmniPath releases, then pass
`?release=<release>` consistently to catalog, file-list and download
requests to pin a release. An OmniPath release pins a resource version; these
version numbers need not match. Save the resolved artifact URLs for reproducibility.

## List resources

```bash
curl -sS "http://127.0.0.1:8085/api/resources?shape=svelte"
```

Each item has `resource_id`, `version`, `entity_count`, `interaction_count`, `total_size_bytes`, and `files[]` (`name`, `url`, `size_bytes`, `rows`). Catalog listings omit `evidence_payloads.parquet`.

To include column schema:

```bash
curl -sS "http://127.0.0.1:8085/api/resources/signor/files"
curl -sS "http://127.0.0.1:8085/api/resources/signor/files?include_evidence=true"
```

## Download parquets

The default zip holds every table except `evidence_payloads.parquet`. Evidence payloads are omitted unless you pass `include_evidence=true`.

```bash
curl -fsSL -o signor.zip "http://127.0.0.1:8085/api/resources/signor/download"
curl -fsSL -o signor-with-evidence.zip \
  "http://127.0.0.1:8085/api/resources/signor/download?include_evidence=true"
```

One file:

```bash
curl -fsSL -o signor-entity.parquet \
  "http://127.0.0.1:8085/api/resources/signor/files/entity.parquet"
curl -fsSL -o signor-relation.parquet \
  "http://127.0.0.1:8085/api/resources/signor/files/relation.parquet"
```

Evidence payloads are available by explicit filename, not by default listing:

```bash
curl -fsSL -o signor-evidence_payloads.parquet \
  "http://127.0.0.1:8085/api/resources/signor/files/evidence_payloads.parquet"
```

Several resources:

```bash
curl -fsSL -o omnipath-resources.zip \
  -H "Content-Type: application/json" \
  -d '{"resource_ids":["signor","chebi"]}' \
  "http://127.0.0.1:8085/api/resources/download"
```

Allowed filenames: the eight table files in the layout above.

## Schema

`entity_id` and `relation_id` number the rows of `entity.parquet` and `relation.parquet` within one resource version. Child tables refer to their entity or relation by that id; `ordinal` keeps the original order. `entity_key` and `relation_key` identify the same entity or relation across resources.

### `entity.parquet`

| column                 | type         | meaning                                                 |
| ---------------------- | ------------ | ------------------------------------------------------- |
| `entity_id`            | int32        | Row number in this file                                 |
| `entity_key`           | string       | Canonical entity identity                               |
| `entity_type`          | string       | Normalized type (protein, small_molecule, …)            |
| `namespace`            | string       | Canonical identifier namespace                          |
| `identifier`           | string       | Canonical identifier                                    |
| `taxon`                | string       | NCBI taxonomy id when known                             |
| `label`                | string       | Display name                                            |
| `reference_entity_key` | string       | Shared reference, normally `entrez:<GeneID>`            |
| `gene_reference_keys`  | list<string> | Catalogue gene links of a product                       |
| `group_connectivity`   | string       | First InChIKey block (chemical structure group)         |
| `has_hierarchy`        | bool         | Ontology-style parent/child present                     |
| `parent_count`         | int64        | Incoming hierarchy edges                                |
| `child_count`          | int64        | Outgoing hierarchy edges                                |
| `*_count`              | int64        | Number of identifiers, annotations, evidence, relations |

Child tables of `entity` (each with `entity_id`, `ordinal`):

- `entity_identifier`: `ns`, `id`, `is_canonical`, `source`
- `entity_annotation`: `term`, `value`, `quantity_*` (measurement fields), `source`, `dataset`
- `entity_evidence`: `source`, `dataset`, `row_id`, `upstream_id`, `annotations`, `molecular_form`

### `relation.parquet`

| column                          | type         | meaning                                     |
| ------------------------------- | ------------ | ------------------------------------------- |
| `relation_id`                   | int32        | Row number in this file                     |
| `relation_key`                  | string       | Canonical relation identity                 |
| `statement_kind`                | string       | `relation` or `ontology`                    |
| `subject_entity_key`            | string       | Subject `entity_key`                        |
| `subject_label`, `subject_type` | string       | Subject display name and type               |
| `predicate`                     | string       | Relation predicate                          |
| `object_entity_key`             | string       | Object `entity_key`                         |
| `object_label`, `object_type`   | string       | Object display name and type                |
| `*_reference_entity_key`        | string       | Endpoint references                         |
| `taxon`                         | string       | Shared or primary taxon                     |
| `is_directed`                   | bool         | Directed edge                               |
| `sign`                          | int32        | `-1`, `0`, or `1`                           |
| `category`                      | string       | `interaction`, `association`, `ontology`, … |
| `interaction_class`             | string       | Optional interaction class                  |
| `sources`                       | list<string> | Provenance sources                          |
| `evidence_count`                | int64        | Number of `relation_evidence` rows          |
| `*_qualifier`                   | list<string> | Biolink qualifiers                          |

Child tables of `relation` (each with `relation_id`, `ordinal`):

- `relation_annotation`: `term`, `value`, `quantity_*`, `source`, `dataset`, `scope`
- `relation_evidence`: `source`, `dataset`, `row_id`, `upstream_id`, `annotations`, `subject_molecular_form`, `object_molecular_form`

Join relation evidence payloads with `relation_key` + `source` + `row_id` within the same resource version. Entity payloads use `entity_key` instead of `relation_key`.

### `evidence_payloads.parquet`

| column         | type   | meaning                                   |
| -------------- | ------ | ----------------------------------------- |
| `relation_key` | string | Parent relation, null for entity payloads |
| `entity_key`   | string | Parent entity, null for relation payloads |
| `source`       | string | Upstream resource                         |
| `row_id`       | string | Source row identity                       |
| `payload_json` | string | Original source record as JSON            |

## Query downloaded files

```bash
python - <<'PY'
import duckdb
con = duckdb.connect()
print(con.execute("SELECT entity_type, count(*) n FROM 'signor-entity.parquet' GROUP BY 1 ORDER BY n DESC").fetchall())
print(con.execute("SELECT predicate, count(*) n FROM 'signor-relation.parquet' GROUP BY 1 ORDER BY n DESC LIMIT 10").fetchall())
PY
```

## Rules

- Prefer `resource_id` values returned by `GET /resources?shape=svelte`.
- Download individual files using `files[].url` when provided, or `/resources/{id}/files/{filename}`. Use `/resources/{id}/download` for ZIPs.
- Pin a published release when reproducibility matters; retain the resolved resource versions and URLs.
- Do not request `evidence_payloads.parquet` unless the task needs raw source records.
- Join child tables to `entity` / `relation` by `entity_id` / `relation_id` within one resource version.
- Filtered slices of relations (not whole resources) use `POST /export`.
