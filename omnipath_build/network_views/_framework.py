"""Declarative network-view framework (Milestone G; presets since cycle 008).

A dataset is described by a :class:`NetworkDefinition` and registered as one
``network_registry`` row. Two generations of definition share that table:

* the **preset** (cycle 008) — metadata over the interaction fact table: which
  sources contribute, which interaction classes and evidence it scopes to, which
  attributes it returns, how it is labelled and curated. It materialises nothing;
  registering it is the whole build step.
* the **matview network** (Milestone G) — metadata plus the curated SQL that
  materialises per-source and combined matviews under its own schema.

The framework manages both uniformly — create the schema and apply the SQL where
there is any, refresh the matviews, and upsert the registry row — so a uniform
API discovers and serves either. Adding a dataset is a definition, never bespoke
framework or API code.

**Transitional columns.** ``schema_name`` and ``combined_relation`` describe a
matview and mean nothing for a preset; they stay nullable while both generations
coexist and are dropped when the last bespoke matview retires (T046). They are
recorded here so they are not left behind as silent dead columns.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from psycopg2 import sql
from psycopg2.extras import Json
import psycopg2.extensions

_SQL_DIR = Path(__file__).with_name('sql')

logger = logging.getLogger(__name__)

# The matview-era registry columns. A preset leaves them NULL; they are dropped
# once the last bespoke matview retires (T046) — see the module docstring.
MATVIEW_ERA_COLUMNS = ('schema_name', 'combined_relation')


@dataclass(frozen=True)
class NetworkDefinition:
    """A dataset: a preset over the fact table, or a matview-backed network.

    The preset fields (data-model §9) describe *what a query for this dataset
    selects and returns*; they carry no SQL:

    ``interaction_class_scope``
        Interaction-class slugs (data-model §8: ``signaling``, ``tf_target``,
        ``ligand_receptor``, …) the preset restricts to; empty means all classes.
        A class is derived from the resource annotations (R18), never from a
        legacy dataset name — a legacy name is preset identity, not a class.
    ``evidence_scope``
        The evidence-type / predicate / confidence filter separating legacy
        datasets that share a class (R8).
    ``default_attributes`` / ``mandatory_attributes``
        Returned when the caller asks for nothing (FR-043), and returned always
        even unrequested (FR-013).
    ``labels``
        Display labels for the preset and its columns.
    ``curation``
        Configurable thresholds and flags — MoA-only, affinity cut-off,
        metabolite-class gate — as config, never inline SQL (FR-016).
    ``attribute_sources``
        Which source supplies each mandatory attribute, carrying the
        interim-vs-Intercell provenance (FR-046).

    ``schema``, ``combined_relation``, ``matviews`` and ``sql_files`` are the
    matview-era fields: a preset leaves them empty.
    """

    name: str
    kind: str
    included_sources: tuple[str, ...] = ()
    interaction_class_scope: tuple[str, ...] = ()
    evidence_scope: Mapping[str, Any] | None = None
    default_attributes: tuple[str, ...] = ()
    mandatory_attributes: tuple[str, ...] = ()
    labels: Mapping[str, Any] | None = None
    curation: Mapping[str, Any] | None = None
    attribute_sources: Mapping[str, Any] | None = None
    # Matview-era fields — retire with the columns above (T046).
    schema: str | None = None
    combined_relation: str | None = None
    matviews: tuple[str, ...] = ()  # refresh order: per-source → combined → annotations
    sql_files: tuple[str, ...] = ()  # applied in order, relative to sql/

    @property
    def is_preset(self) -> bool:
        """True when the dataset is metadata over the fact table (no matview)."""
        return not self.sql_files and not self.matviews

    def sql_text(self) -> str:
        return '\n'.join(
            (_SQL_DIR / name).read_text(encoding='utf-8') for name in self.sql_files
        )


def ensure_network_registry(
    conn: psycopg2.extensions.connection,
    *,
    registry_schema: str = 'public',
) -> None:
    """Create (or extend) the discovery table the network API reads.

    Fresh databases get the full preset table. Databases carrying the Milestone G
    matview descriptor are migrated in place: the preset columns are added, and
    ``schema_name`` / ``combined_relation`` lose their NOT NULL so a preset can
    leave them empty until they are dropped (T046).
    """
    with conn.cursor() as cur:
        cur.execute(
            sql.SQL(
                """
                CREATE TABLE IF NOT EXISTS {}.network_registry (
                  name text PRIMARY KEY,
                  kind text NOT NULL,
                  schema_name text,
                  combined_relation text,
                  included_sources text[] NOT NULL,
                  interaction_class_scope text[],
                  evidence_scope jsonb,
                  default_attributes text[],
                  mandatory_attributes text[],
                  labels jsonb,
                  curation jsonb,
                  attribute_sources jsonb,
                  built_at timestamptz NOT NULL DEFAULT now()
                )
                """
            ).format(sql.Identifier(registry_schema))
        )
        cur.execute(
            sql.SQL(
                """
                ALTER TABLE {}.network_registry
                  ADD COLUMN IF NOT EXISTS interaction_class_scope text[],
                  ADD COLUMN IF NOT EXISTS evidence_scope jsonb,
                  ADD COLUMN IF NOT EXISTS default_attributes text[],
                  ADD COLUMN IF NOT EXISTS mandatory_attributes text[],
                  ADD COLUMN IF NOT EXISTS labels jsonb,
                  ADD COLUMN IF NOT EXISTS curation jsonb,
                  ADD COLUMN IF NOT EXISTS attribute_sources jsonb,
                  ALTER COLUMN schema_name DROP NOT NULL,
                  ALTER COLUMN combined_relation DROP NOT NULL
                """
            ).format(sql.Identifier(registry_schema))
        )
        # The transition is recorded in the database too, so nobody meets these
        # two columns without learning they are on their way out.
        for column in MATVIEW_ERA_COLUMNS:
            cur.execute(
                sql.SQL(
                    'COMMENT ON COLUMN {}.network_registry.{} IS %s'
                ).format(
                    sql.Identifier(registry_schema), sql.Identifier(column)
                ),
                [
                    'Matview-era column: NULL for a preset over the interaction '
                    'fact table. Dropped when the last bespoke matview retires '
                    '(cycle 008, T046).'
                ],
            )
    conn.commit()


def apply_network(
    conn: psycopg2.extensions.connection,
    definition: NetworkDefinition,
) -> None:
    """Create the network's schema + (re)materialise its views from curated SQL.

    A preset materialises nothing — registration is its whole build step.
    """
    if definition.is_preset:
        logger.debug(
            'network-views: %s is a preset over the fact table; nothing to apply',
            definition.name,
        )
        return
    schema_id = sql.Identifier(definition.schema)
    with conn.cursor() as cur:
        cur.execute(
            sql.SQL('CREATE SCHEMA IF NOT EXISTS {}').format(schema_id)
        )
        # Unqualified objects in the curated SQL land in the network schema;
        # public.* (the canonical graph) still resolves.
        cur.execute(
            sql.SQL('SET search_path = {}, public').format(schema_id)
        )
        cur.execute(definition.sql_text())
        cur.execute('RESET search_path')
    conn.commit()


def refresh_network(
    conn: psycopg2.extensions.connection,
    definition: NetworkDefinition,
) -> None:
    """Refresh the network's matviews in dependency order (per-source → combined)."""
    if definition.is_preset:
        logger.debug(
            'network-views: %s is a preset over the fact table; nothing to refresh',
            definition.name,
        )
        return
    schema_id = sql.Identifier(definition.schema)
    with conn.cursor() as cur:
        for matview in definition.matviews:
            cur.execute(
                sql.SQL('REFRESH MATERIALIZED VIEW {}.{}').format(
                    schema_id, sql.Identifier(matview)
                )
            )
    conn.commit()


