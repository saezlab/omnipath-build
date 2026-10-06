"""On-demand resolution runtime over an ``omnipath-identity-v1`` Parquet snapshot.

Same ``resolve()`` contract as :class:`omnipath_resolver.index.FullRuntime`, but the
postings are read from partitioned Parquet on demand (one DuckDB query per batch over
only the partition files the keys hash to) and entity records are assembled on first
use from the member records' hub rows, then cached per snapshot fingerprint in SQLite.
See ``docs/identity-layer-spec.md`` section 4.
"""

from __future__ import annotations

from collections import defaultdict
from functools import lru_cache
import glob
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import threading
import time

from .index_storage import partition
from .observations import CODES

FORMAT = "omnipath-identity-v1"
# Bump when record assembly or label policy changes: cached records are discarded.
RECORD_POLICY = "identity-record-v1"
CACHE_ENV = "OMNIPATH_IDENTITY_CACHE"

KINDS = {"chemical": 1, "protein": 2, "gene": 3}
GENE_NAMESPACES = frozenset({"hgnc", "ensg", "enst", "refseq", "genesymbol", "genesymbol-syn"})
SYMBOL_NAMESPACES = frozenset({"genesymbol", "genesymbol-syn"})
# Tags carried by access rows (spec section 3).
NATIVE, FALLBACK, SECONDARY, SYMBOL_SYNONYM = "native", "fallback", "secondary", "symbol_synonym"

_INCHIKEY = re.compile(r"^[A-Z]{14}-[A-Z]{10}-[A-Z]$")
_INCHIKEY_ANY = re.compile(r"^[A-Z]{14}-[A-Z]{10}-[A-Z0-9]$")
_ACCESSION = re.compile(r"^[A-Z][A-Z0-9]{5,9}(?:-[0-9]+)?$")
MAX_LABEL = 80
# Goslin levels, least to most specific (pygoslin ``LipidLevel`` order).
GOSLIN_LEVELS = (
    "category",
    "class",
    "species",
    "molecular_species",
    "sn_position",
    "structure_defined",
    "full_structure",
    "complete_structure",
)
# Hub rows kept out of ``identifiers``: attributes, and names that only feed labels.
NOT_IDENTIFIERS = frozenset(
    {"smiles", "inchi", "formula", "synonym", "lipid_shorthand", "systematic_name", "iupac_name"}
)
CHEMICAL_NAME_HUBS = ("chebi", "hmdb", "chembl", "pubchem")


def is_identity_snapshot(path) -> bool:
    """True when ``path`` is a directory whose manifest declares the identity format."""
    manifest = Path(path) / "manifest.json"
    if not manifest.is_file():
        return False
    try:
        return json.loads(manifest.read_text()).get("format") == FORMAT
    except (OSError, ValueError):
        return False


def default_cache_dir(path) -> Path:
    return Path(os.environ.get(CACHE_ENV) or Path(path).resolve().parent / "cache")


def open_runtime(path, *, memory_limit=None, cache_dir=None):
    """The runtime for a pinned reference directory, chosen by its manifest ``format``."""
    if is_identity_snapshot(path):
        return IdentityRuntime(path, cache_dir=cache_dir, memory_limit=memory_limit)
    from .index import FullRuntime

    return FullRuntime(path)


@lru_cache(maxsize=1 << 20)
def entity_num(entity_id: str) -> int:
    """First 8 bytes (big-endian) of sha256(entity_id) as an unsigned int, shifted right by one."""
    return int.from_bytes(hashlib.sha256(entity_id.encode()).digest()[:8], "big") >> 1


def decode_key(key: bytes, codes: dict[int, str]):
    """Inverse of ``observations.key``: ``(target, route, ns, scope|None, identifier)``."""
    if (
        len(key) < 7
        or key[0] != 1
        or key[1] not in (1, 2)
        or key[2] not in (1, 2)
        or key[5] not in (0, 1)
    ):
        raise ValueError("Unsupported normalized lookup key")
    ns = codes.get(int.from_bytes(key[3:5], "big"))
    if ns is None:
        raise ValueError("Unsupported identifier namespace")
    offset = 10 if key[5] else 6
    identifier = key[offset:].decode()
    if not identifier:
        raise ValueError("Empty identifier")
    scope = str(int.from_bytes(key[6:10], "big")) if key[5] else None
    return key[1], key[2], ns, scope, identifier


