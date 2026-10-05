# Entities and relations

Every resource version publishes the same three Parquet files. The schemas are
defined once in [`omnipath_core/schema.py`](../packages/core/omnipath_core/schema.py)
and [`molecular_forms.py`](../packages/core/omnipath_core/molecular_forms.py).
The current contract is `serving_schema_version = 4`; build manifests stay at
`schema_version = 1`.

| File | One row is | Holds |
| --- | --- | --- |
| `entities.parquet` | one [entity](glossary.md#entity) | identity, identifiers, annotations, gene references, standalone evidence |
| `relations.parquet` | one [statement](glossary.md#statement) | subject, predicate, object, qualifiers and every evidence occurrence |
| `evidence_payloads.parquet` | one source row | the original source payload as JSON, linked by `row_id` |

## Three questions, three fields

An entity row answers three different questions. Keeping them apart is the
central design choice of the schema.

| Field | Question | Example |
| --- | --- | --- |
| `reference_entity_key` | Which gene (or chemical) is this about? | `entrez:7157` |
| `entity_type` | What did the source describe? | `protein` |
| `molecular_form` (on evidence) | Which product, isoform, modifications or variants did this occurrence report? | `uniprot:P04637`, isoform `P04637-2` |

A record can say *gene reference `entrez:7157`* and *type `protein`* at the same
time. These are compatible statements: the namespace of the reference does not
determine the molecular type the source reported.

```yaml
# Source reports a specific isoform of p53.
entity_type: protein
reference_entity_key: entrez:7157
evidence[0].molecular_form:
  protein_entity_key: <key of the uniprot:P04637 protein row>
  isoform_identifier: {ns: uniprot, id: P04637-2}

# Source reports the TP53 gene itself: a separate, gene-typed row.
entity_type: gene
reference_entity_key: entrez:7157
evidence[0].molecular_form: null

# Source reports a protein but only gives a GeneID: still protein-typed.
entity_type: protein
reference_entity_key: entrez:7157
evidence[0].molecular_form: null
```

> **Decision: keep the source's entity type.** `entity_type` stays what the
> source reported. Resolving a protein to a gene reference does not change its
> type to `gene`. We rejected moving the source type into a separate
> `participant_type` field. *Why:* protein-level and gene-level knowledge are
> different claims, and users need to see the difference. As a result, a
> protein row and a gene row of the same gene are separate entities with the
> same `reference_entity_key`. (5 October 2026)

> **Decision: missing means unspecified.** An absent molecular form, isoform,
> modification or variant means the source did not say. It never means
> gene-level evidence, the canonical isoform, an unmodified protein or the wild
> type. Older evidence without molecular context stays unknown. *Why:* treating
> silence as a claim would create false results in form queries.

## Keys

Keys are deterministic SHA-256 hashes ([`keys.py`](../packages/core/omnipath_core/keys.py)).

- **`entity_key`** = hash(`entity_type`, `namespace`, `identifier`). A protein
  resolved to gene 7157 and a gene resolved to gene 7157 therefore have
  *different* entity keys and share one `reference_entity_key`.
  Unresolved names and synonyms are additionally scoped by source and taxon, and
  unresolved gene symbols by taxon, so equal strings from different contexts are
  not merged.
- **`relation_key`** = hash(subject key, predicate, object key, Biolink
  qualifiers). Symmetric predicates without qualifiers sort their endpoints
  first. Ontology statements are hashed separately (`statement_kind = ontology`).

## `entities.parquet`

| Column | Meaning |
| --- | --- |
| `entity_key` | Row identity (see above) |
| `entity_type` | Biolink type reported by the source, e.g. `protein`, `gene`, `small_molecule` |
| `namespace`, `identifier` | The canonical identifier of this row, e.g. `uniprot` / `P04637` or `inchikey` / … |
| `taxon` | NCBI Taxonomy ID; empty when sources disagree |
| `label` | Display name chosen deterministically |
| `identifiers[]` | All admitted identifiers `{ns, id, is_canonical, source}` |
| `annotations[]` | Entity-level attributes `{term, value, quantity, source, dataset}` |
| `reference_entity_key` | Shared grouping reference, normally an NCBI Gene CURIE |
| `gene_reference_keys[]` | Catalogue gene links of a product row (a protein can link to several genes) |
| `has_hierarchy`, `parent_count`, `child_count` | Ontology position, from distinct ontology edges |
| `evidence[]` | Standalone source occurrences of this entity, each with its own `molecular_form` |

Product rows (primary UniProt proteins and specifically reported transcripts)
are ordinary typed entity rows. Observations point to them through
`molecular_form.protein_entity_key` or `transcript_entity_key`. We do **not**
create an entity for each combination of isoform, modification and variant.

> **Decision: no entity per molecular state.** Reusable protein and transcript
> entities exist, but isoform/modification/variant combinations stay on the
> occurrence. Modifications and variants are stored individually so they can be
> queried. *Why:* the number of combinations is unbounded, and combining evidence
> for X+Y with X+Z must not invent an X+Y+Z form. A state model can be
> reconsidered if pathway state transitions need it. (4 October 2026)

## `relations.parquet`

| Column | Meaning |
| --- | --- |
| `relation_key`, `statement_kind` | Identity; `relation` or `ontology` |
| `subject_entity_key`, `predicate`, `object_entity_key` | The statement; predicate is a Biolink predicate |
| `subject_type` / `object_type`, `*_label` | Endpoint type and label, copied for convenience |
| `subject_reference_entity_key`, `object_reference_entity_key` | Endpoint gene references, used for grouping |
| `taxon`, `is_directed`, `sign` | Scalar summary; `sign` is `1`, `-1` or `0` |
| `category`, `interaction_class` | Presentation categories, e.g. `interaction` / `directed_inhibitory` |
| `sources[]`, `evidence_count` | Contributing resources and number of occurrences |
| `evidence[]` | Every occurrence (see below) |
| `annotations[]` | Statement-level attributes with a `scope` of relation, subject or object |

### Evidence

Each evidence item is one source occurrence:
`{source, dataset, row_id, upstream_id, annotations[], subject_molecular_form, object_molecular_form}`.

- Evidence is a **[multiset](glossary.md#evidence)**: identical occurrences stay separate.
- The two molecular forms stay **paired** on their occurrence. If a symmetric
  relation flips its endpoints, the forms and annotation scopes flip with it.
- Gene-level evidence is never copied onto proteins of that gene.
- `row_id` links to `evidence_payloads.parquet` for the raw source record.

> **Decision: evidence is a multiset with paired forms.** Every source
> occurrence stays a separate evidence item, even when identical, and its
> subject and object forms stay together. *Why:* counts and filters need the
> real occurrences, and a union of forms across a relation would lose which
> participants were observed together.

> **Decision: conflicting taxa project to empty.** The scalar `taxon` is filled
> only when the assertions agree; otherwise it is empty and each occurrence keeps
> its own. *Why:* picking one species would hide a real conflict.

## Molecular form

| Field | Meaning |
| --- | --- |
| `protein_entity_key` / `transcript_entity_key` | Points to the reusable product row, set by resolution only |
| `isoform_identifier` | `{ns, id}` that specifically identifies the reported isoform |
| `sequence_identifiers[]` | Other identifiers that pin the exact sequence |
| `modifications[]` | `{term, residue, position, end_position, coordinate_reference, description}` |
| `variants[]` | `{identifier, reference, alternate, position, end_position, coordinate_reference, description}` |

Positions keep the coordinate reference the source used (identifier and
version, coordinate system, position base). An unknown reference stays unknown;
it is not replaced with the selected primary protein.
