"""Normalize observation evidence and resolve it through the compact index."""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any
from omnipath_core.naming import normalize_namespace
from .identifiers import normalize_identifier, normalize_ns
from .label import assign_label
from .library import pin_library
from .policy import LIBRARIES, EntityPolicy, CHEMICAL, get_policy

RESOLVED_BY_MATCHED = ("parquet",)


@dataclass(frozen=True)
class Vote:
    ns: str
    id: str
    taxon: str | None
    kind: str  # "id" | "symbol"
    primary: bool

    @property
    def key(self) -> tuple[str, str, str | None]:
        return (self.ns, self.id, self.taxon if self.kind == "symbol" else None)


@dataclass(frozen=True)
class Node:
    node_id: str
    taxon: str | None
    canonical_ns: str
    canonical_id: str
    label: str | None
    aliases: tuple[tuple[str, str], ...]
    uniprot: str | None = None
    entrez: str | None = None


@dataclass
class Match:
    canonical_namespace: str
    canonical_identifier: str
    label: str
    resolved_by: str
    taxon: str | None = None
    node_id: str | None = None
    aliases: dict[str, list[str]] = field(default_factory=dict)

    entity_type: str | None = None
    reference_library: str | None = None

    @property
    def matched(self) -> bool:
        return self.node_id is not None


def votes_for(obs: Any, policy: EntityPolicy) -> tuple[list[Vote], dict[str, list[str]]]:
    """Votes plus every observed identifier grouped by normalized namespace."""
    taxon = str(getattr(obs, "taxon", None) or "").strip() or None
    observed: dict[str, list[str]] = defaultdict(list)
    votes: list[Vote] = []
    seen: set[tuple[str, str]] = set()
    if hasattr(obs, "structure_derivations"):
        obs.structure_derivations = []

    def add(ns_raw: Any, id_raw: Any, primary: bool) -> None:
        if str(ns_raw) == "ramp" and policy.library == "gene_protein":
            ns_raw = "ramp_gene"
        pairs = normalize_identifier(ns_raw, id_raw)
        if not pairs:
            slug = normalize_ns(ns_raw)
            text = str(id_raw or "").strip()
            if slug and text and text not in observed[slug]:
                observed[slug].append(text)
            return
        for ns, ident in pairs:
            if ident not in observed[ns]:
                observed[ns].append(ident)
            if (ns, ident) in seen:
                continue
            seen.add((ns, ident))
            if ns in policy.match_namespaces:
                votes.append(Vote(ns, ident, taxon, "id", primary))
            elif ns in policy.symbol_namespaces and taxon:
                votes.append(Vote(ns, ident, taxon, "symbol", primary))

    add(getattr(obs, "namespace", ""), getattr(obs, "identifier", ""), True)
    for item in getattr(obs, "identifiers", None) or []:
        if isinstance(item, dict):
            add(
                item.get("ns") or item.get("type") or "",
                item.get("id") or item.get("identifier") or "",
                False,
            )
    if policy.library == CHEMICAL and not observed.get("inchikey") and observed.get("smiles"):
        from .structures import cached_derivation

        derivations = [cached_derivation(s) for s in sorted(set(observed["smiles"]))]
        if hasattr(obs, "structure_derivations"):
            obs.structure_derivations = derivations
        for result in derivations:
            if result["status"] == "derived" and ("inchikey", result["inchikey"]) not in seen:
                seen.add(("inchikey", result["inchikey"]))
                votes.append(Vote("inchikey", result["inchikey"], None, "id", False))
    return votes, observed


