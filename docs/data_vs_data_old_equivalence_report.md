# data vs data_old equivalence check

Compared current `data/` against `data_old/` after the silver-table cutover.

## File inventory

`data/` has expected additional per-source helper files which are absent from `data_old/`:

```text
gold/<source>/entities/entity_occurrence_map.parquet
```

for:

```text
chebi, corum, guidetopharma, hpo, phenol_explorer, signor, swisslipids, uniprot, wikipathways
```

## Common parquet exact comparison

Common parquet files compared by schema, row count, and sorted row equality.

```text
common parquet files: 50
exact equal:          31
not exact equal:      19
```

Exact equality is too strict for some files because local PK assignment and fallback local IDs can change while row counts stay the same.

## Per-source gold row counts

| source | entity | entity_map | relation | evidence |
|---|---:|---:|---:|---:|
| chebi | 206648 -> 206648 | 207994 -> 207994 | 4677471 -> 4677471 | 4697624 -> 4697624 |
| corum | 7575 -> 7575 | 7575 -> 7575 | 20039 -> 20039 | 20039 -> 20039 |
| guidetopharma | 308 -> 308 | 309 -> 309 | 99 -> 99 | 100 -> 100 |
| hpo | 15571 -> 15568 | 15574 -> 15574 | 264134 -> 264038 | 322515 -> 322515 |
| phenol_explorer | 966 -> 966 | 967 -> 967 | 7001 -> 7001 | 7486 -> 7486 |
| signor | 11294 -> 11294 | 12857 -> 12857 | 32794 -> 32794 | 41279 -> 41279 |
| swisslipids | 100 -> 100 | 100 -> 100 | n/a | n/a |
| uniprot | 65400 -> 65400 | 65400 -> 65400 | 713313 -> 713313 | 713313 -> 713313 |
| wikipathways | 22615 -> 22615 | 28575 -> 28575 | 136231 -> 136231 | 304771 -> 304771 |

Only HPO has source-level row count changes.

## Combined row counts

| file | data_old | data | delta |
|---|---:|---:|---:|
| `combined/entity.parquet` | 303437 | 303423 | -14 |
| `combined/entity_relation.parquet` | 5851082 | 5850986 | -96 |
| `combined/entity_relation_evidence.parquet` | 5867273 | 5867177 | -96 |
| `combined/ontology_term.parquet` | 86913 | 86913 | 0 |
| `combined/relation_annotation_term.parquet` | 7697856 | 7699917 | +2061 |
| `combined/resources.parquet` | 23 | 23 | 0 |

Combined differences are therefore not fully equivalent. The relation count deltas line up with HPO relation aggregation changes.

## HPO canonicalization summary changed

```diff
- ambiguous_entities: 30
+ ambiguous_entities: 16

- entities_updated: 15489
+ entities_updated: 15503

- exact_conflicts: 30
+ exact_conflicts: 16

- identifier_rows_added: 71600
+ identifier_rows_added: 71708

- resolved_entities: 5114
+ resolved_entities: 5128
```

HPO entity candidates/fingerprints are the same count, but canonicalization/reduction changed:

```text
entity_map rows: 15574 -> 15574
entity rows:     15571 -> 15568
relations:       264134 -> 264038
evidence:        322515 -> 322515
```

Interpretation: more HPO protein entities resolved to canonical UniProt IDs, reducing final entity count by 3 and aggregating 96 relation rows, while preserving evidence row count.

## Conclusion

The directories are **not fully equivalent**.

They are mostly row-count equivalent at the per-source gold level, except HPO. The important real differences are:

1. Expected new `entity_occurrence_map.parquet` files.
2. HPO canonicalization/reduction changed.
3. Combined outputs changed as a consequence of HPO and relation annotation rebuilds.
4. Some same-count files are not exact-row-equal because local PKs/fallback local IDs/report row numbers changed.
