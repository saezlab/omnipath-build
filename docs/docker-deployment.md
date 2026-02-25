# Docker Deployment Guide

Deployment has moved to the separate presentation repository.

This repository is responsible for **building and exporting data artifacts** only.

## Build and export data (in this repo)

```bash
# Full pipeline (recommended)
make pipeline

# Or explicit export flow
make gold
make generate-obo
make meilisearch
make meilisearch-build-dump DATA_VERSION=v-YYYYMMDD-HHMMSS
make export-entity DATA_VERSION=v-YYYYMMDD-HHMMSS
make export-ontology DATA_VERSION=v-YYYYMMDD-HHMMSS
make export-finalize DATA_VERSION=v-YYYYMMDD-HHMMSS
```

Artifacts are written under:

- `data/releases/<DATA_VERSION>/`
- `data/releases/latest` (symlink)

Key files:

- `entity_identifier.parquet`
- `omnipath_mi.obo`
- `dumps/<meilisearch dump>`
- `.data_version`

## Deploy services (in presentation repo)

Run Docker Compose and deployment commands from the presentation repository, consuming one of the exported versions from `data/releases/`.
