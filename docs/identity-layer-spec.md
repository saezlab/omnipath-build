# Identity layer and on-demand resolution: implementation spec (phase 1)

Status: working spec for branch `resolution-v2`, 6 October 2026. Rules come from the
proposal "Entity resolution: rules and implementation"; current semantics are mapped
in the compiler brief (section numbers below refer to it as "brief §").

Phase 1 covers chemicals and genes/proteins from the existing hub Parquet files
(`/root/data/hubs/20260909` on nicesrv). Phase 2 adds miRNA, reactions and the
omnipath-utils pair export. Nothing in phase 1 may change the published Parquet
contract (`omnipath_core/schema.py`).

## 1. What replaces what

| Today | Phase 1 |
| --- | --- |
| `build_reference.py` graph, components, decisions, claims | `omnipath_build/identity/` builds one identity snapshot from hubs in DuckDB |
| LMDB compiler (`direct_compact_index`, `full_index*`, `candidate_limit`, `gene_role_index`) | none: the access table is plain partitioned Parquet |
| `FullRuntime` (LMDB) | `omnipath_resolver/identity_runtime.py: IdentityRuntime`, same `resolve()` contract |
| entity records precomputed for 304M entities | built on first use, cached per snapshot fingerprint |

The Rust kernels (`resolve_precomputed_batch`, `resolve_molecular_batch`), `LibraryMatcher`,
`observations.py` key encoding and the writer stay. `LibraryMatcher` picks the runtime from
the snapshot manifest `format`.

## 2. Rules to implement (phase 1)

