# Glossary

The terms we use in code, documentation and discussion. When two words could
mean the same thing, this page says which one we use.

### Abstain
Resolution's choice to leave an observation unresolved because its evidence is
missing, conflicting or ambiguous. Deliberate behaviour, not a failure. See [D9](decisions.md#d9).

### Ambiguous
More than one candidate entity survives resolution. Also used for lookup keys
with more than 10 candidates, which are removed from the index and archived in
`ambiguous.parquet`.

### Anchor
The identifier around which reference entities are built: the full InChIKey
for chemicals and the primary UniProt accession for proteins.

### Annotation
A `{term, value, quantity, source, dataset}` attribute attached to an entity,
relation or evidence occurrence. Terms are Biolink slots or ontology CURIEs.
On relations, `scope` says whether it describes the relation, subject or object.

### Base checkpoint
The point in a PostgreSQL load where all base tables, constraints and indexes
are committed and validated. Later phases resume from here.

### Biolink
The [Biolink Model](https://biolink.github.io/biolink-model/) supplies our entity
types, predicates, qualifiers and annotation slots.

### Build manifest
`build_manifest.json` in each resource version: versions, row counts, sizes and
SHA-256 checksums of the three Parquets, plus build provenance.

### Candidate
An entity in the reference library that a lookup key points to. Resolution
intersects the candidates of all votes.

### Connectivity
The first 14-character block of an InChIKey, which encodes the molecular
skeleton without stereochemistry or charge. Used to group chemicals at query time.

### Entity
One row of `entities.parquet`: a typed thing such as a protein, gene, chemical,
complex or ontology class. Identified by its [entity key](#entity-key).

### Entity key
SHA-256 of `(entity_type, namespace, identifier)`. Two rows with the same
identifier but different types have different keys.

### Entity type
The Biolink type reported by the source, for example `protein` or `gene`.
Resolution never changes it.

### Evidence
One source occurrence supporting an entity or relation:
`{source, dataset, row_id, upstream_id, annotations, molecular forms}`.
Evidence is a multiset, so identical occurrences are kept.

### Evidence payload
The raw source record as JSON, stored in `evidence_payloads.parquet` and linked
to evidence by `row_id`.

### Fallback
The result when resolution cannot establish a reference: the observation keeps
its own native identifier (or reported product), with annotations explaining why.
Also called a *native identity*.

### Gene reference
See [reference entity key](#reference-entity-key).

### Generation
One complete, immutable build of the reference library, published by switching
the `current` symlink. Each resource build pins one generation.

### Hub
A normalized export of a reference source (UniProt, NCBI Gene, Ensembl, HGNC,
ChEBI, …) used to build the reference library.

### inputs_v2
The pypath module that parses source resources into typed entities and
relations. It is the input of every resource build.

### Latest
The API default: the highest numeric version of each resource, including newly
added resources. Changes as builds are published.

### Molecular form
The occurrence-level description of what exactly was observed: product
pointer, isoform, sequence identifiers, modifications and variants. Missing
fields mean unspecified.

### Native identity
See [fallback](#fallback).

### Occurrence
A single appearance of an entity or relation in a source row. Each occurrence
becomes one [evidence](#evidence) item.

### Parquet contract
The three Parquet files and their schemas (`omnipath_core/schema.py`), currently
`serving_schema_version = 4`. The only interface between build, serving and PostgreSQL.

### Phase
A durable, separately committed step of a PostgreSQL load (the base, a
derivation or a product), recorded in `parquet_phase`.

### Predicate
The Biolink relationship of a statement, for example `affects` or `physically_interacts_with`.

### Product
A gene product with its own reusable entity row: a primary UniProt protein or a
specifically reported transcript. Referenced from molecular forms.

### Qualifier
A Biolink annotation that changes what a statement means (for example direction
or aspect). Qualifiers are part of the relation key.

### Reference entity key
The shared grouping reference of an entity, normally `entrez:<GeneID>`. A
protein, a gene and an RNA of the same gene share it while keeping separate
entity keys. Also called *gene reference*.

### Reference library
The compiled LMDB identifier and entity indexes, plus the gene component, that
resolution reads at runtime.

### Relation
In Parquet, a row of `relations.parquet` (a [statement](#statement)). In
PostgreSQL, `relation` is main's graph triple (subject, predicate, object); several
qualified statements can share one triple.

### Release
A JSON manifest pinning one version of each included resource, plus the digest
of each build manifest, for example `2026.09`. Immutable once published.

### Resource
One upstream data provider as built by us, for example SIGNOR or ChEBI. A
resource can contain several datasets.

### Resource version
One immutable, numbered directory of Parquets for a resource, for example
`resources/signor/1.0.0/`. Independent of the provider's own release number.

### Serving schema version
The version of the Parquet record contract (currently 4). Separate from the
build-manifest `schema_version` (1).

### Statement
A subject–predicate–object claim with its qualifiers and evidence. Its
`statement_kind` is `relation` or `ontology`.

### Subset
A curated product built inside a loaded PostgreSQL schema: MetSigDB, network
views or COSMOS.

### Taxon
The NCBI Taxonomy ID of an entity or relation, for example `9606` for human.
Empty when assertions conflict.

### Vote
A normalized identifier from an observation that is allowed to take part in
matching. Names never vote; gene symbols vote only with a taxon.
