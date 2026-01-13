"""Configuration for ontology sources."""

import os
from dataclasses import dataclass


@dataclass
class OntologyConfig:
    """Configuration for an ontology source."""
    source: str  # File path, URL, or OBO Foundry ID
    description: str
    preload: bool = False  # If True, load at startup


# Data directory for local OBO files
DATA_DIR = os.getenv("ONTOLOGY_DATA_DIR", "./data")

# Core ontologies - preloaded at startup
CORE_ONTOLOGIES: dict[str, OntologyConfig] = {
    "omnipath": OntologyConfig(
        source=f"{DATA_DIR}/omnipath_mi.obo",
        description="OmniPath extended PSI-MI CV (combined ontology)",
        preload=True,
    ),
    "gene_ontology": OntologyConfig(
        source="go",  # OBO Foundry ID
        description="Gene Ontology",
        preload=True,
    ),
    "uniprot_keywords": OntologyConfig(
        source="https://rest.uniprot.org/keywords/stream?format=obo&query=(*)",
        description="UniProt Keywords",
        preload=True,
    ),
}

# Cache directory for downloaded ontologies
CACHE_DIR = os.getenv("ONTOLOGY_CACHE_DIR", "./cache")
