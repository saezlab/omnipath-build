"""Ontology label resolution using ontograph.

This module provides utilities to resolve ontology term accessions to their human-readable labels.

"""
from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

from ontograph.client import ClientOntology

__all__ = ["OntologyLabelResolver", "get_default_resolver"]

logger = logging.getLogger(__name__)


class OntologyLabelResolver:
    """Resolves ontology term accessions to labels using ontograph.

    Maps prefixes (MI, OM, GO, etc.) to ontology clients and provides
    efficient label resolution with caching.
    """

    def __init__(self, cache_dir: str | None = None):
        """Initialize the resolver with ontograph clients.

        Args:
            cache_dir: Directory for ontograph cache. If None, uses default.
        """
        self._clients: dict[str, ClientOntology] = {}
        self._prefix_to_ontology: dict[str, str] = {}
        # Use default cache dir if not specified
        if cache_dir is None:
            cache_dir = str(Path.home() / ".cache" / "ontograph")
        self._cache_dir = cache_dir
        self._initialized = False

    def _initialize(self) -> None:
        """Lazy initialization of ontology clients."""
        if self._initialized:
            return

        logger.info("Initializing ontology clients for label resolution...")

        # Define ontology sources and their prefixes
        # Get the project root (3 levels up from this file)
        project_root = Path(__file__).parent.parent.parent
        omnipath_obo = project_root / "omnipath_build" / "data" / "omnipath_mi.obo"

        ontology_configs = [
            {
                "id": "psi_mi",
                "source": "mi",  # OBO Foundry ID for PSI-MI
                "prefixes": ["MI"],
            },
            {
                "id": "omnipath",
                "source": str(omnipath_obo) if omnipath_obo.exists() else None,
                "prefixes": ["OM"],
            },
            {
                "id": "gene_ontology",
                "source": "go",  # OBO Foundry ID
                "prefixes": ["GO"],
            },
            {
                "id": "human_phenotype",
                "source": "hp",  # OBO Foundry ID for HPO
                "prefixes": ["HP"],
            },
            {
                "id": "uniprot_keywords",
                "source": "keywords",  # OBO Foundry ID for UniProt Keywords
                "prefixes": ["KW"],
            },
        ]

        # Load ontologies
        for config in ontology_configs:
            # Skip if source is None
            if config["source"] is None:
                logger.warning(f"Skipping ontology {config['id']}: source not available")
                continue

            try:
                client = ClientOntology(cache_dir=self._cache_dir)
                client.load(source=config["source"], backend="pronto")
                self._clients[config["id"]] = client

                # Map prefixes to ontology ID
                for prefix in config["prefixes"]:
                    self._prefix_to_ontology[prefix] = config["id"]

                logger.info(f"Loaded ontology: {config['id']} (prefixes: {config['prefixes']})")
            except Exception as e:
                logger.warning(f"Failed to load ontology {config['id']}: {e}")

        self._initialized = True

    def _get_prefix(self, accession: str) -> str | None:
        """Extract prefix from accession (e.g., 'MI' from 'MI:0328')."""
        if ":" in accession:
            return accession.split(":", 1)[0]
        return None

    @lru_cache(maxsize=10000)
    def resolve(self, accession: str) -> str:
        """Resolve accession to 'Label:Accession' format.

        Args:
            accession: Ontology term accession (e.g., 'MI:0328')

        Returns:
            Formatted string 'Label:Accession' or 'Accession:Accession' if label not found
        """
        if not self._initialized:
            self._initialize()

        # Extract prefix and find appropriate ontology
        prefix = self._get_prefix(accession)
        if not prefix or prefix not in self._prefix_to_ontology:
            # No ontology for this prefix, use accession as label
            return f"{accession}:{accession}"

        ontology_id = self._prefix_to_ontology[prefix]
        client = self._clients.get(ontology_id)

        if not client:
            return f"{accession}:{accession}"

        # Try to get term label
        try:
            term = client.get_term(accession)
            if term and term.name:
                return f"{term.name}:{accession}"
        except Exception as e:
            logger.debug(f"Failed to resolve {accession}: {e}")

        # Fallback to accession
        return f"{accession}:{accession}"

    def resolve_bulk(self, accessions: list[str]) -> dict[str, str]:
        """Resolve multiple accessions efficiently.

        Args:
            accessions: List of ontology term accessions

        Returns:
            Dictionary mapping accession to 'Label:Accession' format
        """
        return {acc: self.resolve(acc) for acc in accessions}


# Global resolver instance
_global_resolver: OntologyLabelResolver | None = None


def get_default_resolver() -> OntologyLabelResolver:
    """Get or create the default global resolver instance."""
    global _global_resolver
    if _global_resolver is None:
        _global_resolver = OntologyLabelResolver()
    return _global_resolver
