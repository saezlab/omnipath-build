"""On-demand resolution runtime over an ``omnipath-identity-v2`` directory.

Same ``resolve()`` contract as :class:`omnipath_resolver.index.FullRuntime`. Nothing is
materialized for the whole universe: postings are derived per batch from the per-hub
``by_id`` rows (only the partitions the identifiers hash to), the small identity decisions
(exceptions, extra entities, gene products) and the hub ``records`` tables; entity records are
assembled on first use from the members' ``by_record`` rows and cached per identity
fingerprint in SQLite. See ``docs/identity-layer-spec.md`` sections 3 and 4.
"""

from __future__ import annotations

from collections import defaultdict, namedtuple
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

FORMAT = "omnipath-identity-v2"
# Bump when record assembly or label policy changes: cached records are discarded.
RECORD_POLICY = "identity-record-v2"
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

CHEMICAL_HUBS = (
    "chebi", "pubchem", "chembl", "hmdb", "lipidmaps", "swisslipids",
    "bigg", "metanetx", "refmet", "ramp", "kegg",
)  # fmt: skip
# Hub ids that are native identity keys under the hub's own namespace (brief 2.3).
CHEMICAL_OWN = frozenset(
    {
        "chebi",
        "pubchem",
        "chembl",
        "hmdb",
        "lipidmaps",
        "swisslipids",
        "bigg",
        "metanetx",
        "refmet",
        "ramp",
    }
)
PRODUCT_NAMESPACES = frozenset(
    {"uniprot", "uniprot-sec", "uniprot_entry", "ensp", "refseq_protein", "genbank"}
)
# Levels whose Goslin name is a structure: it keys an InChIKey only when unambiguous (rule 5).
STRUCTURE_LEVELS = frozenset({"full_structure", "complete_structure"})
GENE_ALIAS_TYPES = frozenset({"genesymbol", "genesymbol-syn", "hgnc", "ensg"})
_ISOFORM = re.compile(r"^uniprot:([A-Z0-9]+)-[0-9]+$")
_RNA_ACCESSION = re.compile(r"^(?:NM|NR|XM|XR)_[0-9]+$")
CLAIM_TAGS = frozenset({"claim", "version_stripped"})

_Meta = namedtuple("_Meta", "kind anchor taxon quarantined reviewed gene_ids exists")


def _prefixed_anchor(hub: str, anchor: str) -> str:
    if ":" in anchor:
        return anchor
    return ("uniprot:" if hub == "uniprot" else "inchikey:") + anchor


def _symbol_tag(key_ns, source_ns, tag):
    """Brief 2.3b symbol expansions: exact symbols answer both namespaces, synonyms are weaker."""
    if source_ns == "genesymbol" and tag == "claim":
        return "regular"
    if tag in ("claim", SYMBOL_SYNONYM) and source_ns in SYMBOL_NAMESPACES:
        return SYMBOL_SYNONYM
    return None


