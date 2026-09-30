"""Flat Arrow/DuckDB working tables; nested serving records are built once."""

import ast
from pathlib import Path
import shutil

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
from omnipath_core.measurements import QUANTITY_STRUCT
from omnipath_core.display_names import preferred_name_sql

from omnipath_core.biolink import (
    annotation_term,
    annotation_value,
    entity_type,
    statement_identity,
    is_symmetric,
    presentation_category,
    direction_sign_from_qualifiers,
)
from omnipath_core.keys import stable_hash
from omnipath_core.naming import normalize_namespace
from .contracts import ObservationShard
from .canonical import get_policy
from .canonical.library import pin_library
from .duckdb_config import build_memory_limit, configure_memory, build_threads
from .merge_policy import preferred_label

ENTITY_SCHEMA = pa.schema(
    [
        ("entity_key", pa.string()),
        ("entity_type", pa.string()),
        ("namespace", pa.string()),
        ("identifier", pa.string()),
        ("taxon", pa.string()),
        ("label", pa.string()),
        ("has_hierarchy", pa.bool_()),
        ("parent_count", pa.int64()),
        ("child_count", pa.int64()),
        (
            "identifiers",
            pa.list_(
                pa.struct(
                    [
                        ("ns", pa.string()),
                        ("id", pa.string()),
                        ("is_canonical", pa.bool_()),
                        ("source", pa.string()),
                    ]
                )
            ),
        ),
        (
            "annotations",
            pa.list_(
                pa.struct(
                    [
                        ("term", pa.string()),
                        ("value", pa.string()),
                        ("quantity", QUANTITY_STRUCT),
                        ("source", pa.string()),
                        ("dataset", pa.string()),
                    ]
                )
            ),
        ),
    ]
)

RELATION_SCHEMA = pa.schema(
    [
        ("relation_key", pa.string()),
        ("statement_kind", pa.string()),
        ("subject_entity_key", pa.string()),
        ("subject_label", pa.string()),
        ("subject_type", pa.string()),
        ("predicate", pa.string()),
        ("object_entity_key", pa.string()),
        ("object_label", pa.string()),
        ("object_type", pa.string()),
        ("taxon", pa.string()),
        ("is_directed", pa.bool_()),
        ("sign", pa.int32()),
        ("category", pa.string()),
        ("interaction_class", pa.string()),
        ("sources", pa.list_(pa.string())),
        ("evidence_count", pa.int64()),
        (
            "evidence",
            pa.list_(
                pa.struct(
                    [
                        ("source", pa.string()),
                        ("dataset", pa.string()),
                        ("row_id", pa.string()),
                        ("upstream_id", pa.string()),
                        (
                            "annotations",
                            pa.list_(
                                pa.struct(
                                    [
                                        ("term", pa.string()),
                                        ("value", pa.string()),
                                        ("quantity", QUANTITY_STRUCT),
                                        ("source", pa.string()),
                                        ("dataset", pa.string()),
                                        ("scope", pa.string()),
                                    ]
                                )
                            ),
                        ),
                    ]
                )
            ),
        ),
        (
            "annotations",
            pa.list_(
                pa.struct(
                    [
                        ("term", pa.string()),
                        ("value", pa.string()),
                        ("quantity", QUANTITY_STRUCT),
                        ("source", pa.string()),
                        ("dataset", pa.string()),
                        ("scope", pa.string()),
                    ]
                )
            ),
        ),
    ]
)

_PAYLOAD_CHUNK_BYTES = 64 * 1024 * 1024

PAYLOAD_SCHEMA = pa.schema(
    [
        ("relation_key", pa.string()),
        ("entity_key", pa.string()),
        ("source", pa.string()),
        ("row_id", pa.string()),
        ("payload_json", pa.string()),
    ]
)


def _sql_path(path: Path) -> str:
    return str(path).replace("'", "''")


def _read_parquet_sql(paths: list[Path]) -> str:
    quoted = ", ".join(f"'{_sql_path(path)}'" for path in paths)
    return f"read_parquet([{quoted}])"


def _schema(strings, extra=()):
    return pa.schema([(n, pa.string()) for n in strings.split()] + list(extra))


PAYLOAD_STORAGE_SCHEMA = PAYLOAD_SCHEMA.set(
    PAYLOAD_SCHEMA.get_field_index("payload_json"),
    pa.field("payload_json", pa.dictionary(pa.int32(), pa.string())),
)


