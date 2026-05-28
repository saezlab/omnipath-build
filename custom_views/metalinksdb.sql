-- omnipath-build/custom_views/metalinksdb.sql
-- MetaLinksDB materialized views for each interaction source.
-- Apply with:  psql -U omnipath -d omnipath -f metalinksdb.sql
-- Refresh after each:  make load SOURCE=<source> && make derive
--   then: REFRESH MATERIALIZED VIEW CONCURRENTLY metalinksdb_<source>_relations;
--
-- Human-only filter: all views restrict to ncbi_tax_id = '9606' (Homo sapiens).
-- For ChEMBL this comes from annotation term 'Ncbi Tax Id:OM:0205'.
-- When adding a new source, confirm where organism info lives before adding the filter.
--
-- Source IDs (as of omnipath-build c39dd5a rebuild 2026-05-28):
--   chembl=16  bindingdb=12  cellinker=14  guidetopharma=21
--   mrclinksdb=28  stitch=39  tcdb=41
--
-- Entity type IDs: SmallMolecule=2  Protein=3  Complex=4  Reaction=11  Transport=12

-- ────────────────────────────────────────────────────────────────────────────
-- ChEMBL  (source_id = 16)
-- Human-only (ncbi_tax_id = 9606). Expected row count TBD after first full run.
-- ────────────────────────────────────────────────────────────────────────────

SET max_parallel_workers_per_gather = 0;
SET max_parallel_workers = 0;
SET work_mem = '8MB';

DROP MATERIALIZED VIEW IF EXISTS metalinksdb_chembl_relations;
CREATE MATERIALIZED VIEW metalinksdb_chembl_relations AS
WITH

-- !! TESTING ONLY — remove sampled_re and revert the three CTEs below
-- !! (swap IN (SELECT ... FROM sampled_re) back to WHERE source_id = 16)
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
    WHERE source_id = 16
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
-- !! For production: also add `re.source_id = 16` here when sampled_re is removed
WHERE ann.ncbi_tax_id = '9606';

-- Index for fast lookups and CONCURRENT refresh
CREATE UNIQUE INDEX ON metalinksdb_chembl_relations (relation_evidence_id);
CREATE INDEX ON metalinksdb_chembl_relations (compound_entity_id);
CREATE INDEX ON metalinksdb_chembl_relations (protein_entity_id);
CREATE INDEX ON metalinksdb_chembl_relations (compound_canonical_id);
CREATE INDEX ON metalinksdb_chembl_relations (protein_uniprot);

-- ────────────────────────────────────────────────────────────────────────────
-- BindingDB  (source_id = 12)
-- Human-only via e_protein.taxonomy_id = 9606. Expected human rows: TBD.
-- !! TESTING ONLY — sampled_re limits to 10000 rows; remove before full build.
-- ────────────────────────────────────────────────────────────────────────────

DROP MATERIALIZED VIEW IF EXISTS metalinksdb_bindingdb_relations;
CREATE MATERIALIZED VIEW metalinksdb_bindingdb_relations AS
WITH

sampled_re AS (
    SELECT relation_evidence_id, source_id,
           subject_entity_evidence_id, object_entity_evidence_id,
           predicate_id, relation_category_id
    FROM relation_evidence
    WHERE source_id = 12
    LIMIT 10000
),

compound_ids AS (
    SELECT eei.entity_evidence_id,
        MAX(CASE WHEN ie.identifier_type_id = 23 THEN ie.value END) AS bindingdb_compound_id,
        MAX(CASE WHEN ie.identifier_type_id = 13 THEN ie.value END) AS inchikey,
        MAX(CASE WHEN ie.identifier_type_id = 27 THEN ie.value END) AS smiles,
        MAX(CASE WHEN ie.identifier_type_id = 12 THEN ie.value END) AS pubchem_cid,
        MAX(CASE WHEN ie.identifier_type_id = 17 THEN ie.value END) AS chembl_compound_id
    FROM entity_evidence_identifier eei
    JOIN identifier_evidence ie ON ie.identifier_id = eei.identifier_id
    WHERE eei.entity_evidence_id IN (SELECT subject_entity_evidence_id FROM sampled_re)
    GROUP BY eei.entity_evidence_id
),

protein_ids AS (
    SELECT eei.entity_evidence_id,
        MAX(CASE WHEN ie.identifier_type_id = 1 THEN ie.value END) AS uniprot_id
    FROM entity_evidence_identifier eei
    JOIN identifier_evidence ie ON ie.identifier_id = eei.identifier_id
    WHERE eei.entity_evidence_id IN (SELECT object_entity_evidence_id FROM sampled_re)
    GROUP BY eei.entity_evidence_id
),

rel_annotations AS (
    SELECT rea.relation_evidence_id,
        MAX(CASE WHEN a.term = 'Pchembl Value:OM:0708'        THEN a.value END) AS pchembl_value,
        MAX(CASE WHEN a.term = 'Ic50:MI:0641'                 THEN a.value END) AS ic50,
        MAX(CASE WHEN a.term = 'Ki:MI:0643'                   THEN a.value END) AS ki,
        MAX(CASE WHEN a.term = 'Kd:MI:0646'                   THEN a.value END) AS kd,
        MAX(CASE WHEN a.term = 'Ec50:MI:0642'                 THEN a.value END) AS ec50,
        MAX(CASE WHEN a.term = 'Pubmed:MI:0446'               THEN a.value END) AS pubmed_id,
        MAX(CASE WHEN a.term = 'Doi:MI:0574'                  THEN a.value END) AS doi,
        MAX(CASE WHEN a.term = 'Patent Number:OM:0206'        THEN a.value END) AS patent_number,
        MAX(CASE WHEN a.term = 'Comment:MI:0612'              THEN a.value END) AS comment,
        MAX(CASE WHEN a.term = 'Ph:MI:0837'                   THEN a.value END) AS ph,
        MAX(CASE WHEN a.term = 'Temperature Celsius:OM:0701'  THEN a.value END) AS temperature_celsius
    FROM relation_evidence_annotation rea
    JOIN annotation a ON a.annotation_key = rea.annotation_key
    WHERE rea.relation_evidence_id IN (SELECT relation_evidence_id FROM sampled_re)
    GROUP BY rea.relation_evidence_id
)

