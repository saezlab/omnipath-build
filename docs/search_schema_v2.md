# Search Schema v2 Proposal (Entities, Associations, Interactions)

## Why this change

Current search documents mix:

1. **Raw evidence** (what annotations/sources exist)
2. **Derived interpretation** (direction/sign/activation logic)

This proposal keeps the schema **raw-first**, while preserving Meilisearch-friendly flattened fields.

---

## Do we need build-time positive/negative booleans?

Short answer: **No, not required**.

If frontend defines `positive_terms = [t1, t2, t3]`, it can filter directly on flattened term arrays (`interaction_annotation_terms`).

So precomputed `has_positive_sign` / `has_negative_sign` are convenience fields, not mandatory.

---

## Core design principles

1. **Raw evidence is first-class** (`evidence[]` with explicit serial).
2. **Annotation object uses exactly 3 keys**: `term`, `value`, `unit`.
3. **`term` and `unit` are formatted as `label:accession`**.
4. **Filterable annotation terms (no value/unit)** are materialized into one flattened array for Meilisearch.
5. **Derived semantic fields** (direction/sign/activation) are optional and can be computed downstream.

---

## Common annotation object (3-key)

Used inside evidence objects:

```json
{
  "term": "concentration mean:OM:0680",
  "value": "17.73",
  "unit": "milligram per 100 gram:OM:xxxx"
}
```

For pure term assertions (filterable):
- `value = null`
- `unit = null`

---

## 1) `search_entities` v2

Entity docs remain denormalized for search.

### Proposed fields

- `entity_id: int64` (primary key)
- `entity_type: string` (`label:accession`)
- `names: string[]`
- `synonyms: string[]`
- `gene_symbols: string[]`
- `descriptions: string[]`
- `references: string[]`
- `sources: string[]`
- `identifiers: [{ key, value }]`
- `ncbi_tax_id: string | null`
- `cv_terms: string[]` (`label:accession`)
- `cv_terms_go/cv_terms_mi/cv_terms_om/cv_terms_hp/cv_terms_kw: string[]`
- `complexes/pathways/reactions/reactants/products: int64[]`
- `stoichiometry: string[]`
- `pathway_steps: string[]`
- `num_interactions: int`

### Example row (entity)

```json
{
  "entity_id": 268,
  "entity_type": "protein:OM:0013",
  "names": ["PPARG"],
  "synonyms": ["NR1C3"],
  "gene_symbols": ["PPARG"],
  "descriptions": ["Nuclear receptor involved in adipogenesis"],
  "references": ["26481362"],
  "sources": ["UniProt:1234", "SIGNOR:5678"],
  "identifiers": [
    {"key": "uniprot accession:OM:xxxx", "value": "P37231"}
  ],
  "ncbi_tax_id": "9606",
  "cv_terms": ["lipid metabolism:GO:0006629", "agonist:OM:0501"],
  "cv_terms_go": ["lipid metabolism:GO:0006629"],
  "cv_terms_mi": [],
  "cv_terms_om": ["agonist:OM:0501"],
  "cv_terms_hp": [],
  "cv_terms_kw": [],
  "complexes": [9901],
  "pathways": [8802],
  "reactions": [],
  "reactants": [],
  "products": [],
  "stoichiometry": [],
  "pathway_steps": [],
  "num_interactions": 42
}
```

---

## 2) `search_associations` v2

Move from one merged annotation bucket to explicit evidence split.

### Proposed fields

- `association_id: int64`
- `association_key: string` (`parent_member`)
- `parent_entity_id: int64`
- `parent_entity_type: string` (`label:accession`)
- `member_entity_id: int64`
- `member_entity_type: string` (`label:accession`)
- `sources: string[]` (union across evidence)
- `evidence: [ ... ]`
  - `evidence_serial: int` (1..n per association)
  - `source_ids: int64[]`
  - `source_names: string[]`
  - `member_instance_ids: int64[]`
  - `annotations: [{term, value, unit}]`
  - `annotation_terms_filterable: string[]` (`label:accession` terms where `value` and `unit` are null)
