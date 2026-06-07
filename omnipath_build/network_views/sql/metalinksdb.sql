-- omnipath-build/custom_views/metalinksdb.sql
-- MetaLinksDB materialized views for each interaction source.
-- Apply with:  psql -U omnipath -d omnipath -f metalinksdb.sql
-- Refresh after each:  make load SOURCE=<source> && make derive
--   then: REFRESH MATERIALIZED VIEW CONCURRENTLY metalinksdb_<source>_relations;
--
-- Human-only filter: all views use a human_re CTE to pre-filter to human rows
-- before building identifier and annotation CTEs. This avoids processing the
-- full source dataset through expensive aggregations before discarding non-human rows.
-- For ChEMBL the human filter comes from annotation term 'Ncbi Tax Id:OM:0205'.
-- For all other sources it comes from entity_evidence_resolution -> entity.taxonomy_id = 9606.
--
-- Source IDs (as of omnipath-build c39dd5a rebuild 2026-05-28):
--   chembl=16  bindingdb=12  cellinker=14  guidetopharma=21
--   mrclinksdb=28  stitch=39  tcdb=41
--
-- Entity type IDs: SmallMolecule=2  Protein=3  Complex=4  Reaction=11  Transport=12

-- ────────────────────────────────────────────────────────────────────────────
-- ChEMBL  (source_id = 16)
-- Human-only via ncbi_tax_id annotation. human_re pre-filters ~3.4M rows to
-- ~300k human rows before all subsequent CTEs run.
-- ────────────────────────────────────────────────────────────────────────────

SET max_parallel_workers_per_gather = 4;
SET max_parallel_workers = 8;
SET work_mem = '256MB';

-- Drop combined view first so individual source views can be dropped without CASCADE

-- Helper function: resolves a source name to its source_id.
-- Declared STABLE so PostgreSQL evaluates it at plan time,
-- preserving partition pruning for all partitioned tables.
CREATE OR REPLACE FUNCTION get_source_id(source_name text)
RETURNS bigint LANGUAGE plpgsql STABLE AS $$
BEGIN
    RETURN (SELECT source_id FROM public.data_source WHERE name = source_name);
END;
$$;



CREATE OR REPLACE FUNCTION get_entity_type_id(type_name text)
RETURNS bigint LANGUAGE plpgsql STABLE AS $$
BEGIN
    RETURN (SELECT entity_type_id FROM public.vocab_entity_type WHERE name = type_name);
END;
$$;

CREATE OR REPLACE FUNCTION get_identifier_type_id(type_name text)
RETURNS bigint LANGUAGE plpgsql STABLE AS $$
BEGIN
    RETURN (SELECT identifier_type_id FROM public.vocab_identifier_type WHERE name = type_name);
END;
$$;

DROP MATERIALIZED VIEW IF EXISTS metalinksdb_relations;

DROP MATERIALIZED VIEW IF EXISTS metalinksdb_chembl_relations;
CREATE MATERIALIZED VIEW metalinksdb_chembl_relations AS
WITH

-- Pre-filter to human-only relation_evidence rows using the ncbi_tax_id annotation;
-- all subsequent CTEs process only these rows instead of the full 3.4M ChEMBL dataset.
human_re AS (
    SELECT DISTINCT
        re.relation_evidence_id,
        re.subject_entity_evidence_id,
        re.object_entity_evidence_id,
        re.predicate_id,
        re.relation_category_id
    FROM relation_evidence re
    JOIN relation_evidence_annotation rea
        ON  rea.relation_evidence_id = re.relation_evidence_id
        AND rea.source_id            = re.source_id
    JOIN annotation a ON a.annotation_key = rea.annotation_key
        AND a.term  = 'Ncbi Tax Id:OM:0205'
        AND a.value = '9606'
    WHERE re.source_id = get_source_id('chembl')
),

-- Collect raw ChEMBL compound identifiers (ChEMBL ID, InChIKey, SMILES, name) per entity_evidence row.
compound_ids AS (
    SELECT
        eei.entity_evidence_id,
        MAX(CASE WHEN ie.identifier_type_id = get_identifier_type_id('Chembl Compound:MI:0967') THEN ie.value END) AS chembl_compound_id,
        MAX(CASE WHEN ie.identifier_type_id = get_identifier_type_id('Standard Inchi Key:MI:1101') THEN ie.value END) AS inchikey,
        MAX(CASE WHEN ie.identifier_type_id = get_identifier_type_id('Smiles:MI:0239') THEN ie.value END) AS smiles,
        MAX(CASE WHEN ie.identifier_type_id = get_identifier_type_id('Name:OM:0202') THEN ie.value END) AS compound_name
    FROM entity_evidence_identifier eei
    JOIN identifier_evidence ie ON ie.identifier_id = eei.identifier_id
    WHERE eei.source_id = get_source_id('chembl')
      AND eei.entity_evidence_id IN (SELECT subject_entity_evidence_id FROM human_re)
    GROUP BY eei.entity_evidence_id
),

-- Collect raw ChEMBL protein identifiers (UniProt, ChEMBL target ID) per entity_evidence row.
protein_ids AS (
    SELECT
        eei.entity_evidence_id,
        MAX(CASE WHEN ie.identifier_type_id = get_identifier_type_id('Uniprot:MI:1097')  THEN ie.value END) AS uniprot_id,
        MAX(CASE WHEN ie.identifier_type_id = get_identifier_type_id('Chembl Target:MI:1348') THEN ie.value END) AS chembl_target_id
    FROM entity_evidence_identifier eei
    JOIN identifier_evidence ie ON ie.identifier_id = eei.identifier_id
    WHERE eei.source_id = get_source_id('chembl')
      AND eei.entity_evidence_id IN (SELECT object_entity_evidence_id FROM human_re)
    GROUP BY eei.entity_evidence_id
),