def clean_anchor(kind: int, raw) -> str | None:
    """Anchor in the form the decision kernel accepts, else None.

    Chemical anchors are ``inchikey:<key>``, protein anchors ``uniprot:<acc>``; genes and
    lipid-name entities (``goslin:...``) are unanchored for the kernel. Bare keys and
    accessions are accepted and prefixed.
    """
    if not raw:
        return None
    raw = str(raw)
    if kind == 1:
        bare = raw.removeprefix("inchikey:")
        return "inchikey:" + bare if _INCHIKEY.fullmatch(bare) else None
    if kind == 2:
        bare = raw.removeprefix("uniprot:")
        return "uniprot:" + bare if _ACCESSION.fullmatch(bare) else None
    return None


def _taxon(value) -> str | None:
    text = str(value).strip() if value is not None else ""
    return None if text in ("", "0", "None") else text


def _short_first(values):
    """Deterministic choice: shortest, then alphabetical."""
    return min(values, key=lambda v: (len(v), v), default=None)


def _usable(values, limit):
    out = {str(v).strip() for v in values if v is not None}
    return {v for v in out if v and len(v) <= limit}


def choose_label(kind: int, entity_id: str, rows: list[tuple[str, str, str]]) -> str:
    """Label policy (spec rule 13).

    ``rows`` are ``(hub, source_type, value)`` for every member record. First available:
    gene: NCBI Gene symbol; protein: primary gene name > entry name > accession;
    chemical: lipid Goslin shorthand, else ChEBI > HMDB > ChEMBL > PubChem > other hub name
    > systematic name (InChIKey-shaped and over-long values skipped); else the id local part.
    """
    local = entity_id.split(":", 1)[1] if ":" in entity_id else entity_id
    by_type = defaultdict(list)
    for hub, source_type, value in rows:
        by_type[source_type].append((hub, value))

    def values(source_type, hubs=None):
        return [v for h, v in by_type.get(source_type, ()) if hubs is None or h in hubs]

    if kind == 3:
        symbol = _short_first(_usable(values("genesymbol", {"entrez"}), 120))
        return symbol or local
    if kind == 2:
        symbol = _short_first(_usable(values("genesymbol", {"uniprot"}), 120)) or _short_first(
            _usable(values("genesymbol"), 120)
        )
        if symbol:
            return symbol
        entry = _short_first(_usable(values("uniprot_entry", {"uniprot"}), 120))
        return entry or local
    # chemical
    if entity_id.startswith("goslin:"):
        # goslin:<level>:<name> - the name is the most specific level it was anchored at
        parts = entity_id.split(":", 2)
        if len(parts) == 3 and parts[2]:
            return parts[2]
    shorthand = _goslin_shorthand(values("goslin"))
    if shorthand:
        return shorthand
    names = [(h, v) for h, v in by_type.get("name", ())]
    for hub in CHEMICAL_NAME_HUBS:
        pick = _short_first(_chemical_names(v for h, v in names if h == hub))
        if pick:
            return pick
    pick = _short_first(_chemical_names(v for h, v in names if h not in CHEMICAL_NAME_HUBS))
    if pick:
        return pick
    pick = _short_first(_chemical_names(values("systematic_name") + values("iupac_name")))
    return pick or local


def _chemical_names(values):
    return {v for v in _usable(values, MAX_LABEL) if not _INCHIKEY_ANY.fullmatch(v)}


def _goslin_shorthand(values):
    """Most specific ``<level>:<name>`` value, stripped of its level."""
    best = {}
    for value in values:
        level, _, name = str(value).partition(":")
        if level in GOSLIN_LEVELS and name.strip():
            best.setdefault(GOSLIN_LEVELS.index(level), set()).add(name.strip())
    if not best:
        return None
    return _short_first(best[max(best)])


