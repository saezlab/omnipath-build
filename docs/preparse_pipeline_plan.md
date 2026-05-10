# Preparse-backed pipeline plan

## Goal

Make the raw stage explicit and reusable by materializing each dataset's mapper-input records as parquet before silver/gold processing.

The key observation is that the hard part is already solved: existing `inputs_v2` raw parsers turn each source into dictionaries, and mappers already consume dictionaries.

So the new model is:

```text
raw download/API response
  → existing raw_parser emits dict records
  → preparse writes records.parquet with one column per dict key
  → columnar silver mapper reads records.parquet
  → canonical silver parquet tables
  → gold/combined/postgres
```

No generic `_raw_payload_json` column is needed for normal operation. The preparse output should be columnar.

The long-term target is **fully columnar silver processing with no row fallback**: silver builds should not iterate parquet rows into Python dictionaries, should not construct nested `Entity` objects, and should not use arbitrary Python UDFs as the execution mechanism. Any source-specific logic needed for silver must be represented as a declared columnar operation with a Polars/DuckDB implementation.

## Core idea

Each `Dataset` / `OntologyDataset` gets a materialized raw-record snapshot:

```text
data/bronze/<source>/<dataset>/<snapshot_id>/
  records.parquet
  delta.parquet
  manifest.json
```

And a pointer:

```text
data/bronze/<source>/<dataset>/latest.json
```

`records.parquet` contains:

```text
metadata columns
+ one parquet column per raw dict key emitted by the parser
```

Then the silver mapper runs from this parquet instead of reparsing the original source.

The target pipeline is:

```text
records.parquet
  → declarative columnar silver mapper
  → entity_occurrence/entity_identifier/entity_annotation/membership/membership_annotation parquet
```

There should not be two supported production mapper systems. The current row/object mapper is legacy migration input only; each dataset definition should be rewritten so its single silver mapper is columnar.

## Why this fits the current code

Current `Dataset` flow in `pypath/pypath/inputs_v2/base.py`:

```python
Dataset.raw():
    opener = self.download.open(...)
    yield from self._raw_parser(opener, ...)

Dataset.__call__():
    for record in self.raw(...):
        yield self.mapper(record)
```

The existing mapper receives a `dict[str, Any]`, which is why preparse can reuse current raw parsers immediately. For the final columnar pipeline, however, `records.parquet` becomes the silver input and `Dataset.raw()` is no longer part of production silver execution:

```python
Dataset.preparse():
    opener = self.download.open(...)
    for record in self._raw_parser(opener, ...):
        write record columns + metadata to records.parquet

Dataset.build_silver():
    snapshot = self.preparse(...)
    raw_lf = pl.scan_parquet(snapshot.records_path)
    return self.mapper.compile(raw_lf, ...)
```

This preserves the existing raw parser contract while changing the meaning of `Dataset.mapper`: it should become a columnar silver mapper, not a row function.

## Preparse output schema

`records.parquet` should contain metadata columns plus raw columns.

Required metadata columns:

```text
_raw_record_key       string  # content hash over all raw content columns
_source               string
_dataset              string
```

Optional metadata columns:

```text
_raw_member           string  # archive member / file within source, if relevant
_raw_partition        string  # optional source partition
```

Raw content columns:

```text
<one column per key emitted by raw_parser>
```

Examples for IntAct:

```text
#ID(s) interactor A
ID(s) interactor B
Alt. ID(s) interactor A
...
Interaction identifier(s)
...
```

Examples for OBO after `iter_obo`:

```text
id
name
definition
synonyms
alt_ids
namespace
comments
xrefs
is_a
relationships
is_obsolete
ontology
```

Nested/list values should be represented as native parquet list/struct columns where possible, not JSON blobs.

## Record identity

Use content-addressed identity uniformly.

```text
_raw_record_key = hash(all raw content columns)
```

Do not rely on source-provided stable IDs. Source IDs remain normal raw columns.

For a modified record, content-addressed diff sees:

```text
old hash removed
new hash added
```

This is simpler and universal.

## Hashing rules

Hash only raw content columns, not metadata columns.

The hash input should be deterministic:

```text
column_name_1 \x1f canonical_value_1 \x1e
column_name_2 \x1f canonical_value_2 \x1e
...
```

Rules:

- include column names
- use deterministic column ordering
- normalize missing values consistently
- preserve source parser semantics
- canonicalize lists/structs deterministically
- do not include metadata columns such as `_source` or `_dataset`

## Duplicate records

Because identity is content-addressed, exact duplicate records share the same `_raw_record_key`.

Exact duplicates are not expected in normal inputs. `records.parquet` may contain them if the source emits them, but `delta.parquet` operates on distinct content keys only:

```text
_raw_record_key
_change_type             # added / removed
```