-- Pivot ChEMBL activity annotations (pChEMBL, IC50, assay type, literature) into columns per relation_evidence row.
rel_annotations AS NOT MATERIALIZED (
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
    WHERE rea.source_id = get_source_id('chembl')
      AND rea.relation_evidence_id IN (SELECT relation_evidence_id FROM human_re)
    GROUP BY rea.relation_evidence_id
)

SELECT
    'chembl'::text                              AS source,
    re.relation_evidence_id,

    e_compound.entity_id                        AS compound_entity_id,
    e_compound.canonical_identifier             AS compound_canonical_id,
    vit_c.name                                  AS compound_canonical_id_type,
    eer_compound.status_id                      AS compound_resolution_status,

    ci.chembl_compound_id,
    ci.inchikey                                 AS compound_inchikey,
    ci.smiles                                   AS compound_smiles,
    ci.compound_name,

    e_protein.entity_id                         AS protein_entity_id,
    e_protein.canonical_identifier              AS protein_canonical_id,
    vit_p.name                                  AS protein_canonical_id_type,
    eer_protein.status_id                       AS protein_resolution_status,

    pi.uniprot_id                               AS protein_uniprot,
    pi.chembl_target_id                         AS protein_chembl_target,

    vrp.name                                    AS predicate,
    vrc.name                                    AS relation_category,

    ann.chembl_activity_id,
    ann.chembl_assay_id,
    ann.chembl_document_id,
    ann.chembl_target_ann,

    ann.pchembl_value::numeric                  AS pchembl_value,
    ann.confidence_score::numeric               AS confidence_score,
    ann.ic50::numeric                           AS ic50,
    ann.ki::numeric                             AS ki,
    ann.kd::numeric                             AS kd,
    ann.ec50::numeric                           AS ec50,

    ann.assay_category,
    ann.is_binding_assay,
    ann.is_functional_assay,
    ann.is_adme_assay,
    ann.is_toxicity_assay,
    ann.is_inhibition,
    ann.is_activation,
    ann.is_agonist,
    ann.is_antagonist,

    ann.cell_type,
    ann.tissue,
    ann.ncbi_tax_id,
    ann.description,
    ann.chembl_mechanism,

    ann.pubmed_id,
    ann.doi

FROM human_re re

JOIN entity_evidence ee_compound
    ON  ee_compound.source_id = get_source_id('chembl')
    AND ee_compound.entity_evidence_id = re.subject_entity_evidence_id

JOIN entity_evidence ee_protein
    ON  ee_protein.source_id = get_source_id('chembl')
    AND ee_protein.entity_evidence_id = re.object_entity_evidence_id

LEFT JOIN compound_ids ci
    ON ci.entity_evidence_id = re.subject_entity_evidence_id

LEFT JOIN protein_ids pi
    ON pi.entity_evidence_id = re.object_entity_evidence_id

LEFT JOIN entity_evidence_resolution eer_compound
    ON  eer_compound.source_id = get_source_id('chembl')
    AND eer_compound.entity_evidence_id = re.subject_entity_evidence_id
LEFT JOIN entity e_compound
    ON e_compound.entity_id = eer_compound.entity_id
LEFT JOIN vocab_identifier_type vit_c
    ON vit_c.identifier_type_id = e_compound.canonical_identifier_type_id

LEFT JOIN entity_evidence_resolution eer_protein
    ON  eer_protein.source_id = get_source_id('chembl')
    AND eer_protein.entity_evidence_id = re.object_entity_evidence_id
LEFT JOIN entity e_protein
    ON e_protein.entity_id = eer_protein.entity_id
LEFT JOIN vocab_identifier_type vit_p
    ON vit_p.identifier_type_id = e_protein.canonical_identifier_type_id

LEFT JOIN rel_annotations ann
    ON ann.relation_evidence_id = re.relation_evidence_id

JOIN vocab_relation_predicate vrp
    ON vrp.relation_predicate_id = re.predicate_id
JOIN vocab_relation_category vrc
    ON vrc.relation_category_id = re.relation_category_id;

CREATE UNIQUE INDEX ON metalinksdb_chembl_relations (relation_evidence_id);
CREATE INDEX ON metalinksdb_chembl_relations (compound_entity_id);
CREATE INDEX ON metalinksdb_chembl_relations (protein_entity_id);
CREATE INDEX ON metalinksdb_chembl_relations (compound_canonical_id);
CREATE INDEX ON metalinksdb_chembl_relations (protein_uniprot);

-- ────────────────────────────────────────────────────────────────────────────
-- BindingDB  (source_id = 12)
-- Human-only via entity resolution. human_re pre-filters ~2.4M rows
-- (BindingDB is mostly human, so the win here is smaller than ChEMBL).
-- ────────────────────────────────────────────────────────────────────────────

DROP MATERIALIZED VIEW IF EXISTS metalinksdb_bindingdb_relations;
CREATE MATERIALIZED VIEW metalinksdb_bindingdb_relations AS
WITH

-- Pre-filter to human-only relation_evidence rows by resolving the protein entity
-- and checking taxonomy_id = 9606 before building identifier and annotation CTEs.
human_re AS (
    SELECT
        re.relation_evidence_id,
        re.subject_entity_evidence_id,
        re.object_entity_evidence_id,
        re.predicate_id,
        re.relation_category_id
    FROM relation_evidence re
    JOIN entity_evidence ee_protein
        ON  ee_protein.source_id          = re.source_id
        AND ee_protein.entity_evidence_id = re.object_entity_evidence_id
        AND ee_protein.entity_type_id = get_entity_type_id('Gene:MI:0250')
    JOIN entity_evidence_resolution eer
        ON  eer.source_id          = re.source_id
        AND eer.entity_evidence_id = re.object_entity_evidence_id
        AND eer.status_id          = 1
    JOIN entity e ON e.entity_id = eer.entity_id AND e.taxonomy_id = 9606
    WHERE re.source_id = get_source_id('bindingdb')
),

