"""Time the explorer's request types on the schema v2 prototype (DuckDB over Parquet).

Each page runs the queries a v2 API would issue, against views over every resource's
files. "first" is a fresh connection (no Parquet metadata cached), "repeat" the same
page again. Settings match the serving API: 2 threads, 2.5 GB.

Usage: python scripts/schema_v2/bench.py V2_ROOT [--threads 2]
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import duckdb

TABLES = (
    "entity", "entity_identifier", "entity_annotation", "entity_evidence", "entity_group",
    "entity_term", "relation", "relation_annotation", "relation_evidence", "relation_endpoint",
)  # fmt: skip
PARTS = ("has_input", "has_output", "enabled_by", "has_member", "has_part")


ROOT = Path(".")


def connect(root: Path, threads: int, files: dict):
    db = duckdb.connect()
    db.execute(f"SET memory_limit='2500MB'; SET threads={threads}; SET parquet_metadata_cache=true")
    for table in TABLES:
        listed = ", ".join(f"'{p}'" for p in files[table])
        # ``resource`` (source/version) addresses one resource's files: hashed keys span
        # every file's key range, so a multi-key lookup is cheap only per resource.
        db.execute(
            f"""CREATE VIEW {table} AS SELECT * EXCLUDE (filename),
              regexp_extract(filename, '/resources/([^/]+/[^/]+)/', 1) AS resource
            FROM read_parquet([{listed}], union_by_name=true, filename=true)"""
        )
    return db


def lookup(db, table, column, pairs, fields="*", extra=""):
    """Rows of ``table`` for (resource, key) pairs, one query per resource's file."""
    keys = {}
    for resource, key in pairs:
        keys.setdefault(resource, []).append(key)
    found = []
    for resource, values in sorted(keys.items()):
        path = ROOT / "resources" / resource / f"{table}.parquet"
        found += rows(
            db,
            f"SELECT {fields}, '{resource}' AS resource FROM read_parquet('{path}') "
            f"WHERE {column} IN ({','.join('?' * len(values))}) {extra}",
            values,
        )
    return found


TRACE = False


def rows(db, sql, params=()):
    started = time.monotonic()
    cur = db.execute(sql, list(params))
    names = [d[0] for d in cur.description]
    result = [dict(zip(names, r)) for r in cur.fetchall()]
    if TRACE:
        print(
            f"      {time.monotonic() - started:6.3f}s {len(result):5d} rows  {' '.join(sql.split())[:110]}"
        )
    return result


def prefix_range(q):
    q = q.lower()
    return q, q[:-1] + chr(ord(q[-1]) + 1)


# ------------------------------------------------------------------- pages


def search(db, q, limit=20):
    lo, hi = prefix_range(q)
    hits = rows(
        db,
        """SELECT resource, entity_id, min(CASE WHEN term = ? THEN 0 ELSE 1 END) AS rank,
          min(length(term)) AS len
        FROM entity_term WHERE term >= ? AND term < ? GROUP BY resource, entity_id
        ORDER BY rank, len, resource, entity_id LIMIT ?""",
        [q.lower(), lo, hi, limit + 1],
    )
    pairs = [(h["resource"], h["entity_id"]) for h in hits[:limit]]
    entities = lookup(db, "entity", "entity_id", pairs)
    return dict(hits=len(hits), rows=len(entities))


def search_contains(db, q, limit=20):
    hits = rows(
        db,
        "SELECT DISTINCT resource, entity_id FROM entity_term WHERE contains(term, ?) LIMIT ?",
        [q.lower(), limit + 1],
    )
    return dict(hits=len(hits))


