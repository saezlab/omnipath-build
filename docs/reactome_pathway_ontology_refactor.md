# Reactome pathway ontology refactor

## Summary

This refactor changes how Reactome pathways are handled in the build pipeline.

Previously, Reactome pathways were being modeled inside `inputs_v2` as entity records. That approach was not aligned with the intended architecture, where:

- pathways are ontology concepts,
- reactions / controls / complexes / interactions are mechanistic objects,
- proteins / metabolites / other constituents can carry pathway annotations,
- ontology term definitions and hierarchy are handled by the ontology service rather than the silver/entity stream.

The updated implementation removes Reactome pathway entities from the `inputs_v2` output and instead does two things:

1. attaches Reactome pathway identifiers directly to mechanistic records and their constituents as ontology-style term annotations, and
2. exports a dedicated Reactome pathway OBO file that can be loaded by `../omnipath-present/api-service` or other ontology tooling.

---

## Design goal

The target model is:

- **Reactome pathways** = ontology terms, handled separately from the main entity stream
- **Reactome reactions / controls / grouped controllers** = silver-layer entities
- **Participants / controller members / controlled objects** = regular members with attached pathway term accessions
- **Reactome pathway hierarchy** = exported as OBO and loaded into the ontology service

This means there should **not** be a pathway entity dataset emitted from `pypath/pypath/inputs_v2/reactome.py`.

Instead, pathway IDs should be used the same way other controlled vocabulary terms are used: as term accessions attached to entities.

---

## Files changed

### 1. `pypath/pypath/inputs_v2/reactome.py`

#### What changed

This file was reworked so that Reactome no longer emits a `pathways` dataset as silver entities.

#### Before

The module exposed four datasets:

- `reactions`
- `pathways`
- `controls`
- `control_groups`

The `pathways` dataset was mapped to `EntityTypeCv.PATHWAY` / later briefly `EntityTypeCv.CV_TERM` during an intermediate implementation.

#### After

The resource now exposes only:

- `reactions`
- `controls`
- `control_groups`

The `pathways=Dataset(...)` block was removed from the exported resource.

#### Annotation strategy

Mechanistic records now receive Reactome pathway annotations as:

- `IdentifierNamespaceCv.CV_TERM_ACCESSION`

This mirrors the same general pattern used elsewhere for ontology-like annotations, such as GO term attachment in `inputs_v2/uniprot.py`.

#### Where pathway term accessions are attached

##### Reactions

At the reaction entity level:

- `pathway_term_accession`

At participant member entity level:

- `participant_pathway_term_accession`

##### Controls

At the control entity level:

- `pathway_term_accession`

At controller member entity level:

- `controller_pathway_term_accession`

At controlled entity level:

- `controlled_pathway_term_accession`

##### Control groups

At the controller-group entity level:

- `controller_pathway_term_accession`

At controller member entity level:

- `controller_member_pathway_term_accession`

So the silver output keeps the mechanistic objects and their constituents, but pathways are represented only as attached term accessions, not as emitted entities.

---

### 2. `pypath/pypath/inputs_v2/parsers/reactome.py`

This is where most of the logic lives.

#### Cache version bump

The parser cache version was bumped:

- from `_CACHE_VERSION = 3`
- to `_CACHE_VERSION = 4`

This ensures stale pickled pathway/control/reaction records from the old structure do not get silently reused.

#### Internal pathway parsing retained

Even though pathways are no longer emitted as silver entities, pathway parsing was intentionally kept in the parser.

That internal pathway parsing is still needed for two reasons:

1. to generate pathway term annotations for mechanistic objects and their members
2. to export the standalone Reactome pathway OBO

#### Added pathway membership index

A new internal index was introduced:

- `_build_pathway_membership_index(...)`

This function scans Reactome BioPAX pathways and builds a mapping from arbitrary entity/process URIs to the pathways they belong to.

It collects pathway membership from:

- `BP.pathwayComponent`
- `BP.pathwayOrder -> BP.stepProcess`

#### Added ancestor propagation

The same function also detects pathway-to-pathway nesting and constructs a parent map for child pathways.

This allows transitive propagation of pathway annotations.

That means if:

- reaction `R1` is in child pathway `P_child`, and
- `P_child` is part of parent pathway `P_parent`,

then `R1` will be annotated with both:

- `P_child`
- `P_parent`

This is important because downstream ontology-aware filtering often expects annotations to include broader parent context, not just the most specific direct pathway.

#### Added pathway accession extraction helper

A helper was added:

- `_pathway_term_accessions(...)`

This converts the pathway membership index for a given entity URI into a semicolon-delimited list of Reactome stable IDs.

These stable IDs are then attached as `CV_TERM_ACCESSION` annotations in the mapper layer.

#### Reaction records updated

`_iterate_reactions(...)` now accepts the pathway index and computes:

- `pathway_term_accession`

That field is attached to the reaction itself.

Each participant is also assigned:

- `pathway_term_accession`

which is later exposed as:

- `participant_pathway_term_accession`

in the flattened raw record.

So both the reaction and its participating molecules inherit pathway term annotations.

#### Control records updated

`_iterate_controls(...)` now also accepts the pathway index and computes:

- `pathway_term_accession`

That annotation is attached to:

- the control interaction itself
- the controller entity
- the controlled entity
- any controller members created from `memberPhysicalEntity`

These are flattened into fields consumed by `inputs_v2/reactome.py`.

#### Control-group records updated

`_iterate_control_groups(...)` was updated to pass through the pathway term annotations derived during control parsing.

This ensures grouped controllers and their members also carry Reactome pathway annotations.

#### Pathway records retained for OBO generation

`_iterate_pathways(...)` was kept, but its role changed.