-- Collect raw BindingDB compound identifiers (BindingDB ID, InChIKey, SMILES, PubChem CID, ChEMBL ID) per entity_evidence row.
compound_ids AS (
    SELECT eei.entity_evidence_id,
        MAX(CASE WHEN ie.identifier_type_id = get_identifier_type_id('Bindingdb:OM:0006') THEN ie.value END) AS bindingdb_compound_id,
        MAX(CASE WHEN ie.identifier_type_id = get_identifier_type_id('Standard Inchi Key:MI:1101') THEN ie.value END) AS inchikey,
        MAX(CASE WHEN ie.identifier_type_id = get_identifier_type_id('Smiles:MI:0239') THEN ie.value END) AS smiles,
        MAX(CASE WHEN ie.identifier_type_id = get_identifier_type_id('Pubchem Compound:OM:0002') THEN ie.value END) AS pubchem_cid,
        MAX(CASE WHEN ie.identifier_type_id = get_identifier_type_id('Chembl Compound:MI:0967') THEN ie.value END) AS chembl_compound_id
    FROM entity_evidence_identifier eei
    JOIN identifier_evidence ie ON ie.identifier_id = eei.identifier_id
    WHERE eei.source_id = get_source_id('bindingdb')
      AND eei.entity_evidence_id IN (SELECT subject_entity_evidence_id FROM human_re)
    GROUP BY eei.entity_evidence_id
),

-- Collect raw BindingDB protein identifier (UniProt) per entity_evidence row.
protein_ids AS (
    SELECT eei.entity_evidence_id,
        MAX(CASE WHEN ie.identifier_type_id = get_identifier_type_id('Uniprot:MI:1097') THEN ie.value END) AS uniprot_id
    FROM entity_evidence_identifier eei
    JOIN identifier_evidence ie ON ie.identifier_id = eei.identifier_id
    WHERE eei.source_id = get_source_id('bindingdb')
      AND eei.entity_evidence_id IN (SELECT object_entity_evidence_id FROM human_re)
    GROUP BY eei.entity_evidence_id
),

