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

from omnipath_api.store.connection import get_connection
from omnipath_api.molecular import read, columns, occurrences_expression

VERSION = "v2"
ENTITY_COLUMNS = "entity_key, entity_type, namespace, identifier, taxon, label, has_hierarchy, parent_count, child_count, reference_entity_key, gene_reference_keys"
RELATION_COLUMNS = (
    "relation_key, subject_entity_key, subject_label, subject_type, predicate, "
    "object_entity_key, object_label, object_type, taxon, is_directed, sign, "
    "category, interaction_class, sources, evidence_count, subject_reference_entity_key, object_reference_entity_key"
)
TERMS = ("object_aspect_qualifier", "object_direction_qualifier", "causal_mechanism_qualifier")


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
                build(
                    "entities",
                    [entities],
                    f"SELECT {ENTITY_COLUMNS} FROM {read([entities])} ORDER BY label, entity_key",
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
