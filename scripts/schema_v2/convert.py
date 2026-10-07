"""Project published resource Parquets into the normalized schema v2 prototype.

Each resource version keeps its own directory; every table is sorted by the key it
is looked up by, with small row groups, so a lookup reads a few groups per file.

    entity              one row per entity (scalars, group keys, counts)    entity_key
    entity_identifier   one row per identifier                               entity_key, ordinal
    entity_annotation   one row per annotation (quantity flattened)          entity_key, ordinal
    entity_evidence     one row per evidence item                            entity_key, ordinal
    entity_group    entity by reference, gene and connectivity keys      group_key
    entity_term         lowercase search terms (label, identifiers)          term
    relation            one row per relation (scalars, qualifiers)           relation_key
    relation_annotation one row per relation annotation                      relation_key, ordinal
    relation_evidence   one row per relation evidence item                   relation_key, ordinal
    relation_endpoint   each relation under its entity and reference keys    key

Usage: python scripts/schema_v2/convert.py SOURCE_ROOT TARGET_ROOT [resource ...]
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import time
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

ROW_GROUP = 8192
QUALIFIERS = ("object_aspect_qualifier", "object_direction_qualifier", "causal_mechanism_qualifier")
_VALID = "regexp_full_match(upper(identifier), '[A-Z]{14}-[A-Z]{10}-[A-Z]')"
_ALIASES = (
    "list_distinct(list_transform(list_filter(identifiers, x -> lower(x.ns) = 'inchikey'"
    " AND regexp_full_match(upper(x.id), '[A-Z]{14}-[A-Z]{10}-[A-Z]')), x -> left(upper(x.id), 14)))"
)


def explode(source, target, id_name, column, flatten=(), row_group_size=ROW_GROUP):
    """One row per list item: the parent's row id (its row in ``source``), ordinal and the
    item's fields; ``flatten`` struct fields become ``<field>_<subfield>`` columns."""
    reader = pq.ParquetFile(source)
    if column not in reader.schema_arrow.names:
        return 0
    item = reader.schema_arrow.field(column).type.value_type
    fields = [pa.field(id_name, pa.int32()), pa.field("ordinal", pa.int32())]
    for f in item:
        if f.name in flatten:
            fields += [pa.field(f"{f.name}_{s.name}", s.type) for s in f.type]
        else:
            fields.append(f)
    schema = pa.schema(fields)
    rows = offset = 0
    with pq.ParquetWriter(target, schema, compression="zstd") as writer:
        for batch in reader.iter_batches(batch_size=16384, columns=[column]):
            lists = batch.column(column)
            batch_offset, offset = offset, offset + batch.num_rows
            parents = pc.list_parent_indices(lists)
            items = pc.list_flatten(lists)
            if not len(items):
                continue
            starts = pc.cast(pc.take(lists.offsets, parents), pa.int64())
            flat = pc.add(pa.array(range(len(items)), pa.int64()), lists.offsets[0].as_py())
            columns = [
                pc.cast(pc.add(parents, batch_offset), pa.int32()),
                pc.cast(pc.subtract(flat, starts), pa.int32()),
            ]
            for i, f in enumerate(item):
                values = items.field(i)
                if f.name in flatten:
                    columns += [values.field(j) for j in range(len(f.type))]
                else:
                    columns.append(values)
            writer.write_table(pa.table(columns, schema=schema), row_group_size=row_group_size)
            rows += len(items)
    return rows


