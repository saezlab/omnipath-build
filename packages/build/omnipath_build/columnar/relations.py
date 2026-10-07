"""Columnar execution of a RelationBuilder dataset into observation tables.

The same mapping objects and SilverExtractor rules as the row path, evaluated
once per distinct input instead of once per row:

* each relation endpoint (subject, object) per distinct tuple of the columns its
  builder reads; the result is its flattened entity observations;
* each relation-level CV (annotations, identifiers) per distinct tuple of its
  columns; SQL concatenates the CVs per row and applies the builder's dedupe.

Output tables (``obs_*``) hold what the row path's extractor hands to resolution.
"""

from __future__ import annotations

import json
import time
from typing import Any

from omnipath_core.biolink import annotation_value, predicate as biolink_predicate
from omnipath_core.keys import stable_hash

from ..silver import _IDENTIFIER_PRIORITY, SilverExtractor, _coerce_entity, _identifier_parts
from . import distinct
from .dependencies import ALL, RestrictedRow, trace
from .raw import decode

PLACEHOLDER = "⟨row-id⟩"
SAMPLE = 5000


def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _json(value) -> str:
    return json.dumps(value, sort_keys=True, default=str)


class RelationExecutor:
    def __init__(self, db, source: str, dataset: str, mapper, raw_info: dict, workers=None):
        self.db, self.source, self.dataset, self.mapper = db, source, dataset, mapper
        self.columns = raw_info["columns"]
        self.json_columns = set(raw_info["json_columns"])
        self.known = set(self.columns)
        self.workers = workers
        self.timings: dict[str, float] = {}
        self.keys: dict[str, str] = {}
        self.stats: dict[str, Any] = {}
        sample = db.execute(
            f"SELECT {', '.join(_q(c) for c in self.columns)} FROM raw USING SAMPLE {SAMPLE} ROWS"
        ).fetchall()
        self.sample = [
            {c: decode(v, c in self.json_columns) for c, v in zip(self.columns, row)} for row in sample
        ]

    # -- helpers ----------------------------------------------------------------

    def _time(self, name, started):
        self.timings[name] = self.timings.get(name, 0) + time.perf_counter() - started

    def _row(self, deps, values, violations):
        return RestrictedRow(
            {c: decode(v, c in self.json_columns) for c, v in zip(deps, values)},
            violations,
            self.known,
        )

    def _evaluate(self, name, fn, deps):
        """Evaluate ``fn(row)`` per distinct ``deps`` tuple; widen deps on violations."""
        while True:
            columns = self.columns if deps is ALL else sorted(set(deps) & self.known)
            violations_seen: set[str] = set()

            def task(values, columns=columns):
                violations: set[str] = set()
                result = fn(self._row(columns, values, violations))
                if violations:
                    return _json({"violations": sorted(violations)})
                return result

            started = time.perf_counter()
            cols = ", ".join(_q(c) for c in columns)
            key = self._key(columns)
            if columns is self.columns:
                query = f"SELECT {key} AS k, {cols} FROM raw"
            elif columns:
                query = f"SELECT {key} AS k, {cols} FROM raw GROUP BY ALL"
                distinct_tuples, distinct_keys = self.db.execute(
                    f"SELECT count(*), count(DISTINCT k) FROM ({query})"
                ).fetchone()
                if distinct_tuples != distinct_keys:
                    raise RuntimeError(f"{name}: hash collision among {distinct_tuples:,} inputs")
            else:
                query = "SELECT 0 AS k"
            n = distinct.evaluate(self.db, query, "k", columns, task, name, workers=self.workers)
            self._time(name, started)
            for (result,) in self.db.execute(
                f"SELECT result FROM {name} WHERE result LIKE '{{\"violations\"%' LIMIT 100"
            ).fetchall():
                violations_seen |= set(json.loads(result)["violations"])
            if not violations_seen:
                self.stats[name] = dict(columns=columns, distinct=n)
                self.keys[name] = self._key(columns)
                return columns
            if deps is ALL:
                raise RuntimeError(f"{name}: reads {sorted(violations_seen)} with every column given")
            if "*" in violations_seen:
                deps = ALL
            else:
                deps = (set() if deps is ALL else set(deps)) | violations_seen

    def _key(self, columns):
        """The join key of an input tuple: the row itself when every column is read."""
        if columns is self.columns:
            return "rid"
        if not columns:
            return "0"
        return f"hash({', '.join(_q(c) for c in columns)})"

    def _join(self, name, columns):
        return f"LEFT JOIN {name} ON {name}.k = raw_keys.{name}"

    # -- endpoints --------------------------------------------------------------

    def _endpoint(self, builder, side: int, scope: str, name: str):
        source, dataset = self.source, self.dataset

        def build(row):
            from pypath.internals.tabular_builder import EntityBuilder

            if isinstance(builder, EntityBuilder):
                return builder.build(row)
            if callable(builder):
                try:
                    return builder(row)
                except Exception:
                    return None  # RelationBuilder drops the record
            return builder

        def fn(row):
            entity = build(row)
            if entity is None:
                return None
            ex = SilverExtractor(source, dataset)
            key = ex._extract_entity(
                _coerce_entity(entity, default_type="unknown", default_ns="unknown"),
                row_id=PLACEHOLDER,
                path=(side,),
                persist_annotations=False,
            )
            entities = [
                dict(
                    key=e.entity_key, entity_type=e.entity_type, namespace=e.namespace,
                    identifier=e.identifier, taxon=e.taxon, identity_scope=e.identity_scope,
                    label=e.label, molecular_form=_json(e.molecular_form) if e.molecular_form else None,
                    identifiers=[[i["ns"], i["id"], i["is_canonical"]] for i in e.identifiers],
                )  # fmt: skip
                for e in ex.entities.values()
            ]
            relations = [
                dict(
                    relation_key=r.relation_key, statement_kind=r.statement_kind,
                    subject=r.subject_entity_key, predicate=r.predicate, object=r.object_entity_key,
                    upstream_id=r.upstream_id,
                    annotations=[{**a, "quantity": _json(a["quantity"]) if a["quantity"] else None}
                                 for a in r.annotations],
                )  # fmt: skip
                for r in ex.relations
            ]
            annotations = [
                {**a, "quantity": _json(a["quantity"]) if a["quantity"] else None}
                for a in ex._annotations(entity, scope)
            ]
            return json.dumps(
                dict(key=key, entities=entities, relations=relations, annotations=annotations),
                default=str,
            )

        deps = trace(build, self.sample)
        return self._evaluate(name, fn, deps)

    # -- relation-level CVs -----------------------------------------------------

    def _cvs(self, builder, kind: str, prefix: str):
        """Evaluate each CV of an identifiers/annotations builder; returns [(table, columns)]."""
        from pypath.internals.tabular_builder import ColumnCache

        if builder is None:
            return []
        source, dataset = self.source, self.dataset
        tables = []
        for index, cv in enumerate(builder.cvs):

            def fn(row, cv=cv):
                items = []
                ex = SilverExtractor(source, dataset)
                for term, value, unit in builder._expand_cv(cv, row, ColumnCache()):
                    if term is None:
                        continue
                    key = repr(
                        builder._cv_dedupe_key(cv, term, value, unit if kind == "annotation" else None)
                    )
                    clean = builder._clean_term(cv, term)
                    if kind == "identifier":
                        if value is None or value == "":
                            continue
                        parts = _identifier_parts({"type": clean, "value": str(value)})
                        if parts:
                            items.append(dict(k=key, ns=parts[0], id=parts[1], p=_IDENTIFIER_PRIORITY.get(parts[0], 50)))
                    else:
                        annotation = {
                            "term": clean,
                            "value": annotation_value(term, value),
                            "units": str(unit) if unit is not None else None,
                        }
                        for a in ex._annotations({"annotations": [annotation]}, "relation"):
                            items.append(
                                dict(k=key, term=a["term"], value=a["value"],
                                     quantity=_json(a["quantity"]) if a["quantity"] else None)
                            )  # fmt: skip
                return json.dumps(items) if items else None

            def probe(row, cv=cv):
                return builder._expand_cv(cv, row, ColumnCache())

            name = f"{prefix}_{index}"
            tables.append((name, self._evaluate(name, fn, trace(probe, self.sample))))
        return tables

    # -- assembly ---------------------------------------------------------------

    def run(self):
        m = self.mapper
        started = time.perf_counter()
        subject = self._endpoint(m.subject, 0, "subject", "side_subject")
        obj = self._endpoint(m.object, 1, "object", "side_object")
        annotation_cvs = self._cvs(m.annotations, "annotation", "rel_ann")
        identifier_cvs = self._cvs(m.identifiers, "identifier", "rel_id")
        predicate = biolink_predicate(self._predicate())
        self._time("python (distinct evaluation)", started)

        started = time.perf_counter()
        db = self.db
        db.execute(
            "CREATE OR REPLACE TEMP TABLE raw_keys AS SELECT rid, "
            + ", ".join(f"{key} AS {name}" for name, key in self.keys.items())
            + " FROM raw"
        )
        lists = []
        joins = [self._join("side_subject", subject), self._join("side_object", obj)]
        for kind, cvs in (("ann", annotation_cvs), ("id", identifier_cvs)):
            parts = []
            for index, (name, columns) in enumerate(cvs):
                joins.append(self._join(name, columns))
                parts.append(f"list_transform(coalesce({name}.result, '[]')::JSON[], (x, i) -> struct_pack(cv := {index}, i := i, x := x))")
            lists.append(f"flatten([{', '.join(parts)}]) AS {kind}_items" if parts else f"[]::STRUCT(cv INT, i INT, x JSON)[] AS {kind}_items")
        db.execute(f"""CREATE OR REPLACE TEMP TABLE obs_rows AS
            SELECT raw.rid, raw.payload_json, side_subject.result AS s, side_object.result AS o,
                   {', '.join(lists)}
            FROM raw JOIN raw_keys USING (rid) {' '.join(joins)}
            WHERE side_subject.result IS NOT NULL AND side_object.result IS NOT NULL""")
        # The builder's dedupe: the first CV item with a key wins, in CV order.
        for kind in ("ann", "id"):
            db.execute(f"""CREATE OR REPLACE TEMP TABLE obs_{kind} AS
                SELECT rid, row_number() OVER (PARTITION BY rid ORDER BY cv, i) - 1 AS ordinal, x FROM (
                    SELECT rid, u.cv, u.i, u.x FROM (SELECT rid, unnest({kind}_items) AS u FROM obs_rows)
                    QUALIFY row_number() OVER (PARTITION BY rid, u.x->>'k' ORDER BY u.cv, u.i) = 1)""")
        self._time("sql assembly", started)
        return predicate

    def _predicate(self):
        from pypath.internals.tabular_builder import Column, _canonical_predicate

        p = self.mapper.predicate
        if isinstance(p, Column) or callable(p):
            raise NotImplementedError("row-dependent predicates are not in the spike yet")
        return _canonical_predicate(p)


def relation_key_of(subject, predicate, obj, annotations) -> str:
    from omnipath_core.keys import relation_key

    return relation_key(subject, predicate, obj, annotations)


__all__ = ["RelationExecutor", "PLACEHOLDER", "stable_hash"]