def register_network(
    conn: psycopg2.extensions.connection,
    definition: NetworkDefinition,
    *,
    registry_schema: str = 'public',
) -> None:
    """Upsert the dataset's row in ``network_registry`` (stamps ``built_at``).

    The whole preset spec travels with the row, so the API resolves a dataset
    from the registry alone.
    """
    with conn.cursor() as cur:
        cur.execute(
            sql.SQL(
                """
                INSERT INTO {}.network_registry
                  (name, kind, schema_name, combined_relation, included_sources,
                   interaction_class_scope, evidence_scope, default_attributes,
                   mandatory_attributes, labels, curation, attribute_sources,
                   built_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now())
                ON CONFLICT (name) DO UPDATE SET
                  kind = EXCLUDED.kind,
                  schema_name = EXCLUDED.schema_name,
                  combined_relation = EXCLUDED.combined_relation,
                  included_sources = EXCLUDED.included_sources,
                  interaction_class_scope = EXCLUDED.interaction_class_scope,
                  evidence_scope = EXCLUDED.evidence_scope,
                  default_attributes = EXCLUDED.default_attributes,
                  mandatory_attributes = EXCLUDED.mandatory_attributes,
                  labels = EXCLUDED.labels,
                  curation = EXCLUDED.curation,
                  attribute_sources = EXCLUDED.attribute_sources,
                  built_at = now()
                """
            ).format(sql.Identifier(registry_schema)),
            [
                definition.name,
                definition.kind,
                definition.schema,
                definition.combined_relation,
                list(definition.included_sources),
                list(definition.interaction_class_scope) or None,
                Json(definition.evidence_scope)
                if definition.evidence_scope is not None
                else None,
                list(definition.default_attributes),
                list(definition.mandatory_attributes),
                Json(definition.labels) if definition.labels is not None else None,
                Json(definition.curation) if definition.curation is not None else None,
                Json(definition.attribute_sources)
                if definition.attribute_sources is not None
                else None,
            ],
        )
    conn.commit()


@dataclass(frozen=True)
class NetworkViewStats:
    applied: tuple[str, ...] = field(default_factory=tuple)


def apply_all(
    conn: psycopg2.extensions.connection,
    definitions: list[NetworkDefinition],
    *,
    registry_schema: str = 'public',
    log=lambda *_: None,
) -> NetworkViewStats:
    """Apply + register every dataset (the build hook). Idempotent."""
    ensure_network_registry(conn, registry_schema=registry_schema)
    applied: list[str] = []
    for definition in definitions:
        log(f'[network-views] apply {definition.name}')
        apply_network(conn, definition)
        register_network(conn, definition, registry_schema=registry_schema)
        applied.append(definition.name)
    return NetworkViewStats(applied=tuple(applied))


def refresh_all(
    conn: psycopg2.extensions.connection,
    definitions: list[NetworkDefinition],
    *,
    registry_schema: str = 'public',
    log=lambda *_: None,
) -> NetworkViewStats:
    """Refresh + re-register every dataset (fast path; views already exist)."""
    refreshed: list[str] = []
    for definition in definitions:
        log(f'[network-views] refresh {definition.name}')
        refresh_network(conn, definition)
        register_network(conn, definition, registry_schema=registry_schema)
        refreshed.append(definition.name)
    return NetworkViewStats(applied=tuple(refreshed))