Identity (build time):
1. Anchors: chemical = valid full InChIKey (same regex and empty-key exclusions as today);
   protein = UniProt record whose own primary accession it is (same as today);
   gene = every NCBI Gene hub record is the gene entity `entrez:N` (same as today);
   **lipid name** = for a chemical record with no InChIKey, its most specific Goslin name at
   species level or finer becomes its anchor, entity id `goslin:<level>:<name>` (same string
   format as today's goslin ids). Class/category level is never an anchor.
2. A record claiming two or more anchors of the kind (two InChIKeys, or two lipid names at its
   most specific level) is **quarantined**: its own entity, flagged, never connects anything.
3. **Attach (one step):** a record without an anchor joins an entity when its own identity
   cross-references (edges with `semantics='identity_xref'`, either direction, never
   `gene_product`) reach records whose anchors are all the same single anchor and none of those
   records is quarantined. Chains are never followed.
4. **Group:** records that have no anchor, were not attached, are not quarantined and have no
   identity edge to any anchored record are connected through their identity edges. A connected
   group becomes one entity only if it holds at most one record per hub; otherwise every record
   stays its own entity. Group entity id = record of the most preferred hub
   (chebi, lipidmaps, swisslipids, hmdb, chembl, pubchem, kegg, metanetx, bigg, refmet, ramp,
   ramp_gene), lowest `local_id` within that hub.
5. A full-structure Goslin name resolves to an InChIKey only when every record carrying that name
   and an InChIKey agrees on exactly one InChIKey; otherwise the name is not a lookup key for a
   structure entity.
6. Gene–product links (`gene_products`): same derivation as today (brief §3.6).

Lookup (runtime):
7. No candidate cutoff. Postings keep every candidate; more than one surviving candidate means
   the kernel abstains (Ambiguous). Keep the kernel's hard safety limits.
8. Precedence within one posting, applied before the kernel: native identity rows beat
   fallback rows (today's `chemical_fallback`); a primary-accession identity row beats
   `uniprot-sec` claim rows under ns `uniprot`; exact symbols beat `symbol_synonym` rows within
   one taxon. So a secondary accession resolves only when it maps to exactly one entry.
9. Taxon scope exactly as today (brief §1.5–1.6): scoped only when the observation has a symbol
   vote; symbols only scoped; scope = candidate entity taxon.
10. Gene-level namespaces (hgnc, ensg, enst, refseq RNA, genesymbol, genesymbol-syn, route 2)
    post gene entities, built like today's gene-role component (brief §2.7) but without the
    10-gene cap. Product namespaces post protein entities with their `gene_ids`.
11. **A SMILES-derived InChIKey is not a primary anchor.** It votes as an ordinary identity vote
    (`anchor=""`). A stated InChIKey keeps the decide-alone priority.

Entity records and labels:
12. Record = `{entity_id, kind, anchor, taxon, label, identifiers, gene_ids}` exactly as
    `FullRuntime.record` returns (brief §3), built from member records' hub rows.
13. Labels, first available, deterministic, ties to shorter then alphabetical:
    gene: NCBI Gene symbol of the entrez record › `entrez:N` local part;
    protein: primary gene name of the UniProt entry (`genesymbol` claim, not `genesymbol-syn`) ›
    entry name › accession;
    chemical: ChEBI name › HMDB name › ChEMBL name › PubChem name › any other hub `name` ›
    IUPAC/systematic name; skip values matching the InChIKey regex and values over 80 chars;
    lipid (entity with a Goslin anchor, or a structure whose records carry a Goslin name):
    most specific Goslin shorthand;
    otherwise the entity id local part. Never a database ID formatted as a label
    (`CHEBI:…`, `CID:…`) while a name exists.

## 3. Snapshot layout (`<root>/identity/<snapshot>/`)

```
manifest.json            format "omnipath-identity-v1", fingerprint, hubs {name: {sha256, bytes}},
                         rules_sha256 (hash of omnipath_build/identity/*.py), counts, timings
records.parquet          record_id, hub, local_id, anchor, anchor_kind, anchor_count, taxon,
                         reviewed, entity_id, decision
                         decision in: anchored, lipid_name, attached, grouped, structureless,
                         ambiguous_native, quarantined, gene_identity
entities/part=XX/        entity_id, kind ('chemical'|'protein'|'gene'), anchor, taxon,
                         quarantined, preferred_record          (XX = md5(entity_id)[:2])
members/part=XX/         entity_id, record_id                   (XX = md5(entity_id)[:2])
record_rows/part=XX/     record_id, source_type, value           (XX = md5(record_id)[:2])
                         every hub row of the record, normalized like today, incl. names
access/target=T/part=XX/ route, ns, identifier, entity_id, kind, anchor, taxon, quarantined,
                         reviewed, gene_ids (list<varchar>), tag
                         (XX = md5(identifier)[:2], same as index_storage.partition;
                          files sorted by ns, identifier, entity_id; row groups ~64k rows)
gene_products.parquet    protein_entity_id, entrez_id, taxon
```

`tag` in: native, regular, fallback, secondary, symbol_synonym. The access table holds exactly
the assertion relations of brief §2.3 (minus route 3 / product rows), with today's
expansions (uniprot-sec under both namespaces, version-stripped refseq/genbank, isoform → parent
projection) and the entity metadata denormalized onto each row.

## 4. Runtime contract

`IdentityRuntime(path, cache_dir=None)`:
- `lookup_many(keys: list[bytes]) -> dict[bytes, {"candidates": [...], "gene": bool, "products": False}]`
  decoding keys with the existing layout (brief §1.1); one DuckDB query per batch, reading only
  the partitions the keys hash to; precedence rules 8; scope filter rule 9.
- candidate fact = `[num, entity_id, kind(1/2/3), anchor|None, quarantined, reviewed, gene_ids]`;
  `num` = first 8 bytes of sha256(entity_id) as unsigned int >> 1; candidates sorted by num;
  a num collision inside one batch raises.
- `record_many(entity_ids) -> dict[str, record]`, cached in `<cache_dir>/<fingerprint>/entities.sqlite`
  (WAL; concurrent readers; writes inside one transaction per batch).
- `resolve(queries, votes)` = `FullRuntime.resolve` with `lookup_many`/`record_many`; same
  return shape and metrics keys.
- `lookup(key)` and `record(eid)` kept as single-item wrappers.

## 5. Validation

- Gold set: `packages/build/tests/identity/` fixtures: tiny hub Parquet files with one case per
  rule above, expected entity per observation.
- Regression on nicesrv: extract observations once per resource (pre-resolution votes), resolve
  them with `FullRuntime` on the Oct 4 library and with `IdentityRuntime`; report counts per
  outcome and every differing observation, classified by the rule that explains it.

## 6. nicesrv conventions

- Code: `/root/projects/resolution/<checkout>`; push branches to the private bare repo
  `ssh://nicesrv/root/git/omnipath-build.git` (never to GitHub without asking).
- Data: hubs `/root/data/hubs/20260909`, pypath cache `PYPATH_DOWNLOAD_DATADIR=/root/data/pypath-cache`,
  identity snapshots `/root/data/identity/`, regression output `/root/data/regression/`.
- Old library (oracle): `/root/projects/omnipath-releases/20261004-molecular-forms/reference/library`.
- 8 CPUs, 15 GiB RAM, explore.omnipathdb.org runs on the same host: run heavy jobs with
  `systemd-run --scope -p MemoryMax=9G nice -n 10 …`, DuckDB `memory_limit` ≤ 7GB, ≤ 6 threads.
