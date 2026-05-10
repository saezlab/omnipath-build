# Columnar inputs takeover plan

## Goal

Move raw → silver processing to a fully columnar execution model while keeping input modules mostly as declarative source descriptions.

The desired final flow is:

```text
raw download/API response
  → existing raw_parser emits dict records
  → preparse writes records.parquet
  → declarative mapper spec compiles to Polars/DuckDB
  → canonical silver parquet tables
  → gold/combined/postgres
```

Production silver should not iterate parquet rows into Python dictionaries, should not build nested `Entity` objects, and should not use arbitrary Python UDFs as the execution mechanism.

## Important design decision

Do **not** put implementation details such as Polars code inside individual input modules.

Input modules should stay close to the current style:

```python
interactions_schema = EntityBuilder(
    entity_type=EntityTypeCv.INTERACTION,
    identifiers=IdentifiersBuilder(...),
    annotations=AnnotationsBuilder(...),
    membership=MembershipBuilder(...),
)
```

The columnar implementation should live in builder infrastructure, e.g.:

```text
omnipath_build/silver/columnar/
```

The compiler should inspect the declarative mapper and produce the canonical silver tables directly.

## What not to do

Avoid source modules like this:

```python
import polars as pl

class IntActColumnarMapper:
    def compile(...):
        ... huge Polars implementation ...
```

That leaks execution-engine details into source definitions and makes each source a custom implementation instead of a declarative mapping.

Also avoid adding a second production mapper field such as:

```python
Dataset(..., mapper=row_mapper, columnar_mapper=columnar_mapper)
```

The final contract should have one mapper, and that mapper should be declarative/columnar-compilable:

```python
Dataset(
    download=...,
    raw_parser=...,
    mapper=interactions_schema,
)
```

## Current situation

Preparse support has been started under `pypath.inputs_v2.raw_records` and `Dataset.preparse()` / `OntologyDataset.preparse()`.

The intended bronze output is:

```text
data/bronze/<source>/<dataset>/<snapshot_id>/
  records.parquet
  delta.parquet
  manifest.json
```

with:

```text
_raw_record_key
_source
_dataset
+ raw parser columns
```

A previous experiment added a simple columnar infrastructure under:

```text
omnipath_build/silver/columnar/
```

but the source-module conversion approach was rejected because it put too much Polars code into input modules. If that code remains, it should be treated as experimental scaffolding and refactored toward the compiler approach described here.

## Target architecture

### 1. Keep raw parsers

Existing raw parsers are still useful. They normalize heterogeneous input formats into logical records:

```text
CSV/TSV/XML/OBO/API/archive/etc. → dict records
```

Preparse materializes those dict records as columnar parquet.

### 2. Keep declarative mapper modules

Input modules should remain declarative. They may need small spec changes, but not custom Polars implementation.

Good input-module code:

```python
f = FieldConfig(
    extract={
        "mi": Regex(r"(MI:\\d+)"),
        "tax": Regex(r"(-?\\d+)"),
        "pubmed": Regex(r"(?i)pubmed:(\\d+)"),
    },
    map={
        "identifier_cv": identifier_cv_mapping,
    },
    delimiter="|",
)
```

Bad input-module code:

```python
def compile(self, raw_lf: pl.LazyFrame):
    ...
```

### 3. Compile the existing DSL

Implement a compiler for:

```text
EntityBuilder
IdentifiersBuilder
AnnotationsBuilder
CV
Column
FieldConfig
Member
MembershipBuilder
MembersFromList
OntologyBuilder / OntologyDataset
```

The compiler should transform a raw `pl.LazyFrame` into the five canonical silver tables:

```text
entity_occurrence.parquet
entity_identifier.parquet
entity_annotation.parquet
membership.parquet
membership_annotation.parquet
```

No nested `Entity` objects should be built in production silver.

## Columnar-compatible mapper requirements

The existing DSL already has many columnar-friendly concepts:

- constant CV terms
- direct column selection
- delimiter split
- regex extraction
- dictionary mapping
- simple transforms
- membership definitions
- indexed member lists

The main issue is arbitrary Python callables. We should replace them with named declarative operations.

## Replace arbitrary callables with named operations

Current DSL allows:

