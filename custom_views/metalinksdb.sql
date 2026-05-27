-- omnipath-build/custom_views/metalinksdb.sql
-- MetaLinksDB materialized views for each interaction source.
-- Apply with:  psql -U omnipath -d omnipath -f metalinksdb.sql
-- Refresh after each:  make load SOURCE=<source> && make derive
--   then: REFRESH MATERIALIZED VIEW CONCURRENTLY metalinksdb_<source>_relations;
--
-- Human-only filter: all views restrict to ncbi_tax_id = '9606' (Homo sapiens).
-- For ChEMBL this comes from annotation term 'Ncbi Tax Id:OM:0205'.
-- When adding a new source, confirm where organism info lives before adding the filter.

-- ────────────────────────────────────────────────────────────────────────────
-- ChEMBL  (source_id = 6)
-- Human-only (ncbi_tax_id = 9606). Expected row count TBD after first full run.
-- ────────────────────────────────────────────────────────────────────────────

SET max_parallel_workers_per_gather = 0;
SET max_parallel_workers = 0;
SET work_mem = '8MB';

DROP MATERIALIZED VIEW IF EXISTS metalinksdb_chembl_relations;
CREATE MATERIALIZED VIEW metalinksdb_chembl_relations AS
WITH

-- !! TESTING ONLY — remove sampled_re and revert the three CTEs below
-- !! (swap IN (SELECT ... FROM sampled_re) back to WHERE source_id = 6)
-- !! before the full production build. LIMIT is 10000 so the human filter
-- !! below is likely to match some rows (~10% of ChEMBL is human).
sampled_re AS (
    SELECT relation_evidence_id,
           source_id,
           subject_entity_evidence_id,
           object_entity_evidence_id,
           predicate_id,
           relation_category_id
    FROM relation_evidence
    WHERE source_id = 6
    LIMIT 10000
),

-- Aggregate raw compound identifiers per entity_evidence
compound_ids AS (
    SELECT
        eei.entity_evidence_id,
        MAX(CASE WHEN ie.identifier_type_id = 17 THEN ie.value END) AS chembl_compound_id,
        MAX(CASE WHEN ie.identifier_type_id = 13 THEN ie.value END) AS inchikey,
        MAX(CASE WHEN ie.identifier_type_id = 27 THEN ie.value END) AS smiles,
        MAX(CASE WHEN ie.identifier_type_id = 16 THEN ie.value END) AS compound_name
    FROM entity_evidence_identifier eei
    JOIN identifier_evidence ie ON ie.identifier_id = eei.identifier_id
    WHERE eei.entity_evidence_id IN (SELECT subject_entity_evidence_id FROM sampled_re)
    GROUP BY eei.entity_evidence_id
),

-- Aggregate raw protein identifiers per entity_evidence
protein_ids AS (
    SELECT
        eei.entity_evidence_id,
        MAX(CASE WHEN ie.identifier_type_id = 1  THEN ie.value END) AS uniprot_id,
        MAX(CASE WHEN ie.identifier_type_id = 33 THEN ie.value END) AS chembl_target_id
    FROM entity_evidence_identifier eei
    JOIN identifier_evidence ie ON ie.identifier_id = eei.identifier_id
    WHERE eei.entity_evidence_id IN (SELECT object_entity_evidence_id FROM sampled_re)
    GROUP BY eei.entity_evidence_id
),

