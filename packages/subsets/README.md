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

For long product builds, add `--checkpoint-products` (Python:
`checkpoint_products=True`). Each product's tables and metadata commit together;
a later failure or cancellation rolls back only the current product. A session
advisory lock spans all commits, and each product checks the same single release
ID and manifest digest before and after its work. Resume by selecting only the
remaining products with `--products`; existing metadata is not skipped
implicitly.

```sh
uv run --frozen omnipath-subsets build --schema release_2026_09 --checkpoint-products
uv run --frozen omnipath-subsets build --schema release_2026_09 --checkpoint-products --products network_views cosmos
```

The optional Python `on_product_committed(product, result)` callback requires
checkpoint mode and runs only after the durable commit. `result` is a
`BuildResult` snapshot containing release identity and the stats/timings of
products committed by this call. A callback failure stops further builds while
preserving that commit; `subset_build_metadata` is authoritative if observer
persistence fails.

The maintained scientific implementations live in `omnipath_subsets.metsigdb`,
`omnipath_subsets.network_views` and `omnipath_subsets.cosmos`. The standalone
command and PostgreSQL importer share `omnipath_subsets.runner`; they publish
`subset_build_metadata` and `parquet_phase` in the same product transaction.
Explicit selection rebuilds each product. Use `omnipath-postgres --finish` to
skip products already committed for the loaded monthly release.

Historical record-layout readers are explicit compatibility APIs under
`omnipath_subsets.compatibility.record_layout`. Production commands require the
current normalized schema and never detect or select those readers.

## Historical schema adapters

The following query examples and details apply to the earlier resource-record layout. New aligned builds use main's tables, source folds, participant grouping and indexes.

### MetSigDB

`metsigdb_membership` covers Reactome, WikiPathways, KEGG, MACdb and ClassyFire.
Native set IDs, labels, published chemical aliases, selected evidence and exact
resource versions are retained. ClassyFire combines HMDB assignments with
ChemOnt subclass ancestors, using the legacy depth cap of 20.

The chosen `published_entities` policy includes source fallback identities.
The Parquet files stay unchanged. Their serving schema cannot recover the old
matched-only flag; alias provenance does not provide an equivalent filter.
See the [MetSigDB policy and extraction details](omnipath_subsets/compatibility/record_layout/metsigdb/README.md).

### Network presets

`network_registry` stores the current MetaLinksDB, LIANA and Reactions recipes.
These are query presets, matching the maintained legacy registry; they do not
create a materialized network per preset.

```python
from omnipath_subsets.compatibility.record_layout.network_views import query, iter_records

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
requires explicit subject/object role annotations within one published
ConnectomeDB evidence occurrence; missing or contradictory roles are reported.
The adapter never combines partial roles from different evidence. ChEMBL
selection uses mechanism evidence.
Transport projection requires an explicit catalyst and the same chemical in
different verified compartments. Results report missing inputs and limitations.
Network outputs retain compiled statements, selected evidence, measurements and
qualifiers. They omit raw records. `evidence_ordinals` identify the original
published occurrences, including LIANA role support and ChEMBL mechanism selection.

### COSMOS

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
tables retain source versions, coefficient annotations, published member
compartments and diagnostics. Source SHA-256 values retain
record identity without storing the original raw records in PostgreSQL.

## Validation scope

```sh
make test-subsets
```

Tests use disposable PostgreSQL and tiny resolved Parquet fixtures, capped at
20 original source records per resource. They cover representative protein,
chemical, ontology and reaction data, repeat rebuilds and atomic rollback.
Full release comparisons, production performance and web/API compatibility
remain to be validated.
