"""Release-scoped network recipes; registration never owns a transaction."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from psycopg import sql
from psycopg.types.json import Jsonb


@dataclass(frozen=True)
class NetworkDefinition:
    name: str
    kind: str
    included_sources: tuple[str, ...] = ()
    interaction_class_scope: tuple[str, ...] = ()
    evidence_scope: dict[str, Any] | None = None
    default_attributes: tuple[str, ...] = ()
    mandatory_attributes: tuple[str, ...] = ()
    labels: dict[str, Any] | None = None
    curation: dict[str, Any] | None = None
    attribute_sources: dict[str, Any] | None = None
    collapse_mode: str = "endpoints"
    license_scope: dict[str, Any] | None = None
    grain: str = "interaction"
    composition: dict[str, Any] | None = None

    @property
    def is_preset(self) -> bool:
        return True

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


_DEFINITION_COLUMNS = (
    "name",
    "kind",
    "included_sources",
    "interaction_class_scope",
    "evidence_scope",
    "default_attributes",
    "mandatory_attributes",
    "labels",
    "curation",
    "attribute_sources",
    "collapse_mode",
    "license_scope",
    "grain",
    "composition",
)
_JSON_COLUMNS = {
    "evidence_scope",
    "labels",
    "curation",
    "attribute_sources",
    "license_scope",
    "composition",
}


def register(conn, schema: str, definitions) -> None:
    """Upsert presets in the loaded release schema without committing."""
    namespace = sql.Identifier(schema)
    with conn.cursor() as cur:
        cur.execute(
            sql.SQL("""
            CREATE TABLE IF NOT EXISTS {}.network_registry (
                name text PRIMARY KEY, kind text NOT NULL,
                schema_name text, combined_relation text,
                included_sources text[] NOT NULL, interaction_class_scope text[],
                evidence_scope jsonb, default_attributes text[], mandatory_attributes text[],
                labels jsonb, curation jsonb, attribute_sources jsonb,
                collapse_mode text NOT NULL DEFAULT 'endpoints'
                    CHECK (collapse_mode IN ('none', 'assertion', 'endpoints')),
                license_scope jsonb, composition jsonb,
                grain text NOT NULL DEFAULT 'interaction'
                    CHECK (grain IN ('interaction', 'participant')),
                built_at timestamptz NOT NULL DEFAULT now(),
                CHECK (schema_name IS NULL AND combined_relation IS NULL),
                CHECK (composition IS NULL OR
                    (composition ->> 'operation' IN ('union', 'collapse', 'exclude', 'annotate')
                     AND jsonb_typeof(composition -> 'components') = 'array'))
            )
        """).format(namespace)
        )
        statement = sql.SQL("""
            INSERT INTO {}.network_registry ({}, schema_name, combined_relation)
            VALUES ({}, NULL, NULL)
            ON CONFLICT (name) DO UPDATE SET {}, schema_name = NULL,
                combined_relation = NULL, built_at = now()
        """).format(
            namespace,
            sql.SQL(", ").join(map(sql.Identifier, _DEFINITION_COLUMNS)),
            sql.SQL(", ").join(sql.Placeholder() for _ in _DEFINITION_COLUMNS),
            sql.SQL(", ").join(
                sql.SQL("{} = EXCLUDED.{}").format(sql.Identifier(name), sql.Identifier(name))
                for name in _DEFINITION_COLUMNS
                if name != "name"
            ),
        )
        for definition in definitions:
            values = definition.as_dict()
            cur.execute(
                statement,
                tuple(
                    Jsonb(values[name])
                    if name in _JSON_COLUMNS and values[name] is not None
                    else list(values[name])
                    if isinstance(values[name], tuple)
                    else values[name]
                    for name in _DEFINITION_COLUMNS
                ),
            )


def rebuild(conn, schema: str) -> dict[str, Any]:
    """Register the current recipes and report unavailable release resources."""
    from ._definitions import NETWORKS
    from ._query import LIMITATIONS

    register(conn, schema, NETWORKS)
    with conn.cursor() as cur:
        cur.execute(
            sql.SQL("SELECT resource FROM {}.resource_versions").format(sql.Identifier(schema))
        )
        loaded = {row[0] for row in cur.fetchall()}
    return {
        "presets": len(NETWORKS),
        "registered": [definition.name for definition in NETWORKS],
        "missing_resources": {
            definition.name: sorted(set(definition.included_sources) - loaded)
            for definition in NETWORKS
        },
        "limitations": LIMITATIONS,
    }