```python
Column(selector=callable)
Column(extract=callable)
Column(transform=callable)
Column(map=callable)
CV(term=lambda row: ...)
CV(value=lambda row: ...)
EntityBuilder(entity_type=lambda row: ...)
```

For production columnar silver, these should be invalid unless represented as named operations with compiler support.

### Desired operation model

Define operation objects or symbolic names, for example:

```python
Regex(r"(MI:\\d+)")
Lower()
Upper()
DictMap(mapping)
Split("|")
FilterRegex(pattern)
NormalizeChebi()
NormalizeChembl()
IntActIdentifierPairs()
SignorIdentifierPairs()
```

Each operation needs a columnar implementation, e.g. Polars expressions or LazyFrame fragments.

Optional: keep row implementations for tests/equivalence, but production should use the columnar implementation.

## Important current callable patterns to replace

A quick scan found these patterns in `pypath/pypath/inputs_v2`.

### Trivial list-column access

Examples from ChEBI:

```python
CV(term=IdentifierNamespaceCv.SYNONYM, value=lambda row: row.get("synonyms", []))
CV(term=IdentifierNamespaceCv.KEGG_COMPOUND, value=lambda row: row.get("kegg_compound", []))
```

These should become direct list-column specs, e.g.:

```python
CV(term=IdentifierNamespaceCv.SYNONYM, value=f("synonyms", many=True))
```

or equivalent. The compiler should explode list columns.

### Simple transforms

Examples:

- lower
- upper
- split after colon
- normalize HMDB upper-case
- normalize FOODON prefix
- null-if-empty
- boolean flag → CV term

Make these named operations with Polars expression implementations.

### Identifier filters

Examples from GuideToPharma:

```python
_filter_refseq(...)
_filter_uniprot_accessions(...)
_filter_uniprot_entry_names(...)
_normalize_chembl(...)
```

Replace with declarative operations such as:

```python
f("UniProt ID", delimiter="|", filter="uniprot_accession")
f("UniProt ID", delimiter="|", filter="uniprot_entry_name")
f("ChEMBL ID", delimiter="|", transform="normalize_chembl")
f("Human protein RefSeq", delimiter="|", filter=PrefixFilter(("NP_", "XP_", ...)))
```

### Paired term/value extraction

IntAct and SIGNOR currently use callables to parse an identifier field into `(term, value)` pairs.

Current style:

```python
CV(
    term=parsed_identifier_terms("#ID(s) interactor A"),
    value=parsed_identifier_values("#ID(s) interactor A"),
)
```

Desired declarative style:

```python
CVPair.from_field(
    "#ID(s) interactor A",
    parser="intact_identifier_pairs",
)
```

or:

```python
CV(
    term=f("#ID(s) interactor A", extract="intact_identifier_type"),
    value=f("#ID(s) interactor A", extract="intact_identifier_value"),
)
```

The key requirement: term and value must stay index-aligned after splitting/exploding.

### Dynamic term lists

ConnectomeDB currently has:

```python
CV(term=lambda row: _location_terms(row.get("Ligand Location")))
```

Replace with:

```python
CV(term=f("Ligand Location", transform="connectomedb_location_terms"))
```

The operation returns a list of CV terms, then the compiler explodes it.

### Dynamic entity types

Some modules infer entity types via callables or mapping functions. Replace with declarative operations:

```python
entity_type=f("type_column", map="entity_type")
```

or named inference operations when needed.

## Compiler design

### Input

```python
compile_mapper(
    mapper: EntityBuilder | OntologyBuilder | ...,
    raw_lf: pl.LazyFrame,
    *,
    source: str,
    dataset: str,
    snapshot_id: str,
) -> SilverFrames
```

### Output

```python
@dataclass
class SilverFrames:
    entity_occurrence: pl.LazyFrame
    entity_identifier: pl.LazyFrame
    entity_annotation: pl.LazyFrame
    membership: pl.LazyFrame
    membership_annotation: pl.LazyFrame
```

### Occurrence IDs

Use deterministic IDs derived from raw provenance:

```text
parent occurrence:  <dataset>:<raw_record_key>:parent
single member:      <dataset>:<raw_record_key>:member:<member_index_or_name>
list member:        <dataset>:<raw_record_key>:member:<member_index>
membership:         <dataset>:<raw_record_key>:membership:<member_index_or_name>
```

