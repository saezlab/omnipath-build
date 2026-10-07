"""Compute the explorer's default views once at startup, so no visitor waits for them."""

from __future__ import annotations

import logging
import threading
import time

logger = logging.getLogger(__name__)


def default_requests():
    """(engine method, arguments) as the explorer's routes pass them, so the cache matches."""
    from omnipath_api.models import EntityGroupsRequest

    # EntityGroups.svelte without a query: automatic grouping, one member per card.
    groups = EntityGroupsRequest(strategy="auto", member_limit=1).model_dump()
    return [("search_entity_groups", groups)]


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
