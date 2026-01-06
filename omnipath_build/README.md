# omnipath_build

A Python package for building the OmniPath 2.0 knowledge graph database. This package orchestrates the data pipeline from raw source data through normalized tables to searchable indexes.

## Architecture

```
omnipath_build/
├── cli/                  # Command-line interface
│   └── commands.py       # Main CLI entry point (silver, gold, postgres)
├── loaders/              # Data loading/transformation pipelines
│   ├── silver.py         # Silver layer: raw → normalized parquet
│   └── gold.py           # Gold layer: build cross-source tables
├── gold/                 # Gold table builders (modular steps)
│   ├── build_local_tables.py       # Per-source entity processing
│   ├── build_entity_identifiers.py # Cross-source entity resolution
│   └── build_global_tables.py      # Global table aggregation
├── search/               # Meilisearch integration
│   ├── importer.py       # Import data into Meilisearch
│   └── meilisearch.py    # Index settings configuration
├── search_builder/       # Build search-optimized documents
│   ├── build_search_entities.py      # Entity search documents
│   ├── build_search_interactions.py  # Interaction search documents
│   └── schema.py         # CV term hierarchy builder
├── utils/                # Shared utilities
│   ├── path_manager.py   # Centralized path management
│   ├── logging_utils.py  # Logging decorators
│   └── database.py       # Database utilities
└── _archive/             # Archived/unused code
    └── postgres_loader.py  # PostgreSQL loader (for future use)
```

## Data Pipeline

The pipeline follows a medallion architecture:

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         DATA PIPELINE                                   │
├──────────────┬──────────────┬──────────────┬──────────────┬────────────┤
│   SOURCES    │    SILVER    │     GOLD     │    SEARCH    │   OUTPUT   │
│              │              │              │              │            │
│  pypath      │  Normalized  │  Entity      │  Meilisearch │  Next.js   │
│  inputs_v2   │  parquet     │  resolution  │  documents   │  frontend  │
│              │  per-source  │  cross-src   │              │            │
└──────────────┴──────────────┴──────────────┴──────────────┴────────────┘
```

### 1. Silver Layer (`loaders/silver.py`)

Discovers and processes resource generators from `pypath.inputs_v2`:

- Dynamically discovers `Resource` and `Dataset` objects
- Transforms raw data into normalized `Entity` records
- Writes per-source parquet files to `databases/omnipath/data/<source>/`

**Key functions:**
- `discover_resources()` - Find all resource generators
- `process_resource_function()` - Stream records to parquet
- `run_silver_loader()` - Full discovery and processing workflow

### 2. Gold Layer (`loaders/gold.py`)

Builds cross-source tables with entity resolution:

**Step 1: Local Tables** (`gold/build_local_tables.py`)
- Process each source independently
- Create normalized entity, identifier, membership tables
- Handle entity instances and annotations

**Step 2: Entity Identifiers** (`gold/build_entity_identifiers.py`)
- Graph-based equivalence detection (UnionFind)
- Merge entities across sources by InChI, InChIKey, UniProt
- Create mapping from (source_id, local_id) → global entity_id

**Step 3: Global Tables** (`gold/build_global_tables.py`)
- Join local tables with entity mappings
- Aggregate evidence across sources
- Write final parquet tables to `databases/omnipath/output/`

### 3. Search Layer

**Search Document Builders** (`search_builder/`)

Build denormalized documents optimized for Meilisearch:
- `build_search_entities.py` - Entity search with names, identifiers, relationships
- `build_search_interactions.py` - Interaction search with evidence, directions, signs

**Meilisearch Import** (`search/importer.py`)

Import parquet files into Meilisearch using the Rust-based importer:
- Configurable index settings (searchable, filterable attributes)
- Batch import with progress tracking

## CLI Usage

All commands are accessed via the unified CLI:

```bash
# Silver layer
uv run -m omnipath_build.cli.commands silver --source <source>
uv run -m omnipath_build.cli.commands silver --list  # List available sources

# Gold layer
uv run -m omnipath_build.cli.commands gold
uv run -m omnipath_build.cli.commands gold --step local_tables
uv run -m omnipath_build.cli.commands gold --step entity_identifiers
uv run -m omnipath_build.cli.commands gold --step global_tables

# PostgreSQL (archived, via _archive/)
uv run -m omnipath_build.cli.commands postgres --postgres-uri <uri>

# Meilisearch import
uv run -m omnipath_build.search.importer --dataset entities
uv run -m omnipath_build.search.importer --dataset interactions
uv run -m omnipath_build.search.importer --dataset both
```

## Makefile Targets

```bash
make silver SOURCE=<source>    # Process a specific source
make silver-test SOURCE=<src>  # Test mode (100k records max)
make gold                      # Build all gold tables
make meilisearch              # Build search parquet files
make meilisearch-import-all   # Import to Meilisearch
```

## Directory Structure

```
databases/omnipath/
├── data/                     # Silver layer output (per-source)
│   ├── reactome/
│   │   ├── resource.parquet
│   │   ├── reactions.parquet
│   │   └── pathways.parquet
│   └── uniprot/
│       └── ...
├── output/                   # Gold layer output (cross-source)
│   ├── entity.parquet
│   ├── entity_identifiers.parquet
│   ├── interaction.parquet
│   ├── membership.parquet
│   └── ...
└── local_tables/             # Intermediate per-source tables
    └── ...
```

## Dependencies

- **polars** - Fast DataFrame operations
- **pyarrow** - Parquet file I/O
- **pypath-omnipath** - Source data generators (submodule)
- **download-manager** - Download management (submodule)

## Development

```bash
# Install dependencies
uv sync

# Run tests
make silver-test SOURCE=reactome

# Lint
uv run ruff check omnipath_build/
```