def _payload_batches(rows, max_bytes=_PAYLOAD_CHUNK_BYTES, max_rows=65536):
    """Bound unique JSON bytes and references, never expand shared payloads."""
    start = size = references = 0
    seen = set()
    for index, row in enumerate(rows):
        payload = row["payload_json"]
        estimate = 256 + sum(
            4 * len(value)
            for key, value in row.items()
            if key != "payload_json" and isinstance(value, str)
        )
        unique_bytes = 4 * len(payload) if payload is not None and payload not in seen else 0
        if index > start and (
            (unique_bytes and size + unique_bytes > max_bytes)
            or references + estimate > max_bytes
            or index - start >= max_rows
        ):
            yield rows[start:index]
            start, size, references, seen = index, 0, 0, set()
            unique_bytes = 4 * len(payload) if payload is not None else 0
        seen.add(payload)
        size += unique_bytes
        references += estimate
    if start < len(rows):
        yield rows[start:]


def _payload_dictionary(rows):
    values, indices, lookup = [], [], {}
    for row in rows:
        value = row["payload_json"]
        if value is None:
            indices.append(None)
            continue
        if value not in lookup:
            lookup[value] = len(values)
            values.append(value)
        indices.append(lookup[value])
    return pa.DictionaryArray.from_arrays(
        pa.array(indices, type=pa.int32()), pa.array(values, type=pa.string())
    )


ENTITY_INPUT = _schema(
    "old_key entity_type namespace identifier taxon label node_id library scope",
    [("scoped_name", pa.bool_()), ("scoped_symbol", pa.bool_())],
)
IDS_INPUT = _schema("old_key target_ns target_id ns id source")
ENTITY_ANN = _schema(
    "old_key term value source dataset",
    [("quantity", ENTITY_SCHEMA.field("annotations").type.value_type.field("quantity").type)],
)
REL_INPUT = _schema(
    "old_key subject object predicate asserted_taxon qualified statement_kind category interaction_class source dataset row_id upstream_id",
    [("event_id", pa.int64()), ("is_directed", pa.bool_()), ("sign", pa.int32())],
)
REL_ANN = _schema(
    "term value source dataset scope",
    [
        ("event_id", pa.int64()),
        ("ordinal", pa.int64()),
        ("quantity", ENTITY_SCHEMA.field("annotations").type.value_type.field("quantity").type),
    ],
)


