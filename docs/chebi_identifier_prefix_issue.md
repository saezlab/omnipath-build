# Inconsistent ChEBI Identifier Prefixes

## Summary

ChEBI identifiers are inconsistently represented across inputs: most sources emit CURIE-style IDs such as `CHEBI:15377`, while some emit bare numeric IDs such as `15377`.

This causes the same ChEBI accession to appear as different identifiers during merging.

## Observed in silver data

Checked `data/silver/*/1/entity_identifier.parquet` for `MI:0474` / ChEBI identifiers.

| Source | ChEBI rows | With `CHEBI:` | Bare numeric |
|---|---:|---:|---:|
| chebi | 195,394 | 195,394 | 0 |
| foodb | 76,596 | 76,596 | 0 |
| hmdb | 97 | 0 | 97 |
| intact | 173 | 173 | 0 |
| lipidmaps | 64 | 0 | 64 |
| phenol_explorer | 3,673 | 3,673 | 0 |
| reactome | 18,758 | 17,702 | 1,056 |
| signor | 5,106 | 5,106 | 0 |
| swisslipids | 100 | 100 | 0 |
| wikipathways | 35,319 | 35,319 | 0 |

In `data/combined/entity.parquet` there are `763` bare numeric ChEBI identifiers remaining, and `136` entities contain both a bare and prefixed form of a ChEBI ID.

Example combined entity:

```text
identifiers:
  - 28997
  - CHEBI:28997
```

## Likely source of discrepancy

### HMDB

`pypath/pypath/inputs_v2/hmdb.py` defines a ChEBI transform:

```python
'chebi': lambda v: f'CHEBI:{v}' if v else None
```

but the schema only applies extraction:

```python
value=f('chebi_id', extract='chebi')
```

so extracted numeric values are emitted without the prefix.

### LIPID MAPS

`pypath/pypath/inputs_v2/lipidmaps.py` has the same pattern: a `chebi` transform exists but is not used in the ChEBI identifier field.

### Reactome

`pypath/pypath/inputs_v2/parsers/reactome.py` collects ChEBI xrefs directly from BioPAX xref IDs:

```python
elif 'chebi' in db:
    xrefs.setdefault('chebi', []).append(id_str)
```

No normalization is applied there or later in `pypath/pypath/inputs_v2/reactome.py`, so Reactome preserves whatever form appears in the source xref: sometimes bare numeric, sometimes `CHEBI:*`.

## Fix implemented

Normalize all ChEBI identifiers to bare numeric accessions (`<digits>`) at input-module level before writing silver data.

Changes:

- ChEBI fields use regex extraction to keep only the numeric accession.
- HMDB, LIPID MAPS, SwissLipids, ChEMBL, BindingDB, ChEBI, FooDB, Phenol-Explorer, WikiPathways, Reactome, IntAct, and SIGNOR now emit bare numeric ChEBI accessions for `IdentifierNamespaceCv.CHEBI` identifiers.
- ChEBI ontology term IDs remain CURIE-style (`CHEBI:<digits>`) where they are ontology/CV term accessions rather than ChEBI entity identifiers.
