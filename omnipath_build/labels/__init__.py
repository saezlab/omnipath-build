"""Stored human-readable entity labels (FR-031, US8 M-Labels).

Every entity gets a best label by a per-entity-type cascade, precomputed at
build (never per request) and stored on ``entity.label`` with the producing rule
recorded in ``entity.label_rule``. This package holds the label builders run in
``derive``:

* gene-based entities → gene symbol (``populate_entity_labels``, T065);
* chemicals → brevity-first name cascade (T064, future);
* lipids → Goslin short names (omnipath-metabo postbuild, T066, future).

A universal identifier fallback guarantees a non-empty label for every entity
(FR-031); the richer per-type rules overwrite it as they land.
"""

from __future__ import annotations

from omnipath_build.labels.entity_labels import (
    EntityLabelStats,
    populate_entity_labels,
)

__all__ = [
    'EntityLabelStats',
    'populate_entity_labels',
]
