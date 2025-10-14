---
config:
  layout: elk
---
erDiagram
    entity {
        bigint id PK
    }

    interaction {
        bigint id PK
        bigint entity_a_id FK
        bigint entity_b_id FK
        int type_id FK
    }

    entity_identifier {
        bigint id PK
        bigint entity_id FK
        varchar identifier
        int source_id FK
        int identifier_type_id FK
        varchar identifier_kind
    }

    entity_evidence {
        bigint id PK
        bigint entity_id FK
        int source_id FK
        json annotations
    }

    cv_namespace {
        int id PK
        varchar name
        text uri
        text description
    }

    cv_term {
        int id PK
        int namespace_id FK
        varchar accession
        varchar name
        text description
        boolean is_obsolete
        varchar replaces
        varchar replaced_by
    }

    source {
        int id PK
        varchar name
        varchar url
        text description
    }

    reference {
        int id PK
        varchar identifier
        text citation
        int published_year
        varchar journal
        text title
        int type_id FK
    }

    membership {
        bigint id PK
        bigint parent_id FK
        bigint member_id FK
        int role_id FK
        float stoichiometry
        int source_id FK
    }

    interaction_evidence {
        bigint id PK
        bigint interaction_id FK
        int detection_method_id FK
        text causal_statement
        text sentence
        boolean is_directed
        json annotations
        json entity_a_context
        json entity_b_context
        int source_id FK
    }

    compound {
        int id PK
        bigint entity_id FK
        varchar formula
        float molecular_weight
        float exact_mass
        float tpsa
        float logp
        int hbd
        int hba
        int rotatable_bonds
        int aromatic_rings
        int heavy_atoms
    }

    evidence_reference {
        int id PK
        int reference_id FK
        bigint entity_evidence_id FK
        bigint interaction_evidence_id FK
        bigint membership_id FK
    }

    entity ||--o{ entity_identifier : "has identifiers"
    entity ||--o{ interaction : "entity A"
    entity ||--o{ interaction : "entity B"
    entity ||--o{ entity_evidence : "described by"
    entity ||--o{ membership : "as parent"
    entity ||--o{ membership : "as member"
    entity ||--o{ compound : "has properties"

    cv_namespace ||--o{ cv_term : "has terms"
    cv_term ||--o{ entity_identifier : "identifier type"
    cv_term ||--o{ membership : "role"
    cv_term ||--o{ interaction : "interaction type"
    cv_term ||--o{ reference : "reference type"
    cv_term ||--o{ interaction_evidence : "detection method"

    source ||--o{ entity_evidence : "from"
    source ||--o{ membership : "from"
    source ||--o{ interaction_evidence : "from"
    interaction ||--o{ interaction_evidence : "supported by"

    reference ||--o{ evidence_reference : "linked"
    entity_evidence ||--o{ evidence_reference : "cites"
    membership ||--o{ evidence_reference : "cites"
    interaction_evidence ||--o{ evidence_reference : "cites"

---

- `entity.id` values come directly from the identifier clustering step; a dedicated entity dimension table is not materialised yet.
- All former namespace/name pairs now resolve to integer foreign keys referencing `cv_term.id`.
- The compound table is optional and only populated when RDKit is available during the gold build.
- `evidence_reference` allows a reference to be associated with any evidence record; only one of the three evidence foreign keys is populated per row.
- `entity_identifier` now carries a `source_id` and `identifier_kind` (e.g. `source_accession`, `cross_reference`) so the build pipeline can distinguish cluster-safe identifiers from reported cross references.
- TODO: ensure the clustering step and downstream loaders treat only cluster-safe identifier kinds as eligible for entity merges. -> initially only source accessions and standard inchi. but be careful, we dont deduplicate entity evidence.
- references can be stored at the moment with arbitrary values (e.g. doi, pmid, chembl_id -> we will fix / merge that in the future, where we will load the chembl documents that link chembl_id to doi and pmid and maybe the same for pmid only ones where we can load additional facts from crossref or so)
