# OmniPath subsets

Build MetSigDB memberships, network presets and COSMOS reaction networks from
one fixed PostgreSQL release loaded by omnipath-postgres. Products use published
identities and aliases; no resource builds or identifier resolution run.

## Build selected products

```sh
export OMNIPATH_DATABASE_URL='postgresql:///omnipath'
uv run --frozen omnipath-subsets build --schema release_2026_09
uv run --frozen omnipath-subsets build --schema release_2026_09 --products metsigdb
```

The command requires exactly one release in the target schema. It takes the
same schema advisory lock as the loader, rebuilds the selected products in one
transaction and records their counts, release ID and canonical manifest SHA-256
in `subset_build_metadata`. A failure rolls back every selected rebuild,
including changes to existing products. The Python entry point is
`omnipath_subsets.build.build_subsets`.

Each adapter exposes `rebuild(conn, schema)` for a caller-owned transaction.

## MetSigDB

`metsigdb_membership` covers Reactome, WikiPathways, KEGG, MACdb and ClassyFire.
Native set IDs, labels, published chemical aliases, selected evidence and exact
resource versions are retained. ClassyFire combines HMDB assignments with
ChemOnt subclass ancestors, using the legacy depth cap of 20.

The chosen `published_entities` policy includes source fallback identities.
The Parquet files stay unchanged. Their serving schema cannot recover the old
matched-only flag; alias provenance does not provide an equivalent filter.
See the [MetSigDB policy and extraction details](src/omnipath_subsets/metsigdb/README.md).

## Network presets

`network_registry` stores the current MetaLinksDB, LIANA and Reactions recipes.
These are query presets, matching the maintained legacy registry; they do not
create a materialized network per preset.

```python
from omnipath_subsets.network_views import query, iter_records

# Use within a psycopg connection and transaction.
page = query(conn, "release_2026_09", "metalinksdb", organism="9606", limit=100)
for record in iter_records(conn, "release_2026_09", "reactions"):
    ...
```

`query` pages source records, then combines equal published statement keys
within that page. `has_more` indicates another source page; one statement's
contributors may span pages. `iter_records` streams complete source records.

Distinct qualified statements remain distinct. The legacy endpoint collapse
and custom interaction classes are not fully reproduced. LIANA orientation
requires explicit ligand/receptor identifiers in preserved ConnectomeDB payloads;
missing or ambiguous roles are reported. ChEMBL selection uses mechanism evidence.
Transport projection requires an explicit catalyst and the same chemical in
different verified compartments. Results report missing inputs and limitations.

## COSMOS

`cosmos_edge` preserves the product's metabolic pseudo-nodes, connectors and
reversible halves. Pseudo-nodes never enter the shared entity tables.
`cosmos_label` records canonical, mapped, ambiguous or fallback label status.

The adapter uses explicit `enabled_by` catalysts from KEGG, Rhea, Metatlas and
Recon3D. GPR associations do not imply catalysis. Labels use published UniProt
and ChEBI aliases when unambiguous; other identifiers retain their namespace.
Unknown direction stays unknown and does not create a reverse half.

Reaction indexes enumerate separate source events. Unlike the legacy
participant-multiset merge, evidence from different source rows is not pooled
into one pseudo-node. Shared `reaction_context` and `reaction_participant`
tables retain source versions, exact raw payload text, coefficient annotations,
compartment checks and diagnostics.

## Validation scope

```sh
make test-subsets
```

Tests use disposable PostgreSQL and tiny resolved Parquet fixtures, capped at
20 original source records per resource. They cover representative protein,
chemical, ontology and reaction data, repeat rebuilds and atomic rollback.
Full release comparisons, production performance and web/API compatibility
remain to be validated.
