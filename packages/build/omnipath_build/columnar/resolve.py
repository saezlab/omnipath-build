"""Resolve and write columnar observations with the existing resolver and writer.

Resolution depends only on the observation, so each distinct entity observation of
the resource is resolved once, in parallel chunks, and its working-table rows are
made there with the writer's own ``entity_rows``. Relation rows are made in
parallel with ``relation_rows``. One ``ParquetWriter`` then ingests all rows in
bulk and finalizes them as in the row path, so outputs are those of the row path
by construction.
"""

from __future__ import annotations

import json
import multiprocessing as mp
import pickle
import time
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from omnipath_resolver.contracts import RawEntityObservation
from omnipath_build.extract.observations import RawRelationObservation


def export_inputs(db, work: Path, dataset: str, predicate: str) -> dict:
    """Write the observation inputs of resolution and writing as Parquet files."""
    work.mkdir(parents=True, exist_ok=True)
    db.execute(f"""COPY (
        SELECT DISTINCT e->>'key' AS key, e::VARCHAR AS entity FROM (
            SELECT unnest((result->'entities')::JSON[]) AS e
            FROM (SELECT result FROM side_subject UNION ALL SELECT result FROM side_object)
            WHERE result IS NOT NULL)
        QUALIFY row_number() OVER (PARTITION BY key ORDER BY entity) = 1
    ) TO '{work}/entities.parquet' (FORMAT PARQUET, ROW_GROUP_SIZE {ENTITY_CHUNK})""")
    db.execute(f"""COPY (
        WITH up AS (SELECT rid, arg_min(x->>'id', ((x->>'p')::INT, ordinal)) AS upstream_id
                    FROM obs_id GROUP BY rid),
        anns AS (SELECT rid, list(x ORDER BY ordinal) AS anns FROM obs_ann GROUP BY rid)
        SELECT r.rid, '{dataset}:' || r.rid AS row_id, r.payload_json,
               r.s::VARCHAR AS s, r.o::VARCHAR AS o, coalesce(anns.anns, [])::VARCHAR AS anns,
               coalesce(up.upstream_id, '{dataset}:' || r.rid) AS upstream_id
        FROM obs_rows r LEFT JOIN up USING (rid) LEFT JOIN anns USING (rid)
    ) TO '{work}/rows.parquet' (FORMAT PARQUET, ROW_GROUP_SIZE 20000)""")
    return dict(predicate=predicate)


def _observation(entity: dict, source: str) -> RawEntityObservation:
    return RawEntityObservation(
        entity_key=entity["key"],
        entity_type=entity["entity_type"],
        namespace=entity["namespace"],
        identifier=entity["identifier"],
        taxon=entity["taxon"],
        label=entity["label"],
        identity_scope=entity["identity_scope"],
        molecular_form=json.loads(entity["molecular_form"]) if entity["molecular_form"] else None,
        identifiers=[
            {"ns": ns, "id": value, "is_canonical": canonical, "source": source}
            for ns, value, canonical in entity["identifiers"]
        ],
    )


# -- resolution ------------------------------------------------------------------


def _write(rows, schema, path):
    pq.write_table(pa.Table.from_pylist(rows, schema=schema), path)


# Entities per resolution task; entities.parquet has one row group per task.
ENTITY_CHUNK = 4096