class ParquetWriter:
    """Persist narrow working tables, resolve endpoints in SQL, aggregate at close."""

    def __init__(
        self,
        output_dir: str | Path,
        *,
        library_dir: str | Path | None = None,
        memory_limit: str | None = None,
    ):
        self.memory_limit = build_memory_limit(memory_limit)
        self.output_dir = Path(output_dir).resolve()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.library_dir = pin_library(library_dir)
        self.ent_path = self.output_dir / "entities.parquet"
        self.rel_path = self.output_dir / "relations.parquet"
        self.payload_path = self.output_dir / "evidence_payloads.parquet"
        self._payload_writer = pq.ParquetWriter(
            self.payload_path,
            PAYLOAD_STORAGE_SCHEMA,
            compression="zstd",
            dictionary_pagesize_limit=_PAYLOAD_CHUNK_BYTES,
            store_schema=False,
        )
        self._total_entities = self._total_relations = self._total_payloads = 0
        self.metrics = {"entity_chunk_rows": 0, "relation_chunk_rows": 0, "payload_rows": 0}
        self._work = self.output_dir / ".bulk"
        self._work.mkdir()
        self._db = duckdb.connect(str(self._work / "working.duckdb"))
        configure_memory(self._db, self.memory_limit)
        self._db.execute("SET threads=?", [build_threads()])
        self._db.execute("SET preserve_insertion_order=false")
        self._db.execute(f"SET temp_directory='{_sql_path(self._work / 'spill')}'")
        self._db.create_function(
            "ontology_key",
            lambda s, p, o, q: stable_hash("ontology", (s, p, o, ast.literal_eval(q))),
            [str, str, str, str],
            str,
        )
        self._event_id = 0
        self._initialized = set()

    def _input(self, name, rows, schema):
        self._db.register(name, pa.Table.from_pylist(rows, schema=schema))

    def _append(self, name, sql):
        if name not in self._initialized:
            self._db.execute(f"CREATE TABLE {name} AS {sql}")
            self._initialized.add(name)
        else:
            self._db.execute(f"INSERT INTO {name} {sql}")

    def append_observations(self, extractor, resolver, *, on_progress=None):
        resolved = resolver.resolve_entity_targets(extractor.entities, progress=False)
        entities, identifiers, entity_anns = [], [], []
        for old, raw in extractor.entities.items():
            for info in resolved[old]:
                et = info.entity_type or entity_type(raw.entity_type)
                policy = get_policy(et)
                ns = normalize_namespace(info.canonical_namespace) or str(
                    info.canonical_namespace or "unknown"
                )
                taxon = info.taxon or raw.taxon or ""
                entities.append(
                    dict(
                        old_key=old,
                        entity_type=et,
                        namespace=ns,
                        identifier=info.canonical_identifier,
                        taxon=taxon,
                        label=preferred_label(info.canonical_identifier, raw.label, info.label),
                        node_id=info.node_id,
                        library=info.reference_library or policy.library,
                        scope=raw.identity_scope or "",
                        scoped_name=not info.matched and ns in {"name", "synonym"},
                        scoped_symbol=bool(taxon)
                        and not info.matched
                        and (ns in policy.symbol_namespaces or ns == "guidetopharma_target"),
                    )
                )
                for ident in raw.identifiers:
                    identifiers.append(
                        dict(
                            old_key=old,
                            target_ns=ns,
                            target_id=info.canonical_identifier,
                            ns=normalize_namespace(ident.get("ns", "")) or str(ident.get("ns", "")),
                            id=ident["id"],
                            source=ident.get("source", "raw"),
                        )
                    )
                for alias_ns, values in info.aliases.items():
                    ns_norm = normalize_namespace(alias_ns) or str(alias_ns)
                    identifiers.extend(
                        dict(
                            old_key=old,
                            target_ns=ns,
                            target_id=info.canonical_identifier,
                            ns=ns_norm,
                            id=val,
                            source="resolver",
                        )
                        for val in values
                    )
            entity_anns.extend(
                dict(
                    old_key=old,
                    term=annotation_term(a.get("term", "")),
                    value=a.get("value", ""),
                    quantity=a.get("quantity"),
                    source=a.get("source", ""),
                    dataset=a.get("dataset", ""),
                )
                for a in raw.annotations
            )
        self._input("input_entities", entities, ENTITY_INPUT)
        self._db.execute("""CREATE OR REPLACE TEMP TABLE entity_map AS
            WITH base AS (SELECT *, sha256(lower(trim(entity_type)) || chr(0) || lower(trim(namespace)) || chr(0) ||
                               trim(identifier) || chr(0)) AS base_key FROM input_entities)
            SELECT *, CASE WHEN scoped_name THEN sha256(base_key || chr(0) || scope || chr(0) || taxon || chr(0))
                      WHEN scoped_symbol THEN sha256(base_key || chr(0) || taxon || chr(0))
                      ELSE base_key END AS entity_key FROM base""")
        self._append(
            "entities",
            "SELECT entity_key, entity_type, namespace, identifier, taxon, label FROM entity_map",
        )
        self._append(
            "refs",
            """SELECT entity_key, entity_type, namespace, identifier, library, node_id
                                FROM entity_map WHERE node_id IS NOT NULL""",
        )
        self._input("input_ids", identifiers, IDS_INPUT)
        self._append(
            "identifiers",
            """SELECT e.entity_key, i.ns, i.id, i.source,
                   (i.ns=e.namespace AND i.id=e.identifier) AS is_canonical
            FROM input_ids i JOIN entity_map e ON i.old_key=e.old_key AND i.target_ns=e.namespace AND i.target_id=e.identifier
            UNION ALL SELECT entity_key, namespace, identifier, 'canonical', true FROM entity_map""",
        )
        self._input("input_entity_anns", entity_anns, ENTITY_ANN)
        self._append(
            "entity_annotations",
            """SELECT e.entity_key, a.term, a.value, a.quantity, a.source, a.dataset
            FROM input_entity_anns a JOIN entity_map e ON a.old_key=e.old_key""",
        )

        relations, relation_anns = [], []
        for raw in extractor.relations:
            event_id = self._event_id
            self._event_id += 1
            anns = [
                {
                    **a,
                    "term": annotation_term(a["term"]),
                    "value": annotation_value(a["term"], a.get("value")) or "",
                }
                for a in raw.annotations
            ]
            _, pred, _, qualified = statement_identity("", raw.predicate, "", anns)
            if raw.statement_kind not in {"relation", "ontology"}:
                raise ValueError(f"Unknown statement kind: {raw.statement_kind}")
            asserted_taxa = {
                a["value"].removeprefix("NCBITaxon:")
                for a in anns
                if a["term"] == "in_taxon" and a.get("scope", "relation") == "relation"
            }
            # The scalar serving taxon is a consensus projection; evidence retains every assertion.
            asserted_taxon = next(iter(asserted_taxa)) if len(asserted_taxa) == 1 else ""
            directed = not is_symmetric(pred)
            sign = direction_sign_from_qualifiers(qualified)
            relations.append(
                dict(
                    old_key=raw.relation_key,
                    subject=raw.subject_entity_key,
                    object=raw.object_entity_key,
                    predicate=pred,
                    asserted_taxon=asserted_taxon,
                    qualified=str(qualified),
                    statement_kind=raw.statement_kind,
                    is_directed=directed,
                    sign=sign,
                    category=presentation_category(pred),
                    interaction_class="undirected"
                    if not directed
                    else {1: "directed_stimulatory", -1: "directed_inhibitory", 0: "directed"}[
                        sign
                    ],
                    source=str(raw.source),
                    dataset=str(raw.dataset),
                    row_id=str(raw.row_id),
                    upstream_id=str(raw.upstream_id),
                    event_id=event_id,
                )
            )
            relation_anns.extend(
                dict(
                    event_id=event_id,
                    ordinal=i,
                    term=str(a.get("term", "")),
                    value=str(a.get("value", "")),
                    quantity=a.get("quantity"),
                    source=str(a.get("source", "")),
                    dataset=str(a.get("dataset", "")),
                    scope=str(a.get("scope", "relation")),
                )
                for i, a in enumerate(anns)
            )
        self._input("input_relations", relations, REL_INPUT)
        self._db.execute("""CREATE OR REPLACE TEMP TABLE relation_map AS
            WITH endpoints AS (
                SELECT r.*, coalesce(s.entity_key, r.subject) AS s, coalesce(o.entity_key,r.object) AS o
                FROM input_relations r LEFT JOIN entity_map s ON r.subject=s.old_key
                LEFT JOIN entity_map o ON r.object=o.old_key
            ), oriented AS (
                SELECT *, NOT is_directed AND qualified='()' AND s>o AS flipped FROM endpoints
            ), normalized AS (
                SELECT *, CASE WHEN flipped THEN o ELSE s END AS subject_entity_key,
                          CASE WHEN flipped THEN s ELSE o END AS object_entity_key FROM oriented
            ) SELECT *, CASE WHEN statement_kind='ontology'
                THEN ontology_key(subject_entity_key, predicate, object_entity_key, qualified)
                ELSE sha256(subject_entity_key || chr(0) || predicate || chr(0) || object_entity_key || chr(0) ||
                    CASE WHEN qualified='()' THEN '' ELSE qualified || chr(0) END) END AS relation_key
            FROM normalized QUALIFY row_number() OVER (PARTITION BY event_id, relation_key)=1""")
        self._append(
            "relations",
            """SELECT relation_key, statement_kind, subject_entity_key,
            subject_entity_key AS subject_label, 'protein' AS subject_type, predicate,
            object_entity_key, object_entity_key AS object_label, 'protein' AS object_type,
            asserted_taxon AS taxon, is_directed, sign, category, interaction_class,
            event_id, source, dataset, row_id, upstream_id, qualified FROM relation_map""",
        )
        self._input("input_relation_anns", relation_anns, REL_ANN)
        self._append(
            "relation_annotations",
            """SELECT r.relation_key, a.event_id, a.ordinal, a.term, a.value, a.quantity,
            a.source, a.dataset, CASE WHEN r.flipped THEN CASE a.scope WHEN 'subject' THEN 'object'
                WHEN 'object' THEN 'subject' ELSE a.scope END ELSE a.scope END AS scope
            FROM input_relation_anns a JOIN relation_map r USING (event_id)""",
        )
        self._db.execute("""CREATE OR REPLACE TEMP TABLE payload_relation_map AS
            SELECT DISTINCT old_key, relation_key AS new_key
            FROM relation_map""")
        payload_written = 0
        payload_output_rows = 0
        for payload_rows in _payload_batches(extractor.payloads):
            if on_progress:
                on_progress(payload_written, len(extractor.payloads))
            # Only endpoint keys enter SQL. The JSON dictionary stays in Arrow.
            narrow = [
                {key: value for key, value in row.items() if key != "payload_json"}
                | {"payload_index": index}
                for index, row in enumerate(payload_rows)
            ]
            schema = PAYLOAD_SCHEMA.remove(4).append(pa.field("payload_index", pa.int64()))
            self._input("input_payloads", narrow, schema)
            cursor = self._db.execute(
                "SELECT coalesce(r.new_key, p.relation_key) AS relation_key, "
                "coalesce(e.entity_key, p.entity_key) AS entity_key, p.source, p.row_id, p.payload_index "
                "FROM input_payloads p LEFT JOIN payload_relation_map r ON p.relation_key=r.old_key "
                "LEFT JOIN entity_map e ON p.entity_key=e.old_key ORDER BY p.payload_index"
            )
            table = (
                cursor.to_arrow_table()
                if hasattr(cursor, "to_arrow_table")
                else cursor.fetch_arrow_table()
            )
            payloads = _payload_dictionary(payload_rows).take(
                table["payload_index"].combine_chunks()
            )
            table = table.drop(["payload_index"]).append_column("payload_json", payloads)
            self._payload_writer.write_table(table, row_group_size=len(payload_rows))
            payload_written += len(payload_rows)
            payload_output_rows += table.num_rows
        if on_progress:
            on_progress(payload_written, len(extractor.payloads))
        if not extractor.payloads:
            self._input("input_payloads", [], PAYLOAD_SCHEMA.remove(4))
        counts = (
            len(entities),
            self._db.execute("SELECT count(*) FROM relation_map").fetchone()[0],
            payload_output_rows,
        )
        for key, value in zip(("entity_chunk_rows", "relation_chunk_rows", "payload_rows"), counts):
            self.metrics[key] += value
        self._total_payloads += counts[2]
        for name in (
            "input_entities",
            "input_ids",
            "input_entity_anns",
            "input_relations",
            "input_relation_anns",
            "input_payloads",
        ):
            self._db.unregister(name)
        return counts

    def _reference_identifiers(self):
        from .reference.full_index_runtime import FullRuntime

        policies = self._db.execute("SELECT DISTINCT entity_type, library FROM refs").fetchall()
        runtime = FullRuntime(self.library_dir) if policies else None
        for library in dict.fromkeys(lib for _, lib in policies):
            quoted_library = "'" + library.replace("'", "''") + "'"
            entity_ids = (
                row[0]
                for row in self._db.execute(
                    f"SELECT DISTINCT node_id FROM refs WHERE library={quoted_library} ORDER BY node_id"
                ).fetchall()
            )
            self._db.execute(
                "CREATE TEMP TABLE reference_identifiers(entity_id VARCHAR,namespace VARCHAR,identifier VARCHAR)"
            )
            try:
                batch = []
                for entity_id in entity_ids:
                    record = runtime.record(entity_id)
                    batch.extend(
                        {"entity_id": entity_id, "namespace": ns, "identifier": identifier}
                        for ns, identifier in record["identifiers"]
                    )
                    if len(batch) >= 65536:
                        self._db.register("reference_identifier_batch", pa.Table.from_pylist(batch))
                        self._db.execute(
                            "INSERT INTO reference_identifiers SELECT * FROM reference_identifier_batch"
                        )
                        self._db.unregister("reference_identifier_batch")
                        batch.clear()
                if batch:
                    self._db.register("reference_identifier_batch", pa.Table.from_pylist(batch))
                    self._db.execute(
                        "INSERT INTO reference_identifiers SELECT * FROM reference_identifier_batch"
                    )
                    self._db.unregister("reference_identifier_batch")
                for et, lib in policies:
                    if lib != library:
                        continue
                    allowed = sorted(get_policy(et).alias_namespaces)
                    self._db.execute(
                        """INSERT INTO identifiers
                        SELECT r.entity_key,i.namespace,i.identifier,'resolver',
                               i.namespace=r.namespace AND i.identifier=r.identifier
                        FROM (SELECT DISTINCT * FROM refs WHERE entity_type=? AND library=?) r
                        JOIN reference_identifiers i ON i.entity_id=r.node_id
                        WHERE list_contains(?,i.namespace)
                        UNION ALL SELECT entity_key,namespace,identifier,'resolver',true
                        FROM refs WHERE entity_type=? AND library=?""",
                        [et, library, allowed, et, library],
                    )
            finally:
                self._db.execute("DROP TABLE reference_identifiers")
        if runtime is not None:
            runtime.close()

    def _prepare_display(self):
        """Attach global display metadata before constructing nested evidence."""
        from omnipath_core.biolink import hierarchy_direction

        self._db.execute("""CREATE TEMP TABLE entity_display AS
        SELECT entity_key, min(entity_type) AS entity_type, min(namespace) AS namespace,
            min(identifier) AS identifier, coalesce(min(nullif(taxon,'')),'') AS taxon,
            coalesce(first(nullif(label,'') ORDER BY CASE WHEN nullif(label,'') IS NULL THEN 2
                WHEN label<>identifier AND NOT regexp_matches(label,'^[0-9]+$') THEN 0 ELSE 1 END,label), min(identifier)) AS label
        FROM entities GROUP BY entity_key
""")
        directions = []
        for (predicate,) in self._db.execute(
            "SELECT DISTINCT predicate FROM relations WHERE statement_kind='ontology'"
        ).fetchall():
            reverse = hierarchy_direction(predicate)
            if reverse is not None:
                directions.append((predicate, reverse))
        self._db.register(
            "hierarchy_predicates",
            pa.table(
                {
                    "predicate": pa.array([p for p, _ in directions], type=pa.string()),
                    "reverse": pa.array([r for _, r in directions], type=pa.bool_()),
                }
            ),
        )
        self._db.execute("""CREATE TEMP TABLE hierarchy AS
            SELECT DISTINCT
                CASE WHEN reverse THEN object_entity_key ELSE subject_entity_key END AS child,
                CASE WHEN reverse THEN subject_entity_key ELSE object_entity_key END AS parent
            FROM relations JOIN hierarchy_predicates USING(predicate)
            WHERE statement_kind='ontology'
            """)
        self._db.execute("""CREATE TABLE display AS
            SELECT e.*, (coalesce(p.n,0)+coalesce(c.n,0)>0) AS has_hierarchy,
                coalesce(p.n,0)::BIGINT AS parent_count, coalesce(c.n,0)::BIGINT AS child_count
            FROM entity_display e
            LEFT JOIN (SELECT child,count(*) AS n FROM hierarchy GROUP BY child) p ON e.entity_key=p.child
            LEFT JOIN (SELECT parent,count(*) AS n FROM hierarchy GROUP BY parent) c ON e.entity_key=c.parent""")
        self._db.execute("""CREATE TABLE displayed_relations AS
            SELECT r.* REPLACE (
                coalesce(s.label,r.subject_label) AS subject_label,
                coalesce(s.entity_type,r.subject_type) AS subject_type,
                coalesce(o.label,r.object_label) AS object_label,
                coalesce(o.entity_type,r.object_type) AS object_type,
                CASE WHEN EXISTS (SELECT 1 FROM relation_annotations a
                         WHERE a.relation_key=r.relation_key AND a.event_id=r.event_id
                         AND a.term='in_taxon' AND a.scope='relation') THEN r.taxon
                    WHEN nullif(s.taxon,'') IS NOT NULL AND nullif(o.taxon,'') IS NOT NULL AND s.taxon<>o.taxon THEN ''
                    ELSE coalesce(nullif(s.taxon,''),nullif(o.taxon,''),'') END AS taxon)
            FROM relations r LEFT JOIN entity_display s ON r.subject_entity_key=s.entity_key
            LEFT JOIN entity_display o ON r.object_entity_key=o.entity_key""")
        self._db.execute("DROP TABLE relations")
        self._db.execute("ALTER TABLE displayed_relations RENAME TO relations")
        self._db.execute("DROP TABLE hierarchy")
        self._db.execute("DROP TABLE entity_display")
        self._db.unregister("hierarchy_predicates")

    def abort(self) -> None:
        """Release resources after a failed build without publishing final tables."""
        try:
            self._payload_writer.close()
        finally:
            self._db.close()

    def set_threads(self, threads: int) -> None:
        if threads < 1:
            raise ValueError("threads must be positive")
        self._db.execute("SET threads=?", [threads])

    def resolution_summary(self, paths: list[Path]) -> dict:
        """Merge disk-backed key shards and reject contradictory resolutions."""
        from .canonical.stats import ResolutionTracker

        self._db.execute(
            "CREATE TEMP TABLE resolution_keys AS SELECT DISTINCT * FROM read_parquet(?)",
            [[str(path) for path in paths]],
        )
        try:
            duplicates = self._db.execute(
                "SELECT count(*) FROM (SELECT key FROM resolution_keys GROUP BY key HAVING count(*)>1)"
            ).fetchone()[0]
            if duplicates:
                raise ValueError("Inconsistent resolution outcomes between shards")
            tracker = ResolutionTracker()
            try:
                for entity_type, status, rule, count in self._db.execute(
                    "SELECT entity_type,status,rule,count(*) FROM resolution_keys GROUP BY ALL"
                ).fetchall():
                    tracker.add_counts(entity_type, status, rule, count)
                return tracker.summary()
            finally:
                tracker.close()
        finally:
            self._db.execute("DROP TABLE resolution_keys")

    def seal_observation_shard(self) -> ObservationShard:
        """Close a private worker shard without aggregating its observations."""
        self._payload_writer.close()
        self._db.execute("CHECKPOINT")
        self._db.close()
        return {
            "directory": str(self.output_dir),
            "tables": sorted(self._initialized),
            "events": self._event_id,
            "payloads": self._total_payloads,
            "metrics": dict(self.metrics),
        }

    def import_observation_shard(self, shard: ObservationShard) -> None:
        """Import sealed flat tables, assigning disjoint evidence event IDs."""
        directory = Path(shard["directory"])
        self._db.execute(
            f"ATTACH '{_sql_path(directory / '.bulk' / 'working.duckdb')}' AS shard (READ_ONLY)"
        )
        try:
            for table in shard["tables"]:
                if table not in {
                    "entities",
                    "refs",
                    "identifiers",
                    "entity_annotations",
                    "relations",
                    "relation_annotations",
                }:
                    raise ValueError(f"Unknown observation table: {table}")
                projection = "*"
                if table in {"relations", "relation_annotations"}:
                    projection = f"* REPLACE (event_id + {self._event_id} AS event_id)"
                self._append(table, f"SELECT {projection} FROM shard.{table}")
        finally:
            self._db.execute("DETACH shard")
        self._event_id += shard["events"]
        # Preserve dictionary encoding while concatenating logical evidence rows.
        file = pq.ParquetFile(
            directory / "evidence_payloads.parquet", read_dictionary=["payload_json"]
        )
        for group in range(file.num_row_groups):
            self._payload_writer.write_table(file.read_row_group(group))
        self._total_payloads += shard["payloads"]
        for key, value in shard["metrics"].items():
            self.metrics[key] = self.metrics.get(key, 0) + value

    def close(self):
        self._payload_writer.close()
        if not self._initialized:
            # Preserve empty-resource schemas without special SQL inference.
            self._db.close()
            pq.write_table(pa.Table.from_batches([], schema=ENTITY_SCHEMA), self.ent_path)
            pq.write_table(pa.Table.from_batches([], schema=RELATION_SCHEMA), self.rel_path)
            self._total_entities = self._total_relations = 0
        else:
            from .complexes import resolve_complexes

            resolve_complexes(self._db, self.payload_path, self.metrics)
            self._reference_identifiers()
            self._prepare_display()
            parts = {"entities": [], "relations": []}
            for table in (
                "display",
                "identifiers",
                "entity_annotations",
                "relations",
                "relation_annotations",
            ):
                key = "relation_key" if table.startswith("relation") else "entity_key"
                self._db.execute(
                    f"CREATE TABLE sorted_{table} AS SELECT *, hash({key}) % 64 AS bucket FROM {table} ORDER BY bucket"
                )
                self._db.execute(f"DROP TABLE {table}")
                self._db.execute(f"ALTER TABLE sorted_{table} RENAME TO {table}")
            for bucket in range(64):
                for table in (
                    "display",
                    "identifiers",
                    "entity_annotations",
                    "relations",
                    "relation_annotations",
                ):
                    self._db.execute(
                        f"CREATE OR REPLACE TEMP VIEW b_{table} AS SELECT * EXCLUDE(bucket) FROM {table} WHERE bucket={bucket}"
                    )
                for kind, query in (("entities", _entities_sql()), ("relations", _relations_sql())):
                    path = self._work / f"{kind}-{bucket}.parquet"
                    self._db.execute(
                        f"COPY ({query}) TO '{_sql_path(path)}' (FORMAT PARQUET, COMPRESSION ZSTD)"
                    )
                    parts[kind].append(path)
            for kind, key, dest in (
                ("entities", "entity_key", self.ent_path),
                ("relations", "relation_key", self.rel_path),
            ):
                scan = _read_parquet_sql(parts[kind])
                count = self._db.execute(f"SELECT count(*) FROM {scan}").fetchone()[0]
                setattr(self, f"_total_{kind}", count)
                metadata = (
                    ", KV_METADATA {omnipath_label_policy: 'preferred-name-v1'}"
                    if kind == "entities"
                    else ""
                )
                self._db.execute(
                    f"COPY (SELECT * FROM {scan} ORDER BY {key}) TO '{_sql_path(dest)}' (FORMAT PARQUET, COMPRESSION ZSTD{metadata})"
                )
            self.metrics["temporary_chunk_bytes"] = sum(
                p.stat().st_size for p in self._work.rglob("*") if p.is_file()
            )
            self._db.close()
        shutil.rmtree(self._work)
        return (
            self.ent_path,
            self.rel_path,
            self.payload_path,
            self._total_entities,
            self._total_relations,
            self._total_payloads,
        )