GROUPS = """
    keys AS (
      SELECT entity_key,
        CASE WHEN bool_or(entity_type IN ({chemical_types}))
          THEN CASE WHEN NOT bool_or(group_ambiguous) AND min(group_connectivity) = max(group_connectivity)
               THEN 'connectivity:' || min(group_connectivity) END
          ELSE CASE WHEN min(reference_entity_key) = max(reference_entity_key)
               AND bool_and(coalesce(starts_with(reference_entity_key, 'entrez:'), FALSE))
               THEN 'gene:' || min(reference_entity_key) END
        END AS group_key
      FROM selected GROUP BY entity_key
    ), counts AS (
      SELECT coalesce(group_key, 'entity:' || entity_key) AS group_key, count(*) AS n
      FROM keys GROUP BY 1
    )
    SELECT * FROM counts ORDER BY n DESC, group_key LIMIT 21"""


def groups(db, q=""):
    if q:
        lo, hi = prefix_range(q)
        selected = f"""selected AS (SELECT e.* FROM entity e JOIN (
            SELECT DISTINCT resource, entity_id FROM entity_term WHERE term >= '{lo}' AND term < '{hi}'
            ) t USING (resource, entity_id)),"""
    else:
        selected = "selected AS (SELECT * FROM entity),"
    from omnipath_api.queries.connectivity import chemical_types

    types = ", ".join(f"'{t}'" for t in chemical_types())
    page = rows(db, "WITH " + selected + GROUPS.replace("{chemical_types}", types))
    return dict(groups=len(page), largest=page[0]["n"] if page else 0)


def endpoint_page(db, key, kind, predicates=None, limit=50):
    """One page of an entity's (or reference's) relations, hydrated per resource."""
    where, params = "key = ? AND key_kind = ?", [key, kind]
    if predicates:
        where += f" AND predicate IN ({','.join('?' * len(predicates))})"
        params += list(predicates)
    page = rows(
        db,
        f"""SELECT DISTINCT resource, relation_id, predicate, count(*) OVER () AS total
        FROM relation_endpoint WHERE {where} ORDER BY predicate, resource, relation_id LIMIT {int(limit)}""",
        params,
    )
    pairs = [(r["resource"], r["relation_id"]) for r in page]
    # Endpoint labels, types and taxon come with the relation: no entity lookups.
    return pairs, lookup(db, "relation", "relation_id", pairs)


def details(db, key):
    entity = rows(db, "SELECT * FROM entity WHERE entity_key = ?", [key])
    pairs = [(e["resource"], e["entity_id"]) for e in entity]
    identifiers = lookup(
        db, "entity_identifier", "entity_id", pairs, extra="ORDER BY ordinal LIMIT 20"
    )
    annotations = lookup(
        db, "entity_annotation", "entity_id", pairs, extra="ORDER BY ordinal LIMIT 20"
    )
    _, relations = endpoint_page(db, key, "entity", PARTS)
    return dict(
        entity=len(entity),
        relations=sum(e["relation_count"] for e in entity),
        page=len(relations),
        identifiers=len(identifiers),
        annotations=len(annotations),
    )


def relations_page(db, key, limit=50):
    """The dialog's Relations tab: every predicate, one page."""
    _, relations = endpoint_page(db, key, "entity", limit=limit)
    return dict(page=len(relations))


def members(db, reference, kind):
    return [
        (r["resource"], r["entity_id"])
        for r in rows(
            db,
            "SELECT DISTINCT resource, entity_id FROM entity_group WHERE group_key = ? AND kind = ?",
            [reference, kind],
        )
    ]


def gene_group(db, reference):
    pairs = members(db, reference, "reference")
    entities = lookup(db, "entity", "entity_id", pairs)
    annotations = lookup(db, "entity_annotation", "entity_id", pairs)
    identifiers = lookup(db, "entity_identifier", "entity_id", pairs)
    return dict(
        members=len({e["entity_key"] for e in entities}),
        rows=len(entities),
        annotations=len(annotations),
        identifiers=len(identifiers),
    )


def chemical_group(db, connectivity):
    """A structure group's members, with identifiers and annotations, by connectivity."""
    pairs = members(db, connectivity, "connectivity")
    entities = lookup(db, "entity", "entity_id", pairs)
    identifiers = lookup(db, "entity_identifier", "entity_id", pairs, extra="LIMIT 200")
    return dict(
        members=len({e["entity_key"] for e in entities}),
        rows=len(entities),
        identifiers=len(identifiers),
    )


