"""Disposable serving projections, keyed by immutable input files.

Run explicitly: python -m omnipath_api.serving_index --data-root /data
Requests use an existing matching projection or the original Parquets. They never
build indexes, and resource versions are never modified.
"""

from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import time

from omnipath_api.store.connection import format_read_parquet, get_connection
from omnipath_api.molecular import read, columns, occurrences_expression

VERSION = "v3"
ENTITY_COLUMNS = "entity_key, entity_type, namespace, identifier, taxon, label, has_hierarchy, parent_count, child_count, reference_entity_key, gene_reference_keys"
RELATION_COLUMNS = (
    "relation_key, subject_entity_key, subject_label, subject_type, predicate, "
    "object_entity_key, object_label, object_type, taxon, is_directed, sign, "
    "category, interaction_class, sources, evidence_count, subject_reference_entity_key, object_reference_entity_key"
)
TERMS = ("object_aspect_qualifier", "object_direction_qualifier", "causal_mechanism_qualifier")
# A row's chemical connectivity (first InChIKey block) and whether its InChIKeys disagree.
_VALID = "regexp_full_match(upper(identifier), '[A-Z]{14}-[A-Z]{10}-[A-Z]')"
_ALIASES = (
    "list_distinct(list_transform(list_filter(identifiers, x -> lower(x.ns) = 'inchikey'"
    " AND regexp_full_match(upper(x.id), '[A-Z]{14}-[A-Z]{10}-[A-Z]')), x -> left(upper(x.id), 14)))"
)
GROUP_CONNECTIVITY = f"""CASE WHEN namespace = 'inchikey' AND {_VALID} THEN left(upper(identifier), 14)
    WHEN len({_ALIASES}) = 1 THEN ({_ALIASES})[1] END"""
GROUP_AMBIGUOUS = f"""CASE WHEN namespace = 'inchikey' AND {_VALID} THEN FALSE
    ELSE coalesce(len({_ALIASES}) > 1, FALSE) END"""
ADJACENCY_COLUMNS = "entity_key, relation_key, predicate, category, relation_row"


def signature(paths):
    return hashlib.sha256(
        json.dumps(
            sorted(
                (str(Path(p).resolve()), Path(p).stat().st_size, Path(p).stat().st_mtime_ns)
                for p in paths
            )
        ).encode()
    ).hexdigest()


def index_path(root, kind, paths):
    return Path(root) / ".serving" / VERSION / kind / (signature(paths) + ".parquet")


def projected_paths(root, kind, paths):
    return [
        str(p) if (p := index_path(root, kind, [original])).is_file() else str(original)
        for original in paths
    ]


def _split(root, kind, paths):
    """(projection, original) pairs for indexed inputs, and the originals without one."""
    indexed, plain = [], []
    for path in paths:
        target = index_path(root, kind, [path])
        (indexed.append((str(target), str(path))) if target.is_file() else plain.append(str(path)))
    return indexed, plain


def entity_group_rows(engine, paths):
    """Entity scalars with ``group_connectivity`` and ``group_ambiguous``.

    Projections hold both precomputed; an input without one derives them from its
    nested identifiers, so results do not depend on which inputs are indexed.
    """
    indexed, plain = _split(engine.data_root, "entities", paths)
    parts = []
    if indexed:
        parts.append(
            f"SELECT {ENTITY_COLUMNS}, group_connectivity, group_ambiguous "
            f"FROM {engine._read_expr([p for p, _ in indexed])}"
        )
    if plain:
        derived = (
            f"{GROUP_CONNECTIVITY} AS group_connectivity, {GROUP_AMBIGUOUS} AS group_ambiguous"
            if "identifiers" in columns(plain)
            else "NULL::VARCHAR AS group_connectivity, FALSE AS group_ambiguous"
        )
        parts.append(f"SELECT {ENTITY_COLUMNS}, {derived} FROM {engine._read_expr(plain)}")
    return "(" + " UNION ALL ".join(parts) + ")"


def adjacency_rows(engine, paths, keys, where="TRUE", params=()):
    """Relation endpoints of ``keys``: filename (the original relations file), entity_key,
    relation_key, predicate, category and relation_row (its row in that file).

    A relation whose subject and object are both in ``keys`` appears twice; count
    distinct (filename, relation_row) for relations.
    """
    indexed, plain = _split(engine.data_root, "adjacency", paths)
    marks = ",".join("?" for _ in keys)
    parts, values = [], []
    if indexed:
        parts.append(
            f"""SELECT m.filename, {ADJACENCY_COLUMNS}
            FROM {format_read_parquet([p for p, _ in indexed], filename=True)} a
            JOIN (SELECT unnest(?::VARCHAR[]) AS projection, unnest(?::VARCHAR[]) AS filename) m
              ON a.filename = m.projection
            WHERE entity_key IN ({marks}) AND {where}"""
        )
        values += [[p for p, _ in indexed], [o for _, o in indexed], *keys, *params]
    if plain:
        read = format_read_parquet(plain, filename=True, file_row_number=True)
        for side in ("subject", "object"):
            parts.append(
                f"""SELECT filename, {side}_entity_key AS entity_key, relation_key, predicate,
                  category, file_row_number AS relation_row
                FROM {read} WHERE {side}_entity_key IN ({marks}) AND {where}"""
            )
            values += [*keys, *params]
    return "(" + " UNION ALL ".join(parts) + ")", values


