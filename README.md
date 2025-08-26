![project-banner](./docs/assets/project-banner-readme.png)

# omnipath_build


[![Tests](https://img.shields.io/github/actions/workflow/status/saezlab/omnipath_build/test.yml?branch=master)](https://github.com/saezlab/omnipath_build/actions/workflows/test.yml)
[![Docs](https://img.shields.io/badge/docs-MkDocs-blue)](https://saezlab.github.io/omnipath_build/)
![Pre-commit](https://img.shields.io/badge/pre--commit-enabled-brightgreen?logo=pre-commit)
![PyPI](https://img.shields.io/pypi/v/omnipath_build)
![Python](https://img.shields.io/pypi/pyversions/omnipath_build)
![License](https://img.shields.io/github/license/saezlab/omnipath_build)
![Issues](https://img.shields.io/github/issues/saezlab/omnipath_build)
![Last Commit](https://img.shields.io/github/last-commit/saezlab/omnipath_build)

## Description

# Database Builder System

A flexible, scalable database construction system that automatically discovers PyPath biological data sources and creates structured PostgreSQL databases through a three-tier data processing pipeline (Bronze → Silver → Gold).

## Overview

This system provides an automated approach to building biological databases by:

1. **On-demand discovery** of PyPath modules and functions
2. **Simple resource addition** with automatic template generation
3. **Processing data through three layers** with increasing refinement and structure

### Architecture

```
PyPath Resources → On-Demand Templates → Bronze → Silver → Gold
                       ↓                   ↓        ↓       ↓
                  Auto-Generation     Raw Data  Clean   Final
                                                 Data   Tables
```

- **Bronze Layer**: Raw data ingestion from PyPath sources stored as Parquet files
- **Silver Layer**: Cleaned, transformed data in PostgreSQL with standardized schema  
- **Gold Layer**: Final analytical tables with deduplication, enrichment, and aggregations

## Quick Start

### Prerequisites

- Python 3.12+
- Docker & Docker Compose
- [uv](https://docs.astral.sh/uv/) package manager (installed automatically)

### Setup

**One-time setup** - installs dependencies, starts PostgreSQL, and generates resource lists:
```bash
make setup
```

**Daily startup** - ensures PostgreSQL is running and shows available databases:
```bash
make start
```

**Create a new database**:
```bash
make new DB=myproject
```

**Run/update an existing database**:
```bash
make run DB=myproject
```

**Clean shutdown**:
```bash
make stop
```

## Core Workflow

### Step 1: Setup Environment

Complete one-time setup (this is done automatically if you use `make setup`):

```bash
# Generate a complete list of available PyPath resources
uv run python omnipath_build/tools/list_pypath_resources.py
```

This creates `pypath_resources.txt` with all 790+ available PyPath functions organized by module, like:

```
## swisslipids (10 functions)
-------------------------------
  swisslipids.swisslipids_lipids             - function
  swisslipids.swisslipids_reactions          - function
  swisslipids.swisslipids_tissues            - function
  ...

## biogrid (2 functions)
------------------------
  biogrid.biogrid_interactions               - function
  biogrid.biogrid_all_interactions           - function
```

### Step 2: Create Database

Create a new database with proper directory structure:

```bash
# Create a new database using Makefile
make new DB=omnipath

# Or manually:
uv run --env-file .env python omnipath_build/database_manager.py init --database omnipath
```

This creates:
```
omnipath_build/databases/omnipath/
├── bronze/data/         # Raw parquet files (auto-generated)
├── silver/
│   ├── tables.yaml              # Silver schema definitions  
│   └── transformation_functions.sql  # Custom SQL functions
├── gold/                # Final transformation SQL scripts
├── resource/            # Resource configurations (auto-generated)
└── metadata/
    └── tables.yaml      # Metadata schema definitions
```

### Step 3: Add Resources with Auto-Generated Templates

Add specific PyPath functions with automatic template generation:

```bash
# Add specific functions (creates templates automatically)
uv run --env-file .env python omnipath_build/database_manager.py add-resources --database omnipath --resources signor.signor_interactions

# Add multiple functions from different modules
uv run --env-file .env python omnipath_build/database_manager.py add-resources --database omnipath --resources biogrid.biogrid_interactions,uniprot_db.all_uniprots

# Add entire module (all functions)
uv run --env-file .env python omnipath_build/database_manager.py add-resources --database omnipath --resources swisslipids
```

This automatically:
- Discovers the PyPath function
- Executes it to inspect output structure
- Generates a complete YAML template with all fields
- Saves it to `omnipath_build/databases/omnipath/resource/modulename.yaml`

Example auto-generated template (`omnipath_build/databases/omnipath/resource/signor.yaml`):
```yaml
# Resource Configuration for signor.signor_interactions
# Database: omnipath

metadata:
  name: '?'
  description: '?'
  
module: signor
functions:
  signor_interactions:
    description: Downloads signaling interactions from SIGNOR database
    kwargs:
      organism: 9606
      raw_records: false
    processing:
      target_table: '?'
      field_mapping:
      - source: source
        target: '?'
      - source: target  
        target: '?'
      - source: effect
        target: '?'
      - source: mechanism
        target: '?'
      - source: pmid
        target: '?'
```

### Step 4: Configure Your Resources

Edit the auto-generated templates to specify target tables and field mappings:

```bash
# Edit resource configurations
vim omnipath_build/databases/omnipath/resource/signor.yaml
vim omnipath_build/databases/omnipath/resource/biogrid.yaml
```

Fill in the `'?'` placeholders:

```yaml
metadata:
  name: 'SIGNOR Interactions'
  description: 'Protein-protein interactions from SIGNOR database'
  
module: signor
functions:
  signor_interactions:
    kwargs:
      organism: 9606
    processing:
      target_table: interactions
      field_mapping:
      - source: source
        target: entity_a
      - source: target
        target: entity_b  
      - source: effect
        target: interaction_type
      - source: mechanism
        target: mechanism
      - source: pmid
        target: pmid
```

### Step 5: Define Database Schema

Define your silver layer tables in `omnipath_build/databases/omnipath/silver/tables.yaml`:

```yaml
interactions:
  entity_a: "VARCHAR(50)"
  entity_b: "VARCHAR(50)" 
  interaction_type: "VARCHAR(50)"
  mechanism: "TEXT"
  pmid: "VARCHAR(20)"
  source_database: "VARCHAR(50)"
  loaded_at: "TIMESTAMP DEFAULT CURRENT_TIMESTAMP"
```

### Step 6: Load and Process Data

Run the complete data pipeline:

```bash
# Load all layers in sequence using Makefile
make run DB=omnipath

# Or manually:
uv run --env-file .env python omnipath_build/database_manager.py load --database omnipath

# Or load specific layers
uv run --env-file .env python omnipath_build/database_manager.py update --database omnipath --layer bronze

# Check status
uv run --env-file .env python omnipath_build/database_manager.py status --database omnipath
```

## Key Features

### 🚀 On-Demand Template Generation
- No pre-generation of 600+ unused templates
- Templates created only when you need them
- Always up-to-date with latest PyPath version

### 🔍 Smart Resource Discovery  
- Automatically executes PyPath functions to discover output structure
- Works with any PyPath function type (regular, partial, namedtuple, dict returns)
- Handles complex modules like swisslipids that were previously undetectable

### 🧩 Incremental Resource Building
```bash
# Add functions one by one to build up module configs
uv run --env-file .env python omnipath_build/database_manager.py add-resources --database mydb --resources swisslipids.swisslipids_lipids
uv run --env-file .env python omnipath_build/database_manager.py add-resources --database mydb --resources swisslipids.swisslipids_reactions

# Result: Single swisslipids.yaml file with both functions
```

### ⚡ Simple Syntax
- **Specific function**: `module.function` (e.g., `swisslipids.swisslipids_lipids`)  
- **Entire module**: `module` (e.g., `swisslipids`)
- **Multiple resources**: Comma-separated list

## Data Processing Layers

### Bronze Layer
- **Purpose**: Raw data ingestion and storage
- **Format**: Parquet files organized by module/function  
- **Location**: `omnipath_build/databases/{db_name}/bronze/data/`
- **Configuration**: Auto-generated, user-customized YAML files

**Example Bronze Processing**:
- Calls `pypath.inputs.signor.signor_interactions(organism=9606)`
- Stores results as `omnipath_build/databases/omnipath/bronze/data/signor/signor_interactions/YYYYMMDD_HHMMSS.parquet`

### Silver Layer  
- **Purpose**: Data cleaning, standardization
- **Schema**: Defined in `omnipath_build/databases/{db_name}/silver/tables.yaml`
- **Transformations**: Custom SQL functions in `transformation_functions.sql`
- **Storage**: PostgreSQL `silver` schema

### Gold Layer
- **Purpose**: Final deduplicated and integrated tables
- **Processing**: SQL scripts in `omnipath_build/databases/{db_name}/gold/`  
- **Storage**: PostgreSQL `gold` schema

## Command Reference

### Essential Makefile Commands
```bash
# Complete setup and daily workflow
make setup                    # One-time setup
make start                    # Daily startup
make new DB=myproject        # Create new database
make run DB=myproject        # Run/update database
make stop                    # Clean shutdown

# Get help
make help                    # Show all available commands
```

### Resource Discovery
```bash
# List all available PyPath resources
uv run python omnipath_build/tools/list_pypath_resources.py

# Search for specific resources
uv run python omnipath_build/tools/list_pypath_resources.py --search protein
uv run python omnipath_build/tools/list_pypath_resources.py --search lipid

# Custom output file
uv run python omnipath_build/tools/list_pypath_resources.py --output my_resources.txt
```

### Manual Database Management
```bash
# Database lifecycle (if not using Makefile)
uv run --env-file .env python omnipath_build/database_manager.py init --database <name>
uv run --env-file .env python omnipath_build/database_manager.py add-resources --database <name> --resources <module.function>
uv run --env-file .env python omnipath_build/database_manager.py load --database <name>
uv run --env-file .env python omnipath_build/database_manager.py status --database <name>
uv run --env-file .env python omnipath_build/database_manager.py validate --database <name>

# Layer-specific operations  
uv run --env-file .env python omnipath_build/database_manager.py update --database <name> --layer bronze
uv run --env-file .env python omnipath_build/database_manager.py load --database <name> --layers bronze silver

# Module-specific bronze updates
uv run --env-file .env python omnipath_build/database_manager.py update --database <name> --layer bronze --module signor
```

### Resource Addition Examples
```bash
# Add specific functions
uv run --env-file .env python omnipath_build/database_manager.py add-resources --database mydb --resources signor.signor_interactions
uv run --env-file .env python omnipath_build/database_manager.py add-resources --database mydb --resources biogrid.biogrid_interactions,uniprot_db.all_uniprots

# Add entire modules
uv run --env-file .env python omnipath_build/database_manager.py add-resources --database mydb --resources swisslipids
uv run --env-file .env python omnipath_build/database_manager.py add-resources --database mydb --resources swisslipids,biogrid

# Mix specific functions and modules
uv run --env-file .env python omnipath_build/database_manager.py add-resources --database mydb --resources signor.signor_interactions,swisslipids,biogrid.biogrid_interactions
```

## Directory Structure

```
omnipath_build/
├── tools/
│   └── list_pypath_resources.py       # Resource discovery
├── loaders/                            # Data processing pipeline
│   ├── bronze_loader.py               # Raw data ingestion
│   ├── silver_loader.py               # Data cleaning & standardization  
│   ├── gold_loader.py                 # Final table creation
│   └── metadata_loader.py             # Metadata management
├── utils/                             # Shared utilities
│   ├── simple_template_generator.py   # On-demand template generation
│   ├── database.py                    # Database connections
│   └── base_loader.py                 # Common loader functionality  
├── databases/                         # Database instances
│   └── {database_name}/
│       ├── bronze/data/               # Raw parquet files (auto-generated)
│       ├── resource/                  # Resource configurations (auto-generated)
│       ├── silver/                    # Schema & transformations
│       ├── gold/                      # Final processing scripts  
│       └── metadata/                  # Metadata definitions
├── database_manager.py               # Unified management interface
├── Makefile                          # Developer commands
└── docker-compose.yaml               # PostgreSQL service
```

## Complete Example: Signaling Database

### Using Makefile (Recommended)
```bash
# 1. One-time setup
make setup

# 2. Create new database
make new DB=signaling_demo

# 3. Add resources (auto-generates templates)
uv run --env-file .env python omnipath_build/database_manager.py add-resources --database signaling_demo --resources signor.signor_interactions,biogrid.biogrid_interactions

# 4. Customize resource configurations  
vim omnipath_build/databases/signaling_demo/resource/signor.yaml
vim omnipath_build/databases/signaling_demo/resource/biogrid.yaml

# 5. Define silver schema
vim omnipath_build/databases/signaling_demo/silver/tables.yaml

# 6. Load data
make run DB=signaling_demo

# 7. Check results (included in make run output, or manually)
uv run --env-file .env python omnipath_build/database_manager.py status --database signaling_demo
```

### Manual Approach
```bash
# 1. Discover available resources
uv run python omnipath_build/tools/list_pypath_resources.py --search signaling

# 2. Initialize database
uv run --env-file .env python omnipath_build/database_manager.py init --database signaling_demo

# 3-7. Follow steps 3-7 from Makefile approach above
```

## Developer Workflow

### Daily Development Cycle

1. **Start your session**:
   ```bash
   make start
   ```
   - Ensures PostgreSQL is running
   - Shows available databases
   - Ready for development

2. **Work with databases**:
   ```bash
   # Create new databases as needed
   make new DB=myproject
   
   # Update existing databases
   make run DB=myproject
   ```

3. **End your session**:
   ```bash
   make stop
   ```
   - Stops PostgreSQL cleanly
   - Cleans up old log files

### Essential Commands Summary

| Command | Purpose | When to Use |
|---------|---------|-------------|
| `make setup` | Complete one-time setup | First time only |
| `make start` | Daily startup | Start of each session |
| `make new DB=name` | Create database | New projects |
| `make run DB=name` | Update database | Run data pipeline |
| `make stop` | Clean shutdown | End of session |
| `make help` | Show all commands | When you need help |

## Advanced Usage

### Custom Transformations

Add SQL functions to `silver/transformation_functions.sql`:
```sql
CREATE OR REPLACE MACRO standardize_protein_id(field) AS 
    UPPER(TRIM(field));

CREATE OR REPLACE MACRO normalize_effect(field, default_value) AS 
    CASE 
        WHEN field IS NULL OR field = '' THEN default_value
        ELSE LOWER(TRIM(field))
    END;
```

Use in field mappings:
```yaml
field_mapping:
- source: gene_symbol
  target: normalized_symbol
  transform: standardize_protein_id
```

### Incremental Updates

```bash
# Update specific data source
uv run --env-file .env python omnipath_build/database_manager.py update --database omnipath --layer bronze --module signor

# Rebuild silver layer after schema changes
uv run --env-file .env python omnipath_build/database_manager.py update --database omnipath --layer silver

# Add new function to existing module
uv run --env-file .env python omnipath_build/database_manager.py add-resources --database omnipath --resources signor.signor_complexes
```

## Troubleshooting

### Common Issues

**"Could not generate template for resource"**: Verify the module.function name exists in `pypath_resources.txt`

**Database connection errors**: Check PostgreSQL is running via `make start` or `docker-compose up -d postgres`

**Configuration validation errors**: Ensure all `'?'` placeholders are filled in resource YAML files

**Data loading failures**: Check PyPath module availability and internet connection for data download

### Logging
Enable debug logging for detailed execution information:
```bash
# Debug logging is enabled by default in make run
make run DB=mydb

# Or manually with verbose flag
uv run --env-file .env python omnipath_build/database_manager.py load --database mydb --log-level DEBUG
```

## Installation

Get started with a single command:

```bash
make setup
```

This handles all dependencies, services, and initial configuration automatically.

## Contributing

Pull requests are welcome. For major changes, please open an issue first
to discuss what you would like to change.

Please make sure to update tests as appropriate.
