"""COSMOS prior-knowledge network, projected from the reaction stars.

One build-time table, ``cosmos_edge``, holding the binary metabolite/enzyme
edges the COSMOS R package consumes: a reactant feeds the enzyme that turns it
over, that enzyme produces the products, and a connector ties each bare
identifier to the per-reaction gene node that carries it.

The step reads the build database and nothing else. The interactions phase has
already assembled every reaction into an N-ary header with its participants in
role, so the projection is a read of that layer rather than a second pass over
the resources.

A reaction the resources call reversible is emitted twice, the second half with
the arrow turned round and a `_rev` gene node of its own. The direction comes
from what the resources published about the event and is never defaulted: a
reaction nobody stated a direction for keeps a null, because directionless in
the source is a different claim from known to run one way.

One thing the formalism asks for is not in the output. The labels carry the
identifier the entity was canonicalised to rather than the ChEBI and UniProt
COSMOS prefers, with the namespace that answered recorded beside each one — a
partial translation would leave the unresolved majority under a label claiming
a namespace it does not hold, and wiring the translation is work of its own.
"""

from __future__ import annotations

from omnipath_build.cosmos.build import (
    TABLE,
    CosmosBuildStats,
    build_cosmos_projection,
    build_id,
    ensure_cosmos_edge_table,
)

__all__ = [
    'TABLE',
    'CosmosBuildStats',
    'build_cosmos_projection',
    'build_id',
    'ensure_cosmos_edge_table',
]