def build_indexes(engine, *, threads=4, memory_limit="2GB", min_free_disk=20 * 1024**3):
    root = engine.data_root
    inputs = engine._selected_resource_infos(None)
    db = get_connection(memory_limit="2GB")
    db.execute("SET threads=?", [threads])
    db.execute("SET memory_limit=?", [memory_limit])
    temp_root = root / ".serving" / VERSION
    temp_root.mkdir(parents=True, exist_ok=True)
    reports = []
    with tempfile.TemporaryDirectory(prefix="build-", dir=temp_root) as work:
        db.execute("SET temp_directory=?", [str(Path(work) / "spill")])

        def build(kind, paths, query):
            target = index_path(root, kind, paths)
            if target.exists():
                return
            if shutil.disk_usage(root).free < min_free_disk:
                raise RuntimeError("Serving index free disk reserve reached")
            before = signature(paths)
            started = time.monotonic()
            staging = Path(work) / "output.parquet"
            db.execute(
                f"COPY ({query}) TO ? (FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 32768)",
                [str(staging)],
            )
            if signature(paths) != before:
                raise RuntimeError("Resource inventory changed during index construction; retry")
            target.parent.mkdir(parents=True, exist_ok=True)
            staging.replace(target)
            row = dict(
                kind=kind,
                inputs=len(paths),
                bytes=target.stat().st_size,
                seconds=round(time.monotonic() - started, 3),
                path=str(target),
            )
            reports.append(row)
            print(json.dumps(row), flush=True)

        try:
            for info in inputs:
                entities, relations = str(info["entities_path"]), str(info["relations_path"])
                derived = (
                    f"{GROUP_CONNECTIVITY} AS group_connectivity, {GROUP_AMBIGUOUS} AS group_ambiguous"
                    if "identifiers" in columns([entities])
                    else "NULL::VARCHAR AS group_connectivity, FALSE AS group_ambiguous"
                )
                # Sorted by key: lookups by entity key read only the matching row groups.
                build(
                    "entities",
                    [entities],
                    f"SELECT {ENTITY_COLUMNS}, {derived} FROM {read([entities])} ORDER BY entity_key, label",
                )
                terms = ",".join("'" + term + "'" for term in TERMS)
                # Preserve exactly one row per source relation, including rows with
                # no qualifiers. Only qualifier values within that row are distinct.
                build(
                    "relations",
                    [relations],
                    f"""SELECT {RELATION_COLUMNS},
                    list_transform({occurrences_expression(columns([relations]))}, e -> json_object('subject_molecular_form', json_object('protein_entity_key', json_extract_string(e, '$.subject_molecular_form.protein_entity_key'), 'transcript_entity_key', json_extract_string(e, '$.subject_molecular_form.transcript_entity_key'), 'isoform_identifier', json_extract(e, '$.subject_molecular_form.isoform_identifier')), 'object_molecular_form', json_object('protein_entity_key', json_extract_string(e, '$.object_molecular_form.protein_entity_key'), 'transcript_entity_key', json_extract_string(e, '$.object_molecular_form.transcript_entity_key'), 'isoform_identifier', json_extract(e, '$.object_molecular_form.isoform_identifier')))) AS molecular_occurrences,
                    list_distinct(list_transform(list_filter(annotations, a -> a.scope='relation' AND a.term IN ({terms})),
                        a -> struct_pack(term := a.term, value := a.value, scope := a.scope))) AS annotations
                    FROM {read([relations])}""",
                )
                # Relations by endpoint, sorted by entity key, with each relation's row in
                # the original file so a relation is counted once.
                endpoints = format_read_parquet([relations], file_row_number=True)
                build(
                    "adjacency",
                    [relations],
                    f"""SELECT * FROM (
                      SELECT subject_entity_key AS entity_key, relation_key, predicate, category,
                        file_row_number AS relation_row FROM {endpoints}
                      UNION ALL
                      SELECT object_entity_key, relation_key, predicate, category, file_row_number
                      FROM {endpoints}
                    ) WHERE entity_key IS NOT NULL ORDER BY entity_key, relation_row""",
                )
        finally:
            db.close()
    return reports


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", default="data")
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--memory-limit", default="2GB")
    args = parser.parse_args()
    available = (
        len(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else (os.cpu_count() or 1)
    )
    if args.threads < 1 or args.threads >= available:
        parser.error("Threads must be positive and leave at least one available CPU unused")
    from omnipath_api.engine import ParquetServingEngine

    build_indexes(
        ParquetServingEngine(args.data_root), threads=args.threads, memory_limit=args.memory_limit
    )


if __name__ == "__main__":
    main()