def _resolve_chunk(args):
    index, entities_path, source, library_dir, out = args
    from omnipath_resolver import EntityResolver
    from omnipath_build.writer import (
        ENTITY_ANN, ENTITY_EVIDENCE_INPUT, ENTITY_INPUT, IDS_INPUT,
        _resolution_annotations, entity_rows,
    )  # fmt: skip

    table = pq.ParquetFile(entities_path).read_row_group(index)
    observations = {
        k: _observation(json.loads(e), source)
        for k, e in zip(table["key"].to_pylist(), table["entity"].to_pylist())
    }
    resolver = EntityResolver(library_dir, defer_aliases=True, memo_size=0)
    try:
        targets = resolver.resolve_entity_targets(observations, progress=False)
        keys_path = Path(out) / f"resolution_keys-{index:05d}.parquet"
        resolver.export_resolution_keys(keys_path)
    finally:
        resolver.close()
    parts = ([], [], [], [])
    diagnostics = {}
    for key, observation in observations.items():
        for rows, part in zip(parts, entity_rows(key, observation, targets[key])):
            rows.extend(part)
        if _resolution_annotations(targets[key]):
            diagnostics[key] = targets[key]
    for name, rows, schema in zip(
        ("entities", "identifiers", "entity_annotations", "entity_evidence"),
        parts,
        (ENTITY_INPUT, IDS_INPUT, ENTITY_ANN, ENTITY_EVIDENCE_INPUT),
    ):
        _write(rows, schema, Path(out) / f"{name}-{index:05d}.parquet")
    # Relations need targets only for their resolver diagnostics, which most lack.
    with open(Path(out) / f"diagnostics-{index:05d}.pickle", "wb") as handle:
        pickle.dump(diagnostics, handle, protocol=pickle.HIGHEST_PROTOCOL)
    return str(keys_path)


def resolve_distinct(work: Path, source: str, library_dir, *, workers: int):
    """Resolve every distinct entity observation once and write its working-table rows."""
    out = work / "resolution"
    out.mkdir(exist_ok=True)
    entities = pq.ParquetFile(work / "entities.parquet")
    tasks = [
        (i, str(work / "entities.parquet"), source, str(library_dir), str(out))
        for i in range(entities.num_row_groups)
    ]
    with mp.get_context("spawn").Pool(workers) as pool:
        paths = pool.map(_resolve_chunk, tasks, chunksize=1)
    return [Path(p) for p in paths], entities.metadata.num_rows


# -- relations -------------------------------------------------------------------

# Event IDs only need to be unique: a row's relations get rid * EVENTS + n.
EVENTS = 1 << 16


def _relation_chunk(args):
    index, row_groups, work, source, dataset, predicate = args
    from omnipath_core.keys import relation_key
    from omnipath_build.writer import PAYLOAD_SCHEMA, REL_ANN, REL_INPUT, relation_rows

    work = Path(work)
    resolved = {}
    for path in sorted((work / "resolution").glob("diagnostics-*.pickle")):
        with open(path, "rb") as handle:
            resolved.update(pickle.load(handle))
    rows_file = pq.ParquetFile(work / "rows.parquet")
    relations, annotations, payloads = [], [], []
    for group in row_groups:
        for row in rows_file.read_row_group(group).to_pylist():
            batch = _Batch(source, dataset)
            batch.add(row, predicate, relation_key)
            for n, raw in enumerate(batch.relations):
                relation, anns = relation_rows(raw, resolved, row["rid"] * EVENTS + n)
                relations.append(relation)
                annotations.extend(anns)
            payloads.extend(batch.payloads)
    out = work / "relations"
    _write(relations, REL_INPUT, out / f"relations-{index:05d}.parquet")
    _write(annotations, REL_ANN, out / f"relation_annotations-{index:05d}.parquet")
    _write(payloads, PAYLOAD_SCHEMA, out / f"payloads-{index:05d}.parquet")