class LibraryMatcher:
    """Batch resolution against one pinned immutable compact reference."""

    def __init__(self, library_dir, *, defer_aliases=False, memory_limit=None):
        self.library_dir = pin_library(library_dir)
        self.defer_aliases = defer_aliases
        self.memory_limit = memory_limit
        self.metrics = {
            "batches": 0,
            "observations": 0,
            "lookup_seconds": 0.0,
            "kernel_seconds": 0.0,
            "enrichment_seconds": 0.0,
        }
        from ..index import FullRuntime

        self.runtime = (
            FullRuntime(self.library_dir)
            if self.library_dir is not None and self.library_dir.exists()
            else None
        )
        self.libraries = list(LIBRARIES) if self.runtime else []

    def available(self, library):
        return library in self.libraries

    def targets(self, entities):
        from ..observations import (
            observation_bundle,
            flush_lipid_cache,
        )

        prepared = {}
        queries, votes = [], []
        for key, obs in entities.items():
            policy = get_policy(obs.entity_type)
            normalized, observed = votes_for(obs, policy)
            prepared[key] = (obs, policy, observed)
            if not self.available(policy.library):
                continue
            query, rows = observation_bundle(key, obs, normalized, observed, policy.library)
            queries.append(query)
            votes.extend(rows)
        results = {
            key: [self._build(obs, policy, observed, None, "unmatched")]
            for key, (obs, policy, observed) in prepared.items()
        }
        if not queries:
            return results
        resolved, metrics = self.runtime.resolve(queries, votes)
        accepted = {row["input_id"]: row["entities"] for row in resolved["results"]}
        for name, source in (
            ("lookup_seconds", "lookup_seconds"),
            ("kernel_seconds", "decision_seconds"),
            ("enrichment_seconds", "entity_fetch_seconds"),
        ):
            self.metrics[name] += metrics[source]
        for key, ids in accepted.items():
            if ids:
                obs, policy, observed = prepared[key]
                matches = []
                for eid in sorted(set(ids)):
                    row = resolved["records"][eid]
                    ns, identifier = eid.split(":", 1)
                    aliases = [tuple(value) for value in row["identifiers"]]
                    reference_aliases = defaultdict(list)
                    for alias_ns, alias_id in aliases:
                        reference_aliases[alias_ns].append(alias_id)
                    node = Node(
                        eid,
                        row["taxon"] or None,
                        ns,
                        identifier,
                        row["label"] if row["label"] != identifier else None,
                        tuple(aliases),
                    )
                    match = self._build(obs, policy, observed, node, "parquet")
                    match.entity_type = (
                        {1: "small_molecule", 2: "protein", 3: "gene"}[row["kind"]]
                        if row["kind"] != 1
                        else None
                    )
                    match.reference_library = "chemical" if row["kind"] == 1 else "gene_protein"
                    matches.append(match)
                results[key] = matches
        flush_lipid_cache()
        self.metrics["batches"] += 1
        self.metrics["observations"] += len(queries)
        return results

    def match(self, entities):
        """Scalar convenience API; ambiguous fan-out retains the input identity."""
        result = {}
        for key, matches in self.targets(entities).items():
            if len(matches) == 1:
                result[key] = matches[0]
            else:
                obs = entities[key]
                policy = get_policy(obs.entity_type)
                _, observed = votes_for(obs, policy)
                result[key] = self._build(obs, policy, observed, None, "multiple_products")
        return result

    def chemical_targets(self, entities):
        return self.targets(entities)

    def _build(
        self,
        obs: Any,
        policy: EntityPolicy,
        observed: dict[str, list[str]],
        node: Node | None,
        rule: str,
    ) -> Match:
        aliases: dict[str, list[str]] = {ns: list(vals) for ns, vals in observed.items()}
        if node is not None:
            for ns, ident in node.aliases:
                if ns in policy.alias_namespaces:
                    bucket = aliases.setdefault(ns, [])
                    if ident not in bucket:
                        bucket.append(ident)
            canonical_ns, canonical_id = (node.canonical_ns, node.canonical_id)
            for ns, ident in ((node.canonical_ns, node.canonical_id), (canonical_ns, canonical_id)):
                if ident not in aliases.setdefault(ns, []):
                    aliases[ns].append(ident)
            label = node.label or assign_label(canonical_ns, canonical_id, aliases, policy)
            return Match(
                canonical_namespace=canonical_ns,
                canonical_identifier=canonical_id,
                label=label,
                resolved_by=rule,
                taxon=node.taxon or (str(getattr(obs, "taxon", None) or "").strip() or None),
                node_id=node.node_id,
                aliases=observed if self.defer_aliases else aliases,
            )

        # A source-supplied full structure remains its identity even when the
        # catalogue has not seen it yet. Conflicting structures stay unresolved.
        from .policy import INCHIKEY_RE

        structures = {v for v in observed.get("inchikey", []) if INCHIKEY_RE.fullmatch(v)}
        if policy.library == CHEMICAL and len(structures) == 1:
            identifier = next(iter(structures))
            return Match(
                "inchikey",
                identifier,
                assign_label("inchikey", identifier, aliases, policy),
                rule,
                taxon=str(getattr(obs, "taxon", None) or "").strip() or None,
                aliases=aliases,
            )

        raw_ns = str(getattr(obs, "namespace", "") or "")
        raw_id = str(getattr(obs, "identifier", "") or "").strip()
        ns = normalize_namespace(raw_ns) or normalize_ns(raw_ns) or "unknown"
        ident = raw_id
        norm = normalize_identifier(raw_ns, raw_id)
        if norm:
            ns, ident = norm[0]
        for pattern, target_ns in policy.rewrites:
            if re.match(pattern, ident, re.IGNORECASE):
                ns = target_ns
                break
        if ident not in aliases.setdefault(ns, []):
            aliases[ns].append(ident)
        label = assign_label(ns, ident, aliases, policy)
        return Match(
            canonical_namespace=ns,
            canonical_identifier=ident,
            label=label,
            resolved_by=rule,
            taxon=str(getattr(obs, "taxon", None) or "").strip() or None,
            node_id=None,
            aliases=aliases,
        )

    def close(self):
        from ..observations import flush_lipid_cache

        flush_lipid_cache()
        if self.runtime is not None:
            self.runtime.close()
            self.runtime = None
