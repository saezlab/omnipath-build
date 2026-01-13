"""Ontology registry with preload and lazy-load support."""

import logging
from pathlib import Path

from ontograph.client import ClientOntology

from .config import CORE_ONTOLOGIES, CACHE_DIR, OntologyConfig

logger = logging.getLogger(__name__)


class OntologyRegistry:
    """Manages multiple ontologies with preload and lazy-load support.
    
    Core ontologies are preloaded at startup.
    Other ontologies are loaded on-demand and cached.
    """
    
    def __init__(self, cache_dir: str = CACHE_DIR):
        self._cache_dir = Path(cache_dir)
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._ontologies: dict[str, ClientOntology] = {}
        self._configs: dict[str, OntologyConfig] = dict(CORE_ONTOLOGIES)
    
    def preload_core_ontologies(self) -> None:
        """Preload all core ontologies at startup."""
        for ontology_id, config in CORE_ONTOLOGIES.items():
            if config.preload:
                logger.info(f"Preloading ontology: {ontology_id}")
                try:
                    self._load_ontology(ontology_id, config)
                    logger.info(f"Successfully loaded: {ontology_id}")
                except Exception as e:
                    logger.error(f"Failed to load {ontology_id}: {e}")
    
    def _load_ontology(self, ontology_id: str, config: OntologyConfig) -> ClientOntology:
        """Load an ontology from its source."""
        client = ClientOntology(cache_dir=str(self._cache_dir))
        client.load(source=config.source, backend="pronto")
        self._ontologies[ontology_id] = client
        return client
    
    def get(self, ontology_id: str) -> ClientOntology | None:
        """Get an ontology client by ID.
        
        Returns cached client if available, otherwise loads on-demand.
        Returns None if ontology is not configured.
        """
        # Return cached
        if ontology_id in self._ontologies:
            return self._ontologies[ontology_id]
        
        # Load on-demand if configured
        if ontology_id in self._configs:
            config = self._configs[ontology_id]
            return self._load_ontology(ontology_id, config)
        
        # Try loading from OBO Foundry catalog
        logger.info(f"Attempting to load {ontology_id} from OBO Foundry")
        try:
            config = OntologyConfig(
                source=ontology_id,  # OBO Foundry ID
                description=f"On-demand: {ontology_id}",
                preload=False,
            )
            self._configs[ontology_id] = config
            return self._load_ontology(ontology_id, config)
        except Exception as e:
            logger.error(f"Failed to load {ontology_id} from OBO Foundry: {e}")
            return None
    
    def list_available(self) -> dict[str, str]:
        """List all available ontologies with descriptions."""
        return {
            ontology_id: config.description
            for ontology_id, config in self._configs.items()
        }
    
    def is_loaded(self, ontology_id: str) -> bool:
        """Check if an ontology is currently loaded."""
        return ontology_id in self._ontologies


# Global registry instance
registry = OntologyRegistry()
