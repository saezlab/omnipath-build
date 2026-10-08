"""Resolve and write columnar observations with the existing resolver and writer.

Resolution depends only on the observation, so each distinct entity observation of
the resource is resolved once, in parallel chunks, instead of once per batch it
recurs in. Rows are then written in parallel shards by the existing
``ParquetWriter``, whose resolver returns the precomputed targets, and the shards
are finalized as in the row path. Outputs are those of the row path by
construction; the speed comes from distinct inputs and parallelism.
"""

from __future__ import annotations

import json
import multiprocessing as mp
import pickle
import time
from pathlib import Path

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
    ) TO '{work}/entities.parquet' (FORMAT PARQUET)""")
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


def _resolve_chunk(args):
    index, keys, entities_path, source, library_dir, out = args
    from omnipath_resolver import EntityResolver

    table = pq.read_table(entities_path, filters=[("key", "in", keys)])
    observations = {
        k: _observation(json.loads(e), source)
        for k, e in zip(table["key"].to_pylist(), table["entity"].to_pylist())
    }
    resolver = EntityResolver(library_dir, defer_aliases=True)
    try:
        targets = resolver.resolve_entity_targets(observations, progress=False)
        keys_path = Path(out) / f"resolution_keys-{index:05d}.parquet"
        resolver.export_resolution_keys(keys_path)
        stats = resolver.resolution_stats()
    finally:
        resolver.close()
    with open(Path(out) / f"targets-{index:05d}.pickle", "wb") as handle:
        pickle.dump(targets, handle, protocol=pickle.HIGHEST_PROTOCOL)
    return str(keys_path), stats


def resolve_distinct(work: Path, source: str, library_dir, *, workers: int, chunk: int = 4096):
    """Resolve every distinct entity observation once; returns resolution key paths and stats."""
    out = work / "resolution"
    out.mkdir(exist_ok=True)
    keys = pq.read_table(work / "entities.parquet", columns=["key"])["key"].to_pylist()
    tasks = [
        (i, keys[o : o + chunk], str(work / "entities.parquet"), source, str(library_dir), str(out))
        for i, o in enumerate(range(0, len(keys), chunk))
    ]
    with mp.get_context("spawn").Pool(workers) as pool:
        results = pool.map(_resolve_chunk, tasks, chunksize=1)
    return [Path(p) for p, _ in results], [s for _, s in results], len(keys)


class PrecomputedResolver:
    """The writer's resolver interface over targets resolved in advance."""

    def __init__(self, targets: dict):
        self.targets = targets

    def resolve_entity_targets(self, entities, progress=False):
        return {key: self.targets[key] for key in entities}


# -- writing ---------------------------------------------------------------------


def _write_shard(args):
    shard, shards, work, source, dataset, predicate, library_dir, batch = args
    from omnipath_core.keys import relation_key
    from omnipath_build.writer import ParquetWriter

    work = Path(work)
    targets = {}
    for path in sorted((work / "resolution").glob("targets-*.pickle")):
        with open(path, "rb") as handle:
            targets.update(pickle.load(handle))
    resolver = PrecomputedResolver(targets)
    writer = ParquetWriter(work / "shards" / f"shard-{shard:03d}", library_dir=library_dir)
    try:
        rows = pq.ParquetFile(work / "rows.parquet")
        group = _Batch(source, dataset)
        for row_group in range(rows.num_row_groups):
            for row in rows.read_row_group(row_group).to_pylist():
                if row["rid"] % shards != shard:
                    continue
                group.add(row, predicate, relation_key)
                if len(group.relations) >= batch:
                    writer.append_observations(group, resolver)
                    group = _Batch(source, dataset)
        if group.relations:
            writer.append_observations(group, resolver)
        return writer.seal_observation_shard()
    except BaseException:
        writer.abort()
        raise


class _Batch:
    """The part of a SilverExtractor the writer reads: entities, relations, payloads."""

    def __init__(self, source, dataset):
        self.source, self.dataset = source, dataset
        self.entities: dict[str, RawEntityObservation] = {}
        self.relations: list[RawRelationObservation] = []
        self.payloads: list[dict] = []

    def _entities(self, side):
        for entity in side["entities"]:
            if entity["key"] not in self.entities:
                self.entities[entity["key"]] = _observation(entity, self.source)

    def add(self, row, predicate, relation_key):
        s, o = json.loads(row["s"]), json.loads(row["o"])
        self._entities(s)
        self._entities(o)
        row_id = row["row_id"]
        start = len(self.relations)
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
        annotations = [
            {"term": a["term"], "value": a["value"], "quantity": a.get("quantity"),
             "source": self.source, "dataset": self.dataset, "scope": "relation"}
            for a in json.loads(row["anns"])
        ]  # fmt: skip
        annotations = _annotations(annotations)
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
        for rel_key in dict.fromkeys(r.relation_key for r in self.relations[start:]):
            self.payloads.append(
                {"relation_key": rel_key, "source": self.source, "row_id": row_id,
                 "payload_json": row["payload_json"]}
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


def write_resource(work: Path, target: Path, source, dataset, predicate, library_dir, resolution_paths,
                   *, shards: int, batch: int = 20000, final_memory="16GB", final_threads=16):
    """Write shards in parallel, then finalize them into ``target`` as the row path does."""
    from omnipath_build.writer import ParquetWriter

    started = time.perf_counter()
    (work / "shards").mkdir(exist_ok=True)
    tasks = [
        (i, shards, str(work), source, dataset, predicate, str(library_dir), batch)
        for i in range(shards)
    ]
    with mp.get_context("spawn").Pool(shards) as pool:
        sealed = pool.map(_write_shard, tasks, chunksize=1)
    written = time.perf_counter()
    target.mkdir(parents=True, exist_ok=True)
    writer = ParquetWriter(target, library_dir=library_dir, memory_limit=final_memory)
    writer.set_threads(final_threads)
    try:
        for shard in sealed:
            writer.import_observation_shard(shard)
        resolution = writer.resolution_summary(resolution_paths)
        outputs = writer.close()
    except BaseException:
        writer.abort()
        raise
    resolution["complex_composition"] = {
        "scope": "unique_precomposition_output_keys",
        "resolved": writer.metrics.get("composition_complexes", 0),
    }
    (target / "resolution_stats.json").write_text(json.dumps(resolution, indent=2) + "\n")
    return outputs, dict(write_shards=written - started, finalize=time.perf_counter() - written)
