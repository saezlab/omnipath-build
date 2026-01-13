# Ontology Service

REST API for querying biological ontologies using [ontograph](https://github.com/saezlab/ontograph).

## Preloaded Ontologies

| Ontology | Terms | Description |
|----------|-------|-------------|
| OmniPath | ~300 | OmniPath controlled vocabulary |
| PSI-MI | ~3,000 | Proteomics Standards Initiative Molecular Interactions CV |
| Gene Ontology | ~45,000 | GO terms (biological process, molecular function, cellular component) |
| UniProt Keywords | ~1,200 | UniProt keyword hierarchy |

Additional ontologies from OBO Foundry are loaded on-demand.

## Quick Start

```bash
# Install dependencies
uv sync

# Run locally
uv run uvicorn ontology_service.main:app --reload --port 8081
```

## API Endpoints

### Health & Discovery
| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Health check |
| GET | `/ontologies` | List available ontologies |

### Term Lookup
| Method | Path | Description |
|--------|------|-------------|
| GET | `/{ontology}/term/{id}` | Get term info (name, definition) |
| POST | `/{ontology}/terms` | Batch term lookup |

### Navigation
| Method | Path | Description |
|--------|------|-------------|
| GET | `/{ontology}/term/{id}/parents` | Direct parents |
| GET | `/{ontology}/term/{id}/ancestors` | All ancestors (`?depth=N`) |
| GET | `/{ontology}/term/{id}/children` | Direct children |
| GET | `/{ontology}/term/{id}/descendants` | All descendants (`?depth=N`) |

### Hierarchy / Tree
| Method | Path | Description |
|--------|------|-------------|
| GET | `/{ontology}/term/{id}/trajectories` | All paths from root to term |
| POST | `/{ontology}/tree` | Merged tree for multiple terms |

## Examples

```bash
# Get PSI-MI term
curl http://localhost:8081/psi_mi/term/MI:0018

# Get GO term ancestors
curl "http://localhost:8081/gene_ontology/term/GO:0008150/ancestors?depth=2"

# Get all paths from root to a term
curl http://localhost:8081/psi_mi/term/MI:0018/trajectories

# Get merged tree for multiple terms (UI grouping)
curl -X POST http://localhost:8081/psi_mi/tree \
  -H "Content-Type: application/json" \
  -d '{"term_ids": ["MI:0018", "MI:0045"]}'
```

## Tree Response

The `/tree` endpoint merges multiple term paths into a single tree with shared ancestors:

```json
{
  "root": {
    "id": "MI:0000",
    "name": "molecular_interaction",
    "distance": -2,
    "children": [
      {
        "id": "MI:0001",
        "name": "interaction_detection_method",
        "distance": -1,
        "children": [...]
      }
    ]
  }
}
```

## Docker

```bash
docker build -t ontology-service .
docker run -p 8081:8081 ontology-service
```