class _Batch:
    """The part of a SilverExtractor the writer reads, for one input row."""

    def __init__(self, source, dataset):
        self.source, self.dataset = source, dataset
        self.relations: list[RawRelationObservation] = []
        self.payloads: list[dict] = []

    def add(self, row, predicate, relation_key):
        s, o = json.loads(row["s"]), json.loads(row["o"])
        row_id = row["row_id"]
        for side in (s, o):
            for r in side["relations"]:
                self.relations.append(
                    RawRelationObservation(
                        relation_key=r["relation_key"], subject_entity_key=r["subject"],
                        predicate=r["predicate"], object_entity_key=r["object"],
                        source=self.source, dataset=self.dataset, row_id=row_id,
                        upstream_id=r["upstream_id"].replace("⟨row-id⟩", row_id),
                        statement_kind=r["statement_kind"], annotations=_annotations(r["annotations"]),
                    )
                )  # fmt: skip
        annotations = _annotations(
            {"term": a["term"], "value": a["value"], "quantity": a.get("quantity"),
             "source": self.source, "dataset": self.dataset, "scope": "relation"}
            for a in json.loads(row["anns"])
        )  # fmt: skip
        annotations += _annotations(s["annotations"]) + _annotations(o["annotations"])
        key = relation_key(s["key"], predicate, o["key"], annotations)
        # Member relations precede the record's relation, as in SilverExtractor.
        self.relations.append(
            RawRelationObservation(
                relation_key=key, subject_entity_key=s["key"], predicate=predicate,
                object_entity_key=o["key"], source=self.source, dataset=self.dataset,
                row_id=row_id, upstream_id=row["upstream_id"], annotations=annotations,
            )
        )  # fmt: skip
        for rel_key in dict.fromkeys(r.relation_key for r in self.relations):
            self.payloads.append(
                {"relation_key": rel_key, "entity_key": None, "source": self.source,
                 "row_id": row_id, "payload_json": row["payload_json"]}
            )  # fmt: skip


def _annotations(items):
    """Quantities travel as JSON text; the writer takes them as dicts."""
    out = []
    for a in items:
        a = dict(a)
        q = a.get("quantity")
        a["quantity"] = json.loads(q) if isinstance(q, str) else q
        out.append(a)
    return out


# -- writing ---------------------------------------------------------------------


def write_resource(work: Path, target: Path, source, dataset, predicate, library_dir, resolution_paths,
                   *, workers: int, final_memory="16GB", final_threads=16):
    """Make relation rows in parallel, ingest all rows once, then finalize as the row path."""
    from omnipath_build.writer import (
        ENTITY_ANN, ENTITY_EVIDENCE_INPUT, ENTITY_INPUT, IDS_INPUT, REL_ANN, REL_INPUT,
        ParquetWriter,
    )  # fmt: skip

    timings = {}
    started = time.perf_counter()
    (work / "relations").mkdir(exist_ok=True)
    groups = list(range(pq.ParquetFile(work / "rows.parquet").num_row_groups))
    tasks = [
        (i, groups[i::workers], str(work), source, dataset, predicate)
        for i in range(min(workers, len(groups)))
    ]
    with mp.get_context("spawn").Pool(len(tasks)) as pool:
        pool.map(_relation_chunk, tasks, chunksize=1)
    timings["relation_rows"] = time.perf_counter() - started

    started = time.perf_counter()
    read = lambda directory, name, schema: pa.concat_tables(
        [pq.read_table(p, schema=schema) for p in sorted((work / directory).glob(f"{name}-*.parquet"))]
    )  # fmt: skip
    target.mkdir(parents=True, exist_ok=True)
    writer = ParquetWriter(target, library_dir=library_dir, memory_limit=final_memory)
    writer.set_threads(final_threads)
    try:
        entities = read("resolution", "entities", ENTITY_INPUT)
        writer.ingest_entities(
            entities,
            read("resolution", "identifiers", IDS_INPUT),
            read("resolution", "entity_annotations", ENTITY_ANN),
            read("resolution", "entity_evidence", ENTITY_EVIDENCE_INPUT),
        )
        writer.ingest_relations(
            read("relations", "relations", REL_INPUT),
            read("relations", "relation_annotations", REL_ANN),
        )
        writer.ingest_payload_files(sorted((work / "relations").glob("payloads-*.parquet")))
        timings["ingest"] = time.perf_counter() - started
        started = time.perf_counter()
        resolution = writer.resolution_summary(resolution_paths)
        outputs = writer.close()
        timings["finalize"] = time.perf_counter() - started
    except BaseException:
        writer.abort()
        raise
    resolution["complex_composition"] = {
        "scope": "unique_precomposition_output_keys",
        "resolved": writer.metrics.get("composition_complexes", 0),
    }
    (target / "resolution_stats.json").write_text(json.dumps(resolution, indent=2) + "\n")
    return outputs, timings
