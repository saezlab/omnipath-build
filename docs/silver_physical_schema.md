# Silver physical schema

Silver entity data is stored as columnar parquet tables directly in each source silver directory:

```text
<source silver dir>/entity_occurrence.parquet
<source silver dir>/entity_identifier.parquet
<source silver dir>/entity_annotation.parquet
<source silver dir>/membership.parquet
<source silver dir>/membership_annotation.parquet
<source silver dir>/resource.parquet
```

Legacy nested entity parquet files are no longer a supported gold input.

## Tables

### `entity_occurrence.parquet`

One row per emitted entity occurrence, including nested member entities.

| column | type | notes |
|---|---:|---|
| `occurrence_id` | string | deterministic source-local ID, currently `<dataset>:<sequence>` |
| `record_id` | string? | reserved for raw record IDs |
| `parent_occurrence_id` | string? | parent occurrence for nested members |
| `entity_role` | string | `parent` or `member` |
| `entity_type` | string? | raw CV accession/string from silver |
| `source` | string | source module name |
| `dataset` | string | silver dataset/output name |
| `record_class_hint` | string? | reserved |
| `row_number` | int64? | source dataset row number |

### `entity_identifier.parquet`

One row per identifier attached to an occurrence.

| column | type |
|---|---:|
| `occurrence_id` | string |
| `identifier_type` | string? |
| `identifier` | string? |
| `source` | string |
| `dataset` | string |

### `entity_annotation.parquet`

One row per annotation attached to an occurrence.

| column | type |
|---|---:|
| `occurrence_id` | string |
| `term` | string? |
| `value` | string? |
| `unit` | string? |
| `source` | string |
| `dataset` | string |

### `membership.parquet`

One row per parent/member edge from silver membership.

| column | type |
|---|---:|
| `membership_id` | string |
| `parent_occurrence_id` | string |
| `member_occurrence_id` | string |
| `is_parent` | bool? |
| `membership_role` | string? |
| `source` | string |
| `dataset` | string |

### `membership_annotation.parquet`

One row per annotation attached to a membership edge.

| column | type |
|---|---:|
| `membership_id` | string |
| `parent_occurrence_id` | string |
| `member_occurrence_id` | string |
| `term` | string? |
| `value` | string? |
| `unit` | string? |
| `source` | string |
| `dataset` | string |

## Implementation status

- Silver tables are written by default for entity datasets.
- Gold entity builder requires these silver tables.
- Gold entity builder writes `entity_occurrence_map.parquet`, mapping `occurrence_id -> _fingerprint -> entity_pk` for relation building.
- Gold relation builder requires silver tables plus `entity_occurrence_map.parquet`.
