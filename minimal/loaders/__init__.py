"""External lookup and ontology loaders for minimal."""

from minimal.loaders.ontology import OntologyLoadStats, load_ontology_terms
from minimal.loaders.resolver import ResolverLoadStats, load_resolver_tables

__all__ = [
    'OntologyLoadStats',
    'ResolverLoadStats',
    'load_ontology_terms',
    'load_resolver_tables',
]
