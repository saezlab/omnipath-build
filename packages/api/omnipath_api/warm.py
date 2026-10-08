"""Compute the explorer's default views once at startup, so no visitor waits for them."""

from __future__ import annotations

import logging
import threading
import time

logger = logging.getLogger(__name__)


def default_requests():
    """(engine method, arguments) as the explorer's routes pass them, so the cache matches."""
    from omnipath_api.models import ScopedRelationFacetsRequest

    # The explorer's landing page: curated examples, as entities and as groups; and the
    # relation filters' counts without a scope (the taxonomy limit is not part of the key).
    facets = ScopedRelationFacetsRequest(taxonomyLimit=16).model_dump()
    return [
        ("get_entity_examples", {}),
        ("get_scoped_relation_facets", {"payload": facets, "resources": None}),
    ]


def warm(engine):
    for method, kwargs in default_requests():
        started = time.monotonic()
        try:
            with engine.query_scope(), engine.release_scope(engine.releases.default()):
                getattr(engine, method)(**kwargs)
        except Exception:
            logger.exception("Warming %s failed", method)
            continue
        logger.info("Warmed %s in %.1fs", method, time.monotonic() - started)


def start(engine):
    thread = threading.Thread(target=warm, args=(engine,), name="omnipath-warm", daemon=True)
    thread.start()
    return thread
