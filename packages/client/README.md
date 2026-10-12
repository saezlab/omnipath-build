# OmniPath client

Install OmniPath resources on your machine and get a DuckDB database of them. Query
it with SQL from Python, R or the DuckDB shell, or ask the questions the OmniPath
website answers and get the same answers. Nothing is queried over the network; the
API is contacted only while installing.

```python
import omnipath_client as op

op.init(topic="signaling")                  # once: download and write the database

db = op.connect()
db.sql("SELECT * FROM signor.relation LIMIT 5").df()   # your SQL
db.search("TP53")                                      # the website's search
db.relations("TP53", sources="signor")                 # the website's relation export
```

This package lives in `packages/client`. It does not implement the retired
PostgreSQL API.

## Install

From this checkout:

```bash
python -m pip install -e './packages/core' -e './packages/engine' -e './packages/client'
# Optional DataFrame conversions:
python -m pip install -e './packages/client[pandas]'
```

Requires Python 3.11+. The client depends on `omnipath-engine` (the query engine the
API serves) and `omnipath-core` (the table schemas); neither brings a web framework.
The distribution and import names are `omnipath-client` and `omnipath_client`, which
are also used by the colleagues'
[saezlab/omnipath-client](https://github.com/saezlab/omnipath-client). Use a separate
environment for this checkout; a bare `pip install omnipath-client` installs their
published package, not this version. The older `omnipath` package uses a different
namespace and can coexist.

## Initialize

```python
op.init(progress=print)                    # every resource, about 5.6 GB
op.init(topic="cell_cell_communication")   # 9 resources, about 20 MB
op.init(["signor", "intact"])              # by name
```

or from a shell:

```bash
omnipath-client list                       # resources, versions, sizes, tags
omnipath-client init --topic signaling
omnipath-client init signor intact
```

- **Selection.** Resources are chosen by name and by the catalog's tags in four
  dimensions: `topic`, `content`, `molecule` and `knowledge_origin`. Several tags of
  one dimension are alternatives; different dimensions must all match. Without a
  selection, `init` installs every resource. `available()` and `omnipath-client list`
  show what a release offers.
- **Location.** Data goes to the per-user data directory (`data_directory()` returns
  it), to `OMNIPATH_DATA_DIR` when that variable is set, or to `data_dir=...`.
  `connect()` reads the same place. The directory can be moved or copied as a whole.
- **Updates.** `init` only downloads versions that are missing. Running it again
  moves every installed resource to the requested release's versions and removes the
  versions they replace, so a directory always holds one release.
- **Releases.** `init(release="2026.9.6.2")` installs the versions that release pins;
  the default `latest` takes the newest version of each resource.
- **Raw evidence** payloads are downloaded only with `include_evidence=True`
  (`--evidence`), and then kept on later updates.
- **Interruptions.** A resource becomes available when all its files are complete;
  an interrupted `init` keeps what it finished, and the next run continues.

Without `api_url`, `init` uses `OMNIPATH_API_URL` if set, otherwise the development
deployment `https://omnipath-metabo-dev.schaul.click/api`. Downloads are checked for
size and Parquet framing. `connect()` checks that every installed file is present
with its recorded size, and fails with a message naming `init()` when the directory
is empty or damaged.

## The database

`init` ends by writing `omnipath.duckdb` into the data directory and prints its path.

| In the database | What it is |
|---|---|
| `entity`, `relation`, ... | each table over every installed resource, with `_resource` and `_resource_version` columns |
| `signor.relation`, `chembl.entity`, ... | the same tables of one resource, in a schema named after it |
| `resources` | the installed resources, their versions and whether they have raw evidence |

By default these are **views** over the downloaded Parquet files: the database is
about 1 MB and is written in a second. With `materialize=True` (`--materialize`) they
are **native DuckDB tables** copied from the files. Names, columns and answers are
the same; only speed and disk use differ. For every resource of a release:

| | Views (default) | Native tables |
|---|---|---|
| Written in | 1 s | under 3 minutes |
| Extra disk | 1 MB | 13.6 GB |
| Entities of a name or identifier | 0.05 s | 0.02 s |
| Relations of one entity, joined | 0.25 s | 0.05 s |
| A query joining classes, entities and relations of all resources | 4.6 s | 2.0 s |

The choice is remembered: a later `init` keeps it, and `init(materialize=False)`
goes back to views. Updates replace only the rows of resources whose version changed.
The Parquet files stay in both modes; the prebuilt queries read them.

The database is opened read-only, so several sessions and tools can use it at once.
Create your own tables as temporary tables, or attach the database to another one:

```sql
ATTACH '/path/to/omnipath.duckdb' AS omnipath (READ_ONLY);
```

Views name the files by their full path. After moving the data directory, the first
`connect()` writes them again.

## SQL

```python
with op.connect() as db:
    result = db.sql('''
        SELECT e.label, r.predicate, r.object_label, r._resource
        FROM entity AS e
        JOIN relation AS r
          ON e.entity_key = r.subject_entity_key
         AND e._resource = r._resource
        WHERE e.identifier = ?
        LIMIT 20
    ''', ["P04637"])
    print(result.fetchall())
    # result.df()                 # requires the pandas extra
    # result.to_arrow_table()
```

`sql` returns a native `DuckDBPyRelation`; rows are evaluated when you fetch or export
them. `db.connection` is the DuckDB connection itself. It executes local SQL; it is
not a sandbox. Outside Python, open the file with any DuckDB client:

```bash
duckdb -readonly "$(python -c 'import omnipath_client as op; print(op.data_directory())')/omnipath.duckdb"
```

Each resource version has these tables:

| Table | One row per | Refers to |
|---|---|---|
| `entity` | entity, with its label, type, taxon and references | |
| `entity_identifier` | identifier of an entity | `entity_id` |
| `entity_annotation` | annotation of an entity (quantities as `quantity_*` columns) | `entity_id` |
| `entity_evidence` | source record stating an entity | `entity_id` |
| `relation` | relation, with its endpoints, sign, sources and qualifiers | |
| `relation_annotation` | annotation of a relation | `relation_id` |
| `relation_evidence` | source record stating a relation, with molecular forms | `relation_id` |
| `evidence_payloads` | raw source record (only where installed) | `relation_key`, `entity_key` |
| `relation_endpoint` | endpoint key of a relation, sorted by key | `relation_id` |
| `entity_group` | gene, reference or connectivity group of an entity | `entity_id` |
| `entity_term` | lowercase label or identifier of an entity, sorted | `entity_id` |

`entity_id` and `relation_id` number the rows of `entity` and `relation` within one
resource version; join child tables on them together with `_resource`. The 64-character
`entity_key` and `relation_key` identify the same entity or relation across resources.
In the tables over every resource, shared entities and relations are **not
deduplicated**: there is one row per resource that has them. The last three tables
are lookup tables `init` derives from the others; use them to find entities by name
(`entity_term`) and relations by endpoint (`relation_endpoint`) without scanning.

## Prebuilt queries

Three methods run the engine behind the hosted API and explorer on the installed
files, and answer as the website does:

```python
with op.connect() as db:
    db.search("TP53")                                  # ranked entities, merged across resources
    db.search("gluc", entity_types="chemical_entity", limit=50)
    tp53 = db.search("TP53").fetchone()[0]             # entity_key

    db.relations("TP53", sources="signor")             # relations with annotations and evidence
    db.relations("gene:entrez:7157", predicates="affects", taxonomy_ids="9606")
    db.relations(protein_entity_keys=[key], isoform_identifiers=["uniprot:P04637-2"])

    db.entity(tp53)                                    # one entity's details, a dict
```

- `search` and `relations` return DuckDB relations like `sql`, so they convert,
  filter, join and export the same way.
- `relations` takes exact names, identifiers, entity keys or group keys such as
  `gene:entrez:7157`, at either endpoint, and the explorer's filters by name; an
  unknown filter name is an error that lists the valid ones. One value stands for a
  list of one. It returns what the explorer exports: one row per relation with its
  annotations, evidence and the product records its evidence names, at most 100,000
  relations. Larger selections are SQL on `relation`.
- `entity` takes the exact `entity_key`, as `search` returns it.
- `db.engine` is the engine itself, for everything else the API offers (facets,
  grouped search, ontology browsing, molecular context); its methods are the ones the
  API routes call.

With every resource of a release installed, these give the hosted API's answers, with
two differences: taxon names are absent (the taxonomy reference is not a download),
and resources the hosted API excludes from live queries are included here. With a
subset installed, counts and merged entities cover only the installed resources.

The prebuilt queries run Python code and are not available from the DuckDB shell or
other languages; SQL on the database is.

## Validation

From the repository root:

```bash
uv run pytest packages/client/tests -q
uv run python packages/client/examples/smoke.py
```

The tests use Parquet files written by the core fixtures, a mocked catalog and file
host, real DuckDB queries and the real engine. The smoke script contacts the default
live server, installs SIGNOR into a temporary directory and queries it. No deployment
is required for client changes. Client instances are intended for one thread.
