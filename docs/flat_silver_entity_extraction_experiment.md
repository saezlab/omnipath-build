# Flat silver entity extraction experiment

## Question

Would changing silver outputs to a flatter/no-nesting entity-oriented shape improve `build_entities` extraction speed?

## Experiment

Added a reproducible benchmark script:

```text
scripts/experiment_flat_silver_entities.py
```

It simulates silver writers emitting:

```text
entity_rows.parquet       # one row per extracted entity occurrence
identifier_rows.parquet   # one identifier per row
ontology_terms.parquet    # optional
```

Then compares:

1. Current nested silver extraction via `extract_all_from_silver()`.
2. Flat silver rehydrated back into Python `list[dict]` descriptions.
3. Flat silver read directly into deduped Polars frames suitable for `build_entities`.

## Results

### UniProt

```text
current nested extraction:        ~7.9s median
flat -> Python descriptions:      ~3.7s median
flat -> deduped Polars frames:    ~0.15s median
```

### ChEBI

```text
current nested extraction:        ~37.9s median
flat -> Python descriptions:      ~59.4s median
flat -> deduped Polars frames:    ~1.0s median
```

## Conclusion

Flattening silver only helps substantially if `build_entities` also stops reconstructing Python entity-description dictionaries.

Best direction:

- Have silver writers emit normalized entity/identifier parquet tables directly.
- Update `build_entities` to consume those tables as Polars frames.
- Deduplicate by `_fingerprint` columnarly before canonicalization.

Expected benefit from experiment:

```text
UniProt extraction/pre-dedup: ~8s -> ~0.15s
ChEBI extraction/pre-dedup:   tens of seconds -> ~1s
```

The main win is avoiding recursive Python row parsing and avoiding millions of intermediate Python dictionaries.