-- Pivot BindingDB affinity measurements (pChEMBL, IC50, Ki, Kd, EC50) and experimental conditions into columns per relation_evidence row.
rel_annotations AS NOT MATERIALIZED (
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
    WHERE rea.source_id = get_source_id('bindingdb')
      AND rea.relation_evidence_id IN (SELECT relation_evidence_id FROM human_re)
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

FROM human_re re

LEFT JOIN compound_ids ci ON ci.entity_evidence_id = re.subject_entity_evidence_id
LEFT JOIN protein_ids  pi ON pi.entity_evidence_id = re.object_entity_evidence_id
LEFT JOIN entity_evidence_resolution eer_compound
    ON  eer_compound.source_id = get_source_id('bindingdb')
    AND eer_compound.entity_evidence_id = re.subject_entity_evidence_id
LEFT JOIN entity e_compound ON e_compound.entity_id = eer_compound.entity_id
LEFT JOIN vocab_identifier_type vit_c
    ON vit_c.identifier_type_id = e_compound.canonical_identifier_type_id
LEFT JOIN entity_evidence_resolution eer_protein
    ON  eer_protein.source_id = get_source_id('bindingdb')
    AND eer_protein.entity_evidence_id = re.object_entity_evidence_id
LEFT JOIN entity e_protein ON e_protein.entity_id = eer_protein.entity_id
LEFT JOIN vocab_identifier_type vit_p
    ON vit_p.identifier_type_id = e_protein.canonical_identifier_type_id
LEFT JOIN rel_annotations ann ON ann.relation_evidence_id = re.relation_evidence_id
JOIN vocab_relation_predicate vrp ON vrp.relation_predicate_id = re.predicate_id
JOIN vocab_relation_category  vrc ON vrc.relation_category_id  = re.relation_category_id;

CREATE UNIQUE INDEX ON metalinksdb_bindingdb_relations (relation_evidence_id);
CREATE INDEX ON metalinksdb_bindingdb_relations (compound_entity_id);
CREATE INDEX ON metalinksdb_bindingdb_relations (protein_entity_id);
CREATE INDEX ON metalinksdb_bindingdb_relations (compound_canonical_id);
CREATE INDEX ON metalinksdb_bindingdb_relations (protein_uniprot);

-- ────────────────────────────────────────────────────────────────────────────
-- CellLinker  (source_id = 14)  — metabolite-protein pairs only (SM=2→Prot=3)
-- Small source (~5884 human rows); human_re applied for consistency.
-- ────────────────────────────────────────────────────────────────────────────

DROP MATERIALIZED VIEW IF EXISTS metalinksdb_cellinker_relations;
CREATE MATERIALIZED VIEW metalinksdb_cellinker_relations AS
WITH

-- Pre-filter to human SM→Protein rows: compound entity_type_id=2 checked to
-- exclude protein-protein rows; protein resolves to a human entity.
human_re AS (
    SELECT
        re.relation_evidence_id,
        re.subject_entity_evidence_id,
        re.object_entity_evidence_id,
        re.predicate_id,
        re.relation_category_id
    FROM relation_evidence re
    JOIN entity_evidence ee_compound
        ON  ee_compound.source_id          = re.source_id
        AND ee_compound.entity_evidence_id = re.subject_entity_evidence_id
        AND ee_compound.entity_type_id = get_entity_type_id('Chemical:OM:0037')
    JOIN entity_evidence ee_protein
        ON  ee_protein.source_id          = re.source_id
        AND ee_protein.entity_evidence_id = re.object_entity_evidence_id
        AND ee_protein.entity_type_id = get_entity_type_id('Gene:MI:0250')
    JOIN entity_evidence_resolution eer
        ON  eer.source_id          = re.source_id
        AND eer.entity_evidence_id = re.object_entity_evidence_id
        AND eer.status_id          = 1
    JOIN entity e ON e.entity_id = eer.entity_id AND e.taxonomy_id = 9606
    WHERE re.source_id = get_source_id('cellinker')
),

-- Collect raw CellLinker compound identifiers (HMDB ID, PubChem CID, SMILES) per entity_evidence row.
compound_ids AS (
    SELECT eei.entity_evidence_id,
        MAX(CASE WHEN ie.identifier_type_id = get_identifier_type_id('Hmdb:OM:0004')  THEN ie.value END) AS hmdb_id,
        MAX(CASE WHEN ie.identifier_type_id = get_identifier_type_id('Pubchem Compound:OM:0002') THEN ie.value END) AS pubchem_cid,
        MAX(CASE WHEN ie.identifier_type_id = get_identifier_type_id('Smiles:MI:0239') THEN ie.value END) AS smiles
    FROM entity_evidence_identifier eei
    JOIN identifier_evidence ie ON ie.identifier_id = eei.identifier_id
    WHERE eei.source_id = get_source_id('cellinker')
      AND eei.entity_evidence_id IN (SELECT subject_entity_evidence_id FROM human_re)
    GROUP BY eei.entity_evidence_id
),

-- Collect raw CellLinker protein identifiers (UniProt, Entrez, gene name) per entity_evidence row.
protein_ids AS (
    SELECT eei.entity_evidence_id,
        MAX(CASE WHEN ie.identifier_type_id = get_identifier_type_id('Uniprot:MI:1097') THEN ie.value END) AS uniprot_id,
        MAX(CASE WHEN ie.identifier_type_id = get_identifier_type_id('Entrez:MI:0477') THEN ie.value END) AS entrez_id,
        MAX(CASE WHEN ie.identifier_type_id = get_identifier_type_id('Gene Name Primary:OM:0200') THEN ie.value END) AS gene_name
    FROM entity_evidence_identifier eei
    JOIN identifier_evidence ie ON ie.identifier_id = eei.identifier_id
    WHERE eei.source_id = get_source_id('cellinker')
      AND eei.entity_evidence_id IN (SELECT object_entity_evidence_id FROM human_re)
    GROUP BY eei.entity_evidence_id
),

-- Collect CellLinker literature references and interaction annotations per relation_evidence row.
rel_annotations AS (
    SELECT rea.relation_evidence_id,
        MAX(CASE WHEN a.term = 'Pubmed:MI:0446'                  THEN a.value END) AS pubmed_id,
        MAX(CASE WHEN a.term = 'Interaction Xref:OM:1206'        THEN a.value END) AS interaction_xref,
        MAX(CASE WHEN a.term = 'Interaction Annotation:OM:1207'  THEN a.value END) AS interaction_annotation
    FROM relation_evidence_annotation rea
    JOIN annotation a ON a.annotation_key = rea.annotation_key
    WHERE rea.source_id = get_source_id('cellinker')
      AND rea.relation_evidence_id IN (SELECT relation_evidence_id FROM human_re)
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

FROM human_re re
LEFT JOIN compound_ids ci ON ci.entity_evidence_id = re.subject_entity_evidence_id
LEFT JOIN protein_ids  pi ON pi.entity_evidence_id = re.object_entity_evidence_id
LEFT JOIN entity_evidence_resolution eer_compound
    ON  eer_compound.source_id = get_source_id('cellinker')
    AND eer_compound.entity_evidence_id = re.subject_entity_evidence_id
LEFT JOIN entity e_compound ON e_compound.entity_id = eer_compound.entity_id
LEFT JOIN vocab_identifier_type vit_c
    ON vit_c.identifier_type_id = e_compound.canonical_identifier_type_id
LEFT JOIN entity_evidence_resolution eer_protein
    ON  eer_protein.source_id = get_source_id('cellinker')
    AND eer_protein.entity_evidence_id = re.object_entity_evidence_id
LEFT JOIN entity e_protein ON e_protein.entity_id = eer_protein.entity_id
LEFT JOIN vocab_identifier_type vit_p
    ON vit_p.identifier_type_id = e_protein.canonical_identifier_type_id
LEFT JOIN rel_annotations ann ON ann.relation_evidence_id = re.relation_evidence_id
JOIN vocab_relation_predicate vrp ON vrp.relation_predicate_id = re.predicate_id
JOIN vocab_relation_category  vrc ON vrc.relation_category_id  = re.relation_category_id;

CREATE UNIQUE INDEX ON metalinksdb_cellinker_relations (relation_evidence_id);
CREATE INDEX ON metalinksdb_cellinker_relations (compound_entity_id);
CREATE INDEX ON metalinksdb_cellinker_relations (protein_entity_id);
CREATE INDEX ON metalinksdb_cellinker_relations (compound_canonical_id);
CREATE INDEX ON metalinksdb_cellinker_relations (protein_uniprot);

-- ────────────────────────────────────────────────────────────────────────────
-- Guide to Pharmacology  (source_id = 21)
-- Small source (~23904 rows); human_re applied for consistency.
-- ────────────────────────────────────────────────────────────────────────────

DROP MATERIALIZED VIEW IF EXISTS metalinksdb_guidetopharma_relations;
CREATE MATERIALIZED VIEW metalinksdb_guidetopharma_relations AS
WITH

-- Pre-filter to human-only relation_evidence rows via protein entity resolution.
human_re AS (
    SELECT
        re.relation_evidence_id,
        re.subject_entity_evidence_id,
        re.object_entity_evidence_id,
        re.predicate_id,
        re.relation_category_id
    FROM relation_evidence re
    JOIN entity_evidence ee_protein
        ON  ee_protein.source_id          = re.source_id
        AND ee_protein.entity_evidence_id = re.object_entity_evidence_id
        AND ee_protein.entity_type_id = get_entity_type_id('Gene:MI:0250')
    JOIN entity_evidence_resolution eer
        ON  eer.source_id          = re.source_id
        AND eer.entity_evidence_id = re.object_entity_evidence_id
        AND eer.status_id          = 1
    JOIN entity e ON e.entity_id = eer.entity_id AND e.taxonomy_id = 9606
    WHERE re.source_id = get_source_id('guidetopharma')
),

-- Collect raw GuideToPharma compound identifiers (GtP ligand ID, InChIKey, SMILES, PubChem CID, ChEMBL ID) per entity_evidence row.
compound_ids AS (
    SELECT eei.entity_evidence_id,
        MAX(CASE WHEN ie.identifier_type_id = get_identifier_type_id('Guidetopharma:OM:0008') THEN ie.value END) AS guidetopharma_id,
        MAX(CASE WHEN ie.identifier_type_id = get_identifier_type_id('Standard Inchi Key:MI:1101') THEN ie.value END) AS inchikey,
        MAX(CASE WHEN ie.identifier_type_id = get_identifier_type_id('Smiles:MI:0239') THEN ie.value END) AS smiles,
        MAX(CASE WHEN ie.identifier_type_id = get_identifier_type_id('Pubchem Compound:OM:0002') THEN ie.value END) AS pubchem_cid,
        MAX(CASE WHEN ie.identifier_type_id = get_identifier_type_id('Chembl Compound:MI:0967') THEN ie.value END) AS chembl_compound_id
    FROM entity_evidence_identifier eei
    JOIN identifier_evidence ie ON ie.identifier_id = eei.identifier_id
    WHERE eei.source_id = get_source_id('guidetopharma')
      AND eei.entity_evidence_id IN (SELECT subject_entity_evidence_id FROM human_re)
    GROUP BY eei.entity_evidence_id
),

-- Collect raw GuideToPharma protein identifiers (UniProt, HGNC, gene name) per entity_evidence row.
protein_ids AS (
    SELECT eei.entity_evidence_id,
        MAX(CASE WHEN ie.identifier_type_id = get_identifier_type_id('Uniprot:MI:1097') THEN ie.value END) AS uniprot_id,
        MAX(CASE WHEN ie.identifier_type_id = get_identifier_type_id('Hgnc:MI:1095') THEN ie.value END) AS hgnc_id,
        MAX(CASE WHEN ie.identifier_type_id = get_identifier_type_id('Gene Name Primary:OM:0200') THEN ie.value END) AS gene_name
    FROM entity_evidence_identifier eei
    JOIN identifier_evidence ie ON ie.identifier_id = eei.identifier_id
    WHERE eei.source_id = get_source_id('guidetopharma')
      AND eei.entity_evidence_id IN (SELECT object_entity_evidence_id FROM human_re)
    GROUP BY eei.entity_evidence_id
),

-- Pivot GuideToPharma pharmacological annotations (affinity values, endogenous flag, agonist/antagonist/modulator flags) into columns per relation_evidence row.
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
    WHERE rea.source_id = get_source_id('guidetopharma')
      AND rea.relation_evidence_id IN (SELECT relation_evidence_id FROM human_re)
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

FROM human_re re
LEFT JOIN compound_ids ci ON ci.entity_evidence_id = re.subject_entity_evidence_id
LEFT JOIN protein_ids  pi ON pi.entity_evidence_id = re.object_entity_evidence_id
LEFT JOIN entity_evidence_resolution eer_compound
    ON  eer_compound.source_id = get_source_id('guidetopharma')
    AND eer_compound.entity_evidence_id = re.subject_entity_evidence_id
LEFT JOIN entity e_compound ON e_compound.entity_id = eer_compound.entity_id
LEFT JOIN vocab_identifier_type vit_c
    ON vit_c.identifier_type_id = e_compound.canonical_identifier_type_id
LEFT JOIN entity_evidence_resolution eer_protein
    ON  eer_protein.source_id = get_source_id('guidetopharma')
    AND eer_protein.entity_evidence_id = re.object_entity_evidence_id
LEFT JOIN entity e_protein ON e_protein.entity_id = eer_protein.entity_id
LEFT JOIN vocab_identifier_type vit_p
    ON vit_p.identifier_type_id = e_protein.canonical_identifier_type_id
LEFT JOIN rel_annotations ann ON ann.relation_evidence_id = re.relation_evidence_id
JOIN vocab_relation_predicate vrp ON vrp.relation_predicate_id = re.predicate_id
JOIN vocab_relation_category  vrc ON vrc.relation_category_id  = re.relation_category_id;

CREATE UNIQUE INDEX ON metalinksdb_guidetopharma_relations (relation_evidence_id);
CREATE INDEX ON metalinksdb_guidetopharma_relations (compound_entity_id);
CREATE INDEX ON metalinksdb_guidetopharma_relations (protein_entity_id);
CREATE INDEX ON metalinksdb_guidetopharma_relations (compound_canonical_id);
CREATE INDEX ON metalinksdb_guidetopharma_relations (protein_uniprot);

-- ────────────────────────────────────────────────────────────────────────────
-- MRCLinksDB  (source_id = 28)  — inherently human, small source (~1468 rows).
-- ────────────────────────────────────────────────────────────────────────────

DROP MATERIALIZED VIEW IF EXISTS metalinksdb_mrclinksdb_relations;
CREATE MATERIALIZED VIEW metalinksdb_mrclinksdb_relations AS
WITH

-- Pre-filter to human SM→Protein rows: compound entity_type_id=2 checked to
-- exclude any non-metabolite rows.
human_re AS (
    SELECT
        re.relation_evidence_id,
        re.subject_entity_evidence_id,
        re.object_entity_evidence_id,
        re.predicate_id,
        re.relation_category_id
    FROM relation_evidence re
    JOIN entity_evidence ee_compound
        ON  ee_compound.source_id          = re.source_id
        AND ee_compound.entity_evidence_id = re.subject_entity_evidence_id
        AND ee_compound.entity_type_id = get_entity_type_id('Chemical:OM:0037')
    JOIN entity_evidence ee_protein
        ON  ee_protein.source_id          = re.source_id
        AND ee_protein.entity_evidence_id = re.object_entity_evidence_id
        AND ee_protein.entity_type_id = get_entity_type_id('Gene:MI:0250')
    JOIN entity_evidence_resolution eer
        ON  eer.source_id          = re.source_id
        AND eer.entity_evidence_id = re.object_entity_evidence_id
        AND eer.status_id          = 1
    JOIN entity e ON e.entity_id = eer.entity_id AND e.taxonomy_id = 9606
    WHERE re.source_id = get_source_id('mrclinksdb')
),

-- Collect raw MRCLinksDB compound identifiers (HMDB ID, PubChem CID, SMILES) per entity_evidence row.
compound_ids AS (
    SELECT eei.entity_evidence_id,
        MAX(CASE WHEN ie.identifier_type_id = get_identifier_type_id('Hmdb:OM:0004')  THEN ie.value END) AS hmdb_id,
        MAX(CASE WHEN ie.identifier_type_id = get_identifier_type_id('Pubchem Compound:OM:0002') THEN ie.value END) AS pubchem_cid,
        MAX(CASE WHEN ie.identifier_type_id = get_identifier_type_id('Smiles:MI:0239') THEN ie.value END) AS smiles
    FROM entity_evidence_identifier eei
    JOIN identifier_evidence ie ON ie.identifier_id = eei.identifier_id
    WHERE eei.source_id = get_source_id('mrclinksdb')
      AND eei.entity_evidence_id IN (SELECT subject_entity_evidence_id FROM human_re)
    GROUP BY eei.entity_evidence_id
),

-- Collect raw MRCLinksDB protein identifiers (UniProt, Entrez, gene name) per entity_evidence row.
protein_ids AS (
    SELECT eei.entity_evidence_id,
        MAX(CASE WHEN ie.identifier_type_id = get_identifier_type_id('Uniprot:MI:1097') THEN ie.value END) AS uniprot_id,
        MAX(CASE WHEN ie.identifier_type_id = get_identifier_type_id('Entrez:MI:0477') THEN ie.value END) AS entrez_id,
        MAX(CASE WHEN ie.identifier_type_id = get_identifier_type_id('Gene Name Primary:OM:0200') THEN ie.value END) AS gene_name
    FROM entity_evidence_identifier eei
    JOIN identifier_evidence ie ON ie.identifier_id = eei.identifier_id
    WHERE eei.source_id = get_source_id('mrclinksdb')
      AND eei.entity_evidence_id IN (SELECT object_entity_evidence_id FROM human_re)
    GROUP BY eei.entity_evidence_id
),

-- Collect MRCLinksDB literature references and curator comments per relation_evidence row.
rel_annotations AS (
    SELECT rea.relation_evidence_id,
        MAX(CASE WHEN a.term = 'Pubmed:MI:0446'   THEN a.value END) AS pubmed_id,
        MAX(CASE WHEN a.term = 'Comment:MI:0612'  THEN a.value END) AS comment
    FROM relation_evidence_annotation rea
    JOIN annotation a ON a.annotation_key = rea.annotation_key
    WHERE rea.source_id = get_source_id('mrclinksdb')
      AND rea.relation_evidence_id IN (SELECT relation_evidence_id FROM human_re)
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

FROM human_re re
LEFT JOIN compound_ids ci ON ci.entity_evidence_id = re.subject_entity_evidence_id
LEFT JOIN protein_ids  pi ON pi.entity_evidence_id = re.object_entity_evidence_id
LEFT JOIN entity_evidence_resolution eer_compound
    ON  eer_compound.source_id = get_source_id('mrclinksdb')
    AND eer_compound.entity_evidence_id = re.subject_entity_evidence_id
LEFT JOIN entity e_compound ON e_compound.entity_id = eer_compound.entity_id
LEFT JOIN vocab_identifier_type vit_c
    ON vit_c.identifier_type_id = e_compound.canonical_identifier_type_id
LEFT JOIN entity_evidence_resolution eer_protein
    ON  eer_protein.source_id = get_source_id('mrclinksdb')
    AND eer_protein.entity_evidence_id = re.object_entity_evidence_id
LEFT JOIN entity e_protein ON e_protein.entity_id = eer_protein.entity_id
LEFT JOIN vocab_identifier_type vit_p
    ON vit_p.identifier_type_id = e_protein.canonical_identifier_type_id
LEFT JOIN rel_annotations ann ON ann.relation_evidence_id = re.relation_evidence_id
JOIN vocab_relation_predicate vrp ON vrp.relation_predicate_id = re.predicate_id
JOIN vocab_relation_category  vrc ON vrc.relation_category_id  = re.relation_category_id;

CREATE UNIQUE INDEX ON metalinksdb_mrclinksdb_relations (relation_evidence_id);
CREATE INDEX ON metalinksdb_mrclinksdb_relations (compound_entity_id);
CREATE INDEX ON metalinksdb_mrclinksdb_relations (protein_entity_id);
CREATE INDEX ON metalinksdb_mrclinksdb_relations (compound_canonical_id);
CREATE INDEX ON metalinksdb_mrclinksdb_relations (protein_uniprot);

-- ────────────────────────────────────────────────────────────────────────────
-- STITCH  (source_id = 39)
-- Human filter uses entity_evidence.taxonomy_id = 9606 directly — simpler than
-- identifier-based approach and gives the planner correct cardinality estimates.
DROP MATERIALIZED VIEW IF EXISTS metalinksdb_stitch_relations;
CREATE MATERIALIZED VIEW metalinksdb_stitch_relations AS
WITH

-- Pre-filter to human SM→Protein rows using taxonomy_id directly on entity_evidence.
human_re AS (
    SELECT DISTINCT
        re.relation_evidence_id,
        re.subject_entity_evidence_id,
        re.object_entity_evidence_id,
        re.predicate_id,
        re.relation_category_id
    FROM relation_evidence re
    JOIN entity_evidence ee_compound
        ON  ee_compound.source_id          = re.source_id
        AND ee_compound.entity_evidence_id = re.subject_entity_evidence_id
        AND ee_compound.entity_type_id = get_entity_type_id('Chemical:OM:0037')
    JOIN entity_evidence ee_protein
        ON  ee_protein.source_id          = re.source_id
        AND ee_protein.entity_evidence_id = re.object_entity_evidence_id
        AND ee_protein.entity_type_id = get_entity_type_id('Gene:MI:0250')
        AND ee_protein.taxonomy_id        = 9606
    WHERE re.source_id = get_source_id('stitch')
),

-- Collect raw STITCH compound identifier (PubChem CID) per entity_evidence row.
compound_ids AS NOT MATERIALIZED (
    SELECT eei.entity_evidence_id,
        MAX(CASE WHEN ie.identifier_type_id = get_identifier_type_id('Pubchem Compound:OM:0002') THEN ie.value END) AS pubchem_cid
    FROM entity_evidence_identifier eei
    JOIN identifier_evidence ie ON ie.identifier_id = eei.identifier_id
    WHERE eei.source_id = get_source_id('stitch')
      AND eei.entity_evidence_id IN (SELECT subject_entity_evidence_id FROM human_re)
    GROUP BY eei.entity_evidence_id
),

-- Collect raw STITCH protein identifier (Ensembl ID) per entity_evidence row; UniProt comes from canonical resolution.
protein_ids AS NOT MATERIALIZED (
    SELECT eei.entity_evidence_id,
        MAX(CASE WHEN ie.identifier_type_id = get_identifier_type_id('Ensembl:MI:0476') THEN ie.value END) AS ensembl_id
    FROM entity_evidence_identifier eei
    JOIN identifier_evidence ie ON ie.identifier_id = eei.identifier_id
    WHERE eei.source_id = get_source_id('stitch')
      AND eei.entity_evidence_id IN (SELECT object_entity_evidence_id FROM human_re)
    GROUP BY eei.entity_evidence_id
),

-- Pivot STITCH interaction scores and action type flags (binding, inhibition, activation, enzymatic) into columns per relation_evidence row.
rel_annotations AS NOT MATERIALIZED (
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
    WHERE rea.source_id = get_source_id('stitch')
      AND rea.relation_evidence_id IN (SELECT relation_evidence_id FROM human_re)
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

FROM human_re re

LEFT JOIN compound_ids ci ON ci.entity_evidence_id = re.subject_entity_evidence_id
LEFT JOIN protein_ids  pi ON pi.entity_evidence_id = re.object_entity_evidence_id
LEFT JOIN entity_evidence_resolution eer_compound
    ON  eer_compound.source_id = get_source_id('stitch')
    AND eer_compound.entity_evidence_id = re.subject_entity_evidence_id
LEFT JOIN entity e_compound ON e_compound.entity_id = eer_compound.entity_id
LEFT JOIN vocab_identifier_type vit_c
    ON vit_c.identifier_type_id = e_compound.canonical_identifier_type_id
LEFT JOIN entity_evidence_resolution eer_protein
    ON  eer_protein.source_id = get_source_id('stitch')
    AND eer_protein.entity_evidence_id = re.object_entity_evidence_id
LEFT JOIN entity e_protein ON e_protein.entity_id = eer_protein.entity_id
LEFT JOIN vocab_identifier_type vit_p
    ON vit_p.identifier_type_id = e_protein.canonical_identifier_type_id
LEFT JOIN rel_annotations ann ON ann.relation_evidence_id = re.relation_evidence_id
JOIN vocab_relation_predicate vrp ON vrp.relation_predicate_id = re.predicate_id
JOIN vocab_relation_category  vrc ON vrc.relation_category_id  = re.relation_category_id;

CREATE UNIQUE INDEX ON metalinksdb_stitch_relations (relation_evidence_id);
CREATE INDEX ON metalinksdb_stitch_relations (compound_entity_id);
CREATE INDEX ON metalinksdb_stitch_relations (protein_entity_id);
CREATE INDEX ON metalinksdb_stitch_relations (compound_canonical_id);
CREATE INDEX ON metalinksdb_stitch_relations (protein_uniprot);


-- ────────────────────────────────────────────────────────────────────────────
-- TCDB  (source_id = 41)  — transporter-substrate pairs
-- Direction REVERSED: Protein(3, subject) → SmallMolecule(2, object)
-- Small source (20k rows); human_re applied for consistency.
-- ────────────────────────────────────────────────────────────────────────────

DROP MATERIALIZED VIEW IF EXISTS metalinksdb_tcdb_relations;
CREATE MATERIALIZED VIEW metalinksdb_tcdb_relations AS
WITH

-- Pre-filter to human-only rows; TCDB direction is reversed — protein is the subject.
human_re AS (
    SELECT
        re.relation_evidence_id,
        re.subject_entity_evidence_id,
        re.object_entity_evidence_id,
        re.predicate_id,
        re.relation_category_id
    FROM relation_evidence re
    JOIN entity_evidence ee_compound
        ON  ee_compound.source_id          = re.source_id
        AND ee_compound.entity_evidence_id = re.object_entity_evidence_id   -- compound is OBJECT for TCDB
        AND ee_compound.entity_type_id = get_entity_type_id('Chemical:OM:0037')
    JOIN entity_evidence ee_protein
        ON  ee_protein.source_id          = re.source_id
        AND ee_protein.entity_evidence_id = re.subject_entity_evidence_id   -- protein is SUBJECT for TCDB
        AND ee_protein.entity_type_id = get_entity_type_id('Gene:MI:0250')
    JOIN entity_evidence_resolution eer
        ON  eer.source_id          = re.source_id
        AND eer.entity_evidence_id = re.subject_entity_evidence_id
        AND eer.status_id          = 1
    JOIN entity e ON e.entity_id = eer.entity_id AND e.taxonomy_id = 9606
    WHERE re.source_id = get_source_id('tcdb')
),

-- Collect raw TCDB compound identifiers (ChEBI ID, compound name) per entity_evidence row.
compound_ids AS (
    SELECT eei.entity_evidence_id,
        MAX(CASE WHEN ie.identifier_type_id = get_identifier_type_id('Chebi:MI:0474')  THEN ie.value END) AS chebi_id,
        MAX(CASE WHEN ie.identifier_type_id = get_identifier_type_id('Name:OM:0202') THEN ie.value END) AS compound_name
    FROM entity_evidence_identifier eei
    JOIN identifier_evidence ie ON ie.identifier_id = eei.identifier_id
    WHERE eei.source_id = get_source_id('tcdb')
      AND eei.entity_evidence_id IN (SELECT object_entity_evidence_id FROM human_re)
    GROUP BY eei.entity_evidence_id
),

-- Collect raw TCDB protein identifiers (UniProt, TCDB family ID) per entity_evidence row.
protein_ids AS (
    SELECT eei.entity_evidence_id,
        MAX(CASE WHEN ie.identifier_type_id = get_identifier_type_id('Uniprot:MI:1097')  THEN ie.value END) AS uniprot_id,
        MAX(CASE WHEN ie.identifier_type_id = get_identifier_type_id('Tcdb:OM:0238') THEN ie.value END) AS tcdb_id
    FROM entity_evidence_identifier eei
    JOIN identifier_evidence ie ON ie.identifier_id = eei.identifier_id
    WHERE eei.source_id = get_source_id('tcdb')
      AND eei.entity_evidence_id IN (SELECT subject_entity_evidence_id FROM human_re)
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

FROM human_re re
LEFT JOIN entity_evidence_resolution eer_protein
    ON  eer_protein.source_id = get_source_id('tcdb')
    AND eer_protein.entity_evidence_id = re.subject_entity_evidence_id
LEFT JOIN entity e_protein
    ON  e_protein.entity_id            = eer_protein.entity_id
LEFT JOIN vocab_identifier_type vit_p
    ON  vit_p.identifier_type_id       = e_protein.canonical_identifier_type_id
LEFT JOIN entity_evidence_resolution eer_compound
    ON  eer_compound.source_id = get_source_id('tcdb')
    AND eer_compound.entity_evidence_id = re.object_entity_evidence_id
LEFT JOIN entity e_compound
    ON  e_compound.entity_id           = eer_compound.entity_id
LEFT JOIN vocab_identifier_type vit_c
    ON  vit_c.identifier_type_id       = e_compound.canonical_identifier_type_id
LEFT JOIN compound_ids ci ON ci.entity_evidence_id = re.object_entity_evidence_id
LEFT JOIN protein_ids  pi ON pi.entity_evidence_id = re.subject_entity_evidence_id
JOIN vocab_relation_predicate vrp ON vrp.relation_predicate_id = re.predicate_id
JOIN vocab_relation_category  vrc ON vrc.relation_category_id  = re.relation_category_id;

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

-- Aggregates all resolved protein–metabolite pairs across sources into one row per canonical (compound, protein) pair,
-- collecting source names, relation types (interaction/transport), best pChEMBL value, and literature references.

CREATE OR REPLACE FUNCTION get_entity_type_id(type_name text)
RETURNS bigint LANGUAGE plpgsql STABLE AS $$
BEGIN
    RETURN (SELECT entity_type_id FROM public.vocab_entity_type WHERE name = type_name);
END;
$$;

CREATE OR REPLACE FUNCTION get_identifier_type_id(type_name text)
RETURNS bigint LANGUAGE plpgsql STABLE AS $$
BEGIN
    RETURN (SELECT identifier_type_id FROM public.vocab_identifier_type WHERE name = type_name);
END;
$$;

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
