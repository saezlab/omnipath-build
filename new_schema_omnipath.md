---
config:
  layout: elk
---
erDiagram
    entity {
        bigint id PK
        int cv_term_id FK
        timestamp created_at
    }

membership {
    bigint id PK
    bigint parent_id FK
    bigint member_id FK
    float stoichiometry
    int role_id FK
    bigint annotation_record_id FK
    bigint provenance_id FK
}

interaction {
    bigint id PK
    bigint entity_a_id FK
    bigint entity_b_id FK
}

entity_identifier {
    bigint id PK
    bigint entity_id FK
    int cv_term_id FK
    varchar identifier
    timestamp created_at
    bigint provenance_id FK
}

provenance {
    bigint id PK
    int source_id FK
    int primary_source_id FK
    bigint reference_id FK
    timestamp created_at
}

interaction_evidence {
    bigint id PK
    bigint interaction_id FK
    bigint provenance_id FK
    bigint annotation_record_id FK
    bigint entity_a_annotation_record_id FK
    bigint entity_b_annotation_record_id FK
    int type_id FK
    int direction_id FK
    int sign_id FK
    int causal_mechanism_id FK
    int causal_statement_id FK
    text sentence
    boolean is_directed
    timestamp created_at
}

annotation_record {
    bigint id PK
    bigint provenance_id FK
    timestamp created_at
    text note
}

annotation {
    bigint id PK
    bigint record_id FK
    int term_id FK
    int value_term_id FK
    text value_text
    float value_num
    varchar units
    timestamp created_at
}

entity_annotation_record {
    bigint entity_id FK
    bigint annotation_record_id FK
    varchar role
}

reference {
    bigint id PK
    int type_id FK
    text value
    text citation
    int year
    text journal
    text title
}

source {
    int id PK
    varchar name
    varchar url
    text description
    timestamp created_at
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
    int replaces
    int replaced_by
}

protein {
    bigint entity_id PK
    text name
    varchar class
    text sequence
}

compound {
    bigint entity_id PK
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

reaction {
    bigint entity_id PK
    text equation
    varchar directionality
    varchar pathway
    varchar ec_number
    text smiles
}

%% Relationships

entity ||--o{ entity_identifier : "has identifiers"
entity_identifier }o--|| cv_term : "type"
entity_identifier }o--|| provenance : "has provenance"

entity ||--o{ membership : "has members"
membership }o--|| entity : "member entity"
membership }o--|| annotation_record : "has annotations"

entity ||--o{ interaction : "participates in"

interaction ||--o{ interaction_evidence : "supported by"
interaction_evidence }o--|| provenance : "has provenance"

entity ||--o{ entity_annotation_record : "has annotations"
entity_annotation_record }o--|| annotation_record : ""

annotation_record ||--o{ annotation : "contains"
annotation_record }o--|| provenance : "has provenance"

provenance }o--|| source : "source"
provenance }o--o| source : "primary source"
provenance }o--o| reference : "cites"

cv_namespace ||--o{ cv_term : "has terms"
annotation }o--|| cv_term : "typed by"
annotation }o--|| cv_term : "qualifier"
membership }o--|| cv_term : "role"
entity }o--|| cv_term : "typed by"
interaction_evidence }o--|| cv_term : "type"
interaction_evidence }o--|| cv_term : "causal mechanism"
interaction_evidence }o--|| cv_term : "causal statement"
interaction_evidence }o--|| cv_term : "direction"
interaction_evidence }o--|| cv_term : "sign"

%% Evidence-level annotations
interaction_evidence }o--|| annotation_record : "has annotation"
interaction_evidence }o--|| annotation_record : "entity A context"
interaction_evidence }o--|| annotation_record : "entity B context"

reference }o--|| cv_term : "type"

entity ||--|| protein : "protein details"
entity ||--|| compound : "compound details"
entity ||--|| reaction : "reaction details"