Preparse manifests should report duplicate-key statistics so unexpected duplicates are visible.

## Snapshot and delta behavior

Each preparse run writes a new immutable snapshot directory.

Update flow:

```text
1. Open/download raw source.
2. Run existing raw_parser fully.
3. Write complete records.parquet.
4. Compare key multiplicities against previous latest snapshot.
5. Write delta.parquet in the new snapshot directory.
6. Run downstream processing from records.parquet/delta.parquet.
7. Only after success, advance latest.json.
```

`latest.json` should not be advanced before downstream processing succeeds.

## Dataset API changes

Add preparse and columnar mapper support to `Dataset` and `OntologyDataset` in `inputs_v2/base.py`.

Conceptual API:

```python
class Dataset:
    def preparse(self, *, source: str, dataset: str, data_root: Path, force_refresh=False, **kwargs) -> RawSnapshot:
        ...

    def build_silver(
        self,
        *,
        source: str,
        dataset: str,
        data_root: Path,
        output_dir: Path,
        force_refresh: bool = False,
        changed_only: bool = False,
        **kwargs,
    ) -> None:
        snapshot = self.preparse(...)
        raw_lf = pl.scan_parquet(snapshot.records_path)
        if changed_only:
            raw_lf = raw_lf.join(pl.scan_parquet(snapshot.delta_path).filter(...), on="_raw_record_key")
        frames = self.mapper.compile(
            raw_lf,
            source=source,
            dataset=dataset,
            snapshot_id=snapshot.snapshot_id,
        )
        write_canonical_silver_tables(frames, output_dir)
```

`Dataset.raw()` and `Dataset.__call__()` should be removed from the production silver path. During migration, old row mappers can live in tests or temporary comparison scripts, but the `Dataset` contract itself should converge on one mapper: the columnar silver mapper.

## Discovery/orchestration changes

Current silver discovery wraps each dataset as a `ResourceFunction` with:

```python
call=dataset_obj
```

We should extend `ResourceFunction` to know:

```text
source
dataset/function_name
dataset object
output kind
```

Then silver processing can do:

```python
dataset_obj.build_silver(
    source=resource_fn.source,
    dataset=resource_fn.function_name,
    data_root=data_root,
    output_dir=silver_output_dir,
    changed_only=False,  # initially full source behavior
)
```

Later, incremental silver can pass `changed_only=True` or use `delta.parquet` directly to process added/removed keys. No orchestration path should iterate raw records into dictionaries for production silver.

## Columnar silver execution from parquet

The silver mapper should consume `records.parquet` as a columnar relation, not as Python row dictionaries.

Conceptual execution:

```python
raw_lf = pl.scan_parquet(snapshot.records_path)
silver_frames = dataset.mapper.compile(
    raw_lf,
    source=resource_fn.source,
    dataset=resource_fn.function_name,
    snapshot_id=snapshot.snapshot_id,
)
write_canonical_silver_tables(silver_frames)
```

The mapper emits the canonical silver tables directly:

```text
entity_occurrence.parquet
entity_identifier.parquet
entity_annotation.parquet
membership.parquet
membership_annotation.parquet
```

It should not emit nested `Entity` objects and should not call arbitrary row functions. There is no production row fallback.

## Generality across source types

This design works because it materializes the output of existing raw parsers, not the physical file format.

Examples:

- CSV/TSV: parser emits one dict per row; parquet has one column per source column.
- OBO: parser emits one dict per `[Term]`; parquet has one column per term field.
- XML: parser emits one dict per element; parquet has one column per flattened field.
- RDA: parser emits one dict per converted interaction; parquet has columns from that dict.
- SQLite: parser emits one dict per query row; parquet has one column per query column.
- Multi-file archive: parser emits logical dict records; parquet materializes those logical records.

So format-specific complexity stays inside existing raw parsers.

## Optimized preparsers

Initial implementation should use existing raw parsers for all sources.

Later, common formats can have optimized preparsers that produce the exact same dict schema faster:

```text
CSV/TSV/semicolon
zip/gzip text tables
OBO stanza parser
JSON/JSONL
SQLite table/query
XML element parser
```

Optimization rule:

```text
optimized preparser output must be schema-compatible with raw_parser output
```

## Single mapper contract

Every `Dataset` / `OntologyDataset` should expose exactly one silver mapper, and that mapper should be columnar.

Conceptual final API:

```python
Dataset(
    download=...,
    raw_parser=...,
    mapper=ColumnarEntitySpec(...),
)
```

or for custom multi-frame mappings:

```python
class MyColumnarMapper:
    def compile(self, raw_lf: pl.LazyFrame, *, source: str, dataset: str, snapshot_id: str) -> SilverFrames:
        ...
```

