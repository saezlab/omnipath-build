"""Declarative network-view framework (Milestone G).

A specialized interaction network is described by a :class:`NetworkDefinition`
(metadata + the curated SQL that materialises it). The framework manages every
network's lifecycle uniformly — create its schema, apply its SQL (idempotent
``DROP … IF EXISTS`` / ``CREATE``), refresh its matviews, and register it in
``network_registry`` — so the views survive a fresh rebuild with no manual
``psql -f`` and a uniform API can discover/serve them.

The per-source / combined matview SQL stays curated (reviewed, correct) and is
applied under the network's schema via ``search_path``; the declarative bits
(schema, included sources, combined contract, refresh order) live in the
definition and drive registration + the API. Adding a network is a definition
plus its SQL — no bespoke framework or API code.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from psycopg2 import sql
from psycopg2.extras import Json
import psycopg2.extensions

_SQL_DIR = Path(__file__).with_name('sql')


@dataclass(frozen=True)
class NetworkDefinition:
    """A specialized network: metadata + the curated SQL that materialises it."""

    name: str
    kind: str
    schema: str
    included_sources: tuple[str, ...]
    combined_relation: str
    matviews: tuple[str, ...]  # refresh order: per-source → combined → annotations
    sql_files: tuple[str, ...]  # applied in order, relative to sql/

    def sql_text(self) -> str:
        return '\n'.join(
            (_SQL_DIR / name).read_text(encoding='utf-8') for name in self.sql_files
        )


def ensure_network_registry(
    conn: psycopg2.extensions.connection,
    *,
    registry_schema: str = 'public',
) -> None:
    """Create the discovery table the network API reads."""
    with conn.cursor() as cur:
        cur.execute(
            sql.SQL(
                """
                CREATE TABLE IF NOT EXISTS {}.network_registry (
                  name text PRIMARY KEY,
                  kind text NOT NULL,
                  schema_name text NOT NULL,
                  combined_relation text NOT NULL,
                  included_sources text[] NOT NULL,
                  built_at timestamptz NOT NULL DEFAULT now()
                )
                """
            ).format(sql.Identifier(registry_schema))
        )
    conn.commit()


def apply_network(
    conn: psycopg2.extensions.connection,
    definition: NetworkDefinition,
) -> None:
    """Create the network's schema + (re)materialise its views from curated SQL."""
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
    """Upsert the network's row in ``network_registry`` (stamps ``built_at``)."""
    with conn.cursor() as cur:
        cur.execute(
            sql.SQL(
                """
                INSERT INTO {}.network_registry
                  (name, kind, schema_name, combined_relation, included_sources,
                   built_at)
                VALUES (%s, %s, %s, %s, %s, now())
                ON CONFLICT (name) DO UPDATE SET
                  kind = EXCLUDED.kind,
                  schema_name = EXCLUDED.schema_name,
                  combined_relation = EXCLUDED.combined_relation,
                  included_sources = EXCLUDED.included_sources,
                  built_at = now()
                """
            ).format(sql.Identifier(registry_schema)),
            [
                definition.name,
                definition.kind,
                definition.schema,
                definition.combined_relation,
                list(definition.included_sources),
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
    """Apply + register every network (the build hook). Idempotent."""
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
    """Refresh + re-register every network (fast path; views already exist)."""
    refreshed: list[str] = []
    for definition in definitions:
        log(f'[network-views] refresh {definition.name}')
        refresh_network(conn, definition)
        register_network(conn, definition, registry_schema=registry_schema)
        refreshed.append(definition.name)
    return NetworkViewStats(applied=tuple(refreshed))