-- Pivot annotation terms into columns per relation_evidence
rel_annotations AS (
    SELECT
        rea.relation_evidence_id,
        MAX(CASE WHEN a.term = 'Chembl Activity:OM:0228'   THEN a.value END) AS chembl_activity_id,
        MAX(CASE WHEN a.term = 'Chembl Assay:OM:0225'      THEN a.value END) AS chembl_assay_id,
        MAX(CASE WHEN a.term = 'Chembl Document:OM:0226'   THEN a.value END) AS chembl_document_id,
        MAX(CASE WHEN a.term = 'Chembl Target:MI:1348'     THEN a.value END) AS chembl_target_ann,
        MAX(CASE WHEN a.term = 'Pchembl Value:OM:0708'     THEN a.value END) AS pchembl_value,
        MAX(CASE WHEN a.term = 'Confidence Score:OM:0762'  THEN a.value END) AS confidence_score,
        MAX(CASE WHEN a.term = 'Ic50:MI:0641'              THEN a.value END) AS ic50,
        MAX(CASE WHEN a.term = 'Ki:MI:0643'                THEN a.value END) AS ki,
        MAX(CASE WHEN a.term = 'Kd:MI:0646'                THEN a.value END) AS kd,
        MAX(CASE WHEN a.term = 'Ec50:MI:0642'              THEN a.value END) AS ec50,
        MAX(CASE WHEN a.term = 'Assay Category:OM:0761'    THEN a.value END) AS assay_category,
        MAX(CASE WHEN a.term = 'Cell Type:OM:0765'         THEN a.value END) AS cell_type,
        MAX(CASE WHEN a.term = 'Tissue:OM:0764'            THEN a.value END) AS tissue,
        MAX(CASE WHEN a.term = 'Ncbi Tax Id:OM:0205'       THEN a.value END) AS ncbi_tax_id,
        MAX(CASE WHEN a.term = 'Description:OM:0613'       THEN a.value END) AS description,
        MAX(CASE WHEN a.term = 'Chembl Mechanism:OM:0227'  THEN a.value END) AS chembl_mechanism,
        MAX(CASE WHEN a.term = 'Pubmed:MI:0446'            THEN a.value END) AS pubmed_id,
        MAX(CASE WHEN a.term = 'Doi:MI:0574'               THEN a.value END) AS doi,
        BOOL_OR(a.term = 'Binding:OM:0751')                                  AS is_binding_assay,
        BOOL_OR(a.term = 'Functional:OM:0752')                               AS is_functional_assay,
        BOOL_OR(a.term = 'Adme:OM:0753')                                     AS is_adme_assay,
        BOOL_OR(a.term = 'Toxicity:OM:0754')                                 AS is_toxicity_assay,
        BOOL_OR(a.term = 'Inhibition:OM:0931')                               AS is_inhibition,
        BOOL_OR(a.term = 'Activation:OM:0930')                               AS is_activation,
        BOOL_OR(a.term = 'Agonist:OM:0901')                                  AS is_agonist,
        BOOL_OR(a.term = 'Antagonist:OM:0920')                               AS is_antagonist
    FROM relation_evidence_annotation rea
    JOIN annotation a ON a.annotation_key = rea.annotation_key
    WHERE rea.relation_evidence_id IN (SELECT relation_evidence_id FROM sampled_re)
    GROUP BY rea.relation_evidence_id
)