SELECT
    'bindingdb'::text               AS source,
    re.relation_evidence_id,
    e_compound.entity_id            AS compound_entity_id,
    e_compound.canonical_identifier AS compound_canonical_id,
    vit_c.name                      AS compound_canonical_id_type,
    eer_compound.status_id          AS compound_resolution_status,
    ci.bindingdb_compound_id,
    ci.inchikey                     AS compound_inchikey,
    ci.smiles                       AS compound_smiles,
    ci.pubchem_cid                  AS compound_pubchem_cid,
    ci.chembl_compound_id           AS compound_chembl_id,
    e_protein.entity_id             AS protein_entity_id,
    e_protein.canonical_identifier  AS protein_canonical_id,
    vit_p.name                      AS protein_canonical_id_type,
    eer_protein.status_id           AS protein_resolution_status,
    pi.uniprot_id                   AS protein_uniprot,
    vrp.name                        AS predicate,
    vrc.name                        AS relation_category,
    NULLIF(regexp_replace(ann.pchembl_value, '[^0-9.eE+-]', '', 'g'), '')::numeric AS pchembl_value,
    NULLIF(regexp_replace(ann.ic50,          '[^0-9.eE+-]', '', 'g'), '')::numeric AS ic50,
    NULLIF(regexp_replace(ann.ki,            '[^0-9.eE+-]', '', 'g'), '')::numeric AS ki,
    NULLIF(regexp_replace(ann.kd,            '[^0-9.eE+-]', '', 'g'), '')::numeric AS kd,
    NULLIF(regexp_replace(ann.ec50,          '[^0-9.eE+-]', '', 'g'), '')::numeric AS ec50,
    ann.pubmed_id,
    ann.doi,
    ann.patent_number,
    ann.comment,
    NULLIF(regexp_replace(ann.ph,            '[^0-9.eE+-]', '', 'g'), '')::numeric AS ph,
    NULLIF(split_part(ann.temperature_celsius, ' ', 1), '')::numeric AS temperature_celsius

FROM sampled_re re  -- !! TESTING ONLY: replace with `relation_evidence re` for production

JOIN entity_evidence ee_compound
    ON  ee_compound.source_id          = re.source_id
    AND ee_compound.entity_evidence_id = re.subject_entity_evidence_id
    AND ee_compound.entity_type_id     = 2
JOIN entity_evidence ee_protein
    ON  ee_protein.source_id          = re.source_id
    AND ee_protein.entity_evidence_id = re.object_entity_evidence_id
    AND ee_protein.entity_type_id     = 3
LEFT JOIN compound_ids ci ON ci.entity_evidence_id = re.subject_entity_evidence_id
LEFT JOIN protein_ids  pi ON pi.entity_evidence_id = re.object_entity_evidence_id
LEFT JOIN entity_evidence_resolution eer_compound
    ON  eer_compound.source_id          = re.source_id
    AND eer_compound.entity_evidence_id = re.subject_entity_evidence_id
LEFT JOIN entity e_compound ON e_compound.entity_id = eer_compound.entity_id
LEFT JOIN vocab_identifier_type vit_c
    ON vit_c.identifier_type_id = e_compound.canonical_identifier_type_id
LEFT JOIN entity_evidence_resolution eer_protein
    ON  eer_protein.source_id          = re.source_id
    AND eer_protein.entity_evidence_id = re.object_entity_evidence_id
LEFT JOIN entity e_protein ON e_protein.entity_id = eer_protein.entity_id
LEFT JOIN vocab_identifier_type vit_p
    ON vit_p.identifier_type_id = e_protein.canonical_identifier_type_id
LEFT JOIN rel_annotations ann ON ann.relation_evidence_id = re.relation_evidence_id
JOIN vocab_relation_predicate vrp ON vrp.relation_predicate_id = re.predicate_id
JOIN vocab_relation_category  vrc ON vrc.relation_category_id  = re.relation_category_id
WHERE e_protein.taxonomy_id = 9606;

CREATE UNIQUE INDEX ON metalinksdb_bindingdb_relations (relation_evidence_id);
CREATE INDEX ON metalinksdb_bindingdb_relations (compound_entity_id);
CREATE INDEX ON metalinksdb_bindingdb_relations (protein_entity_id);
CREATE INDEX ON metalinksdb_bindingdb_relations (compound_canonical_id);
CREATE INDEX ON metalinksdb_bindingdb_relations (protein_uniprot);

-- ────────────────────────────────────────────────────────────────────────────
-- CellLinker  (source_id = 14)  — metabolite-protein pairs only (SM=2→Prot=3)
-- Human-only via e_protein.taxonomy_id = 9606. Expected human rows: ~5884.
-- No sampling needed — only 5884 metabolite-protein rows total.
-- ────────────────────────────────────────────────────────────────────────────

DROP MATERIALIZED VIEW IF EXISTS metalinksdb_cellinker_relations;
CREATE MATERIALIZED VIEW metalinksdb_cellinker_relations AS
WITH

compound_ids AS (
    SELECT eei.entity_evidence_id,
        MAX(CASE WHEN ie.identifier_type_id = 9  THEN ie.value END) AS hmdb_id,
        MAX(CASE WHEN ie.identifier_type_id = 12 THEN ie.value END) AS pubchem_cid,
        MAX(CASE WHEN ie.identifier_type_id = 27 THEN ie.value END) AS smiles
    FROM entity_evidence_identifier eei
    JOIN identifier_evidence ie ON ie.identifier_id = eei.identifier_id
    WHERE eei.source_id = 14
    GROUP BY eei.entity_evidence_id
),

