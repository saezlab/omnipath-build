"""OmniPath REST API over the Parquet serving engine.

Serves the engine's analytical queries, autocomplete, faceted filtering and binary
exports over HTTP; the engine itself lives in ``omnipath_engine``.
"""

from .server import create_app, run_server, serve

__all__ = [
    "create_app",
    "run_server",
    "serve",
]