- `association_annotation_terms: string[]` (flattened union across all evidence; required for Meilisearch)

### Example row (association)

```json
{
  "association_id": 1001,
  "association_key": "10015_12643",
  "parent_entity_id": 10015,
  "parent_entity_type": "food:OM:0035",
  "member_entity_id": 12643,
  "member_entity_type": "small molecule:OM:0020",
  "sources": ["FooDB"],
  "evidence": [
    {
      "evidence_serial": 1,
      "source_ids": [2001],
      "source_names": ["FooDB"],
      "member_instance_ids": [774411],
      "annotations": [
        {
          "term": "concentration mean:OM:0680",
          "value": "17.73",
          "unit": null
        },
        {
          "term": "experimentally observed:OM:0900",
          "value": null,
          "unit": null
        }
      ],
      "annotation_terms_filterable": ["experimentally observed:OM:0900"]
    }
  ],
  "association_annotation_terms": ["experimentally observed:OM:0900"]
}
```

---

## 3) `search_interactions` v2

Keep pair-level document, but make evidence explicit and raw.

### Proposed fields

- `interaction_id: int64` (deterministic export id)
- `interaction_key: string` (`member_a-member_b`)
- `member_a_id: int64`
- `member_b_id: int64`
- `member_types: string[]` (`label:accession`)
- `sources: string[]` (union across evidence)
- `evidence: [ ... ]`
  - `evidence_serial: int` (1..n per interaction key)
  - `interaction_entity_ids: int64[]` (underlying interaction entities collapsed into this pair)
  - `source_ids: int64[]`
  - `source_names: string[]`
  - `interaction_annotations: [{term, value, unit}]`
  - `member_a_annotations: [{term, value, unit}]`
  - `member_b_annotations: [{term, value, unit}]`
  - `annotation_terms_filterable: string[]` (union of value/unit-less terms across all 3 annotation groups)
- `interaction_annotation_terms: string[]` (flattened union across evidence; required for Meilisearch)

### Optional/deprecated derived fields

- `directions`
- `has_direction`
- `has_positive_sign`
- `has_negative_sign`

These can be removed once frontend fully owns ontology-based interpretation.

### Example row (interaction)

```json
{
  "interaction_id": 501,
  "interaction_key": "10-268",
  "member_a_id": 10,
  "member_b_id": 268,
  "member_types": ["small molecule:OM:0020", "protein:OM:0013"],
  "sources": ["BindingDB:1766"],
  "evidence": [
    {
      "evidence_serial": 1,
      "interaction_entity_ids": [900001],
      "source_ids": [1766],
      "source_names": ["BindingDB"],
      "interaction_annotations": [
        {
          "term": "pubmed:MI:0446",
          "value": "10346931",
          "unit": null
        },
        {
          "term": "agonist:OM:0501",
          "value": null,
          "unit": null
        }
      ],
      "member_a_annotations": [],
      "member_b_annotations": [
        {
          "term": "target role:OM:0600",
          "value": null,
          "unit": null
        }
      ],
      "annotation_terms_filterable": ["agonist:OM:0501", "target role:OM:0600"]
    }
  ],
  "interaction_annotation_terms": ["agonist:OM:0501", "target role:OM:0600"]
}
```

---

## Meilisearch requirement (explicit)

Keep these flattened top-level arrays:

- `interaction_annotation_terms: string[]`
- `association_annotation_terms: string[]`

Definition:
- include only annotation `term` values where `value == null/""` and `unit == null/""`.
- union across all evidence entries in the document.

---

## Deterministic evidence serial

`evidence_serial` should be deterministic per document by sorting evidence groups with a stable key, e.g.:

1. source_ids (sorted)
2. interaction_entity_ids / member_instance_ids
3. normalized annotation tuples `(term, value, unit)`

Then assign 1..n.