protein_ids AS (
    SELECT eei.entity_evidence_id,
        MAX(CASE WHEN ie.identifier_type_id = 1 THEN ie.value END) AS uniprot_id,
        MAX(CASE WHEN ie.identifier_type_id = 3 THEN ie.value END) AS entrez_id,
        MAX(CASE WHEN ie.identifier_type_id = 5 THEN ie.value END) AS gene_name
    FROM entity_evidence_identifier eei
    JOIN identifier_evidence ie ON ie.identifier_id = eei.identifier_id
    WHERE eei.source_id = 14
    GROUP BY eei.entity_evidence_id
),

rel_annotations AS (
    SELECT rea.relation_evidence_id,
        MAX(CASE WHEN a.term = 'Pubmed:MI:0446'                  THEN a.value END) AS pubmed_id,
        MAX(CASE WHEN a.term = 'Interaction Xref:OM:1206'        THEN a.value END) AS interaction_xref,
        MAX(CASE WHEN a.term = 'Interaction Annotation:OM:1207'  THEN a.value END) AS interaction_annotation
    FROM relation_evidence_annotation rea
    JOIN annotation a ON a.annotation_key = rea.annotation_key
    WHERE rea.source_id = 14
    GROUP BY rea.relation_evidence_id
)

SELECT
    'cellinker'::text               AS source,
    re.relation_evidence_id,
    e_compound.entity_id            AS compound_entity_id,
    e_compound.canonical_identifier AS compound_canonical_id,
    vit_c.name                      AS compound_canonical_id_type,
    eer_compound.status_id          AS compound_resolution_status,
    ci.hmdb_id                      AS compound_hmdb_id,
    ci.pubchem_cid                  AS compound_pubchem_cid,
    ci.smiles                       AS compound_smiles,
    e_protein.entity_id             AS protein_entity_id,
    e_protein.canonical_identifier  AS protein_canonical_id,
    vit_p.name                      AS protein_canonical_id_type,
    eer_protein.status_id           AS protein_resolution_status,
    pi.uniprot_id                   AS protein_uniprot,
    pi.entrez_id                    AS protein_entrez,
    pi.gene_name                    AS protein_gene_name,
    vrp.name                        AS predicate,
    vrc.name                        AS relation_category,
    ann.pubmed_id,
    ann.interaction_xref,
    ann.interaction_annotation

FROM relation_evidence re
JOIN entity_evidence ee_compound
    ON  ee_compound.source_id          = re.source_id
    AND ee_compound.entity_evidence_id = re.subject_entity_evidence_id
    AND ee_compound.entity_type_id     = 2
JOIN entity_evidence ee_protein
    ON  ee_protein.source_id          = re.source_id
    AND ee_protein.entity_evidence_id = re.object_entity_evidence_id
    AND ee_protein.entity_type_id     = 3
LEFT JOIN compound_ids ci ON ci.entity_evidence_id = re.subject_entity_evidence_id
LEFT JOIN protein_ids  pi ON pi.entity_evidence_id = re.object_entity_evidence_id
LEFT JOIN entity_evidence_resolution eer_compound
    ON  eer_compound.source_id          = re.source_id
    AND eer_compound.entity_evidence_id = re.subject_entity_evidence_id
LEFT JOIN entity e_compound ON e_compound.entity_id = eer_compound.entity_id
LEFT JOIN vocab_identifier_type vit_c
    ON vit_c.identifier_type_id = e_compound.canonical_identifier_type_id
LEFT JOIN entity_evidence_resolution eer_protein
    ON  eer_protein.source_id          = re.source_id
    AND eer_protein.entity_evidence_id = re.object_entity_evidence_id
LEFT JOIN entity e_protein ON e_protein.entity_id = eer_protein.entity_id
LEFT JOIN vocab_identifier_type vit_p
    ON vit_p.identifier_type_id = e_protein.canonical_identifier_type_id
LEFT JOIN rel_annotations ann ON ann.relation_evidence_id = re.relation_evidence_id
JOIN vocab_relation_predicate vrp ON vrp.relation_predicate_id = re.predicate_id
JOIN vocab_relation_category  vrc ON vrc.relation_category_id  = re.relation_category_id
WHERE re.source_id = 14
  AND e_protein.taxonomy_id = 9606;

CREATE UNIQUE INDEX ON metalinksdb_cellinker_relations (relation_evidence_id);
CREATE INDEX ON metalinksdb_cellinker_relations (compound_entity_id);
CREATE INDEX ON metalinksdb_cellinker_relations (protein_entity_id);
CREATE INDEX ON metalinksdb_cellinker_relations (compound_canonical_id);
CREATE INDEX ON metalinksdb_cellinker_relations (protein_uniprot);

-- ────────────────────────────────────────────────────────────────────────────
-- Guide to Pharmacology  (source_id = 21)  — metabolite-protein pairs (SM=2→Prot=3)
-- Human-only via e_protein.taxonomy_id = 9606. Expected human rows: ~23904.
-- No sampling needed — 23904 metabolite-protein rows total.
-- ────────────────────────────────────────────────────────────────────────────

DROP MATERIALIZED VIEW IF EXISTS metalinksdb_guidetopharma_relations;
CREATE MATERIALIZED VIEW metalinksdb_guidetopharma_relations AS
WITH

compound_ids AS (
    SELECT eei.entity_evidence_id,
        MAX(CASE WHEN ie.identifier_type_id = 41 THEN ie.value END) AS guidetopharma_id,
        MAX(CASE WHEN ie.identifier_type_id = 13 THEN ie.value END) AS inchikey,
        MAX(CASE WHEN ie.identifier_type_id = 27 THEN ie.value END) AS smiles,
        MAX(CASE WHEN ie.identifier_type_id = 12 THEN ie.value END) AS pubchem_cid,
        MAX(CASE WHEN ie.identifier_type_id = 17 THEN ie.value END) AS chembl_compound_id
    FROM entity_evidence_identifier eei
    JOIN identifier_evidence ie ON ie.identifier_id = eei.identifier_id
    WHERE eei.source_id = 21
    GROUP BY eei.entity_evidence_id
),

