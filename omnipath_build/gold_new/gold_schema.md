    interaction {
        bigint id PK
        bigint entity_a_id FK
        bigint entity_b_id FK
        bigint type_id FK
    }

    entity_identifier {
        bigint id PK
        bigint entity_id FK
        varchar identifier
        bigint type_id FK
        bigint source_id FK
    }

    entity_evidence {
        bigint id PK
        bigint entity_id FK
        int entity_type_id FK
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
        varchar type_namespace_name
        varchar type_name
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
        bigint interaction_type_id FK
        bigint detection_method_id FK
        bigint causal_mechanism_id FK
        bigint causal_statement_id FK
        text sentence
        json interaction_annotations
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
