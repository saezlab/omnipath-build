-- Gold entity table
--
-- This script creates the entity table by grouping entity evidence records based on shared identifiers.
-- Each entity represents a unique biological entity (protein, small molecule, etc.) that may have
-- multiple pieces of evidence from different sources.
--
-- Grouping strategy (in order of preference):
--   1. UniProt accession - used for proteins (ensures same protein from different sources = same entity)
--   2. InChI - used for small molecules (ensures same chemical structure from different sources = same entity)
--   3. Source accession - fallback for entities without UniProt or InChI (each source gets its own entity)
--
-- The script performs four main operations:
--   1. Creates entity groups by identifying shared UniProt/InChI/source accession identifiers
--   2. Exports the entity table with unique entity IDs
--   3. Updates entity_evidence.parquet with the assigned entity_id for each evidence record
--   4. Updates entity_identifier.parquet with the assigned entity_id for each identifier record

-- Step 1: Create entity groups based on unique InChI or UniProt identifiers
CREATE TEMP TABLE entity_groups AS
WITH entity_identifiers AS (
    SELECT
        entity_evidence_id,
        identifier,
        identifier_kind
    FROM read_parquet('entity_identifier.parquet')
),

-- Get UniProt identifiers
uniprot_ids AS (
    SELECT DISTINCT
        entity_evidence_id,
        identifier AS uniprot
    FROM entity_identifiers
    WHERE identifier_kind = 'uniprot'
),

-- Get InChI identifiers
inchi_ids AS (
    SELECT DISTINCT
        entity_evidence_id,
        identifier AS inchi
    FROM entity_identifiers
    WHERE identifier_kind = 'inchi'
),

-- Get source accession identifiers
source_accession_ids AS (
    SELECT DISTINCT
        entity_evidence_id,
        identifier AS source_accession
    FROM entity_identifiers
    WHERE identifier_kind = 'source_accession'
),

-- Combine grouping keys (prefer InChI or UniProt, fallback to source accession)
entity_keys AS (
    SELECT
        ee.id AS entity_evidence_id,
        COALESCE(u.uniprot, i.inchi, s.source_accession) AS grouping_key
    FROM read_parquet('entity_evidence.parquet') ee
    LEFT JOIN uniprot_ids u ON u.entity_evidence_id = ee.id
    LEFT JOIN inchi_ids i ON i.entity_evidence_id = ee.id
    LEFT JOIN source_accession_ids s ON s.entity_evidence_id = ee.id
    WHERE COALESCE(u.uniprot, i.inchi, s.source_accession) IS NOT NULL
)

-- Assign entity IDs to each grouping key
SELECT
    DENSE_RANK() OVER (ORDER BY grouping_key) AS entity_id,
    entity_evidence_id
FROM entity_keys;

-- Step 2: Export entity table
COPY (
    SELECT DISTINCT entity_id AS id
    FROM entity_groups
    ORDER BY id
) TO 'entity.parquet' (FORMAT PARQUET);

-- Step 3: Update entity_evidence with entity_id
COPY (
    SELECT
        ee.id,
        eg.entity_id,
        ee.source_id,
        ee.parent_entity_evidence_id,
        ee.annotations
    FROM read_parquet('entity_evidence.parquet') ee
    LEFT JOIN entity_groups eg ON eg.entity_evidence_id = ee.id
    ORDER BY ee.id
) TO 'entity_evidence.parquet' (FORMAT PARQUET);

-- Step 4: Update entity_identifier with entity_id
COPY (
    SELECT
        ei.id,
        eg.entity_id,
        ei.identifier,
        ei.source_id,
        ei.identifier_type_name,
        ei.identifier_kind
    FROM read_parquet('entity_identifier.parquet') ei
    LEFT JOIN entity_groups eg ON eg.entity_evidence_id = ei.entity_evidence_id
    ORDER BY ei.id
) TO 'entity_identifier.parquet' (FORMAT PARQUET);
