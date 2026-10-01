# Main-compatible PostgreSQL migration on nicesrv

The Parquet outputs, upstream resolution/reference/cache, independent resource updates and separate monthly PostgreSQL release schedule are unchanged. The active PostgreSQL implementation now reuses main9f9bb709c764's physical schema, dictionaries, source partitions, keys/indexes, downstream derivations and product implementations, with explicit Biolink and published-identity adapters. Nested record JSON and raw source payloads are excluded.

Commitd0a1c80c33699353a589e06b957ad49960f01ef2 is pushed on parquet-migration. The frozen main reference remains untracked at main-reference/9f9bb709c764. The exact pypath CV oracle is privately pinned to main's gitlink33f37fbaab59993d24f5c32bb4b3e7587085bcf4; migration pypath is unchanged.

## Completed pilot milestone

Two tiny synthetic Parquet pilots exercised DuckDB staging,20COPYtables, restored main constraints/indexes, main shared derivations and all three product commits. Initial failures exposed manifest cost shaping and bitmap extension placement; both were corrected and resumed from PostgreSQL without repeated COPY. The second completed pilot verified that product contents, subset_build_metadata and parquet_phase become visible together; selected-product rebuild retained the others. See build-log.md for actual phase timings and private report paths. Product science is established by nonempty independent fixtures, rather than the empty products in this tiny orchestration pilot.

Local focused suite:393passed,326explicit server tests skipped,5.06s. Guarded private suite:110passed,7.78s; final exact-main oracle rerun15passed,3.27s. Coverage includes executed types/constraints/partitions/index operator classes/predicates/statistics/views/functions, nullable source facts, merged reactions/stoichiometry/transport, measurements/publications, ontology closure/labels, source-local miRBase maturation including shared graph claims, complete COSMOS rows/stats, label/display identities, two-schema bitmap use and durable checkpoint behavior. A bounded all-five-source MetSigDB oracle is being completed while the full load runs. These are development checks, not a mandatory full-release verification/rebuild/rollback phase.

## Full load in progress

The single builder started2026-10-01T20:25:13.932388+00:00 as omnipath-migration-main-aligned-20261001.service. Destination aligned_full_20261001 in the new private PostgreSQL18 at127.0.0.1:5441; release2026.9.30.4,46resources, canonical SHA25606f55112b371055b9b539c27eb7886d403b0131098393d1bafe4723ce006c03e. DuckDB2threads/2GB, builder5GiB/2CPU, complete base and product checkpoints. No verifier or rollback-rebuild job is queued. Runtime files and provenance were verified before launch.

At20:27UTC it was preparing existing published arrays, with no committed base yet. Initial free disk560,904,417,280bytes. The15-minute monitor is active. Full completion, product sample results, phase durations and final storage remain pending.

The authorized cleanup removed only the old private full_20260930 schema, freeing192.47GiB. Its DDL/report backups remain under /root/projects/omnipath-migration/main-alignment-20261001. Both original pilot schemas, all artifacts/cache/reference and production/prototype services were preserved. New PostgreSQL18 uses a separate container and volume.

## Declared differences and remaining boundaries

Published identities have explicit status5/published, rather than fabricated original resolver flags. Source-scoped name identities and source-owned taxonomy/annotation claims remain recoverable through narrow companions. MetSigDB retains the approved published-chemical policy. Biolink vocabulary and supported role/quantity/publication semantics are mapped to main's downstream model.

Original occurrence trees, matched/reason diagnostics, old gene anchoring and some precise classification/role/transport/definition distinctions are absent from the unchanged artifacts. They are documented in postgres-parquet-column-map.md and postgres-main-parity-checklist.md and exposed as unavailable capabilities. No downstream resolver or resource rebuild repairs these by changing the input contract. Historical network query helpers are explicit historical readers; main presets expose the exact registry/builders and normalized fact tables for the separate main serving consumer. The existing Parquet API/web deployment is untouched.
