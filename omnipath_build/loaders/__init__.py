"""External lookup and ontology loaders for omnipath_build."""

from omnipath_build.loaders.ontology import OntologyLoadStats, load_ontology_terms
from omnipath_build.loaders.resolver import (
    ResolverLoadStats,
    load_resolver_sources,
    load_resolver_tables,
)

__all__ = [
    'OntologyLoadStats',
    'ResolverLoadStats',
    'load_ontology_terms',
    'load_resolver_sources',
    'load_resolver_tables',
]