protein_ids AS (
    SELECT eei.entity_evidence_id,
        MAX(CASE WHEN ie.identifier_type_id = 1 THEN ie.value END) AS uniprot_id,
        MAX(CASE WHEN ie.identifier_type_id = 4 THEN ie.value END) AS hgnc_id,
        MAX(CASE WHEN ie.identifier_type_id = 5 THEN ie.value END) AS gene_name
    FROM entity_evidence_identifier eei
    JOIN identifier_evidence ie ON ie.identifier_id = eei.identifier_id
    WHERE eei.source_id = 21
    GROUP BY eei.entity_evidence_id
),

rel_annotations AS (
    SELECT rea.relation_evidence_id,
        MAX(CASE WHEN a.term = 'Pubmed:MI:0446'                THEN a.value END) AS pubmed_id,
        MAX(CASE WHEN a.term = 'Affinity Median:OM:0628'       THEN a.value END) AS affinity_median,
        MAX(CASE WHEN a.term = 'Affinity Low:OM:0627'          THEN a.value END) AS affinity_low,
        MAX(CASE WHEN a.term = 'Affinity High:OM:0626'         THEN a.value END) AS affinity_high,
        MAX(CASE WHEN a.term = 'Ncbi Tax Id:OM:0205'           THEN a.value END) AS ncbi_tax_id,
        BOOL_OR(a.term = 'Endogenous:OM:0625')                                   AS is_endogenous,
        BOOL_OR(a.term = 'Inhibitor:OM:1004')                                    AS is_inhibitor,
        BOOL_OR(a.term = 'Inhibition:OM:0931')                                   AS is_inhibition,
        BOOL_OR(a.term = 'Agonist:OM:1001')                                      AS is_agonist,
        BOOL_OR(a.term = 'Antagonist:OM:1002')                                   AS is_antagonist,
        BOOL_OR(a.term = 'Full Agonist:OM:0902')                                 AS is_full_agonist,
        BOOL_OR(a.term = 'Partial Agonist:OM:0903')                              AS is_partial_agonist,
        BOOL_OR(a.term = 'Inverse Agonist:OM:0904')                              AS is_inverse_agonist,
        BOOL_OR(a.term = 'Activator:OM:1003')                                    AS is_activator,
        BOOL_OR(a.term = 'Binding:OM:0980')                                      AS is_binding,
        BOOL_OR(a.term = 'Allosteric Modulator:OM:1005')                         AS is_allosteric_modulator,
        BOOL_OR(a.term = 'Channel Blocker:OM:1020')                              AS is_channel_blocker
    FROM relation_evidence_annotation rea
    JOIN annotation a ON a.annotation_key = rea.annotation_key
    WHERE rea.source_id = 21
    GROUP BY rea.relation_evidence_id
)

SELECT
    'guidetopharma'::text           AS source,
    re.relation_evidence_id,
    e_compound.entity_id            AS compound_entity_id,
    e_compound.canonical_identifier AS compound_canonical_id,
    vit_c.name                      AS compound_canonical_id_type,
    eer_compound.status_id          AS compound_resolution_status,
    ci.guidetopharma_id             AS compound_guidetopharma_id,
    ci.inchikey                     AS compound_inchikey,
    ci.smiles                       AS compound_smiles,
    ci.pubchem_cid                  AS compound_pubchem_cid,
    ci.chembl_compound_id           AS compound_chembl_id,
    e_protein.entity_id             AS protein_entity_id,
    e_protein.canonical_identifier  AS protein_canonical_id,
    vit_p.name                      AS protein_canonical_id_type,
    eer_protein.status_id           AS protein_resolution_status,
    pi.uniprot_id                   AS protein_uniprot,
    pi.hgnc_id                      AS protein_hgnc,
    pi.gene_name                    AS protein_gene_name,
    vrp.name                        AS predicate,
    vrc.name                        AS relation_category,
    ann.pubmed_id,
    ann.affinity_median::numeric    AS affinity_median,
    ann.affinity_low::numeric       AS affinity_low,
    ann.affinity_high::numeric      AS affinity_high,
    ann.ncbi_tax_id,
    ann.is_endogenous,
    ann.is_inhibitor,
    ann.is_inhibition,
    ann.is_agonist,
    ann.is_antagonist,
    ann.is_full_agonist,
    ann.is_partial_agonist,
    ann.is_inverse_agonist,
    ann.is_activator,
    ann.is_binding,
    ann.is_allosteric_modulator,
    ann.is_channel_blocker

FROM relation_evidence re
JOIN entity_evidence ee_compound
    ON  ee_compound.source_id          = re.source_id
    AND ee_compound.entity_evidence_id = re.subject_entity_evidence_id
    AND ee_compound.entity_type_id     = 2
JOIN entity_evidence ee_protein
    ON  ee_protein.source_id          = re.source_id
    AND ee_protein.entity_evidence_id = re.object_entity_evidence_id
    AND ee_protein.entity_type_id     = 3
LEFT JOIN compound_ids ci ON ci.entity_evidence_id = re.subject_entity_evidence_id
LEFT JOIN protein_ids  pi ON pi.entity_evidence_id = re.object_entity_evidence_id
LEFT JOIN entity_evidence_resolution eer_compound
    ON  eer_compound.source_id          = re.source_id
    AND eer_compound.entity_evidence_id = re.subject_entity_evidence_id
LEFT JOIN entity e_compound ON e_compound.entity_id = eer_compound.entity_id
LEFT JOIN vocab_identifier_type vit_c
    ON vit_c.identifier_type_id = e_compound.canonical_identifier_type_id