Do not use mutable counters for columnar silver.

### Provenance

Silver rows should include or preserve:

```text
_raw_record_key
_snapshot_id
_source
_dataset
```

The current canonical silver schemas may need to be extended. Existing `record_id` can be set to `_raw_record_key` for compatibility.

## Compiler responsibilities

### Entity occurrence table

For each parent entity:

```text
occurrence_id
record_id = _raw_record_key
parent_occurrence_id = null
entity_role = parent
entity_type
source
dataset
row_number
```

For members:

```text
occurrence_id
record_id = _raw_record_key
parent_occurrence_id = parent occurrence
entity_role = member
entity_type
source
dataset
row_number
```

### Identifier table

For each `CV` in `IdentifiersBuilder`:

1. compile term expression
2. compile value expression
3. split/explode list values as required
4. preserve index alignment when term and value are both lists
5. filter null/blank/placeholder values
6. deduplicate per occurrence/type/value

### Annotation table

Same as identifiers, plus optional unit expression:

```text
occurrence_id
term
value
unit
source
dataset
```

Annotations with term only and no value are valid if current row semantics allow them.

### Membership table

For each `Member`:

```text
membership_id
parent_occurrence_id
member_occurrence_id
is_parent
membership_role
source
dataset
```

For `MembersFromList`, explode index-aligned lists and produce one member per valid index.

### Membership annotation table

Compile annotations attached to membership edges.

## Handling `Column` semantics

Current `Column.extract(row)` semantics include:

1. lookup selector
2. split on delimiter
3. normalize token: strip whitespace and quotes
4. skip null/blank/`-`, unless `preserve_indices=True`
5. apply extract steps
6. apply transform
7. apply mapping
8. apply default

The columnar compiler must match these semantics closely.

Special attention:

- `preserve_indices=True` is important for `MembersFromList`.
- If term/value/unit lists have different lengths, current builder broadcasts singleton values.
- Deduplication happens per builder output.
- Empty identifiers suppress entity creation unless entity type allows empty identifiers.

## Suggested implementation phases

### Phase 1: Compiler skeleton

Create or refactor:

```text
omnipath_build/silver/columnar/
  __init__.py
  frames.py
  compiler.py
  expressions.py
  operations.py
  writer.py
```

Add:

```python
is_columnar_compilable(mapper) -> bool
compile_mapper(mapper, raw_lf, source, dataset, snapshot_id) -> SilverFrames
```

For production no-row-fallback, silver should eventually do:

```python
if not is_columnar_compilable(dataset.mapper):
    raise RuntimeError(...)
```

### Phase 2: Compile simple `EntityBuilder`

Support:

- constant entity type
- constant CV term
- direct column value
- delimiter split
- regex extraction
- dict mapping
- simple annotations
- no membership

Good test target: `hpo.annotations`.

Input module should remain as `EntityBuilder`; do not rewrite it to `ColumnarEntitySpec` unless that spec becomes the declarative replacement for `EntityBuilder` project-wide.

### Phase 3: Compile `Member` and simple memberships

Support:

- parent entity
- fixed member definitions
- member identifiers/annotations
- membership annotations

Good test targets:

- BindingDB-like two-member interaction patterns
- IntAct after replacing identifier-pair callables with named declarative operations

### Phase 4: Compile `MembersFromList`

Support index-aligned list explosion with `preserve_indices=True`.

Targets:

- CORUM complexes
- CellPhoneDB complexes
- Reactome list members
- PTFI / Phenol Explorer style list members

### Phase 5: Named operation registry

Create a registry mapping operation names to columnar implementations.

Initial operations likely needed:

```text
lower
upper
postcolon
null_if_blank
boolean_flag_to_cv
normalize_chebi
normalize_chembl
normalize_hmdb
normalize_kegg_compound
normalize_foodon
uniprot_accession_filter
uniprot_entry_name_filter
refseq_prefix_filter
species_to_taxid
connectomedb_location_terms
intact_identifier_pairs
signor_identifier_pairs
```

For pair operations, consider an expression that returns a list of structs:

```text
list<struct<term: string, value: string>>
```

