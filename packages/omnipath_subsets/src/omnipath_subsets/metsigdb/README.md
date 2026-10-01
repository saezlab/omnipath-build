MetSigDB reads the resolved release already loaded into PostgreSQL. It never
downloads source files, opens a resolution reference or translates identifiers.

`rebuild(conn, schema)` replaces all five products within the caller's
transaction and returns release stamps and counts. It preserves the membership
table's existing columns and row key, using published text entity keys.

- Reactome and WikiPathways use typed chemical–pathway relations.
- KEGG joins pathway–reaction and reaction–chemical relations. Its eleven
  overview maps retain the existing `overview_map` subtype.
- MACdb uses chemical–trait associations and the published entity annotation
  `macdb:trait_type`, retaining native trait IDs and labels. It selects the
  smallest nonempty value within the exact resource, version and trait entity.
- ClassyFire combines HMDB assignments with ChemOnt subclass ancestors. Direct
  assignments win; ancestors use the shortest depth, capped at 20 as in the old
  product. Part-of axioms do not imply class membership.

The chosen migration policy uses all published chemical entities, including
source fallback identities. Serving schema 3 cannot reproduce the old
matched-only filter: resolver-labelled aliases occur on both matched and
fallback entities. `eligibility_policy='legacy_matched'` therefore raises before
writes. This policy changes no Parquet schemas or resolution behavior.

Projection selects canonical entity fields in resource/version order only for
chosen entity keys, and reads aliases across all pinned resources using
resource/version/entity predicates that match the existing identifier primary
key. Duplicate aliases and entities without projection aliases retain the same
membership and aggregation behavior.
The projected aggregate is materialized for reuse within each resource
query, so membership joins do not repeatedly recompute alias aggregates.

Projection reuses published aliases. HMDB accessions use at least seven digits;
the structure key is the connectivity block of a valid published InChIKey.
Taxonomy comes from the source's set entity. Provenance records the exact source
version, relation and selected evidence occurrence. `build_id` is the canonical
release-manifest hash.

Missing inputs are reported as skipped. ClassyFire requires both HMDB and
ChemOnt. A resource build's existing cap determines the input; subset extraction
does not cap memberships again, so set sizes count the complete selected input.
Rebuilds query PostgreSQL only; original source payloads remain in the versioned
Parquets for API inspection and reference replay.