def _entities_sql():
    nested = """WITH base AS (
        SELECT * FROM b_display
    ), ids AS (
        SELECT entity_key, ns, id, source, bool_or(is_canonical) AS is_canonical
        FROM b_identifiers GROUP BY entity_key, ns, id, source
    ), grouped_ids AS (
        SELECT entity_key, list(struct_pack(ns:=ns,id:=id,is_canonical:=is_canonical,source:=source)
            ORDER BY is_canonical DESC,ns,id,source) AS identifiers FROM ids GROUP BY entity_key
    ), anns AS (
        SELECT entity_key, list(DISTINCT struct_pack(term:=term,value:=value,quantity:=quantity,source:=source,dataset:=dataset)
            ORDER BY struct_pack(term:=term,value:=value,quantity:=quantity,source:=source,dataset:=dataset)) AS annotations
        FROM b_entity_annotations GROUP BY entity_key
    ) SELECT base.*,
        grouped_ids.identifiers, coalesce(anns.annotations, []) AS annotations
        FROM base JOIN grouped_ids USING(entity_key) LEFT JOIN anns USING(entity_key)"""
    return f"""SELECT * REPLACE ({preferred_name_sql()} AS label,
        CASE WHEN label IS NULL OR label = '' OR list_contains(list_transform(identifiers, x -> x.id), label)
        THEN identifiers ELSE list_append(identifiers, struct_pack(ns := 'name', id := label, is_canonical := false, source := '')) END AS identifiers)
        FROM ({nested})"""


