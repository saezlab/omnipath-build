"""Historical network queries and explicit current-main preset registration.

``main`` exports the unchanged main definitions and registry builders (psycopg2).
The main build registers presets over normalized interaction fact tables; its
serving consumer is separate. Historical ``query`` / ``iter_records`` retain
their published-statement response contract only for the historical layout.
"""

from omnipath_postgres.main_compat import network_views as main

from ._definitions import LIANA, METALINKSDB, NETWORKS, REACTIONS
from ._query import iter_records, query
from ._registry import NetworkDefinition, rebuild

__all__ = [
    "NetworkDefinition",
    "NETWORKS",
    "METALINKSDB",
    "LIANA",
    "REACTIONS",
    "rebuild",
    "iter_records",
    "query",
    "main",
]