LEFT JOIN entity_evidence_resolution eer_protein
    ON  eer_protein.source_id          = re.source_id
    AND eer_protein.entity_evidence_id = re.object_entity_evidence_id
LEFT JOIN entity e_protein ON e_protein.entity_id = eer_protein.entity_id
LEFT JOIN vocab_identifier_type vit_p
    ON vit_p.identifier_type_id = e_protein.canonical_identifier_type_id
LEFT JOIN rel_annotations ann ON ann.relation_evidence_id = re.relation_evidence_id
JOIN vocab_relation_predicate vrp ON vrp.relation_predicate_id = re.predicate_id
JOIN vocab_relation_category  vrc ON vrc.relation_category_id  = re.relation_category_id
WHERE re.source_id = 21
  AND e_protein.taxonomy_id = 9606;

CREATE UNIQUE INDEX ON metalinksdb_guidetopharma_relations (relation_evidence_id);
CREATE INDEX ON metalinksdb_guidetopharma_relations (compound_entity_id);
CREATE INDEX ON metalinksdb_guidetopharma_relations (protein_entity_id);
CREATE INDEX ON metalinksdb_guidetopharma_relations (compound_canonical_id);
CREATE INDEX ON metalinksdb_guidetopharma_relations (protein_uniprot);

-- ────────────────────────────────────────────────────────────────────────────
-- MRCLinksDB  (source_id = 28)  — metabolite-protein pairs only (SM=2→Prot=3)
-- Inherently human; e_protein.taxonomy_id = 9606 still applied for consistency.
-- Expected human rows: ~1468. No sampling needed.
-- ────────────────────────────────────────────────────────────────────────────

DROP MATERIALIZED VIEW IF EXISTS metalinksdb_mrclinksdb_relations;
CREATE MATERIALIZED VIEW metalinksdb_mrclinksdb_relations AS
WITH

compound_ids AS (
    SELECT eei.entity_evidence_id,
        MAX(CASE WHEN ie.identifier_type_id = 9  THEN ie.value END) AS hmdb_id,
        MAX(CASE WHEN ie.identifier_type_id = 12 THEN ie.value END) AS pubchem_cid,
        MAX(CASE WHEN ie.identifier_type_id = 27 THEN ie.value END) AS smiles
    FROM entity_evidence_identifier eei
    JOIN identifier_evidence ie ON ie.identifier_id = eei.identifier_id
    WHERE eei.source_id = 28
    GROUP BY eei.entity_evidence_id
),

protein_ids AS (
    SELECT eei.entity_evidence_id,
        MAX(CASE WHEN ie.identifier_type_id = 1 THEN ie.value END) AS uniprot_id,
        MAX(CASE WHEN ie.identifier_type_id = 3 THEN ie.value END) AS entrez_id,
        MAX(CASE WHEN ie.identifier_type_id = 5 THEN ie.value END) AS gene_name
    FROM entity_evidence_identifier eei
    JOIN identifier_evidence ie ON ie.identifier_id = eei.identifier_id
    WHERE eei.source_id = 28
    GROUP BY eei.entity_evidence_id
),

rel_annotations AS (
    SELECT rea.relation_evidence_id,
        MAX(CASE WHEN a.term = 'Pubmed:MI:0446'   THEN a.value END) AS pubmed_id,
        MAX(CASE WHEN a.term = 'Comment:MI:0612'  THEN a.value END) AS comment
    FROM relation_evidence_annotation rea
    JOIN annotation a ON a.annotation_key = rea.annotation_key
    WHERE rea.source_id = 28
    GROUP BY rea.relation_evidence_id
)

SELECT
    'mrclinksdb'::text              AS source,
    re.relation_evidence_id,
    e_compound.entity_id            AS compound_entity_id,
    e_compound.canonical_identifier AS compound_canonical_id,
    vit_c.name                      AS compound_canonical_id_type,
    eer_compound.status_id          AS compound_resolution_status,
    ci.hmdb_id                      AS compound_hmdb_id,
    ci.pubchem_cid                  AS compound_pubchem_cid,
    ci.smiles                       AS compound_smiles,
    e_protein.entity_id             AS protein_entity_id,
    e_protein.canonical_identifier  AS protein_canonical_id,
    vit_p.name                      AS protein_canonical_id_type,
    eer_protein.status_id           AS protein_resolution_status,
    pi.uniprot_id                   AS protein_uniprot,
    pi.entrez_id                    AS protein_entrez,
    pi.gene_name                    AS protein_gene_name,
    vrp.name                        AS predicate,
    vrc.name                        AS relation_category,
    ann.pubmed_id,
    ann.comment

FROM relation_evidence re
JOIN entity_evidence ee_compound
    ON  ee_compound.source_id          = re.source_id
    AND ee_compound.entity_evidence_id = re.subject_entity_evidence_id
    AND ee_compound.entity_type_id     = 2
JOIN entity_evidence ee_protein
    ON  ee_protein.source_id          = re.source_id
    AND ee_protein.entity_evidence_id = re.object_entity_evidence_id
    AND ee_protein.entity_type_id     = 3
LEFT JOIN compound_ids ci ON ci.entity_evidence_id = re.subject_entity_evidence_id
LEFT JOIN protein_ids  pi ON pi.entity_evidence_id = re.object_entity_evidence_id
LEFT JOIN entity_evidence_resolution eer_compound
    ON  eer_compound.source_id          = re.source_id
    AND eer_compound.entity_evidence_id = re.subject_entity_evidence_id
LEFT JOIN entity e_compound ON e_compound.entity_id = eer_compound.entity_id
LEFT JOIN vocab_identifier_type vit_c
    ON vit_c.identifier_type_id = e_compound.canonical_identifier_type_id
LEFT JOIN entity_evidence_resolution eer_protein
    ON  eer_protein.source_id          = re.source_id
    AND eer_protein.entity_evidence_id = re.object_entity_evidence_id