class IdentityRuntime:
    """``FullRuntime``-compatible runtime over an identity snapshot directory."""

    def __init__(self, path, *, cache_dir=None, memory_limit=None, threads=None):
        import duckdb

        self.path = Path(path)
        manifest = json.loads((self.path / "manifest.json").read_text())
        if manifest.get("format") != FORMAT:
            raise ValueError("An omnipath-identity-v1 snapshot is required")
        if not manifest.get("fingerprint"):
            raise ValueError("Identity snapshot manifest has no fingerprint")
        if "namespace_codes" in manifest and manifest["namespace_codes"] != CODES:
            raise ValueError("Namespace encoding differs from the snapshot")
        self.manifest = manifest
        self.fingerprint = manifest["fingerprint"]
        self.codes = {code: ns for ns, code in CODES.items()}
        config = {}
        if memory_limit:
            config["memory_limit"] = str(memory_limit)
        threads = threads or int(os.environ.get("OMNIPATH_BUILD_DUCKDB_THREADS") or 0)
        if threads:
            config["threads"] = int(threads)
        self._db = duckdb.connect(":memory:", config=config)
        self._lock = threading.RLock()
        self._partitions = {}
        self.cache_dir = Path(cache_dir) if cache_dir is not None else default_cache_dir(self.path)
        self._cache = self._open_cache()

    # ------------------------------------------------------------------ cache

    def _open_cache(self):
        directory = self.cache_dir / str(self.fingerprint)
        try:
            directory.mkdir(parents=True, exist_ok=True)
            target = str(directory / "entities.sqlite")
        except OSError:
            target = ":memory:"  # read-only location: still correct, just not shared
        con = sqlite3.connect(target, timeout=120, check_same_thread=False, isolation_level=None)
        if target != ":memory:":
            con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA synchronous=NORMAL")
        con.execute("PRAGMA busy_timeout=120000")
        con.execute("BEGIN IMMEDIATE")
        con.execute("CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT)")
        con.execute(
            "CREATE TABLE IF NOT EXISTS records(entity_id TEXT PRIMARY KEY, record TEXT NOT NULL)"
            " WITHOUT ROWID"
        )
        row = con.execute("SELECT value FROM meta WHERE key='record_policy'").fetchone()
        if row is not None and row[0] != RECORD_POLICY:
            con.execute("DELETE FROM records")
        con.execute("INSERT OR REPLACE INTO meta VALUES('record_policy', ?)", (RECORD_POLICY,))
        con.execute("COMMIT")
        return con

    def _cached(self, entity_ids):
        found = {}
        ids = list(entity_ids)
        for offset in range(0, len(ids), 900):
            chunk = ids[offset : offset + 900]
            marks = ",".join("?" * len(chunk))
            for eid, record in self._cache.execute(
                f"SELECT entity_id, record FROM records WHERE entity_id IN ({marks})", chunk
            ):
                found[eid] = json.loads(record)
        return found

    def _store(self, records):
        if not records:
            return
        self._cache.execute("BEGIN IMMEDIATE")
        try:
            self._cache.executemany(
                "INSERT OR REPLACE INTO records VALUES(?, ?)",
                [(eid, json.dumps(rec, separators=(",", ":"))) for eid, rec in records.items()],
            )
        except BaseException:
            self._cache.execute("ROLLBACK")
            raise
        self._cache.execute("COMMIT")

    # ----------------------------------------------------------------- files

    def _files(self, relative, parts):
        """Parquet files under ``<relative>/part=XX/`` for the given partitions (missing = empty)."""
        files = []
        for part in sorted(parts):
            cache_key = (relative, part)
            if cache_key not in self._partitions:
                self._partitions[cache_key] = sorted(
                    glob.glob(str(self.path / relative / f"part={part}" / "*.parquet"))
                )
            files.extend(self._partitions[cache_key])
        return files

    def _query(self, sql, files, tables, params=()):
        """Run ``sql`` (using ``{files}`` as the Parquet source) with temp arrow tables registered."""
        import pyarrow as pa

        if not files:  # every partition empty: nothing to read
            return []
        with self._lock:
            cur = self._db.cursor()
            try:
                for name, columns in tables.items():
                    cur.register(name, pa.table(columns))
                return cur.execute(sql, [files, *params]).fetchall()
            finally:
                for name in tables:
                    cur.unregister(name)
                cur.close()

    # ---------------------------------------------------------------- lookup

    def lookup_many(self, keys):
        decoded = {}
        for key in dict.fromkeys(keys):
            decoded[key] = decode_key(key, self.codes)
        rows = defaultdict(list)
        for target in (1, 2):
            wanted = [
                (key, d)
                for key, d in decoded.items()
                # Symbols exist only taxon-scoped: an unscoped symbol key always misses.
                if d[0] == target and not (d[3] is None and d[2] in SYMBOL_NAMESPACES)
            ]
            if not wanted:
                continue
            files = self._files(f"access/target={target}", {partition(d[4]) for _, d in wanted})
            if not files:
                continue
            tables = {
                "_keys": dict(
                    i=[i for i in range(len(wanted))],
                    route=[d[1] for _, d in wanted],
                    ns=[d[2] for _, d in wanted],
                    identifier=[d[4] for _, d in wanted],
                    scope=[d[3] for _, d in wanted],
                )
            }
            sql = """
                SELECT k.i, a.entity_id, a.kind, a.anchor, a.taxon, a.quarantined, a.reviewed,
                       a.gene_ids, a.tag
                FROM read_parquet(?, union_by_name=true) a
                JOIN _keys k ON a.identifier = k.identifier AND a.ns = k.ns
                     AND CAST(a.route AS INTEGER) = k.route
                WHERE k.scope IS NULL OR CAST(a.taxon AS VARCHAR) = k.scope
            """
            for i, *row in self._query(sql, files, tables):
                rows[wanted[i][0]].append(row)
        out, nums = {}, {}
        for key, (target, route, ns, scope, identifier) in decoded.items():
            candidates = self._posting(ns, rows.get(key, ()))
            for fact in candidates:
                if nums.setdefault(fact[0], fact[1]) != fact[1]:
                    raise ValueError(
                        f"Entity number collision between {nums[fact[0]]} and {fact[1]}"
                    )
            out[key] = dict(
                candidates=candidates,
                gene=target == 2 and (route == 2 or ns in GENE_NAMESPACES),
                products=False,
            )
        return out

    @staticmethod
    def _posting(ns, rows):
        """Apply precedence (rule 8) to one posting and build its candidate facts."""
        if not rows:
            return []
        tags = {r[7] for r in rows}
        dropped = set()
        if NATIVE in tags:
            dropped.add(FALLBACK)  # native identity rows beat fallback rows
        if ns == "uniprot" and tags - {SECONDARY}:
            dropped.add(SECONDARY)  # a primary accession beats secondary-accession claims
        exact_taxa = {r[3] for r in rows if r[7] != SYMBOL_SYNONYM}
        facts = {}
        for entity_id, kind, anchor, taxon, quarantined, reviewed, gene_ids, tag in sorted(
            rows, key=lambda r: (r[0], r[7])
        ):
            if tag in dropped or (tag == SYMBOL_SYNONYM and taxon in exact_taxa):
                continue  # exact symbols beat synonyms within one taxon
            if entity_id in facts:
                continue
            kind = KINDS[kind] if isinstance(kind, str) else int(kind)
            facts[entity_id] = [
                entity_num(entity_id),
                entity_id,
                kind,
                clean_anchor(kind, anchor),
                bool(quarantined),
                bool(reviewed),
                [str(g) for g in (gene_ids or ())],
            ]
        return sorted(facts.values(), key=lambda f: f[0])

    def lookup(self, key):
        return self.lookup_many([key])[key]

    # --------------------------------------------------------------- records

    def record_many(self, entity_ids):
        wanted = list(dict.fromkeys(entity_ids))
        found = self._cached(wanted)
        missing = [eid for eid in wanted if eid not in found]
        if missing:
            built = self._build_records(missing)
            absent = [eid for eid in missing if eid not in built]
            if absent:
                raise ValueError("Candidate references absent entity: " + absent[0])
            self._store(built)
            found.update(built)
        return {eid: found[eid] for eid in wanted}

    def record(self, entity_id):
        return self.record_many([entity_id])[entity_id]

    def _build_records(self, entity_ids):
        entities = {
            row[0]: row
            for row in self._query(
                "SELECT e.entity_id, e.kind, e.anchor, e.taxon FROM read_parquet(?, union_by_name=true) e"
                " JOIN _want w ON e.entity_id = w.entity_id",
                self._files("entities", {partition(e) for e in entity_ids}),
                {"_want": dict(entity_id=entity_ids)},
            )
        }
        found = list(entities)
        members = defaultdict(list)
        if found:
            for eid, record_id in self._query(
                "SELECT m.entity_id, m.record_id FROM read_parquet(?, union_by_name=true) m"
                " JOIN _want w ON m.entity_id = w.entity_id",
                self._files("members", {partition(e) for e in found}),
                {"_want": dict(entity_id=found)},
            ):
                members[eid].append(record_id)
        record_ids = sorted({r for ids in members.values() for r in ids})
        rows = defaultdict(list)
        if record_ids:
            for record_id, source_type, value in self._query(
                "SELECT r.record_id, r.source_type, r.value FROM read_parquet(?, union_by_name=true) r"
                " JOIN _want w ON r.record_id = w.record_id",
                self._files("record_rows", {partition(r) for r in record_ids}),
                {"_want": dict(record_id=record_ids)},
            ):
                rows[record_id].append((source_type, value))
        proteins = [eid for eid, row in entities.items() if KINDS.get(row[1], row[1]) == 2]
        genes = defaultdict(set)
        products = self.path / "gene_products.parquet"
        if proteins and products.is_file():
            for protein, entrez in self._query(
                "SELECT g.protein_entity_id, g.entrez_id FROM read_parquet(?, union_by_name=true) g"
                " JOIN _want w ON g.protein_entity_id = w.entity_id",
                [str(products)],
                {"_want": dict(entity_id=proteins)},
            ):
                genes[protein].add(str(entrez))
        built = {}
        for eid, (_, kind, anchor, taxon) in entities.items():
            kind = KINDS[kind] if isinstance(kind, str) else int(kind)
            hub_rows = []
            for record_id in members.get(eid, ()):
                hub = record_id.split(":", 1)[0]
                hub_rows.extend((hub, st, v) for st, v in rows.get(record_id, ()))
            identifiers = {
                (st, str(v)) for _, st, v in hub_rows if st not in NOT_IDENTIFIERS and v is not None
            }
            if ":" in eid:
                identifiers.add(tuple(eid.split(":", 1)))
            built[eid] = dict(
                entity_id=eid,
                kind=kind,
                anchor=clean_anchor(kind, anchor),
                taxon=_taxon(taxon),
                label=choose_label(kind, eid, hub_rows),
                identifiers=[list(p) for p in sorted(identifiers)],
                gene_ids=sorted(genes.get(eid, ())),
            )
        return built

    # --------------------------------------------------------------- resolve

    def close(self):
        with self._lock:
            if self._cache is not None:
                self._cache.close()
                self._cache = None
            if self._db is not None:
                self._db.close()
                self._db = None

    def resolve(self, queries, votes, *, decision_batch_size=4096):
        from omnipath_resolver._omnipath_resolver import (
            resolve_precomputed_batch,
            resolve_molecular_batch,
        )

        start = time.perf_counter()
        keys = sorted({v["lookup_key"] for v in votes})
        selected = self.lookup_many(keys)
        postings, metadata = [], {}
        for key in keys:
            value = selected[key]
            postings.append((key, [c[0] for c in value["candidates"]]))
            metadata.update({c[0]: tuple(c) for c in value["candidates"]})
        grouped = defaultdict(list)
        primary_symbol = {
            v["input_id"]
            for v in votes
            if v["ns"] == "genesymbol" and selected[v["lookup_key"]]["candidates"]
        }
        for v in votes:
            if v["ns"] == "genesymbol-syn" and v["input_id"] in primary_symbol:
                continue
            value = selected[v["lookup_key"]]
            grouped[v["input_id"]].append(
                (
                    v["lookup_key"],
                    v["anchor"],
                    v["route"],
                    v["ordinal"],
                    value["gene"] or v.get("gene_only", False),
                    value["products"],
                )
            )
        batches = [(q["input_id"], q["target"], grouped[q["input_id"]]) for q in queries]
        fetched = time.perf_counter()
        result = []
        molecular = {}
        for offset in range(0, len(batches), decision_batch_size):
            chunk = batches[offset : offset + decision_batch_size]
            chemical = [q for q in chunk if q[1] == 1]
            biological = [q for q in chunk if q[1] == 2]
            if chemical:
                result.extend(
                    resolve_precomputed_batch(
                        chemical, postings, [tuple(m[:6]) for m in metadata.values()]
                    )
                )
            if biological:
                facts = [
                    tuple(m[:6]) + (list(m[6]) if len(m) > 6 else [],) for m in metadata.values()
                ]
                for row in resolve_molecular_batch(biological, postings, facts):
                    result.append(row[:4])
                    molecular[row[0]] = dict(
                        protein_entity_id=row[4], gene_candidates=row[5], gene_mapping_status=row[6]
                    )
        decided = time.perf_counter()
        accepted = {eid for _, _, ids, _ in result for eid in ids}
        accepted.update(
            v["protein_entity_id"] for v in molecular.values() if v["protein_entity_id"]
        )
        records = self.record_many(sorted(accepted))
        finished = time.perf_counter()
        return dict(
            results=[
                dict(
                    input_id=i,
                    outcome=o,
                    entities=sorted(ids),
                    candidate_count=n,
                    **molecular.get(i, {}),
                )
                for i, o, ids, n in result
            ],
            records=records,
        ), dict(
            lookup_seconds=fetched - start,
            decision_seconds=decided - fetched,
            entity_fetch_seconds=finished - decided,
            total_seconds=finished - start,
            unique_keys=len(keys),
            candidate_records=len(metadata),
            entity_records=len(records),
        )
