"""Run the columnar executor on a relation dataset and diff it with the row path.

    uv run python scripts/columnar/run_relations.py bindingdb interactions WORK \
        --reference REF [--max-records N] [--param bindingdb_release=202605]
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import duckdb

from omnipath_build.columnar.raw import load_raw
from omnipath_build.columnar.relations import PLACEHOLDER, RelationExecutor


def materialize(db, dataset: str, predicate: str):
    lit = PLACEHOLDER.replace("'", "''")
    db.execute(f"""CREATE OR REPLACE TABLE c_entities AS
        SELECT DISTINCT e->>'key' AS key, e->>'entity_type' AS entity_type, e->>'namespace' AS namespace,
               e->>'identifier' AS identifier, e->>'taxon' AS taxon, e->>'identity_scope' AS identity_scope,
               e->>'label' AS label, e->>'molecular_form' AS molecular_form
        FROM (SELECT unnest((result->'entities')::JSON[]) AS e
              FROM (SELECT result FROM side_subject UNION ALL SELECT result FROM side_object)
              WHERE result IS NOT NULL)""")
    db.execute("""CREATE OR REPLACE TABLE c_entity_identifiers AS
        SELECT DISTINCT e->>'key' AS key, i->>0 AS ns, i->>1 AS id FROM (
            SELECT e, unnest((e->'identifiers')::JSON[]) AS i FROM (
                SELECT unnest((result->'entities')::JSON[]) AS e
                FROM (SELECT result FROM side_subject UNION ALL SELECT result FROM side_object)
                WHERE result IS NOT NULL))""")
    db.execute(f"""CREATE OR REPLACE TABLE c_relations AS
        WITH up AS (SELECT rid, arg_min(x->>'id', ((x->>'p')::INT, ordinal)) AS upstream_id FROM obs_id GROUP BY rid)
        SELECT '{dataset}:' || r.rid AS row_id, 'relation' AS statement_kind, s->>'key' AS subject,
               '{predicate}' AS predicate, o->>'key' AS object,
               coalesce(up.upstream_id, '{dataset}:' || r.rid) AS upstream_id
        FROM obs_rows r LEFT JOIN up USING (rid)
        UNION ALL
        SELECT '{dataset}:' || rid, x->>'statement_kind', x->>'subject', x->>'predicate', x->>'object',
               replace(x->>'upstream_id', '{lit}', '{dataset}:' || rid)
        FROM (SELECT rid, unnest((s->'relations')::JSON[]) AS x FROM obs_rows
              UNION ALL SELECT rid, unnest((o->'relations')::JSON[]) FROM obs_rows)""")
    db.execute(f"""CREATE OR REPLACE TABLE c_relation_annotations AS
        WITH parts AS (
            SELECT rid, 0 AS part, ordinal, x->>'term' AS term, x->>'value' AS value, x->>'quantity' AS quantity,
                   'relation' AS scope FROM obs_ann
            UNION ALL SELECT rid, 1, generate_subscripts(a, 1), unnest(a)->>'term', unnest(a)->>'value',
                   unnest(a)->>'quantity', 'subject' FROM (SELECT rid, (s->'annotations')::JSON[] AS a FROM obs_rows)
            UNION ALL SELECT rid, 2, generate_subscripts(a, 1), unnest(a)->>'term', unnest(a)->>'value',
                   unnest(a)->>'quantity', 'object' FROM (SELECT rid, (o->'annotations')::JSON[] AS a FROM obs_rows)
        )
        SELECT '{dataset}:' || rid AS row_id,
               row_number() OVER (PARTITION BY rid ORDER BY part, ordinal) - 1 AS ordinal,
               term, value, quantity, scope FROM parts""")


def compare(db, ref: Path):
    r = lambda t: f"read_parquet('{ref}/{t}/*.parquet')"
    checks = {
        "entities (key, type, ns, id, taxon, scope)": (
            f"SELECT DISTINCT key, entity_type, namespace, identifier, taxon, identity_scope FROM {r('entities')}",
            "SELECT key, entity_type, namespace, identifier, taxon, identity_scope FROM c_entities",
        ),
        "entity labels": (
            f"SELECT DISTINCT key, label FROM {r('entities')}",
            "SELECT key, label FROM c_entities",
        ),
        "entity molecular forms": (
            f"SELECT DISTINCT key, molecular_form FROM {r('entities')}",
            "SELECT key, molecular_form FROM c_entities",
        ),
        "entity identifiers": (
            f"SELECT DISTINCT key, ns, id FROM {r('entity_identifiers')}",
            "SELECT key, ns, id FROM c_entity_identifiers",
        ),
        "relations": (
            f"SELECT row_id, statement_kind, subject, predicate, object, upstream_id FROM {r('relations')}",
            "SELECT row_id, statement_kind, subject, predicate, object, upstream_id FROM c_relations",
        ),
        "relation annotations (main relation)": (
            f"""SELECT r.row_id, a.ordinal, a.term, a.value, a.quantity, a.scope
                FROM {r('relation_annotations')} a JOIN {r('relations')} r USING (rid)
                WHERE r.statement_kind = 'relation' AND r.upstream_id NOT LIKE '%:member:%'""",
            "SELECT row_id, ordinal, term, value, quantity, scope FROM c_relation_annotations",
        ),
    }
    report = {}
    for name, (a, b) in checks.items():
        only_ref = db.execute(f"SELECT count(*) FROM (({a}) EXCEPT ALL ({b}))").fetchone()[0]
        only_new = db.execute(f"SELECT count(*) FROM (({b}) EXCEPT ALL ({a}))").fetchone()[0]
        total = db.execute(f"SELECT count(*) FROM ({a})").fetchone()[0]
        report[name] = dict(reference=total, only_reference=only_ref, only_columnar=only_new)
        print(f"{name:42s} ref={total:>10,}  only_ref={only_ref:>8,}  only_columnar={only_new:>8,}")
        if only_ref or only_new:
            for row in db.execute(f"SELECT 'ref', * FROM (({a}) EXCEPT ALL ({b})) LIMIT 3").fetchall():
                print("   ", row)
            for row in db.execute(f"SELECT 'new', * FROM (({b}) EXCEPT ALL ({a})) LIMIT 3").fetchall():
                print("   ", row)
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("source")
    parser.add_argument("dataset")
    parser.add_argument("work", type=Path)
    parser.add_argument("--reference", type=Path)
    parser.add_argument("--max-records", type=int)
    parser.add_argument("--workers", type=int)
    parser.add_argument("--threads", type=int, default=16)
    parser.add_argument("--param", action="append", default=[])
    args = parser.parse_args()

    from omnipath_build.discovery import discover_datasets
    from omnipath_build.two_phase import load_mapper

    args.work.mkdir(parents=True, exist_ok=True)
    db = duckdb.connect(str(args.work / "columnar.duckdb"))
    db.execute(f"SET threads={args.threads}; SET preserve_insertion_order=false; SET temp_directory='{args.work}/spill'")
    _, found, _ = discover_datasets(source=args.source, datasets=[args.dataset])
    ds = found[0]
    params = dict(p.split("=", 1) for p in args.param)
    timings = {}

    started = time.perf_counter()
    raw_info = load_raw(
        db,
        ds.raw_dataset.raw(source=args.source, dataset=args.dataset, max_records=args.max_records, **params),
        max_records=args.max_records,
    )
    timings["parse + load raw"] = time.perf_counter() - started
    print(f"raw: {raw_info['rows']:,} rows, {len(raw_info['columns'])} columns in {timings['parse + load raw']:.1f}s", flush=True)

    mapper = load_mapper(ds.qualified_module, args.dataset)
    executor = RelationExecutor(db, args.source, args.dataset, mapper, raw_info, workers=args.workers)
    predicate = executor.run()
    timings.update(executor.timings)
    started = time.perf_counter()
    materialize(db, args.dataset, predicate)
    timings["materialize"] = time.perf_counter() - started
    for name, stat in executor.stats.items():
        cols = "ALL" if stat["columns"] == raw_info["columns"] else ", ".join(stat["columns"])
        print(f"  {name:16s} distinct={stat['distinct']:>10,}  columns: {cols[:110]}")
    for name, seconds in timings.items():
        print(f"  {name:32s} {seconds:8.1f}s")
    report = compare(db, args.reference) if args.reference else {}
    (args.work / "report.json").write_text(
        json.dumps(dict(rows=raw_info["rows"], timings=timings, stats=executor.stats, compare=report), indent=2, default=str)
    )


if __name__ == "__main__":
    main()