LEFT JOIN entity e_protein ON e_protein.entity_id = eer_protein.entity_id
LEFT JOIN vocab_identifier_type vit_p
    ON vit_p.identifier_type_id = e_protein.canonical_identifier_type_id
LEFT JOIN rel_annotations ann ON ann.relation_evidence_id = re.relation_evidence_id
JOIN vocab_relation_predicate vrp ON vrp.relation_predicate_id = re.predicate_id
JOIN vocab_relation_category  vrc ON vrc.relation_category_id  = re.relation_category_id
WHERE re.source_id = 28
  AND e_protein.taxonomy_id = 9606;

CREATE UNIQUE INDEX ON metalinksdb_mrclinksdb_relations (relation_evidence_id);
CREATE INDEX ON metalinksdb_mrclinksdb_relations (compound_entity_id);
CREATE INDEX ON metalinksdb_mrclinksdb_relations (protein_entity_id);
CREATE INDEX ON metalinksdb_mrclinksdb_relations (compound_canonical_id);
CREATE INDEX ON metalinksdb_mrclinksdb_relations (protein_uniprot);

-- ────────────────────────────────────────────────────────────────────────────
-- STITCH  (source_id = 39)
-- Human-only via e_protein.taxonomy_id = 9606. Expected human rows: ~338k.
-- !! TESTING ONLY — sampled_re limits to 10000 rows; remove before full build.
-- ────────────────────────────────────────────────────────────────────────────

DROP MATERIALIZED VIEW IF EXISTS metalinksdb_stitch_relations;
CREATE MATERIALIZED VIEW metalinksdb_stitch_relations AS
WITH

sampled_re AS (
    SELECT relation_evidence_id, source_id,
           subject_entity_evidence_id, object_entity_evidence_id,
           predicate_id, relation_category_id
    FROM relation_evidence
    WHERE source_id = 39
    LIMIT 10000
),

compound_ids AS (
    SELECT eei.entity_evidence_id,
        MAX(CASE WHEN ie.identifier_type_id = 12 THEN ie.value END) AS pubchem_cid
    FROM entity_evidence_identifier eei
    JOIN identifier_evidence ie ON ie.identifier_id = eei.identifier_id
    WHERE eei.entity_evidence_id IN (SELECT subject_entity_evidence_id FROM sampled_re)
    GROUP BY eei.entity_evidence_id
),

protein_ids AS (
    SELECT eei.entity_evidence_id,
        MAX(CASE WHEN ie.identifier_type_id = 2 THEN ie.value END) AS ensembl_id
    FROM entity_evidence_identifier eei
    JOIN identifier_evidence ie ON ie.identifier_id = eei.identifier_id
    WHERE eei.entity_evidence_id IN (SELECT object_entity_evidence_id FROM sampled_re)
    GROUP BY eei.entity_evidence_id
),

rel_annotations AS (
    SELECT rea.relation_evidence_id,
        MAX(CASE WHEN a.term = 'Stitch Action Score:OM:1213'    THEN a.value END) AS stitch_action_score,
        MAX(CASE WHEN a.term = 'Confidence Value:OM:1201'       THEN a.value END) AS confidence_value,
        MAX(CASE WHEN a.term = 'Control Type:OM:1212'           THEN a.value END) AS control_type,
        BOOL_OR(a.term = 'Stereospecific:OM:1214')                                AS is_stereospecific,
        BOOL_OR(a.term = 'Binding:OM:0980')                                       AS is_binding,
        BOOL_OR(a.term = 'Enzymatic Reaction:MI:0414')                            AS is_enzymatic,
        BOOL_OR(a.term = 'Inhibition:OM:0931')                                    AS is_inhibition,
        BOOL_OR(a.term = 'Activation:OM:0930')                                    AS is_activation
    FROM relation_evidence_annotation rea
    JOIN annotation a ON a.annotation_key = rea.annotation_key
    WHERE rea.relation_evidence_id IN (SELECT relation_evidence_id FROM sampled_re)
    GROUP BY rea.relation_evidence_id
)

SELECT
    'stitch'::text                  AS source,
    re.relation_evidence_id,
    e_compound.entity_id            AS compound_entity_id,
    e_compound.canonical_identifier AS compound_canonical_id,
    vit_c.name                      AS compound_canonical_id_type,
    eer_compound.status_id          AS compound_resolution_status,
    ci.pubchem_cid                  AS compound_pubchem_cid,
    e_protein.entity_id             AS protein_entity_id,
    e_protein.canonical_identifier  AS protein_canonical_id,
    vit_p.name                      AS protein_canonical_id_type,
    eer_protein.status_id           AS protein_resolution_status,
    pi.ensembl_id                   AS protein_ensembl,
    -- protein_uniprot comes from canonical resolution, not raw STITCH identifiers
    e_protein.canonical_identifier  AS protein_uniprot,
    vrp.name                        AS predicate,
    vrc.name                        AS relation_category,
    ann.stitch_action_score::numeric AS stitch_action_score,
    ann.confidence_value::numeric    AS confidence_value,
    ann.control_type,
    ann.is_stereospecific,
    ann.is_binding,
    ann.is_enzymatic,
    ann.is_inhibition,
    ann.is_activation

FROM sampled_re re  -- !! TESTING ONLY: replace with `relation_evidence re` for production

JOIN entity_evidence ee_compound
    ON  ee_compound.source_id          = re.source_id
    AND ee_compound.entity_evidence_id = re.subject_entity_evidence_id
    AND ee_compound.entity_type_id     = 2
JOIN entity_evidence ee_protein
    ON  ee_protein.source_id          = re.source_id
    AND ee_protein.entity_evidence_id = re.object_entity_evidence_id
    AND ee_protein.entity_type_id     = 3
