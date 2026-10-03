"""Selection query service, including domain SQL and result shaping."""

from __future__ import annotations

import logging
from typing import Any


from omnipath_api.serving_index import projected_paths


logger = logging.getLogger(__name__)


class SelectionQueries:
    """Selection queries over the engine storage and shaping contract."""

    def resolve_selection_scope(self, payload=None):
        return self._cached_facets(
            "selection-scope",
            dict(payload or {}),
            None,
            lambda p, _: self._resolve_selection_scope_uncached(p),
        )

    def _resolve_selection_scope_uncached(
        self, payload: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        payload = payload or {}
        seeds = [str(v).strip() for v in (payload.get("entityPks") or []) if str(v).strip()]
        resolved = self.resolve_entity_keys(seeds) if seeds else []
        entity_pks = resolved or seeds
        include_associated = payload.get("includeAssociatedEntities") is not False
        if include_associated and entity_pks:
            paths = self._resolve_relation_paths()
            if paths:
                read_expr = self._read_expr(projected_paths(self.data_root, "relations", paths))
                placeholders = "SELECT unnest(?::VARCHAR[])"
                extra = self._db.execute(
                    f"""
                    SELECT DISTINCT CASE
                        WHEN subject_entity_key IN ({placeholders}) THEN object_entity_key
                        ELSE subject_entity_key
                    END
                    FROM {read_expr}
                    WHERE subject_entity_key IN ({placeholders}) OR object_entity_key IN ({placeholders})
                    """,
                    [entity_pks, entity_pks, entity_pks],
                ).fetchall()
                seen = set(entity_pks)
                for row in extra:
                    if row and row[0] and str(row[0]) not in seen:
                        seen.add(str(row[0]))
                        entity_pks.append(str(row[0]))
        return {
            "entityPks": entity_pks,
            "seedEntityPks": seeds,
            "termEntityPks": [],
            "ontologyTermIds": [str(v) for v in (payload.get("annotationTermIds") or [])],
            "criteriaCount": len(seeds),
            "expandedEntityCount": max(0, len(entity_pks) - len(seeds)),
        }
