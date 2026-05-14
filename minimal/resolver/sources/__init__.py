from __future__ import annotations

from minimal.resolver.sources.chemicals import (
    CHEMICAL_SOURCES,
    build_chemical_identifier_lookup,
    materialize_chemical_sources,
)
from minimal.resolver.sources.pubchem import materialize_pubchem_compound_sdf
from minimal.resolver.sources.proteins import (
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