def _admit(cls, ns, hub, source_ns, tag, identifier, anchor, anchor_count):
    """Tag a by_id row takes as a lookup row for key namespace ``ns``, or None (brief 2.2-2.3)."""
    if cls == "chem":
        if ns == "inchikey":  # only an anchored structure (anchor_count = 1) is an InChIKey key
            ok = anchor_count == 1 and anchor and anchor.removeprefix("inchikey:") == identifier
            return NATIVE if ok else None
        if ns == "goslin":
            return NATIVE
        if ns in CHEMICAL_OWN:
            if tag == "native":
                return NATIVE if hub == ns else None
            if tag == "claim" and hub == ns and ns in ("chebi", "bigg"):
                return FALLBACK if ns == "bigg" else "regular"
            return None
        if ns == "kegg":
            return FALLBACK
        return "regular" if tag in ("claim", "native") else None  # cas, drugbank
    if cls == "ramp_gene":
        return "regular" if tag == "native" else None
    if cls == "gene":
        if ns in SYMBOL_NAMESPACES:
            return _symbol_tag(ns, source_ns, tag)
        if ns == "refseq":
            ok = source_ns == "refseq" and _RNA_ACCESSION.fullmatch(identifier)
            return "regular" if ok and tag in CLAIM_TAGS else None
        return "regular" if source_ns == ns and tag in CLAIM_TAGS else None
    if cls == "product":
        if ns in ("uniprot", "uniprot-sec"):
            if source_ns == "uniprot" and tag == "native":
                return NATIVE if ns == "uniprot" else None
            if (source_ns == "uniprot-sec" and tag in ("claim", SECONDARY)) or (
                source_ns == "uniprot" and tag == SECONDARY
            ):
                return SECONDARY
            return None
        return "regular" if source_ns == ns and tag in CLAIM_TAGS else None
    return None


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
    """``FullRuntime``-compatible runtime over an identity directory and its hub indexes."""

    def __init__(self, path, *, cache_dir=None, memory_limit=None, threads=None):
        import duckdb

        self.path = Path(path)
        manifest = json.loads((self.path / "manifest.json").read_text())
        if manifest.get("format") != FORMAT:
            raise ValueError("An omnipath-identity-v2 identity directory is required")
        if not manifest.get("fingerprint"):
            raise ValueError("Identity manifest has no fingerprint")
        if not manifest.get("hub_indexes"):
            raise ValueError("Identity manifest names no hub indexes")
        if "namespace_codes" in manifest and manifest["namespace_codes"] != CODES:
            raise ValueError("Namespace encoding differs from the identity manifest")
        self.manifest = manifest
        self.fingerprint = manifest["fingerprint"]
        self.hub_dirs = {}
        for hub, directory in manifest["hub_indexes"].items():
            directory = Path(directory)
            self.hub_dirs[hub] = (
                directory if directory.is_absolute() else self.path / directory
            ).resolve()
        self.chemical_hubs = [h for h in CHEMICAL_HUBS if h in self.hub_dirs]
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
        self._lipids = None
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

    def _files(self, base, relative, parts):
        """Parquet files under ``<base>/<relative>/part=XX/`` for the partitions (missing = empty)."""
        files = []
        for part in sorted(parts):
            cache_key = (str(base), relative, part)
            if cache_key not in self._partitions:
                self._partitions[cache_key] = sorted(
                    glob.glob(str(Path(base) / relative / f"part={part}" / "*.parquet"))
                )
            files.extend(self._partitions[cache_key])
        return files

    def _table(self, name):
        path = self.path / name
        return [str(path)] if path.is_file() else []

    def _query(self, sql, files, tables, params=()):
        """Run ``sql`` over the Parquet ``files`` (bound to ``?``) with temp arrow tables."""
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

    # ------------------------------------------------- small indexed lookups

    def _exceptions(self, record_ids):
        """record_id -> (entity_id, decision, quarantined) for records with an exception."""
        ids = sorted(set(record_ids))
        if not ids:
            return {}
        rows = self._query(
            "SELECT e.record_id, e.entity_id, e.decision, e.quarantined"
            " FROM read_parquet(?, union_by_name=true) e JOIN _want w ON e.record_id = w.id",
            self._table("exceptions.parquet"),
            {"_want": dict(id=ids)},
        )
        return {r: (e, d, bool(q)) for r, e, d, q in rows}

    def _exception_members(self, entity_ids):
        ids = sorted(set(entity_ids))
        found = defaultdict(list)
        if ids:
            for entity_id, record_id in self._query(
                "SELECT e.entity_id, e.record_id"
                " FROM read_parquet(?, union_by_name=true) e JOIN _want w ON e.entity_id = w.id",
                self._table("exception_members.parquet"),
                {"_want": dict(id=ids)},
            ):
                found[entity_id].append(record_id)
        return found

    def _extra(self, entity_ids):
        """entity_id -> (kind, taxon, quarantined) for entities not derivable from a record."""
        ids = sorted(set(entity_ids))
        if not ids:
            return {}
        rows = self._query(
            "SELECT e.entity_id, e.kind, e.taxon, e.quarantined"
            " FROM read_parquet(?, union_by_name=true) e JOIN _want w ON e.entity_id = w.id",
            self._table("entities_extra.parquet"),
            {"_want": dict(id=ids)},
        )
        return {e: (k, _taxon(t), bool(q)) for e, k, t, q in rows}

    def _records(self, hub, local_ids):
        """local_id -> (taxon, anchor, anchor_count, reviewed) from a hub's records.parquet."""
        ids = sorted(set(local_ids))
        if not ids or hub not in self.hub_dirs:
            return {}
        path = self.hub_dirs[hub] / "records.parquet"
        rows = self._query(
            "SELECT r.local_id, r.taxon, r.anchor, r.anchor_count, r.reviewed"
            " FROM read_parquet(?, union_by_name=true) r JOIN _want w ON r.local_id = w.id",
            [str(path)] if path.is_file() else [],
            {"_want": dict(id=ids)},
        )
        return {lid: (_taxon(t), a, int(n or 0), bool(rv)) for lid, t, a, n, rv in rows}

    def _gene_products(self, name, column, ids):
        ids = sorted(set(ids))
        if not ids:
            return {}
        found = defaultdict(list)
        for key, other, taxon in self._query(
            f"SELECT g.{column}, g.{'entrez_id' if column == 'protein_entity_id' else 'protein_entity_id'},"
            f" g.taxon FROM read_parquet(?, union_by_name=true) g JOIN _want w ON g.{column} = w.id",
            self._table(name),
            {"_want": dict(id=ids)},
        ):
            found[str(key)].append((str(other), _taxon(taxon)))
        return found

    def _by_protein(self, protein_ids):
        """protein entity id -> [(entrez_id, taxon)]"""
        return self._gene_products(
            "gene_products_by_protein.parquet", "protein_entity_id", protein_ids
        )

    def _by_gene(self, entrez_ids):
        """entrez id -> [(protein entity id, taxon)]"""
        return self._gene_products("gene_products_by_gene.parquet", "entrez_id", entrez_ids)

    def _lipid_structures(self):
        """Goslin full-structure name -> inchikey entity id (rule 5; unique names only)."""
        if self._lipids is None:
            self._lipids = {}
            files = self._table("lipid_structures.parquet")
            if files:
                with self._lock:
                    cur = self._db.cursor()
                    try:
                        table = cur.execute(
                            "SELECT * FROM read_parquet(?)", [files[0]]
                        ).to_arrow_table()
                    finally:
                        cur.close()
                names = table.column_names
                name_col = next((c for c in ("goslin", "identifier", "name") if c in names), None)
                key_col = next((c for c in ("inchikey", "entity_id", "anchor") if c in names), None)
                if name_col and key_col:
                    for name, key in zip(
                        table.column(name_col).to_pylist(), table.column(key_col).to_pylist()
                    ):
                        if name and key:
                            key = str(key)
                            self._lipids[name] = (
                                key if key.startswith("inchikey:") else "inchikey:" + key
                            )
        return self._lipids

    # ---------------------------------------------------------------- lookup

    def _plan(self, target, route, ns):
        """(class, source namespaces, hubs) a key reads, or None when it can never match."""
        if target == 1:
            if route != 1:
                return None
            if ns in CHEMICAL_OWN:
                return "chem", [ns], [ns]
            if ns in ("cas", "drugbank", "kegg", "inchikey", "goslin"):
                return "chem", [ns], self.chemical_hubs
            return None
        if route == 2:
            if ns == "entrez":
                return "entrez", [], []
            if ns == "ramp_gene":
                return "ramp_gene", ["ramp_gene"], ["ramp_gene"]
            return None
        if ns in GENE_NAMESPACES:
            sources = ["genesymbol", "genesymbol-syn"] if ns in SYMBOL_NAMESPACES else [ns]
            return "gene", sources, ["uniprot", "entrez"]
        if ns in PRODUCT_NAMESPACES:
            sources = ["uniprot", "uniprot-sec"] if ns in ("uniprot", "uniprot-sec") else [ns]
            return "product", sources, ["uniprot"]
        return None

    def _by_id(self, hubs, pairs):
        """by_id rows for ``(source ns, identifier)`` pairs: (pair index, hub, row fields...)."""
        hubs = [h for h in hubs if h in self.hub_dirs]
        files, owner = [], {}
        for hub in hubs:
            base = self.hub_dirs[hub]
            found = self._files(base, "by_id", {partition(i) for _, i in pairs})
            files.extend(found)
            for f in found:
                owner[f] = hub
        if not files:
            return []
        rows = self._query(
            "SELECT k.i, a.local_id, a.tag, a.anchor, a.anchor_count, a.filename"
            " FROM read_parquet(?, union_by_name=true, filename=true) a"
            " JOIN _src k ON a.identifier = k.identifier AND a.ns = k.ns",
            files,
            {
                "_src": dict(
                    i=list(range(len(pairs))),
                    ns=[n for n, _ in pairs],
                    identifier=[i for _, i in pairs],
                )
            },
        )
        return [
            (i, owner[filename], local_id, tag, anchor, int(count or 0))
            for i, local_id, tag, anchor, count, filename in rows
        ]

    def _entity(self, hub, local_id, anchor, anchor_count, exceptions):
        """Entity of a record: its exception, else its anchor, else entrez id, else record id."""
        record_id = f"{hub}:{local_id}"
        if record_id in exceptions:
            entity_id, _, quarantined = exceptions[record_id]
            return entity_id, quarantined
        if anchor_count == 1 and anchor:
            return _prefixed_anchor(hub, anchor), False
        if hub == "entrez":
            return "entrez:" + local_id, False
        return record_id, False

    def lookup_many(self, keys):
        decoded = {key: decode_key(key, self.codes) for key in dict.fromkeys(keys)}
        plans = {}
        for key, (target, route, ns, scope, identifier) in decoded.items():
            # Symbols exist only taxon-scoped: an unscoped symbol key always misses.
            if scope is None and ns in SYMBOL_NAMESPACES:
                continue
            plan = self._plan(target, route, ns)
            if plan:
                plans[key] = plan

        # 1. by_id rows per key, admitted according to brief 2.2-2.3 (hits: key -> rows)
        hits = defaultdict(list)
        groups = defaultdict(list)
        for key, (cls, sources, hubs) in plans.items():
            if sources:
                groups[tuple(hubs)].append(key)
        for hubs, group in groups.items():
            pairs = [(src, decoded[key][4]) for key in group for src in plans[key][1]]
            owners = [key for key in group for _ in plans[key][1]]
            for i, hub, local_id, tag, anchor, count in self._by_id(hubs, pairs):
                key, (src, identifier) = owners[i], pairs[i]
                cls, _, _ = plans[key]
                out = _admit(cls, decoded[key][2], hub, src, tag, identifier, anchor, count)
                if out:
                    hits[key].append((hub, local_id, out, anchor, count, identifier))

        # 2. records -> entities (exceptions, anchors, entrez ids, record ids)
        exceptions = self._exceptions(
            f"{hub}:{local_id}" for rows in hits.values() for hub, local_id, *_ in rows
        )
        candidates = defaultdict(list)  # key -> [(entity_id, tag, source hub)]
        quarantined = set()
        for key, rows in hits.items():
            for hub, local_id, tag, anchor, count, identifier in rows:
                entity_id, flagged = self._entity(hub, local_id, anchor, count, exceptions)
                if flagged:
                    quarantined.add(entity_id)
                if decoded[key][2] == "inchikey" and entity_id != "inchikey:" + identifier:
                    continue  # an overridden record does not create its structure's entity
                candidates[key].append((entity_id, tag, hub))
        for key, (cls, _, _) in plans.items():
            if cls == "entrez":
                candidates[key].append(("entrez:" + decoded[key][4], NATIVE, "entrez"))

        # 3. rule 5: a structure is a goslin key only if the name maps to exactly that InChIKey
        for key, (cls, _, _) in plans.items():
            if cls == "chem" and decoded[key][2] == "goslin":
                identifier = decoded[key][4]
                if identifier.partition(":")[0] in STRUCTURE_LEVELS:
                    unique = self._lipid_structures().get(identifier)
                    candidates[key] = [
                        c
                        for c in candidates[key]
                        if not c[0].startswith("inchikey:") or c[0] == unique
                    ]

        # 4. isoform -> parent projection (products and gene-level postings)
        projected = {}
        isoforms = {
            c[0]
            for key, cs in candidates.items()
            if plans[key][0] in ("product", "gene")
            for c in cs
            if _ISOFORM.fullmatch(c[0])
        }
        if isoforms:
            parents = self._records("uniprot", {_ISOFORM.fullmatch(e).group(1) for e in isoforms})
            for entity_id in isoforms:
                parent = _ISOFORM.fullmatch(entity_id).group(1)
                if parents.get(parent, (None, None, 0, False))[2] == 1:
                    projected[entity_id] = "uniprot:" + parent

        # 5. entity metadata, gene links
        wanted = {c[0] for cs in candidates.values() for c in cs}
        wanted |= set(projected.values())
        meta = self._meta(wanted - set(projected)) if wanted else {}
        links = {}
        proteins = {
            projected.get(c[0], c[0])
            for key, cs in candidates.items()
            if plans[key][0] == "gene"
            for c in cs
            if c[2] == "uniprot"
        }
        if proteins:
            links = self._by_protein(proteins)
            genes = {"entrez:" + g for p in proteins for g, _ in links.get(p, ())}
            meta.update(self._meta((genes | proteins) - set(meta)))

        # 6. posting per key: scope filter, then precedence
        out, nums = {}, {}
        for key, (target, route, ns, scope, identifier) in decoded.items():
            cls = plans[key][0] if key in plans else None
            rows = []
            for entity_id, tag, hub in candidates.get(key, ()):
                entity_id = projected.get(entity_id, entity_id)
                if cls == "gene":
                    rows.extend(self._gene_rows(entity_id, tag, hub, meta, links, quarantined))
                    continue
                m = meta.get(entity_id)
                if m is None or (cls == "entrez" and not m.exists):
                    continue
                if target == 1 and m.kind != 1:
                    continue
                rows.append(
                    (
                        entity_id,
                        m.kind,
                        m.anchor,
                        m.taxon,
                        m.quarantined or entity_id in quarantined,
                        m.reviewed,
                        [entity_id[7:]] if entity_id.startswith("entrez:") else m.gene_ids,
                        tag,
                    )
                )
            if scope is not None:
                rows = [r for r in rows if r[3] == scope]
            facts = self._posting(ns, rows)
            for fact in facts:
                if nums.setdefault(fact[0], fact[1]) != fact[1]:
                    raise ValueError(
                        f"Entity number collision between {nums[fact[0]]} and {fact[1]}"
                    )
            out[key] = dict(
                candidates=facts,
                gene=target == 2 and (route == 2 or ns in GENE_NAMESPACES),
                products=False,
            )
        return out

    @staticmethod
    def _gene_rows(entity_id, tag, hub, meta, links, quarantined):
        """Gene-level posting rows (rule 10): genes, or proteins linked to exactly one gene."""
        if hub == "entrez":
            genes = [(entity_id, None, None)] if entity_id.startswith("entrez:") else []
        else:
            link = links.get(entity_id, ())
            protein = meta.get(entity_id)
            ids = {g for g, _ in link}
            link_taxa = {t for _, t in link if t}
            if len(ids) != 1 or len(link_taxa) > 1 or protein is None or protein.quarantined:
                return []
            genes = [("entrez:" + next(iter(ids)), next(iter(link_taxa), None), protein.taxon)]
        rows = []
        for gene, link_taxon, protein_taxon in genes:
            m = meta.get(gene)
            if m is None or not m.exists or m.quarantined or gene in quarantined:
                continue
            taxa = {t for t in (m.taxon, link_taxon, protein_taxon) if t}
            if len(taxa) > 1:
                continue
            taxon = m.taxon or link_taxon or protein_taxon
            rows.append((gene, 3, None, taxon, False, False, [gene[7:]], tag))
        return rows

    def _meta(self, entity_ids):
        """Kind, anchor, taxon, quarantine, review state and gene links of entities."""
        ids = sorted(set(entity_ids))
        extras = self._extra(ids)
        proteins = {e: e[8:] for e in ids if e.startswith("uniprot:")}
        genes = {e: e[7:] for e in ids if e.startswith("entrez:")}
        others = {
            e: tuple(e.split(":", 1))
            for e in ids
            if e not in proteins and e not in genes and ":" in e
        }
        uniprot = self._records("uniprot", proteins.values())
        entrez = self._records("entrez", genes.values())
        by_gene = self._by_gene(set(genes.values()) - set(entrez))
        by_protein = self._by_protein(proteins)
        hub_records = defaultdict(set)
        for hub, local in others.values():
            if hub in self.hub_dirs and hub not in ("uniprot", "entrez"):
                hub_records[hub].add(local)
        records = {hub: self._records(hub, locals_) for hub, locals_ in hub_records.items()}
        result = {}
        for entity_id in ids:
            extra = extras.get(entity_id)
            kind, anchor, taxon, reviewed, gene_ids, exists = 1, None, None, False, [], False
            if entity_id in proteins:
                kind = 2
                record = uniprot.get(proteins[entity_id])
                exists = record is not None
                if record:
                    taxon, raw_anchor, count, reviewed = record
                    anchor = entity_id if count == 1 and raw_anchor else None
                gene_ids = sorted({g for g, _ in by_protein.get(entity_id, ())})
            elif entity_id in genes:
                kind = 3
                record = entrez.get(genes[entity_id])
                link = by_gene.get(genes[entity_id], ())
                exists = record is not None or bool(link)
                taxon = record[0] if record else min((t for _, t in link if t), default=None)
            elif entity_id in others:
                hub, local = others[entity_id]
                kind = 1 if hub in CHEMICAL_HUBS or hub in ("inchikey", "goslin") else 3
                exists = False
                record = records.get(hub, {}).get(local)
                if record:
                    exists, taxon = True, record[0]
                if hub == "inchikey":
                    anchor = entity_id
            quarantined = False
            if extra:
                exists = True
                kind = KINDS.get(extra[0], kind) if isinstance(extra[0], str) else int(extra[0])
                taxon = extra[1] or taxon
                quarantined = extra[2]
            result[entity_id] = _Meta(
                kind, clean_anchor(kind, anchor), taxon, quarantined, reviewed, gene_ids, exists
            )
        return result

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
            facts[entity_id] = [
                entity_num(entity_id),
                entity_id,
                kind,
                anchor,
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

    def _members(self, entity_ids, extras):
        """entity_id -> member record ids (by_id anchors, own record, exception_members)."""
        members = defaultdict(list)
        for entity_id, record_ids in self._exception_members(entity_ids).items():
            members[entity_id].extend(record_ids)
        # anchored entities: the records whose by_id anchor row names the entity
        lookups = []  # (entity id, hubs, source ns, identifier)
        for entity_id in entity_ids:
            if entity_id.startswith("inchikey:"):
                lookups.append((entity_id, self.chemical_hubs, "inchikey", entity_id[9:]))
            elif entity_id.startswith("uniprot:"):
                lookups.append((entity_id, ["uniprot"], "uniprot", entity_id[8:]))
        by_hubs = defaultdict(list)
        for item in lookups:
            by_hubs[tuple(item[1])].append(item)
        for hubs, items in by_hubs.items():
            pairs = [(ns, identifier) for _, _, ns, identifier in items]
            rows = self._by_id(hubs, pairs)
            exceptions = self._exceptions(f"{hub}:{local}" for _, hub, local, *_ in rows)
            for i, hub, local_id, tag, anchor, count in rows:
                entity_id = items[i][0]
                if hub == "uniprot" and tag != "native":
                    continue
                if self._entity(hub, local_id, anchor, count, exceptions)[0] == entity_id:
                    members[entity_id].append(f"{hub}:{local_id}")
        # record-id entities (own record) and entrez genes (the entrez record)
        own = {}
        for entity_id in entity_ids:
            hub = entity_id.split(":", 1)[0]
            if entity_id.startswith(("inchikey:", "goslin:")) or hub not in self.hub_dirs:
                continue
            if hub == "uniprot" and entity_id not in members:
                own[entity_id] = entity_id
            elif hub != "uniprot":
                own[entity_id] = entity_id
        if own:
            exceptions = self._exceptions(own.values())
            for entity_id, record_id in own.items():
                if exceptions.get(record_id, (entity_id,))[0] == entity_id:
                    members[entity_id].append(record_id)
        return {e: sorted(set(r)) for e, r in members.items()}

    def _by_record(self, record_ids):
        """record id -> [(source_type, value)] from the hubs' by_record rows."""
        by_hub = defaultdict(list)
        for record_id in set(record_ids):
            hub, _, local = record_id.partition(":")
            if hub in self.hub_dirs:
                by_hub[hub].append((record_id, local))
        rows = defaultdict(list)
        for hub, items in by_hub.items():
            files = self._files(self.hub_dirs[hub], "by_record", {partition(r) for r, _ in items})
            for local_id, source_type, value in self._query(
                "SELECT r.local_id, r.source_type, r.value"
                " FROM read_parquet(?, union_by_name=true) r JOIN _want w ON r.local_id = w.id",
                files,
                {"_want": dict(id=sorted({local for _, local in items}))},
            ):
                rows[f"{hub}:{local_id}"].append((source_type, value))
        return rows

    def _build_records(self, entity_ids):
        entity_ids = list(entity_ids)
        extras = self._extra(entity_ids)
        members = self._members(entity_ids, extras)
        rows = self._by_record({r for ids in members.values() for r in ids})
        meta = self._meta(entity_ids)

        # gene aliases: genesymbol/-syn, hgnc, ensg of proteins linked to exactly that one gene
        genes = [e for e in entity_ids if e.startswith("entrez:")]
        linked = defaultdict(list)
        if genes:
            by_gene = self._by_gene(e[7:] for e in genes)
            proteins = {p for links in by_gene.values() for p, _ in links}
            by_protein = self._by_protein(proteins)
            alias_rows = self._by_record({p for p in proteins if p.startswith("uniprot:")})
            for gene in genes:
                gene_taxon = meta[gene].taxon
                for protein, link_taxon in by_gene.get(gene[7:], ()):
                    ids = {g for g, _ in by_protein.get(protein, ())}
                    taxa = {t for t in (gene_taxon, link_taxon) if t}
                    if ids == {gene[7:]} and len(taxa) <= 1:
                        linked[gene].extend(
                            ("uniprot", st, v)
                            for st, v in alias_rows.get(protein, ())
                            if st in GENE_ALIAS_TYPES
                        )

        built = {}
        for entity_id in entity_ids:
            m = meta[entity_id]
            member_ids = members.get(entity_id, [])
            hub_rows = []
            for record_id in member_ids:
                hub = record_id.split(":", 1)[0]
                hub_rows.extend((hub, st, v) for st, v in rows.get(record_id, ()))
            if not (hub_rows or m.exists):
                continue
            hub_rows_all = hub_rows + linked.get(entity_id, [])
            identifiers = {
                (st, str(v))
                for _, st, v in hub_rows_all
                if st not in NOT_IDENTIFIERS and v is not None
            }
            identifiers.update(tuple(r.split(":", 1)) for r in member_ids)  # each record's own id
            if ":" in entity_id:
                identifiers.add(tuple(entity_id.split(":", 1)))
            built[entity_id] = dict(
                entity_id=entity_id,
                kind=m.kind,
                anchor=m.anchor,
                taxon=m.taxon,
                label=choose_label(m.kind, entity_id, hub_rows),
                identifiers=[list(p) for p in sorted(identifiers)],
                gene_ids=m.gene_ids,
            )
        return built

    # ------------------------------------------------------------- resolve

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
