"""Build the exclusion metabolite tables from a loaded release.

The product reads what the release already published. It resolves no
identifier, calls no service and downloads nothing. Both tables are replaced
whole inside the product transaction, so the same release and the same seed
files always give the same rows.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from psycopg2 import sql
from psycopg2.extras import execute_values

from omnipath_subsets.exclusion_metabolites import resolve, seeds, version

_SQL_DIR = Path(__file__).with_name("sql")

logger = logging.getLogger(__name__)

ORIGIN = {
    "metaproviz_seed": "curated",
    "project_seed": "curated",
    "override_add": "curated",
    "chebi_hierarchy": "derived",
    "chebi_role": "derived",
}


def _sql_text(name: str) -> str:
    return (_SQL_DIR / name).read_text(encoding="utf-8")


def ensure_tables(conn, *, schema: str = "public") -> None:
    """Create both tables and the review view if they are absent.

    The DDL is idempotent. Applying it to populated tables leaves the shape and
    the rows untouched.
    """
    with conn.cursor() as cur:
        # Unqualified names in the DDL land in the target schema. public.*
        # still resolves for anything the view reads from the canonical layer.
        cur.execute(sql.SQL("SET search_path = {}, public").format(sql.Identifier(schema)))
        cur.execute(_sql_text("exclusion_tables.sql"))
    conn.commit()


def _build_id(cur) -> str:
    cur.execute("SELECT manifest_sha256 FROM parquet_release WHERE singleton")
    row = cur.fetchone()
    if row is None:
        raise LookupError("exclusion_metabolites: parquet_release is empty; load a release first")
    return row[0]


def _coverage(resolutions: list[resolve.Resolution]) -> list[dict]:
    """Seed counts per class and namespace, with the seeds behind each count."""
    groups: dict[tuple[str, str], list[resolve.Resolution]] = {}
    for resolution in resolutions:
        key = (resolution.seed.exclusion_class, resolution.seed.namespace)
        groups.setdefault(key, []).append(resolution)
    rows = []
    for (exclusion_class, namespace), members in sorted(groups.items()):
        unresolved = [r.seed.identifier for r in members if not r.resolved]
        fallback_only = [r.seed.identifier for r in members if r.fallback_only]
        rows.append(
            {
                "exclusion_class": exclusion_class,
                "namespace": namespace,
                "resolved": sum(1 for r in members if r.resolved),
                "fallback_only": len(fallback_only),
                "unresolved": len(unresolved),
                "detail": {"unresolved": unresolved, "fallback_only": fallback_only},
            }
        )
    return rows


def _curated_rows(resolutions, *, list_version: str, build_id: str) -> list[tuple]:
    """One row per entity, class and source; the first seed to name it keeps the row."""
    rows: dict[tuple, tuple] = {}
    for resolution in resolutions:
        seed = resolution.seed
        for entity_id in resolution.entity_ids:
            key = (entity_id, seed.exclusion_class, seed.assignment_source)
            rows.setdefault(
                key,
                (
                    entity_id,
                    seed.exclusion_class,
                    seed.assignment_source,
                    ORIGIN[seed.assignment_source],
                    "entity",
                    seed.namespace,
                    seed.identifier,
                    seed.line,
                    None,
                    list_version,
                    build_id,
                ),
            )
    return [rows[key] for key in sorted(rows)]


def build_exclusion_metabolites(
    conn,
    *,
    schema: str = "public",
    seed_directory: Path | str | None = None,
) -> dict:
    """Publish the exclusion tables for the release loaded in ``schema``."""
    ensure_tables(conn, schema=schema)
    seed_set = seeds.read_seeds(seed_directory or seeds.SEED_DIR)
    listed = version.list_version(seed_set.paths)
    with conn.cursor() as cur:
        cur.execute(sql.SQL("SET search_path = {}, public").format(sql.Identifier(schema)))
        build_id = _build_id(cur)
        resolutions = resolve.resolve_seeds(cur, seed_set.seeds)
        rows = _curated_rows(resolutions, list_version=listed.value, build_id=build_id)
        coverage = _coverage(resolutions)

        cur.execute("TRUNCATE exclusion_metabolite, exclusion_metabolite_report")
        execute_values(
            cur,
            "INSERT INTO exclusion_metabolite (entity_id, exclusion_class, assignment_source, "
            "origin, match_kind, seed_namespace, seed_identifier, evidence, via_entity_id, "
            "list_version, build_id) VALUES %s",
            rows,
        )
        cur.execute(
            _sql_text("report.sql"),
            {"build_id": build_id, "coverage": json.dumps(coverage)},
        )
        cur.execute("ANALYZE exclusion_metabolite")
        cur.execute("ANALYZE exclusion_metabolite_report")

    classes = {name: 0 for name in seeds.CLASSES}
    for exclusion_class in classes:
        classes[exclusion_class] = len({r[0] for r in rows if r[1] == exclusion_class})
    stats = {
        "build_id": build_id,
        "list_version": listed.value,
        "uncommitted_inputs": listed.uncommitted,
        "rows": len(rows),
        "classes": classes,
        "resolved_seeds": sum(c["resolved"] for c in coverage),
        "fallback_only_seeds": sum(c["fallback_only"] for c in coverage),
        "unresolved_seeds": sum(c["unresolved"] for c in coverage),
    }
    logger.info("exclusion_metabolites: %s", stats)
    return stats
