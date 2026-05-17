"""Resolver source builders for protein and chemical identifier mappings."""

from __future__ import annotations

from omnipath_build.resolver.sources.chemicals import (
    CHEMICAL_SOURCES,
    build_chemical_identifier_lookup,
    materialize_chemical_sources,
)
from omnipath_build.resolver.sources.pubchem import materialize_pubchem_compound_sdf
from omnipath_build.resolver.sources.proteins import (
    build_protein_identifier_lookup,
    materialize_proteins,
)

__all__ = [
    'CHEMICAL_SOURCES',
    'build_chemical_identifier_lookup',
    'build_protein_identifier_lookup',
    'materialize_chemical_sources',
    'materialize_pubchem_compound_sdf',
    'materialize_proteins',
]
