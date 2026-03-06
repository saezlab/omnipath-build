# Docker Deployment Guide

Deployment has moved to the separate presentation repository.

This repository is responsible for **building and exporting data artifacts** only.

## Build data and import indexes (in this repo)

```bash
# Full pipeline: build data artifacts + import into Meilisearch
make pipeline

# Or run each phase separately:
make pipeline-data          # DAG-based: silver → gold → search parquet
make pipeline-index         # Import search parquet into Meilisearch

# Options via environment variables:
make pipeline-data JOBS=8 TEST_MODE=1
make pipeline-index FULL_REINDEX=1
```

Artifacts are written under `data/outputs/` (managed by the DAG scheduler).

Key files produced by `pipeline-data`:

- Per-source silver parquet (per source directory)
- Combined gold tables (entity identifiers, global tables)
- Search parquet files (entities, interactions, associations, sources)

## Deploy services (in presentation repo)

Run Docker Compose and deployment commands from the presentation repository, consuming one of the exported versions.
