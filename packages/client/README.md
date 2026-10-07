# OmniPath DuckDB client

A first client for the OmniPath Parquet tables. Discover immutable resource
versions through the API, then query their HTTPS files with local DuckDB. Download
a portable snapshot when you need offline access.

This package lives in `packages/client`. It is a new
implementation, independent of the server/build packages. It does not implement
the retired PostgreSQL API. The existing
[saezlab/omnipath-client](https://github.com/saezlab/omnipath-client) inspired the
`lookup` and `related` interface; their behavior here follows the new schema.

## Install

From this checkout:

```bash
python -m pip install -e './packages/client'
# Optional DataFrame / Arrow conversions:
python -m pip install -e './packages/client[pandas,arrow]'
```

Requires Python 3.11+. Runtime dependencies are DuckDB and httpx. The distribution
and import names are `omnipath-client` and `omnipath_client`, which are also used by
the colleagues' client. Use a separate environment for this checkout; a bare
`pip install omnipath-client` installs their published package, not this version.
The older `omnipath` package uses a different namespace and can coexist.

## Query remote files

```python
from omnipath_client import Client

with Client() as op:
    print([(r['resource_id'], r['version']) for r in op.resources()])
    result = op.relations(
        resources='signor',
        filters={'taxon': '9606'},
        columns=['subject_label', 'predicate', 'object_label'],
    ).limit(20)
    print(result.fetchall())
    # result.df()                 # requires the pandas extra
    # result.to_arrow_table()     # requires the arrow extra
```

Without `api_url`, the client uses `OMNIPATH_API_URL` if set, otherwise the
development deployment `https://omnipath-metabo-dev.schaul.click/api`. Remote
reads stream Parquet ranges lazily and are not checksum-verified; offline
snapshots (`download`) verify every file's SHA-256. File locations come from the catalog's `files[].url`;
public HTTPS access needs no S3 credentials. DuckDB loads `httpfs` on first remote
use and attempts installation if necessary. The initial extension installation
requires network access to DuckDB's extension service.

## Tables

Each resource version publishes these tables:

| Table | One row per | Refers to |
|---|---|---|
| `entity` | entity, with its label, type, taxon and references | |
| `entity_identifier` | identifier of an entity | `entity_id` |
| `entity_annotation` | annotation of an entity (quantities as `quantity_*` columns) | `entity_id` |
| `entity_evidence` | source record stating an entity | `entity_id` |
| `relation` | relation, with its endpoints, sign, sources and qualifiers | |
| `relation_annotation` | annotation of a relation | `relation_id` |
| `relation_evidence` | source record stating a relation, with molecular forms | `relation_id` |
| `evidence_payloads` | raw source record (opt-in) | `relation_key`, `entity_key` |

`entity_id` and `relation_id` number the rows of `entity` and `relation` within one
resource version; join child tables on them together with `_resource`. The 64-character
`entity_key` and `relation_key` identify the same entity or relation across resources.
`table(name, ...)` reads any of them; `entities()`, `relations()` and `evidence()` are
shortcuts for `entity`, `relation` and `evidence_payloads`.

`table`, `entities`, `relations`, `evidence`, `lookup`, `related` and `sql` return native
`DuckDBPyRelation` objects. Query construction can read file metadata, but does not
materialize the entire dataset. Rows are evaluated when you fetch or export them.
Select columns and add filters before fetching. Broad scans and joins can still
transfer much of a file. Keep the client open while using its relations.

## Releases and provenance

```python
with Client() as op:
    releases = op.releases()  # published release manifests
    print([release['version'] for release in releases])

# Choose an actual version from that response:
with Client(release='2026.9.6.2') as op:
    print(op.files('signor'))
```

A client resolves its catalog once. Even `release='latest'` retains the resource
versions and file URLs discovered on first use; create a new client to refresh.
A numbered OmniPath release pins resource versions that can have different numbers.
If evidence discovery detects that Latest advanced, it fails instead of combining
versions. Use a numbered release for a stable full snapshot.

Every query adds `_resource` and `_resource_version`. Combining resources uses
`UNION ALL BY NAME`: additive columns are filled with NULL, incompatible types may
raise a DuckDB error, and shared entities/relations are **not deduplicated**.
Catalog metadata includes source licensing information from the API.
Resources excluded by the hosted API's live query policy remain accessible as
Parquet here; choose `resources` explicitly when limiting your analysis.

## Filters, lookups and neighbors

```python
with Client() as op:
    human = op.entities('signor', filters={
        'taxon': '9606',
        'entity_type': ['protein', 'gene'],
    })
    tp53 = op.lookup('TP53', resources='signor')
    edges = op.related('TP53', resources='signor').limit(20)
    outgoing = op.related(subject='TP53', resources='signor').limit(20)
    print(edges.fetchall())
```

Filters are ANDed. A list is an IN filter; `None` matches SQL NULL; an empty list
matches no rows. Values are parameterized and column names are quoted. Invalid
column names raise DuckDB errors. More complex filters can use the returned
relation's `.filter(...)` method.

`lookup` matches exact labels, canonical identifiers, alias identifiers and
entity keys, ignoring case. It returns all matches, including ambiguous names
across taxa and resources. It performs no fuzzy matching, identifier translation
or automatic pivoting. `related(query)` matches either endpoint;
`related(subject=..., object=...)` applies both endpoint constraints. Joins match
entity keys within each resource. Results are `relation` rows.
Omitting `resources` on table methods selects every resource in the catalog;
explicit selection is recommended for large deployments.

## SQL and evidence

```python
with Client(release='2026.9.6.2') as op:
    result = op.sql('''
        SELECT e.label, r.predicate, r.object_label, r._resource
        FROM entity AS e
        JOIN relation AS r
          ON e.entity_key = r.subject_entity_key
         AND e._resource = r._resource
        WHERE e.identifier = ?
        LIMIT 20
    ''', resources='signor', parameters=['P04637'])
    print(result.fetchall())
```

`sql` exposes every published table (`entity`, `entity_identifier`, ...,
`relation_evidence`) for the supplied resources within that query. It accepts SELECT/CTE queries and preserves earlier lazy results when you
select different resources later. It executes local SQL; it is not a sandbox.
Raw payloads are opt-in: use `op.evidence('signor')`, or
`op.sql(..., resources='signor', include_evidence=True)` to include the
`evidence_payloads` table. Join relation payloads using `relation_key`, `source` and
`row_id` within the same resource version. Entity payloads use `entity_key`.

## Download and work offline

```python
with Client(release='2026.9.6.2') as op:
    snapshot = op.download('./signor-snapshot', resources='signor')
    # Add include_evidence=True only when raw payloads are needed.

with Client.from_snapshot('./signor-snapshot') as op:
    print(op.relations('signor', filters={'taxon': '9606'}).limit(20).fetchall())
```

A snapshot contains `snapshot.json` and versioned resource directories. Downloads
are streamed to a staging directory, checked for size and Parquet headers, and
published only after all selected files succeed. The destination must not exist.
Snapshots include SHA-256 digests to detect later local corruption; these digests
are computed during download, not independent authenticity proofs from upstream.
Offline queries check each file on first use and require neither API access nor
`httpfs`. Copy the entire snapshot directory to move it to another machine.

This version has explicit full-file snapshots, not an automatic persistent range
cache. It has no server-side query fallback, authentication workflow, retry/resume
manager or graph conversion. Client instances are intended for one thread.

## Validation

From the repository root:

```bash
.venv/bin/python -m pytest packages/client/tests -q
PYTHONPATH=packages/client .venv/bin/python \
  packages/client/examples/smoke_remote.py
```

The tests use generated Parquet files, mocked discovery/download HTTP, and real
DuckDB queries. A separate HTTP fixture verifies partial reads; that test skips
when `httpfs` is not installed. The smoke script contacts the default live server, fetches a small
remote query, downloads SIGNOR's entity/relation files, and repeats the query from
an offline snapshot. No deployment is required for client changes.

## Gene references and exact products

`entity_key` identifies a source-typed record; `reference_entity_key` is its
shared gene reference.

```python
# Shared gene knowledge across protein/gene/RNA source types.
gene_relations = op.related_reference('entrez:7157', resources='signor')

# Only occurrences naming this reusable product and this exact isoform.
product_relations = op.related_product(
    product_entity_key, resources='signor',
    isoform_identifier='uniprot:P04637-2', endpoint='source',
)
# Preserve these reusable rows alongside exported product_relations.
products = op.referenced_products(product_relations, resources='signor')
```

`related_product()` keeps source-typed relation endpoints and adds the matching
`relation_evidence` rows as an `evidence` list (and their number as `evidence_count`). Product and isoform constraints apply to
the same endpoint of the same observation; unknown forms do not match.
`product_type='transcript'` selects transcript references. An absent isoform,
modification or variant never asserts canonical isoform or wild type.