def _relations_sql():
    scalar = [
        f.name
        for f in RELATION_SCHEMA
        if f.name not in {"relation_key", "sources", "evidence_count", "evidence", "annotations"}
    ]
    scalars = ",".join(
        "CASE WHEN count(DISTINCT taxon)=1 THEN min(taxon) ELSE '' END AS taxon"
        if n == "taxon"
        else f'min("{n}") AS "{n}"'
        for n in scalar
    )
    ann = "struct_pack(term:=term,value:=value,quantity:=quantity,source:=source,dataset:=dataset,scope:=scope)"
    return f"""WITH base AS (
        SELECT relation_key, {scalars}, list(DISTINCT source ORDER BY source) AS sources
        FROM b_relations GROUP BY relation_key
    ), occurrence_anns AS (
        SELECT relation_key, event_id, list({ann} ORDER BY ordinal) AS annotations
        FROM b_relation_annotations GROUP BY relation_key, event_id
    ), evidence AS (
        SELECT r.relation_key, struct_pack(source:=r.source,dataset:=r.dataset,row_id:=r.row_id,
            upstream_id:=r.upstream_id,annotations:=coalesce(a.annotations,[])) AS item
        FROM b_relations r LEFT JOIN occurrence_anns a USING(relation_key, event_id)
    ), grouped_evidence AS (
        SELECT relation_key, count(*) AS evidence_count, list(item ORDER BY item) AS evidence
        FROM evidence GROUP BY relation_key
    ), anns AS (
        SELECT relation_key, list(DISTINCT {ann} ORDER BY {ann}) AS annotations
        FROM b_relation_annotations GROUP BY relation_key
    ) SELECT base.*, grouped_evidence.evidence_count, grouped_evidence.evidence, coalesce(anns.annotations,[]) AS annotations
        FROM base JOIN grouped_evidence USING(relation_key) LEFT JOIN anns USING(relation_key)"""