LEFT JOIN compound_ids ci ON ci.entity_evidence_id = re.subject_entity_evidence_id
LEFT JOIN protein_ids  pi ON pi.entity_evidence_id = re.object_entity_evidence_id
LEFT JOIN entity_evidence_resolution eer_compound
    ON  eer_compound.source_id          = re.source_id
    AND eer_compound.entity_evidence_id = re.subject_entity_evidence_id
LEFT JOIN entity e_compound ON e_compound.entity_id = eer_compound.entity_id
LEFT JOIN vocab_identifier_type vit_c
    ON vit_c.identifier_type_id = e_compound.canonical_identifier_type_id
LEFT JOIN entity_evidence_resolution eer_protein
    ON  eer_protein.source_id          = re.source_id
    AND eer_protein.entity_evidence_id = re.object_entity_evidence_id
LEFT JOIN entity e_protein ON e_protein.entity_id = eer_protein.entity_id
LEFT JOIN vocab_identifier_type vit_p
    ON vit_p.identifier_type_id = e_protein.canonical_identifier_type_id
LEFT JOIN rel_annotations ann ON ann.relation_evidence_id = re.relation_evidence_id
JOIN vocab_relation_predicate vrp ON vrp.relation_predicate_id = re.predicate_id
JOIN vocab_relation_category  vrc ON vrc.relation_category_id  = re.relation_category_id
WHERE e_protein.taxonomy_id = 9606;

CREATE UNIQUE INDEX ON metalinksdb_stitch_relations (relation_evidence_id);
CREATE INDEX ON metalinksdb_stitch_relations (compound_entity_id);
CREATE INDEX ON metalinksdb_stitch_relations (protein_entity_id);
CREATE INDEX ON metalinksdb_stitch_relations (compound_canonical_id);
CREATE INDEX ON metalinksdb_stitch_relations (protein_uniprot);

-- ────────────────────────────────────────────────────────────────────────────
-- TCDB  (source_id = 41)  — transporter-substrate pairs
-- Direction REVERSED vs other sources: Protein(3, subject) → SmallMolecule(2, object)
-- Human-only via e_protein.taxonomy_id = 9606.
-- Note: SmallMolecule resolution is ~14% (ChEBI IDs mostly unmatched in canonical entity table).
-- No sampling needed — only 20039 rows total.
-- ────────────────────────────────────────────────────────────────────────────

DROP MATERIALIZED VIEW IF EXISTS metalinksdb_tcdb_relations;
CREATE MATERIALIZED VIEW metalinksdb_tcdb_relations AS
WITH

compound_ids AS (
    SELECT eei.entity_evidence_id,
        MAX(CASE WHEN ie.identifier_type_id = 8  THEN ie.value END) AS chebi_id,
        MAX(CASE WHEN ie.identifier_type_id = 16 THEN ie.value END) AS compound_name
    FROM entity_evidence_identifier eei
    JOIN identifier_evidence ie ON ie.identifier_id = eei.identifier_id
    WHERE eei.source_id = 41
    GROUP BY eei.entity_evidence_id
),

protein_ids AS (
    SELECT eei.entity_evidence_id,
        MAX(CASE WHEN ie.identifier_type_id = 1  THEN ie.value END) AS uniprot_id,
        MAX(CASE WHEN ie.identifier_type_id = 77 THEN ie.value END) AS tcdb_id
    FROM entity_evidence_identifier eei
    JOIN identifier_evidence ie ON ie.identifier_id = eei.identifier_id
    WHERE eei.source_id = 41
    GROUP BY eei.entity_evidence_id
)

SELECT
    'tcdb'::text                        AS source,
    re.relation_evidence_id,
    e_compound.entity_id                AS compound_entity_id,
    e_compound.canonical_identifier     AS compound_canonical_id,
    vit_c.name                          AS compound_canonical_id_type,
    eer_compound.status_id              AS compound_resolution_status,
    ci.chebi_id                         AS compound_chebi_id,
    ci.compound_name,
    e_protein.entity_id                 AS protein_entity_id,
    e_protein.canonical_identifier      AS protein_canonical_id,
    vit_p.name                          AS protein_canonical_id_type,
    eer_protein.status_id               AS protein_resolution_status,
    e_protein.canonical_identifier      AS protein_uniprot,
    pi.uniprot_id                       AS protein_raw_uniprot,
    pi.tcdb_id,
    vrp.name                            AS predicate,
    vrc.name                            AS relation_category

FROM relation_evidence re
-- REVERSED: protein is subject (entity_type=3), compound is object (entity_type=2)
JOIN entity_evidence ee_protein
    ON  ee_protein.source_id           = re.source_id
    AND ee_protein.entity_evidence_id  = re.subject_entity_evidence_id
    AND ee_protein.entity_type_id      = 3
JOIN entity_evidence ee_compound
    ON  ee_compound.source_id          = re.source_id
    AND ee_compound.entity_evidence_id = re.object_entity_evidence_id
    AND ee_compound.entity_type_id     = 2
LEFT JOIN entity_evidence_resolution eer_protein
    ON  eer_protein.source_id          = re.source_id
    AND eer_protein.entity_evidence_id = re.subject_entity_evidence_id
LEFT JOIN entity e_protein
    ON  e_protein.entity_id            = eer_protein.entity_id
LEFT JOIN vocab_identifier_type vit_p
    ON  vit_p.identifier_type_id       = e_protein.canonical_identifier_type_id
LEFT JOIN entity_evidence_resolution eer_compound
    ON  eer_compound.source_id         = re.source_id
    AND eer_compound.entity_evidence_id = re.object_entity_evidence_id
LEFT JOIN entity e_compound
    ON  e_compound.entity_id           = eer_compound.entity_id
LEFT JOIN vocab_identifier_type vit_c
    ON  vit_c.identifier_type_id       = e_compound.canonical_identifier_type_id
