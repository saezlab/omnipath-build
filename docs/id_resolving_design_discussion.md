# ID resolving design discussion

## Context

A proposal came up to replace the current local `id_resolver` in `omnipath_build` with `../omnipath-utils`. We reviewed both systems from a high-level design perspective and considered whether identifier mapping should happen earlier, inside input modules.

## Short conclusion

The current `id_resolver` is closer to the right design for the build pipeline than a direct replacement with `omnipath-utils` would be today.

That does **not** mean `id_resolver` is the final ideal package boundary. Rather:

- the current resolver design is good because it is narrow, deterministic, artifact-backed, and batch-oriented;
- `omnipath-utils` is broader and likely the right long-term home for generic mapping infrastructure;
- `omnipath_build` should still own build-specific canonicalization policy.

A direct replacement would risk mixing up generic ID translation with source-specific entity canonicalization.

## Current `id_resolver`

The current `id_resolver` is small and pipeline-specific. It materializes local parquet mapping tables and resolves identifiers in bulk using Polars joins.

Current scope:

- proteins:
  - reference identifier → primary UniProt
  - secondary UniProt → primary UniProt
- small molecules:
  - ChEBI / HMDB / LipidMaps / SwissLipids → Standard InChI

Important properties:

- local materialized mapping artifacts;
- deterministic build inputs;
- no runtime service dependency;
- batch-friendly parquet/Polars design;
- explicit resolution columns such as `resolved_id`, `resolved_id_type`, `resolution_status`, and `resolution_source`;
- aligned with the current gold canonicalization step.

This makes it a good fit for the build pipeline.

## `omnipath-utils`

`omnipath-utils` is broader. It provides generic ID translation, taxonomy, reflists, orthology, optional database/server mode, and user-facing mapping APIs.

It is better understood as a general utility/service layer answering:

> What mappings exist between biological identifier types?

That is different from the build pipeline question:

> Given this source evidence, what canonical OmniPath entity should this record refer to?

Key mismatches today:

- generic one-to-many translation does not directly encode build canonicalization policy;
- current build uses MI/OM-style identifier labels, while `omnipath-utils` uses utility-level names such as `uniprot`, `hmdb`, `chebi`, `inchi`;
- chemical canonicalization to **Standard InChI** is not yet equivalent;
- DB schema/string-length assumptions may be unsafe for long InChI strings;
- live backend/service calls would weaken reproducibility unless materialized into versioned artifacts first.

## Should mapping happen in input modules?

Mostly no.

Input modules should preserve source truth. They should parse source records and emit source-provided identifiers, attributes, evidence, and provenance.

Input modules may do syntactic normalization, for example:

- strip whitespace;
- parse CURIEs;
- split identifier lists;
- normalize obvious source-specific formatting;
- standardize taxonomy fields.

But they should generally not decide canonical identity, for example:

- `TP53 → P04637`;
- `HMDB0000122 → Standard InChI`.

Reasons:

- canonicalization policy would be duplicated across input modules;
- sources could silently use different resolver versions;
- ambiguity handling would become inconsistent;
- source-native identifiers might be lost too early;
- provenance would become less clear;
- changing resolver policy would require re-running source extraction.

A narrow exception is optional, source-local enrichment where the mapping file is treated as part of that source adapter and provenance is explicit.

## Ideal design from scratch

The clean design separates three concerns:

1. source parsing;
2. identifier translation;
3. entity canonicalization.

### 1. Source parsing

Input modules emit what the source says:

```text
source record
  → source entities
  → source identifiers
  → source attributes
  → evidence/provenance
```

They do not resolve to global canonical entities.

### 2. Versioned mapping artifacts

A central mapping build step produces local, versioned artifacts:

```text
data/mappings/<version>/
  protein/
    uniprot_secondary_to_primary.parquet
    genesymbol_to_uniprot.parquet
    ensembl_to_uniprot.parquet
    entrez_to_uniprot.parquet
  chemical/
    chebi_to_standard_inchi.parquet
    hmdb_to_standard_inchi.parquet
    lipidmaps_to_standard_inchi.parquet
    swisslipids_to_standard_inchi.parquet
  metadata.json
```

These artifacts should be:

- local;
- reproducible;
- versioned;
- batch-queryable;
- accompanied by source/version metadata;
- independent of a running web service.

`omnipath-utils` could be the package that builds these artifacts.

### 3. Resolver

The resolver should be a batch candidate-resolution engine. It takes source identifier rows and annotates possible canonical targets.

Input grain:

```text
entity_pk
entity_type
taxonomy_id
identifier_type
identifier
source
```

Output grain:

```text
entity_pk
identifier_type
identifier
resolved_id
resolved_id_type
resolution_status
resolution_source
evidence_strength/confidence
```

This stage should expose candidate resolutions, not hide ambiguity.

### 4. Canonicalizer

The canonicalizer applies build-specific policy.

For proteins, the canonical target is primary UniProt. Example policy:

- primary UniProt identity is strong evidence;
- secondary UniProt maps to primary;
- Ensembl/Entrez references are strong evidence;
- gene symbols are weaker evidence;
- taxonomy-scoped mappings beat global mappings;
- weak evidence may enrich identifiers but should not veto strong evidence;
- multiple strong targets produce ambiguity rather than arbitrary selection.

For chemicals, the canonical target is Standard InChI. Example policy:

- Standard InChI identity wins;
- source-native mappings that converge to one InChI collapse;
- conflicting InChIs produce ambiguity;
- names/synonyms should not canonicalize unless explicitly allowed.

## Package boundary recommendation

### `omnipath-utils` should own

- generic ID type registry;
- mapping source registry;
- taxonomy registry;
- generic translation APIs;
- optional DB/server mode;
- versioned mapping artifact builders.

### `omnipath_build` should own

- source extraction;
- resolver adapter to mapping artifacts;
- build-specific canonicalization policy;
- ambiguity/conflict reports;
- gold table construction.

In other words:

- `omnipath-utils` answers: **what mappings exist?**
- `omnipath_build` answers: **what canonical entity should this source evidence become?**

## Migration direction

The recommended path is not to replace `id_resolver` directly, but to evolve toward a clearer layered design:

1. keep the current `resolve_identifier_frame`-style resolver contract stable;
2. add an adapter between build identifier labels and `omnipath-utils` ID type names;
3. compare current resolver output against `omnipath-utils`-derived mappings source by source;
4. fix gaps, especially Standard InChI support and long identifier handling;
5. move generic mapping artifact construction upstream into `omnipath-utils` if appropriate;
6. keep canonicalization policy in `omnipath_build`.

## Final assessment

The current `id_resolver` has the better design shape for the build pipeline because it is:

- narrow;
- deterministic;
- artifact-backed;
- batch-oriented;
- explicit about resolution status;
- separated from input parsing;
- close to canonicalization but not embedded in every input module.

The long-term ideal is to preserve these properties while letting `omnipath-utils` provide the shared, generic mapping infrastructure underneath.
