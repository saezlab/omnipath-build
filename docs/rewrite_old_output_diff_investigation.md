# Rewrite vs Current Output Difference Investigation

This note compares the checked-in current pipeline outputs under `data/` with
the checked-in rewrite outputs under `data_rewrite/`.

The comparison was done directly against DuckDB state:

```text
current source gold:
  data/gold/signor/state.duckdb
  data/gold/uniprot/state.duckdb

rewrite source gold:
  data_rewrite/state/sources/signor.duckdb
  data_rewrite/state/sources/uniprot.duckdb

current combined:
  data/combined/state.duckdb

rewrite combined:
  data_rewrite/state/combined.duckdb
```

The existing rewrite state was later rebuilt after the relation-object lookup
fix. This note keeps both the original diagnosis and the post-rebuild outcome.

## Source Gold Counts Before Rebuild

### SIGNOR

SIGNOR row counts match exactly:

| table | current | rewrite | diff |
|---|---:|---:|---:|
| entity | 11,294 | 11,294 | 0 |
| entity_evidence | 80,105 | 80,105 | 0 |
| entity_relation | 32,794 | 32,794 | 0 |
| entity_relation_evidence | 41,279 | 41,279 | 0 |

SIGNOR key sets still differ:

| domain | shared keys | current only | rewrite only |
|---|---:|---:|---:|
| entity | 9,204 | 2,090 | 2,090 |
| relation | 19,896 | 12,898 | 12,898 |

Interpretation:

SIGNOR is not missing rows. The mismatch is key-content drift: entities and
relations are present in equal numbers, but some canonical entity keys differ.
This matches the earlier hypothesis that the remaining SIGNOR gap is in
canonicalization/reduction choices, not in silver or row production.

### UniProt

UniProt source-gold counts differ:

| table | current | rewrite | diff |
|---|---:|---:|---:|
| entity | 66,130 | 66,601 | +471 |
| entity_evidence | 1,238,702 | 1,239,903 | +1,201 |
| entity_relation | 1,192,785 | 523,918 | -668,867 |
| entity_relation_evidence | 1,192,785 | 1,192,785 | 0 |

The `+1,201` evidence/map style difference is explained by rewrite also loading
the UniProt keyword ontology dataset into source-gold. The checked-in current
UniProt source-gold state only has the protein dataset.

The relation issue is more important:

| metric | current | rewrite |
|---|---:|---:|
| relation evidence rows | 1,192,785 | 1,192,785 |
| distinct relation keys in evidence | 1,192,785 | 523,918 |
| distinct logical relation tuples | 1,192,785 | 523,918 |

So rewrite did not lose UniProt relation evidence. It collapsed many evidence
rows into fewer relation keys.

## UniProt Relation Root Cause

The existing rewrite UniProt state has many relation evidence rows with an empty
object entity key:

| metric | count |
|---|---:|
| relation evidence rows with empty object key | 713,313 |
| relation keys with empty object key | 44,446 |
| affected subject entity keys | 44,446 |

This exactly explains the shape of the relation mismatch:

```text
713,313 evidence rows with empty object keys
collapse to 44,446 source-gold relation rows

713,313 - 44,446 = 668,867 fewer source-gold relation rows
```

The combined layer then drops those malformed source relations because the
object entity key cannot join to a combined object entity:

| rewrite UniProt source-gold metric | count |
|---|---:|
| source relation rows | 523,918 |
| source relation rows with empty object key | 44,446 |
| source relation rows with non-empty object key | 479,472 |
| relation evidence rows with empty object key | 713,313 |
| relation evidence rows with non-empty object key | 479,472 |

Combined relation count confirms this:

```text
rewrite combined relations = 512,266
SIGNOR source relations    = 32,794
UniProt non-empty relations = 479,472
32,794 + 479,472 = 512,266
```

Current combined has 1,225,579 relations, rewrite combined has 512,266:

```text
1,225,579 - 512,266 = 713,313
```

That combined relation deficit equals the number of UniProt relation evidence
rows with empty object keys.

## Code Cause

The bug was in the rewrite direct source-gold relation builder.

Before the fix, `_build_relation_evidence_from_frames` built the
`entity_pk -> entity_key` lookup from `gold_entity_occurrence_map`:

```python
entity_key_map = {
    int(row['entity_pk']): str(row['entity_key'])
    for row in occurrence_map.select(['entity_pk', 'entity_key']).unique().iter_rows(named=True)
}
```

That works for source-record entities with occurrences, but UniProt annotation
object entities are materialized via `gold_entity_map`. Many of those ontology
or annotation-object entities do not have a source occurrence row. As a result,
the relation builder could resolve the object fingerprint to an `entity_pk`, but
could not resolve that `entity_pk` to an `entity_key`, and wrote:

```text
object_entity_key = ''
```

The fix is to build the lookup from `gold_entity_map`, not from the occurrence
map:

```python
entity_key_map = {
    int(row['entity_pk']): str(row['entity_key'])
    for row in entity_map.select(['entity_pk', 'entity_key']).unique().iter_rows(named=True)
}
```

A regression assertion was added to the fixture test:

```text
tests/test_rewrite_pipeline_work_tracking.py
```

It verifies that a UniProt fixture source-gold run produces no relation evidence
rows with an empty object entity key.

## Combined Counts Before Rebuild

Existing combined state, before rebuilding with the fix:

| table | current | rewrite | diff |
|---|---:|---:|---:|
| entity | 69,756 | 70,227 | +471 |
| entity_evidence | 77,424 | 77,895 | +471 |
| entity_relation | 1,225,579 | 512,266 | -713,313 |
| entity_relation_evidence | 1,234,064 | 520,751 | -713,313 |

The entity `+471` is consistent with the rewrite UniProt ontology dataset being
included while the checked-in current UniProt source-gold state only contains
proteins.

The relation `-713,313` is the malformed UniProt annotation-object relation
evidence described above.

## Post-Rebuild Result

After forcing a UniProt source-all source-gold rebuild from existing rewrite
silver state, then rerunning combined for `signor,uniprot`, the large relation
count gap is resolved.

Post-rebuild UniProt source-gold:

| table | current | rewrite | diff |
|---|---:|---:|---:|
| entity | 66,130 | 66,601 | +471 |
| entity_evidence | 1,238,702 | 1,239,903 | +1,201 |
| entity_relation | 1,192,785 | 1,192,785 | 0 |
| entity_relation_evidence | 1,192,785 | 1,192,785 | 0 |

Post-rebuild rewrite UniProt malformed relation evidence:

```text
object_entity_key is null or '' -> 0 rows
```

Post-rebuild combined counts:

| table | current | rewrite | diff |
|---|---:|---:|---:|
| entity | 69,756 | 70,227 | +471 |
| entity_evidence | 77,424 | 77,895 | +471 |
| entity_relation | 1,225,579 | 1,225,579 | 0 |
| entity_relation_evidence | 1,234,064 | 1,234,064 | 0 |

Source scopes were consumed by the successful combined run:

```text
uniprot source_run_scope_raw_record     0
uniprot source_run_scope_occurrence     0
uniprot source_run_scope_entity         0
uniprot source_run_scope_relation       0
signor source_run_scope_raw_record      0
signor source_run_scope_occurrence      0
signor source_run_scope_entity          0
signor source_run_scope_relation        0
```

The remaining entity/evidence count differences are still explained by rewrite
including UniProt ontology rows in source-gold and combined.

## What Changed After Rebuild

After rebuilding UniProt source-gold with the fixed lookup:

Observed source-gold changes:

- `new_uniprot.gold_entity_relation_evidence` still has 1,192,785 rows.
- `object_entity_key = ''` dropped from 713,313 rows to 0.
- `new_uniprot.gold_entity_relation` increased from 523,918 to 1,192,785 rows.

Observed combined changes:

- rewrite combined relation count now matches current exactly.
- rewrite combined relation evidence count now matches current exactly.
- the main remaining mismatches are now:
  - UniProt ontology inclusion differences;
  - SIGNOR and UniProt key-set drift from canonicalization choices.

## Remaining Open Difference

After this relation-object bug is fixed and rebuilt, the key-set drift still
needs separate investigation.

Known examples:

- SIGNOR has equal row counts but `2,090` entity keys and `12,898` relation
  keys differ in each direction.
- Prior notes suggest this is downstream of silver and likely caused by
  canonicalization/reduction where grouped choices depend on ambiguous
  resolver/taxonomy data or row ordering.

That is a distinct issue from the large relation-count deficit.