Then explode the struct and project fields.

### Phase 6: Ontology compiler

OBO-like `records.parquet` has columns such as:

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

Compile to CV-term silver entities columnarly:

- `id` → `CV_TERM_ACCESSION`
- `alt_ids` explode → `CV_TERM_ACCESSION`
- `name` → `NAME`
- `synonyms` explode → `SYNONYM`
- `definition`, `comments`, `is_obsolete`, `namespace` → annotations
- `is_a`, `relationships` → relation representation according to downstream expectations

### Phase 7: Enforce no row fallback

Once all active datasets compile columnarly:

- remove production use of `Dataset.__call__()` for silver
- remove row iteration from silver build
- fail if mapper is not columnar-compilable
- keep row path only in tests if needed

## IntAct-specific migration notes

IntAct should remain declarative. Do not implement `IntActColumnarMapper` in the input module.

Current hard part:

```python
parsed_identifier_terms(column_name)
parsed_identifier_values(column_name)
```

These parse MITAB identifier fields with rules:

- split field on `|`
- parse `prefix:value`
- normalize prefix:
  - `MINT-` under `intact`/`psi-mi` → `mint`
  - invalid `intact` values ignored unless starting `EBI-` or `IM-`
  - non-numeric `hgnc` → `genesymbol`
  - `chembl compound` numeric → `chembl internal id`
  - invalid `refseq`/`entrez`/`ensembl`/GI prefixes → `genbank identifier`
- normalize value:
  - `uniprotkb` strips `-PRO_...`
  - `chebi` strips optional `CHEBI:` prefix
- map prefix to `IdentifierNamespaceCv`
- emit aligned `(term, value)` pairs

Represent this as a named operation, probably returning list-of-struct:

```python
CVPair.from_field(
    "#ID(s) interactor A",
    operation="intact_identifier_pairs",
)
```

or equivalent.

Other IntAct fields are mostly straightforward:

```text
extract MI terms: r"(MI:\d+)"
extract taxon:    r"(-?\d+)"
extract pubmed:   r"(?i)pubmed:(\d+)"
extract intact:   r"intact:([^|\"]+)"
```

## Testing strategy

### Unit tests

Use small in-memory `pl.DataFrame(...).lazy()` inputs and compile mapper specs.

Assert:

- expected occurrence rows
- expected identifier rows
- expected annotation rows
- expected membership rows
- deterministic IDs
- null/blank/`-` filtering
- delimiter explosion
- term/value list alignment

### Equivalence tests

During migration only, compare legacy row mapper output to columnar compiler output for sampled records.

This comparison should live in tests or migration scripts, not production silver.

### End-to-end smoke tests

Run a small selected source through:

```text
preparse → columnar compiler → silver tables → gold smoke check
```

Good early source:

```text
hpo.annotations
```

Then:

```text
intact.interactions
```

after IntAct identifier pair parsing has a named columnar operation.

## Files likely involved

Core docs:

```text
docs/preparse_pipeline_plan.md
docs/columnar_inputs_takeover_plan.md
```

Preparse:

```text
pypath/pypath/inputs_v2/raw_records.py
pypath/pypath/inputs_v2/base.py
```

Silver build:

```text
omnipath_build/silver/build.py
omnipath_build/silver/tables.py
```

Columnar compiler:

```text
omnipath_build/silver/columnar/
```

Declarative DSL to inspect/possibly extend:

```text
pypath/pypath/internals/tabular_builder.py
pypath/pypath/internals/ontology_builder.py
```

Candidate source modules:

```text
pypath/pypath/inputs_v2/hpo.py
pypath/pypath/inputs_v2/intact.py
pypath/pypath/inputs_v2/bindingdb.py
pypath/pypath/inputs_v2/chebi.py
pypath/pypath/inputs_v2/signor.py
pypath/pypath/inputs_v2/guidetopharma.py
```

## Summary

The right solution is not custom columnar mappers inside every input module.

The right solution is:

```text
existing declarative input module
  → compiler validates columnar-compatible operations
  → compiler emits Polars/DuckDB silver frames
  → canonical silver parquet tables
```

Input modules may need small declarative changes to replace arbitrary Python callables with named operations, but they should remain source descriptions, not execution-engine implementations.
