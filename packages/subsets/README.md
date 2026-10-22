# OmniPath subsets

Build the exclusion metabolite list, MetSigDB memberships, network presets and
COSMOS reaction networks from one fixed PostgreSQL release loaded by omnipath-postgres. Products use published
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

The maintained scientific implementations live in `omnipath_subsets.exclusion_metabolites`,
`omnipath_subsets.metsigdb`, `omnipath_subsets.network_views` and
`omnipath_subsets.cosmos`. The standalone
command and PostgreSQL importer share `omnipath_subsets.runner`; they publish
`subset_build_metadata` and `parquet_phase` in the same product transaction.
Explicit selection rebuilds each product. Use `omnipath-postgres --finish` to
skip products already committed for the loaded monthly release.

## Products

- **Exclusion metabolites:** the chemical entities that mass spectrometry does not
  measure (ions, atoms, small molecules, xenobiotics and cofactors), in classes.
  MetSigDB and COSMOS read it. See [Exclusion metabolites](#exclusion-metabolites).
- **MetSigDB:** metabolite memberships in Reactome, WikiPathways, KEGG, MACdb
  and ClassyFire sets, read from the normalized entity, evidence and ontology tables.
- **Network views:** the MetaLinksDB, LIANA and Reactions preset definitions and
  registry. Registering a preset does not create a materialized network; consumers
  apply the stored recipe.
- **COSMOS:** metabolite/enzyme edges projected from the shared reaction tables,
  including reversible reactions and labels drawn from published identifiers.
  GeneID-centered catalysts keep their GeneID label and `entrez` namespace,
  including records whose source reported a protein. UniProt catalogue aliases
  of a gene do not select a protein participant or create extra reaction nodes.
  Canonical UniProt product identities keep their UniProt labels. The optional
  legacy utils translation also preserves GeneID and accepts other catalyst
  mappings only when exactly one UniProt target answers. Connectors use the
  same identifier and namespace as their reaction node.

All four products use the current PostgreSQL schema. The earlier resource-record
subset implementations and their verification command have been removed.

## Exclusion metabolites

The product publishes two tables in the release schema: `exclusion_metabolite`
(one row for each entity, class and source of the assignment) and
`exclusion_metabolite_report` (what the list covers and what it does not). It
reads one loaded release, resolves no identifier and downloads nothing. It
runs first, and it rebuilds alone:

```sh
make rebuild-subsets POSTGRES_SCHEMA=release_2026_09 SUBSET_PRODUCTS=exclusion_metabolites
```

### The classes

| Class | How its members are found |
|---|---|
| `ions` | The MetaProViz seeds, and every ChEBI `subclass_of` descendant of `CHEBI:24867`. |
| `atoms` | The MetaProViz seeds, and every ChEBI descendant of `CHEBI:33250`. |
| `small_molecules` | The MetaProViz KEGG seeds. Nothing derives them. |
| `cofactors` | Chemicals with the ChEBI cofactor role (`CHEBI:23357`), and the core list. |
| `xenobiotics` | Valid and empty until a curator adds a member. |

A variant of a listed member (a charge, tautomer or stereo form that shares its
structure key) is listed in the member's classes as a row of its own. Its
`match_kind` is `structure_key` and `via_entity_id` names the member. An entity
that matched in its own right gets no such row.

### The three files you edit

They are in `omnipath_subsets/exclusion_metabolites/seed/`. Edit one, commit it
and rebuild the product. No code changes.

- `metaproviz_seed.yaml`: the MetaProViz lists, vendored with their commit and
  licence. Change it only to take a newer MetaProViz version.
- `project_cofactors.yaml`: the reviewed core of cofactors. One line for each
  member: `{id: "CHEBI:16761", name: ADP}`. The name is for the reader and is not
  matched. Add a line when the role misses a cofactor.
- `overrides.yaml`: your changes, each with a reason.

```yaml
add:  [{class: ions, namespace: chebi, id: "CHEBI:29101", note: why it belongs}]
veto: [{class: cofactors, namespace: chebi, id: "CHEBI:15422", note: why it does not}]
```

Classes are `ions`, `atoms`, `small_molecules`, `xenobiotics` and `cofactors`.
Namespaces are `chebi`, `kegg`, `hmdb`, `pubchem`, `lipidmaps`, `swisslipids`,
`chembl` and `drugbank`. A ChEBI id may be written with or without `CHEBI:`, and
an HMDB id in either width. A malformed file stops the build and says why.

**A veto wins.** It removes the entity from that class, whatever put it there:
a seed, the ontology, an addition or a structure-key match. A vetoed member does
not pull in its variants, and a vetoed variant stays out. Other classes are not
affected. Each veto appears in the report with the rows it removed, and a veto
that removed nothing appears too, because it may be stale.

### Read the list

```sql
-- the members of a class, with where each came from
SELECT label, chebi, kegg, hmdb, assignment_source, origin, match_kind
FROM exclusion_metabolite_members WHERE exclusion_class = 'cofactors' ORDER BY label;

-- what the build covered, and what it did not
SELECT kind, exclusion_class, namespace, resolved, fallback_only, unresolved
FROM exclusion_metabolite_report ORDER BY kind, exclusion_class;
```

`origin` is `curated` for the three seed files and `derived` for the ontology. An
entity with both has two rows. In the report, `seed_coverage` counts the seeds that
resolved and lists the ones that did not. `shared_key` lists the variants a key
pulled in. `veto_applied` lists your vetoes.

Every row carries `list_version`, the commit of the three files. A build from
uncommitted edits is stamped `uncommitted:<sha256>` and the report says so, so
commit before you build anything you intend to keep.

## Validation scope

```sh
make test-subsets
```

Tests use disposable PostgreSQL and bounded synthetic fixtures. Current product
parity tests under `packages/postgres/tests` compare complete rows, scientific
semantics, preset definitions and database objects with frozen reference code.
Runner tests cover release identity, locking, durable product checkpoints,
rollback, resume and explicit rebuild. These developer tests do not add a
verification or rebuild phase to production runs.