SELECT
    -- Source tracking (needed when UNIONing across resources later)
    'chembl'::text                              AS source,
    re.relation_evidence_id,

    -- Canonical compound (cross-resource join key)
    e_compound.entity_id                        AS compound_entity_id,
    e_compound.canonical_identifier             AS compound_canonical_id,
    vit_c.name                                  AS compound_canonical_id_type,
    eer_compound.status_id                      AS compound_resolution_status,

    -- Raw ChEMBL compound identifiers (traceability + fallback for unresolved)
    ci.chembl_compound_id,
    ci.inchikey                                 AS compound_inchikey,
    ci.smiles                                   AS compound_smiles,
    ci.compound_name,

    -- Canonical protein (cross-resource join key)
    e_protein.entity_id                         AS protein_entity_id,
    e_protein.canonical_identifier              AS protein_canonical_id,
    vit_p.name                                  AS protein_canonical_id_type,
    eer_protein.status_id                       AS protein_resolution_status,

    -- Raw protein identifiers
    pi.uniprot_id                               AS protein_uniprot,
    pi.chembl_target_id                         AS protein_chembl_target,

    -- Relation type
    vrp.name                                    AS predicate,
    vrc.name                                    AS relation_category,

    -- ChEMBL record IDs
    ann.chembl_activity_id,
    ann.chembl_assay_id,
    ann.chembl_document_id,
    ann.chembl_target_ann,

    -- Quantitative affinity (stored as text in annotation; cast at query time)
    ann.pchembl_value::numeric                  AS pchembl_value,
    ann.confidence_score::numeric               AS confidence_score,
    ann.ic50::numeric                           AS ic50,
    ann.ki::numeric                             AS ki,
    ann.kd::numeric                             AS kd,
    ann.ec50::numeric                           AS ec50,

    -- Assay classification
    ann.assay_category,
    ann.is_binding_assay,
    ann.is_functional_assay,
    ann.is_adme_assay,
    ann.is_toxicity_assay,
    ann.is_inhibition,
    ann.is_activation,
    ann.is_agonist,
    ann.is_antagonist,

    -- Context
    ann.cell_type,
    ann.tissue,
    ann.ncbi_tax_id,
    ann.description,
    ann.chembl_mechanism,

    -- Literature
    ann.pubmed_id,
    ann.doi

FROM sampled_re re  -- !! TESTING ONLY: replace with `relation_evidence re` for production

-- Subject entity_evidence (compound)
JOIN entity_evidence ee_compound
    ON  ee_compound.source_id          = re.source_id
    AND ee_compound.entity_evidence_id = re.subject_entity_evidence_id

-- Object entity_evidence (protein)
JOIN entity_evidence ee_protein
    ON  ee_protein.source_id          = re.source_id
    AND ee_protein.entity_evidence_id = re.object_entity_evidence_id

-- Raw compound identifiers
LEFT JOIN compound_ids ci
    ON ci.entity_evidence_id = re.subject_entity_evidence_id

-- Raw protein identifiers
LEFT JOIN protein_ids pi
    ON pi.entity_evidence_id = re.object_entity_evidence_id

-- Compound canonical resolution
LEFT JOIN entity_evidence_resolution eer_compound
    ON  eer_compound.source_id          = re.source_id
    AND eer_compound.entity_evidence_id = re.subject_entity_evidence_id
LEFT JOIN entity e_compound
    ON e_compound.entity_id = eer_compound.entity_id
LEFT JOIN vocab_identifier_type vit_c
    ON vit_c.identifier_type_id = e_compound.canonical_identifier_type_id

-- Protein canonical resolution
LEFT JOIN entity_evidence_resolution eer_protein
    ON  eer_protein.source_id          = re.source_id
    AND eer_protein.entity_evidence_id = re.object_entity_evidence_id
LEFT JOIN entity e_protein
    ON e_protein.entity_id = eer_protein.entity_id
LEFT JOIN vocab_identifier_type vit_p
    ON vit_p.identifier_type_id = e_protein.canonical_identifier_type_id

-- Relation annotations
LEFT JOIN rel_annotations ann
    ON ann.relation_evidence_id = re.relation_evidence_id

-- Relation type vocab
JOIN vocab_relation_predicate vrp
    ON vrp.relation_predicate_id = re.predicate_id
JOIN vocab_relation_category vrc
    ON vrc.relation_category_id = re.relation_category_id

-- Human-only filter: restrict to Homo sapiens (taxon 9606)
-- !! For production: also add `re.source_id = 6` here when sampled_re is removed
WHERE ann.ncbi_tax_id = '9606';

-- Index for fast lookups and CONCURRENT refresh
CREATE UNIQUE INDEX ON metalinksdb_chembl_relations (relation_evidence_id);
CREATE INDEX ON metalinksdb_chembl_relations (compound_entity_id);
CREATE INDEX ON metalinksdb_chembl_relations (protein_entity_id);
CREATE INDEX ON metalinksdb_chembl_relations (compound_canonical_id);
CREATE INDEX ON metalinksdb_chembl_relations (protein_uniprot);