The supported mapping language should be declarative and expression-backed:

```text
col(name)
lit(value)
strip/lower/upper
split(delimiter)
explode/list explode
regex_extract(pattern)
regex_filter(pattern)
dict_map(mapping)
null_if(values)
concat / concat_list
when/then/otherwise
list_get/list_eval
```

Source-specific behavior should be promoted to named reusable operations with columnar implementations, for example:

```text
uniprot_accession_filter
uniprot_entry_name_filter
refseq_prefix_filter
normalize_chembl
normalize_chebi
normalize_kegg_compound
normalize_hmdb
normalize_foodon
species_to_taxid
boolean_flag_to_cv
connectomedb_location_terms
signor_identifier_pairs
```

Arbitrary Python row callables are not part of the production columnar execution contract. If current source logic needs Python code, replace it with a named declarative operation and implement that operation columnarly.

## Deterministic silver identifiers

Columnar silver should use deterministic IDs derived from raw provenance instead of mutable writer counters.

Recommended occurrence IDs:

```text
parent occurrence:  <dataset>:<raw_record_key>:parent
member occurrence:  <dataset>:<raw_record_key>:member:<member_path_or_index>
membership:         <dataset>:<raw_record_key>:membership:<member_path_or_index>
```

Silver rows should carry provenance:

```text
_raw_record_key
_snapshot_id
_source
_dataset
```

`record_id` can be set to `_raw_record_key` for compatibility, but the explicit provenance columns should be added to the canonical silver schemas.

## Incremental silver path

Once preparse snapshots exist, silver can become incremental.

For added keys:

```text
read rows from new records.parquet where key in delta added
map to Entity/OntologyTerm
insert new silver rows with _raw_record_key provenance
```

For removed keys:

```text
look up old silver rows by _raw_record_key
delete them
```

Duplicate content keys are not represented as a separate delta type. They should be reported in the manifest and investigated if present.

Silver rows should include provenance:

```text
_raw_record_key
_snapshot_id
_source
_dataset
```

## Transitional mode

The first implementation does not need row-level silver mutation.

A safe transition is:

```text
always preparse to records.parquet/delta.parquet
columnar mapper reads records.parquet
if delta is non-empty, rebuild that source's silver/gold fully
rebuild combined or aggregate incrementally later
```

During migration, legacy row mappers may be used outside the production path to compare outputs and prove equivalence. Production silver should have no row fallback: if `dataset.mapper` is not a columnar mapper, silver orchestration should raise an error.

## Long-term mode

The final model is:

```text
preparse full dataset
  → delta by content hash
  → map only added/changed-effective records
  → delete outputs for removed records
  → update source contribution rows
  → recompute affected combined aggregates
  → postgres diff/apply
```

## Implementation steps

1. Add/finish `RawSnapshot` / preparse utilities under `omnipath_build/raw/` or `pypath.inputs_v2`.
2. Add parquet writer that accepts arbitrary parser-emitted dicts and writes metadata + separate columns.
3. Add deterministic content hashing for scalar/list/struct values.
4. Add delta computation by distinct `_raw_record_key`.
5. Add `Dataset.preparse()` / `OntologyDataset.preparse()`.
6. Make pipeline call datasets with preparse context.
7. Add provenance columns to silver schemas: `_raw_record_key`, `_snapshot_id`, `_source`, `_dataset`.
8. Add `omnipath_build/silver/columnar/` with:
   - `spec.py` for the declarative columnar mapping IR
   - `expr.py` / `ops.py` for expression-backed operations
   - `compiler.py` for raw parquet → silver frames
   - `writer.py` for canonical silver table writing
9. Implement deterministic occurrence and membership ID generation from `_raw_record_key`.
10. Implement a columnar compiler for the common `EntityBuilder` subset: constants, columns, regex extraction, split/explode, dict mapping, annotations, single members, and indexed `MembersFromList`.
11. Add a dedicated `ColumnarOntologyMapper` for OBO-like `OntologyDataset` records.
12. Convert current Python callable patterns into named declarative operations with Polars/DuckDB implementations.
13. Rewrite every `Dataset` / `OntologyDataset` so `mapper` is a columnar mapper.
14. Move legacy row mappers into tests or temporary migration comparison scripts, not the production dataset contract.
15. Enforce production columnarity: silver build fails if `dataset.mapper` is not columnar.
16. Later: use `delta.parquet` for row-level silver/contribution updates.

## Summary

The new raw stage should materialize the existing parser output as columnar parquet:

```text
raw_parser dicts → records.parquet columns → columnar silver mapper → canonical silver tables
```

This makes the raw layer diffable, reusable, and incremental-friendly. The final architecture should preserve parser reuse while replacing production row/object silver mapping with explicit columnar mapper specifications.
