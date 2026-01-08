# Docker Deployment Guide

This guide explains how to deploy the OmniPath application using Docker Compose.

## Architecture

The deployment consists of three services:

1. **omnipath-meilisearch** - Full-text search engine (pre-loaded with data dump)
2. **entity-service** - Rust-based identifier lookup service (reads Parquet files)
3. **next-omnipath** - Next.js frontend

## Prerequisites

- Docker and Docker Compose installed
- Data files generated (see "Building Data Files" below)

## Quick Start

### 1. Build Data Files

If you haven't already generated the data files:

```bash
# Run the full data pipeline
make gold-meilisearch-import

# Or step by step:
make gold                     # Generate global tables
make meilisearch              # Build search parquet files
make meilisearch-import-all   # Import into Meilisearch (needs local instance)
```

### 2. Set Up Data Directory

Copy the required data files to the `data/` directory:

```bash
make docker-data-setup
```

This copies:
- `entity_identifier.parquet` - Entity lookup data
- `search_entities.parquet` - Entity search data
- `search_interactions.parquet` - Interaction search data

### 3. Create Meilisearch Dump

With Meilisearch running locally (containing imported data):

```bash
# Start local Meilisearch first
docker compose up -d omnipath-meilisearch

# Import data
make meilisearch-import-all

# Generate dump
make meilisearch-dump
```

The dump will be saved to `data/dumps/` with a `latest.dump` symlink.

### 4. Build and Start All Services

```bash
# Build Docker images
make docker-build

# Start with pre-loaded Meilisearch dump
make docker-up-fresh
```

Or manually:

```bash
docker compose build
MEILI_IMPORT_DUMP=/dumps/latest.dump docker compose up -d
```

## Environment Variables

Create a `.env` file with required and optional settings:

```bash
# Required
MEILI_MASTER_KEY=your-secure-key-here

# Optional (with defaults)
MEILI_ENV=development
NEXT_PUBLIC_ENVIRONMENT=production
NEXT_PUBLIC_DOMAIN=localhost
RUST_LOG=info
```

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `MEILI_MASTER_KEY` | **Yes** | - | Meilisearch API key |
| `MEILI_ENV` | No | `development` | Meilisearch environment |
| `NEXT_PUBLIC_ENVIRONMENT` | No | `production` | App environment |
| `NEXT_PUBLIC_DOMAIN` | No | `localhost` | Domain for the frontend |
| `RUST_LOG` | No | `info` | Entity service log level |


## Updating Data

When your source data changes:

1. Regenerate the data files:
   ```bash
   make gold
   make meilisearch
   ```

2. Update the data directory:
   ```bash
   make docker-data-setup
   ```

3. Update Meilisearch:
   ```bash
   # Option A: Reimport and create new dump
   docker compose up -d omnipath-meilisearch
   make meilisearch-import-all
   make meilisearch-dump
   docker compose down
   make docker-up-fresh

   # Option B: Restart entity-service only (if only parquet changed)
   docker compose restart entity-service
   ```

## Directory Structure

```
.
├── omnipath-present/               # Presentation layer (services & frontend)
│   ├── data/                       # Mounted into containers (gitignored)
│   │   ├── entity_identifier.parquet  # Entity lookup data
│   │   ├── search_entities.parquet    # Entity search data  
│   │   ├── search_interactions.parquet
│   │   └── dumps/
│   │       ├── *.dump              # Meilisearch dumps
│   │       └── latest.dump -> ...  # Symlink to latest dump
│   ├── docker-compose.yaml
│   ├── entity-service/
│   │   └── Dockerfile
│   └── next-omnipath/
│       └── Dockerfile
├── omnipath_build/                 # Data processing Python package
├── databases/                      # Output location for built data
└── Makefile                        # Build & deployment orchestration
```

## Troubleshooting

### Meilisearch not importing dump

Check that:
1. The dump file exists: `ls -la data/dumps/latest.dump`
2. The file is readable: `file data/dumps/latest.dump`
3. The dump was created with the same Meilisearch version

### Entity service failing to start

Check the parquet file:
```bash
docker compose logs entity-service
# Verify file exists
ls -la data/entity_identifier.parquet
```

### Frontend can't connect to services

Check service health:
```bash
docker compose ps
docker compose logs next-omnipath
```

Verify internal networking:
```bash
docker compose exec next-omnipath wget -qO- http://omnipath-meilisearch:7700/health
docker compose exec next-omnipath wget -qO- http://entity-service:8080/health
```