LEFT JOIN compound_ids ci ON ci.entity_evidence_id = re.object_entity_evidence_id
LEFT JOIN protein_ids  pi ON pi.entity_evidence_id = re.subject_entity_evidence_id
JOIN vocab_relation_predicate vrp ON vrp.relation_predicate_id = re.predicate_id
JOIN vocab_relation_category  vrc ON vrc.relation_category_id  = re.relation_category_id
WHERE re.source_id = 41
  AND e_protein.taxonomy_id = 9606;

CREATE UNIQUE INDEX ON metalinksdb_tcdb_relations (relation_evidence_id);
CREATE INDEX ON metalinksdb_tcdb_relations (compound_entity_id);
CREATE INDEX ON metalinksdb_tcdb_relations (protein_entity_id);
CREATE INDEX ON metalinksdb_tcdb_relations (compound_canonical_id);
CREATE INDEX ON metalinksdb_tcdb_relations (protein_uniprot);

-- ────────────────────────────────────────────────────────────────────────────
-- Combined aggregated view — one row per unique resolved (compound, protein) pair
-- Aggregates across all per-source views. Requires all per-source views above.
-- relation_types: array of distinct relationship categories ('interaction', 'transport')
-- ────────────────────────────────────────────────────────────────────────────

DROP MATERIALIZED VIEW IF EXISTS metalinksdb_relations;
CREATE MATERIALIZED VIEW metalinksdb_relations AS
SELECT
    compound_entity_id,
    MAX(compound_canonical_id)                                          AS compound_canonical_id,
    MAX(compound_canonical_id_type)                                     AS compound_canonical_id_type,
    protein_entity_id,
    MAX(protein_canonical_id)                                           AS protein_canonical_id,
    MAX(protein_canonical_id_type)                                      AS protein_canonical_id_type,
    MAX(protein_uniprot)                                                AS protein_uniprot,
    array_agg(DISTINCT source ORDER BY source)                          AS sources,
    COUNT(DISTINCT source)                                              AS source_count,
    array_agg(DISTINCT relation_type ORDER BY relation_type)            AS relation_types,
    MAX(pchembl_value)                                                  AS best_pchembl_value,
    array_agg(DISTINCT pubmed_id) FILTER (WHERE pubmed_id IS NOT NULL)  AS pubmed_ids,
    array_agg(DISTINCT doi)       FILTER (WHERE doi       IS NOT NULL)  AS dois
FROM (
    SELECT source, compound_entity_id, compound_canonical_id, compound_canonical_id_type,
           protein_entity_id, protein_canonical_id, protein_canonical_id_type,
           protein_uniprot, 'interaction'::text AS relation_type, pchembl_value, pubmed_id, doi
    FROM metalinksdb_chembl_relations
    WHERE compound_resolution_status = 1 AND protein_resolution_status = 1

    UNION ALL

    SELECT source, compound_entity_id, compound_canonical_id, compound_canonical_id_type,
           protein_entity_id, protein_canonical_id, protein_canonical_id_type,
           protein_uniprot, 'interaction'::text AS relation_type, pchembl_value, pubmed_id, doi
    FROM metalinksdb_bindingdb_relations
    WHERE compound_resolution_status = 1 AND protein_resolution_status = 1

    UNION ALL

    SELECT source, compound_entity_id, compound_canonical_id, compound_canonical_id_type,
           protein_entity_id, protein_canonical_id, protein_canonical_id_type,
           protein_uniprot, 'interaction'::text AS relation_type, NULL AS pchembl_value, pubmed_id, NULL AS doi
    FROM metalinksdb_cellinker_relations
    WHERE compound_resolution_status = 1 AND protein_resolution_status = 1

    UNION ALL

    SELECT source, compound_entity_id, compound_canonical_id, compound_canonical_id_type,
           protein_entity_id, protein_canonical_id, protein_canonical_id_type,
           protein_uniprot, 'interaction'::text AS relation_type, NULL AS pchembl_value, pubmed_id, NULL AS doi
    FROM metalinksdb_guidetopharma_relations
    WHERE compound_resolution_status = 1 AND protein_resolution_status = 1

    UNION ALL

    SELECT source, compound_entity_id, compound_canonical_id, compound_canonical_id_type,
           protein_entity_id, protein_canonical_id, protein_canonical_id_type,
           protein_uniprot, 'interaction'::text AS relation_type, NULL AS pchembl_value, NULL AS pubmed_id, NULL AS doi
    FROM metalinksdb_mrclinksdb_relations
    WHERE compound_resolution_status = 1 AND protein_resolution_status = 1

    UNION ALL

    SELECT source, compound_entity_id, compound_canonical_id, compound_canonical_id_type,
           protein_entity_id, protein_canonical_id, protein_canonical_id_type,
           protein_uniprot, 'interaction'::text AS relation_type, NULL AS pchembl_value, NULL AS pubmed_id, NULL AS doi
    FROM metalinksdb_stitch_relations
    WHERE compound_resolution_status = 1 AND protein_resolution_status = 1

    UNION ALL

    SELECT source, compound_entity_id, compound_canonical_id, compound_canonical_id_type,
           protein_entity_id, protein_canonical_id, protein_canonical_id_type,
           protein_uniprot, 'transport'::text AS relation_type,
           NULL::numeric AS pchembl_value, NULL::text AS pubmed_id, NULL::text AS doi
    FROM metalinksdb_tcdb_relations
    WHERE compound_resolution_status = 1 AND protein_resolution_status = 1
) combined
GROUP BY compound_entity_id, protein_entity_id;

CREATE UNIQUE INDEX ON metalinksdb_relations (compound_entity_id, protein_entity_id);
CREATE INDEX ON metalinksdb_relations (compound_canonical_id);
CREATE INDEX ON metalinksdb_relations (protein_uniprot);
CREATE INDEX ON metalinksdb_relations (source_count);
CREATE INDEX ON metalinksdb_relations USING gin (relation_types);