def copy(db, query, target, row_group_size=ROW_GROUP, max_sort_rows=2_000_000):
    """COPY a query to Parquet. A trailing ORDER BY is applied in key ranges of at most
    ``max_sort_rows`` rows (boundaries from a sample), appended in order: one global sort
    of ten million rows with long string keys did not fit in memory."""
    options = f"(FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE {row_group_size})"
    ordered = re.search(r"\s+ORDER BY\s+([\w., ]+?)\s*$", query)
    if not ordered:
        db.execute(f"COPY ({query}) TO '{target}' {options}")
        return
    order = ", ".join(c.strip().split(".")[-1] for c in ordered.group(1).split(","))
    key = order.split(",")[0].strip()
    unsorted = Path(f"{target}.unsorted.parquet")
    db.execute(f"COPY ({query[: ordered.start()]}) TO '{unsorted}' {options}")
    try:
        total = pq.ParquetFile(unsorted).metadata.num_rows
        parts = -(-total // max_sort_rows)
        if parts <= 1:
            db.execute(
                f"COPY (SELECT * FROM '{unsorted}' ORDER BY {order}) TO '{target}' {options}"
            )
            return
        sample = sorted(
            r[0]
            for r in db.execute(
                f"SELECT {key} FROM (SELECT {key} FROM '{unsorted}' WHERE {key} IS NOT NULL) USING SAMPLE 200000 ROWS"
            ).fetchall()
        )
        bounds = sorted({sample[len(sample) * i // parts] for i in range(1, parts)})
        ranges = list(zip([None, *bounds], [*bounds, None]))
        writer = None
        try:
            for lo, hi in ranges:
                where = (
                    " AND ".join(
                        [f"{key} >= ?"] * (lo is not None) + [f"{key} < ?"] * (hi is not None)
                    )
                    or "TRUE"
                )
                params = [v for v in (lo, hi) if v is not None]
                batches = db.execute(
                    f"SELECT * FROM '{unsorted}' WHERE {where} ORDER BY {order}", params
                ).fetch_record_batch(row_group_size)
                for batch in batches:
                    if writer is None:
                        writer = pq.ParquetWriter(target, batch.schema, compression="zstd")
                    writer.write_batch(batch, row_group_size=row_group_size)
        finally:
            if writer is not None:
                writer.close()
    finally:
        unsorted.unlink(missing_ok=True)


def convert(db, folder: Path, out: Path):
    out.mkdir(parents=True, exist_ok=True)
    ent, rel = folder / "entities.parquet", folder / "relations.parquet"
    report = {}

    def timed(name, fn):
        started = time.monotonic()
        rows = fn()
        report[name] = dict(rows=rows, seconds=round(time.monotonic() - started, 1))

    def count(table):
        return pq.ParquetFile(out / f"{table}.parquet").metadata.num_rows

    # Relations first: entity counts read the endpoint table.
    rel_names = set(pq.read_schema(rel).names)
    ann = "annotations" if "annotations" in rel_names else "[]"
    qualifiers = ", ".join(
        f"list_distinct(list_transform(list_filter({ann}, a -> a.scope = 'relation' AND a.term = '{q}'), a -> a.value)) AS {q}"
        for q in QUALIFIERS
    )
    sref = (
        "subject_reference_entity_key"
        if "subject_reference_entity_key" in rel_names
        else "NULL::VARCHAR"
    )
    oref = (
        "object_reference_entity_key"
        if "object_reference_entity_key" in rel_names
        else "NULL::VARCHAR"
    )
    # Row ids: a row's position in the published file, which is sorted by its hash key.
    # Child tables refer to their parent by id within the resource; the 64-character
    # keys stay only where they are cross-resource identities (and compress, sorted).
    relations = f"read_parquet('{rel}', file_row_number=true)"
    timed(
        "relation",
        lambda: (
            copy(
                db,
                f"""
        SELECT file_row_number::INTEGER AS relation_id, relation_key, statement_kind, subject_entity_key,
          {sref} AS subject_reference_entity_key, subject_label, subject_type, predicate,
          object_entity_key, {oref} AS object_reference_entity_key, object_label, object_type, taxon,
          is_directed, sign, category, interaction_class, sources, evidence_count,
          coalesce(len({ann}), 0) AS annotation_count, {qualifiers}
        FROM {relations} ORDER BY relation_id""",
                out / "relation.parquet",
            ),
            count("relation"),
        )[1],
    )
    report["relation_keys_sorted"] = db.execute(f"""
        SELECT coalesce(bool_and(relation_key >= previous), TRUE) FROM (
          SELECT relation_key, lag(relation_key) OVER (ORDER BY relation_id) AS previous
          FROM read_parquet('{out / "relation.parquet"}'))""").fetchone()[0]
    timed(
        "relation_annotation",
        lambda: explode(
            rel,
            out / "relation_annotation.parquet",
            "relation_id",
            "annotations",
            flatten={"quantity"},
        ),
    )
    timed(
        "relation_evidence",
        lambda: explode(rel, out / "relation_evidence.parquet", "relation_id", "evidence"),
    )
    timed(
        "relation_endpoint",
        lambda: (
            copy(
                db,
                f"""
        SELECT * FROM (
          SELECT subject_entity_key AS key, 'entity' AS key_kind, 'subject' AS side,
            file_row_number::INTEGER AS relation_id, predicate, category FROM {relations}
          UNION ALL SELECT object_entity_key, 'entity', 'object', file_row_number::INTEGER, predicate, category FROM {relations}
          UNION ALL SELECT {sref}, 'reference', 'subject', file_row_number::INTEGER, predicate, category FROM {relations}
          UNION ALL SELECT {oref}, 'reference', 'object', file_row_number::INTEGER, predicate, category FROM {relations}
        ) WHERE key IS NOT NULL ORDER BY key, relation_id""",
                out / "relation_endpoint.parquet",
            ),
            count("relation_endpoint"),
        )[1],
    )

    ent_names = set(pq.read_schema(ent).names)
    has = lambda c: c in ent_names  # noqa: E731
    conn = (
        f"CASE WHEN namespace = 'inchikey' AND {_VALID} THEN left(upper(identifier), 14) WHEN len({_ALIASES}) = 1 THEN ({_ALIASES})[1] END"
        if has("identifiers")
        else "NULL::VARCHAR"
    )
    amb = (
        f"CASE WHEN namespace = 'inchikey' AND {_VALID} THEN FALSE ELSE coalesce(len({_ALIASES}) > 1, FALSE) END"
        if has("identifiers")
        else "FALSE"
    )
    ref = "reference_entity_key" if has("reference_entity_key") else "NULL::VARCHAR"
    genes = "gene_reference_keys" if has("gene_reference_keys") else "[]::VARCHAR[]"
    length = lambda c: f"coalesce(len({c}), 0)" if has(c) else "0"  # noqa: E731
    entities = f"read_parquet('{ent}', file_row_number=true)"
    timed(
        "entity",
        lambda: (
            copy(
                db,
                f"""
        WITH relations AS (
          SELECT key AS entity_key, count(DISTINCT relation_id) AS relation_count
          FROM read_parquet('{out / "relation_endpoint.parquet"}') WHERE key_kind = 'entity' GROUP BY key)
        SELECT file_row_number::INTEGER AS entity_id, e.entity_key, entity_type, namespace, identifier,
          taxon, label, has_hierarchy, parent_count, child_count, {ref} AS reference_entity_key,
          {genes} AS gene_reference_keys, {conn} AS group_connectivity, {amb} AS group_ambiguous,
          {length("identifiers")} AS identifier_count, {length("annotations")} AS annotation_count,
          {length("evidence")} AS evidence_count, coalesce(r.relation_count, 0) AS relation_count
        FROM {entities} e LEFT JOIN relations r USING (entity_key)
        ORDER BY entity_id""",
                out / "entity.parquet",
            ),
            count("entity"),
        )[1],
    )
    report["entity_keys_sorted"] = db.execute(f"""
        SELECT coalesce(bool_and(entity_key >= previous), TRUE) FROM (
          SELECT entity_key, lag(entity_key) OVER (ORDER BY entity_id) AS previous
          FROM read_parquet('{out / "entity.parquet"}'))""").fetchone()[0]
    timed(
        "entity_identifier",
        lambda: explode(ent, out / "entity_identifier.parquet", "entity_id", "identifiers"),
    )
    timed(
        "entity_annotation",
        lambda: explode(
            ent, out / "entity_annotation.parquet", "entity_id", "annotations", flatten={"quantity"}
        ),
    )
    timed(
        "entity_evidence",
        lambda: explode(ent, out / "entity_evidence.parquet", "entity_id", "evidence"),
    )
    timed(
        "entity_group",
        lambda: (
            copy(
                db,
                f"""
        SELECT * FROM (
          SELECT {ref} AS group_key, 'reference' AS kind, file_row_number::INTEGER AS entity_id FROM {entities}
          UNION ALL SELECT unnest({genes}), 'gene', file_row_number::INTEGER FROM {entities}
          -- Chemical structure groups: the first InChIKey block, unless the row's keys disagree.
          UNION ALL SELECT group_connectivity, 'connectivity', entity_id
            FROM read_parquet('{out / "entity.parquet"}') WHERE NOT group_ambiguous
        ) WHERE group_key IS NOT NULL ORDER BY group_key, entity_id""",
                out / "entity_group.parquet",
            ),
            count("entity_group"),
        )[1],
    )
    ids = (
        f"UNION ALL SELECT lower(i.id), 'identifier', file_row_number::INTEGER FROM {entities}, unnest(identifiers) t(i) WHERE i.id IS NOT NULL"
        if has("identifiers")
        else ""
    )
    timed(
        "entity_term",
        lambda: (
            copy(
                db,
                f"""
        SELECT DISTINCT term, kind, entity_id FROM (
          SELECT lower(label) AS term, 'label' AS kind, file_row_number::INTEGER AS entity_id FROM {entities} WHERE label IS NOT NULL
          UNION ALL SELECT lower(identifier), 'identifier', file_row_number::INTEGER FROM {entities} WHERE identifier IS NOT NULL
          {ids}
        ) ORDER BY term, entity_id""",
                out / "entity_term.parquet",
            ),
            count("entity_term"),
        )[1],
    )
    payloads = folder / "evidence_payloads.parquet"
    if payloads.exists():
        (out / "evidence_payloads.parquet").unlink(missing_ok=True)
        (out / "evidence_payloads.parquet").hardlink_to(payloads)
    return report


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("source", type=Path)
    parser.add_argument("target", type=Path)
    parser.add_argument("resources", nargs="*")
    parser.add_argument("--memory", default="3GB")
    parser.add_argument("--threads", type=int, default=4)
    args = parser.parse_args()
    db = duckdb.connect()
    db.execute(f"SET memory_limit='{args.memory}'; SET threads={args.threads}")
    # Every output is explicitly ordered; insertion order only costs sort memory.
    db.execute("SET preserve_insertion_order=false")
    db.execute(f"SET temp_directory='{args.target / '.tmp'}'")
    folders = sorted(
        f for f in (args.source / "resources").glob("*/*") if (f / "entities.parquet").is_file()
    )
    if args.resources:
        folders = [f for f in folders if f.parent.name in args.resources]
    for folder in folders:
        out = args.target / "resources" / folder.parent.name / folder.name
        if (out / "entity_term.parquet").exists():
            continue
        started = time.monotonic()
        staging = out.with_name(out.name + ".partial")
        shutil.rmtree(staging, ignore_errors=True)
        report = convert(db, folder, staging)
        staging.rename(out)
        print(
            json.dumps(
                dict(
                    resource=folder.parent.name,
                    seconds=round(time.monotonic() - started, 1),
                    tables=report,
                )
            ),
            flush=True,
        )
    shutil.rmtree(args.target / ".tmp", ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
