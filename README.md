![project-banner](./docs/assets/project-banner-readme.png)

# omnipath_build

- [ ] TODO: Add badges to your project.

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
- PostgreSQL (via Docker)
- Required Python packages (see `requirements.txt`)

### Setup

1. **Clone and setup environment**:
```bash
git clone <repository>
cd database-builder
pip install -r requirements.txt
```

2. **Start PostgreSQL**:
```bash
docker-compose up -d postgres
```

3. **Configure environment variables** (optional):
```bash
export POSTGRES_HOST=localhost
export POSTGRES_PORT=5436
export POSTGRES_USER=postgres
export POSTGRES_PASSWORD=""
```

## Core Workflow

### Step 1: Discover Available Resources

List all available PyPath modules and functions:

```bash
# Generate a complete list of available PyPath resources
python tools/list_pypath_resources.py

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

### Step 2: Initialize Database

Create a new database with proper directory structure:

```bash
# Initialize a new database
python database_manager.py init --database omnipath
```

This creates:
```
databases/omnipath/
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
python database_manager.py add-resources --database omnipath --resources signor.signor_interactions

# Add multiple functions from different modules
python database_manager.py add-resources --database omnipath --resources biogrid.biogrid_interactions,uniprot_db.all_uniprots

# Add entire module (all functions)
python database_manager.py add-resources --database omnipath --resources swisslipids
```

This automatically:
- Discovers the PyPath function
- Executes it to inspect output structure
- Generates a complete YAML template with all fields
- Saves it to `databases/omnipath/resource/modulename.yaml`

Example auto-generated template (`databases/omnipath/resource/signor.yaml`):
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
vim databases/omnipath/resource/signor.yaml
vim databases/omnipath/resource/biogrid.yaml
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

Define your silver layer tables in `databases/omnipath/silver/tables.yaml`:

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
# Load all layers in sequence  
python database_manager.py load --database omnipath

# Or load specific layers
python database_manager.py update --database omnipath --layer bronze

# Check status
python database_manager.py status --database omnipath
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
python database_manager.py add-resources --database mydb --resources swisslipids.swisslipids_lipids
python database_manager.py add-resources --database mydb --resources swisslipids.swisslipids_reactions

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
- **Location**: `databases/{db_name}/bronze/data/`
- **Configuration**: Auto-generated, user-customized YAML files

**Example Bronze Processing**:
- Calls `pypath.inputs.signor.signor_interactions(organism=9606)`
- Stores results as `databases/omnipath/bronze/data/signor/signor_interactions/YYYYMMDD_HHMMSS.parquet`

### Silver Layer  
- **Purpose**: Data cleaning, standardization
- **Schema**: Defined in `databases/{db_name}/silver/tables.yaml`
- **Transformations**: Custom SQL functions in `transformation_functions.sql`
- **Storage**: PostgreSQL `silver` schema

### Gold Layer
- **Purpose**: Final deduplicated and integrated tables
- **Processing**: SQL scripts in `databases/{db_name}/gold/`  
- **Storage**: PostgreSQL `gold` schema

## Command Reference

### Resource Discovery
```bash
# List all available PyPath resources
python tools/list_pypath_resources.py

# Search for specific resources
python tools/list_pypath_resources.py --search protein
python tools/list_pypath_resources.py --search lipid

# Custom output file
python tools/list_pypath_resources.py --output my_resources.txt
```

### Database Management
```bash
# Database lifecycle
python database_manager.py init --database <name>
python database_manager.py add-resources --database <name> --resources <module.function>
python database_manager.py load --database <name>
python database_manager.py status --database <name>
python database_manager.py validate --database <name>

# Layer-specific operations  
python database_manager.py update --database <name> --layer bronze
python database_manager.py load --database <name> --layers bronze silver

# Module-specific bronze updates
python database_manager.py update --database <name> --layer bronze --module signor
```

### Resource Addition Examples
```bash
# Add specific functions
python database_manager.py add-resources --database mydb --resources signor.signor_interactions
python database_manager.py add-resources --database mydb --resources biogrid.biogrid_interactions,uniprot_db.all_uniprots

# Add entire modules
python database_manager.py add-resources --database mydb --resources swisslipids
python database_manager.py add-resources --database mydb --resources swisslipids,biogrid

# Mix specific functions and modules
python database_manager.py add-resources --database mydb --resources signor.signor_interactions,swisslipids,biogrid.biogrid_interactions
```

## Directory Structure

```
database-builder/
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
└── docker-compose.yaml               # PostgreSQL service
```

## Complete Example: Signaling Database

```bash
# 1. Discover available resources
python tools/list_pypath_resources.py --search signaling

# 2. Initialize database
python database_manager.py init --database signaling_demo

# 3. Add resources (auto-generates templates)
python database_manager.py add-resources --database signaling_demo --resources signor.signor_interactions,biogrid.biogrid_interactions

# 4. Customize resource configurations  
vim databases/signaling_demo/resource/signor.yaml
vim databases/signaling_demo/resource/biogrid.yaml

# 5. Define silver schema
vim databases/signaling_demo/silver/tables.yaml

# 6. Load data
python database_manager.py load --database signaling_demo

# 7. Check results
python database_manager.py status --database signaling_demo
```

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
python database_manager.py update --database omnipath --layer bronze --module signor

# Rebuild silver layer after schema changes
python database_manager.py update --database omnipath --layer silver

# Add new function to existing module
python database_manager.py add-resources --database omnipath --resources signor.signor_complexes
```

## Troubleshooting

### Common Issues

**"Could not generate template for resource"**: Verify the module.function name exists in `pypath_resources.txt`

**Database connection errors**: Check PostgreSQL is running via `docker-compose up -d postgres`

**Configuration validation errors**: Ensure all `'?'` placeholders are filled in resource YAML files

**Data loading failures**: Check PyPath module availability and internet connection for data download

### Logging
Enable debug logging for detailed execution information:
```bash
python database_manager.py load --database mydb --verbose
```

## Installation

- [ ] TODO: Add installation instructions for your project, if applicable.

```bash
# Example
pip install <name-of-my-project>
```

## Usage

- [ ] TODO: Add usage instructions for your project.

```python
import foobar

# returns 'words'
foobar.pluralize('word')

# returns 'geese'
foobar.pluralize('goose')

# returns 'phenomenon'
foobar.singularize('phenomena')
```

## Contributing

Pull requests are welcome. For major changes, please open an issue first
to discuss what you would like to change.

Please make sure to update tests as appropriate.

- [ ] TODO: add contribution guidelines. All of them can be modified in the mkdocs documentation (./docs/community)

## License

[MIT](https://choosealicense.com/licenses/mit/)

- [ ] TODO: Modify this based on the license you choose.
- [ ] TODO: Modify the LICENSE file based on the license you choose.