It now acts as an internal ontology export source rather than an entity-emission source.

The pathway raw records now expose fields more suitable for ontology export:

- `display_name`
- `synonyms`
- `reactome_stable_id`
- `reactome_id`
- `go`
- `ncbi_tax_id`
- `definition`
- `comments`
- child pathway information:
  - `child_pathway_display_name`
  - `child_pathway_reactome_stable_id`
  - `child_pathway_uri`
  - `child_pathway_step_order`

This structure makes it easy to turn Reactome pathways into OBO terms with hierarchy.

#### Controlled pathway typing tweak

In controls, if the controlled object is itself a pathway, it is now typed internally as:

- `EntityTypeCv.CV_TERM`

rather than `EntityTypeCv.PATHWAY`

This reflects the ontology interpretation of pathways better than treating them as mechanistic objects.

---

### 3. `pypath/scripts/export_reactome_pathway_obo.py`

A new export script was added to generate a standalone Reactome pathway OBO.

#### Purpose

This script creates an ontology file that can be loaded into the ontology service instead of emitting Reactome pathways as silver entities.

#### Data source

The script reuses existing Reactome parsing logic:

- `pypath.inputs_v2.reactome.download`
- `pypath.inputs_v2.parsers.reactome._raw(..., data_type='pathways')`

This avoids duplicating BioPAX traversal logic.

#### What the script exports

For each Reactome pathway term, the OBO includes:

- `id:` = Reactome stable ID, e.g. `R-HSA-109581`
- `name:` = Reactome pathway display name
- `def:` = pathway definition/description when available
- `synonym:` = exact synonyms from Reactome names
- `comment:` = authored / reviewed / edited comment lines
- `xref:` values such as:
  - `Reactome:<internal_id>`
  - `GO:<go_id>`
  - `NCBITaxon:<taxon_id>`
- hierarchical relations as:
  - `relationship: part_of <parent_pathway_id>`

#### Hierarchy encoding

The script exports parent-child pathway relationships using OBO `relationship: part_of` lines.

This choice fits Reactome pathway nesting well because the relationship is conceptual containment rather than strict class subsumption.

#### Additional formatting details

The script also writes a small OBO header, including:

- `format-version: 1.2`
- `ontology: reactome_pathways`
- `default-namespace: reactome_pathways`
- a `part_of` typedef block

#### Validation

The script was executed successfully and produced:

- `2848` Reactome pathway terms

A sample exported term looked like:

```obo
[Term]
id: R-HSA-109581
name: Apoptosis
xref: Reactome:109581
xref: GO:0006915
xref: NCBITaxon:9606
relationship: part_of R-HSA-5357801 ! Programmed Cell Death
```

And more detailed pathways include definitions, synonyms, and comments.

---

### 4. `Makefile`

A convenience target was added:

- `generate-reactome-obo`

It runs:

```bash
uv run python pypath/scripts/export_reactome_pathway_obo.py omnipath_build/data/reactome_pathways.obo
```

This makes it straightforward to regenerate the Reactome pathway ontology file without having to remember the exact command.

---

## Final behavior after refactor

### Silver/entity output

Reactome now contributes:

- reactions
- controls
- control groups

These records carry Reactome pathway annotations as `CV_TERM_ACCESSION` values.

Reactome does **not** contribute pathway entities to the silver layer.

### Ontology output

Reactome pathways are exported separately as:

- `omnipath_build/data/reactome_pathways.obo`

This file is intended for loading by the ontology service.

---

## Why this is better

This structure cleanly separates ontology and mechanism.

### 1. Pathways are represented as concepts, not mechanistic entities

Reactome pathways are better understood as ontology terms:

- they have stable accessions
- names and definitions
- synonyms
- parent/child hierarchy
- xrefs to GO and taxonomy

That is ontology behavior, not ordinary entity behavior.

### 2. Mechanistic objects stay mechanistic

Reactions and controls remain the primary biological process objects in the data model.

They are not forced into awkward membership relationships with pathway entities. Instead, they simply carry pathway annotations.

### 3. Constituents inherit pathway context

Because pathway terms are attached to participants, controllers, controlled objects, and controller members, lower-level molecules can also be filtered or interpreted in pathway context.

### 4. Ontology service can resolve labels and hierarchy centrally

This matches the architecture in `../omnipath-present/api-service`, where ontology content is loaded separately and used for:

- term resolution
- ancestor/descendant traversal
- tree exploration
- label and definition lookup

This avoids duplicating pathway ontology content into every entity record.

---

## Commands

### Regenerate the Reactome pathway OBO

```bash
make generate-reactome-obo
```

or directly:

```bash
uv run python pypath/scripts/export_reactome_pathway_obo.py omnipath_build/data/reactome_pathways.obo
```

### Validate syntax of changed Python files

```bash
uv run python -m py_compile \
  pypath/pypath/inputs_v2/reactome.py \
  pypath/pypath/inputs_v2/parsers/reactome.py \
  pypath/scripts/export_reactome_pathway_obo.py
```

---

## Next logical step

The next step is to register `reactome_pathways.obo` in the ontology service configuration in:

- `../omnipath-present/api-service`

so the frontend and API can resolve Reactome pathway IDs just like GO / MI / OmniPath terms.

That would complete the architecture:

- silver/search data contains Reactome pathway accessions as annotations
- ontology service provides Reactome pathway labels, definitions, parents, children, and tree traversal

---

## Notes

During development there was an intermediate implementation that briefly treated pathways as `CV_TERM` entities inside `inputs_v2`. That was intentionally superseded.

The final state is:

- **no emitted pathway dataset in Reactome inputs_v2**
- **yes exported Reactome pathway OBO**
- **yes direct Reactome pathway term annotations on mechanistic and constituent records**