def molecular_context(db, reference, limit=20):
    pairs, relations = endpoint_page(db, reference, "reference", limit=limit)
    evidence = lookup(db, "relation_evidence", "relation_id", pairs)
    products = lookup(db, "entity", "entity_id", members(db, reference, "gene"))
    standalone = lookup(
        db,
        "entity_evidence",
        "entity_id",
        members(db, reference, "reference"),
        extra=f"LIMIT {limit + 1}",
    )
    return dict(
        relations=len(relations),
        evidence=len(evidence),
        products=len(products),
        standalone=len(standalone),
    )


def facets(db):
    return dict(types=len(rows(db, "SELECT entity_type, count(*) FROM entity GROUP BY 1")))


# ------------------------------------------------------------------- run


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("--threads", type=int, default=2)
    parser.add_argument(
        "--only", help="run pages whose name contains this text, with query timings"
    )
    args = parser.parse_args()
    global TRACE, ROOT
    TRACE = bool(args.only)
    ROOT = args.root
    files = {
        t: sorted(str(p) for p in args.root.glob(f"resources/*/*/{t}.parquet")) for t in TABLES
    }
    setup = connect(args.root, args.threads, files)

    def key(sql, params=()):
        found = rows(setup, sql, params)
        return found[0]["entity_key"] if found else None

    targets = {
        "TP53 protein": key("SELECT entity_key FROM entity WHERE reference_entity_key = 'entrez:7157' AND entity_type = 'protein' AND namespace = 'entrez' LIMIT 1"),
        "caffeine": key("SELECT e.entity_key FROM entity_term t JOIN entity e USING (resource, entity_id) WHERE t.term = 'caffeine' ORDER BY e.entity_key LIMIT 1"),
        "glucose": key("SELECT e.entity_key FROM entity_term t JOIN entity e USING (resource, entity_id) WHERE t.term = 'glucose' ORDER BY e.entity_key LIMIT 1"),
        "water": key("SELECT e.entity_key FROM entity_term t JOIN entity e USING (resource, entity_id) WHERE t.term = 'water' ORDER BY e.entity_key LIMIT 1"),
    }  # fmt: skip
    setup.close()
    pages = [
        ("facets", lambda db: facets(db)),
        ("groups, first page", lambda db: groups(db)),
        ("groups, 'glucose'", lambda db: groups(db, "glucose")),
        ("search 'TP53' (prefix)", lambda db: search(db, "TP53")),
        ("search 'caffeine' (prefix)", lambda db: search(db, "caffeine")),
        ("search 'pln' (contains)", lambda db: search_contains(db, "pln")),
        *[
            (f"details {name}", (lambda k: lambda db: details(db, k))(k))
            for name, k in targets.items()
        ],
        ("relations tab TP53", lambda db: relations_page(db, targets["TP53 protein"])),
        ("gene group PLN", lambda db: gene_group(db, "entrez:5350")),
        ("molecular context PLN", lambda db: molecular_context(db, "entrez:5350")),
        ("chemical group glucose", lambda db: chemical_group(db, "WQZGKKKJIJFFOK")),
    ]
    results = []
    if args.only:
        pages = [(n, p) for n, p in pages if args.only in n]
    for name, page in pages:
        db = connect(args.root, args.threads, files)
        started = time.monotonic()
        result = page(db)
        first = time.monotonic() - started
        started = time.monotonic()
        page(db)
        repeat = time.monotonic() - started
        db.close()
        results.append(
            dict(page=name, first=round(first, 3), repeat=round(repeat, 3), result=result)
        )
        print(
            f"{first:7.3f}s  {repeat:7.3f}s  {name:28s} {json.dumps(result, default=str)}",
            flush=True,
        )
    return results


if __name__ == "__main__":
    main()
