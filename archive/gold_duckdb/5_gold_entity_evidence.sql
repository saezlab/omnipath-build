-- Step 1: Create entity_evidence as a temporary view, then export
CREATE OR REPLACE TEMP VIEW entity_evidence AS
SELECT
    ROW_NUMBER() OVER (ORDER BY source, accession) AS id,
    NULL AS entity_id,
    source AS source_id,
    parent_accession,
    annotations
FROM read_parquet('../../databases/omnipath/data/*/*/silver/silver_entities.parquet',
                  hive_partitioning=false, filename=true, union_by_name=true)
WHERE accession IS NOT NULL;

COPY entity_evidence TO 'entity_evidence.parquet' (FORMAT 'parquet');

-- Step 2: Create entity_identifier
CREATE OR REPLACE TEMP VIEW entity_identifier AS
WITH silver AS (
    SELECT
        ROW_NUMBER() OVER (ORDER BY source, accession) AS id,
        source,
        accession,
        entity_type,
        inchikey,
        smiles,
        inchi,
        identifiers,
        name,
        synonyms
    FROM read_parquet('../../databases/omnipath/data/*/*/silver/silver_entities.parquet',
                      hive_partitioning=false, filename=true, union_by_name=true)
    WHERE accession IS NOT NULL
),
source_accession_identifiers AS (
    SELECT id AS entity_evidence_id, NULL AS entity_id,
           accession AS identifier, source AS source_id,
           source AS identifier_type_name, 'source_accession' AS identifier_kind
    FROM silver
),
cross_reference_identifiers AS (
    SELECT id AS entity_evidence_id, NULL AS entity_id,
           xref.value AS identifier, source AS source_id,
           xref.type AS identifier_type_name, 'cross_reference' AS identifier_kind
    FROM silver, UNNEST(identifiers) AS t(xref)
),
structural_identifiers AS (
    SELECT id AS entity_evidence_id, NULL AS entity_id,
           inchikey AS identifier, source AS source_id,
           'inchikey' AS identifier_type_name, 'inchikey' AS identifier_kind
    FROM silver WHERE inchikey IS NOT NULL
    UNION ALL
    SELECT id, NULL, smiles, source, 'smiles', 'smiles'
    FROM silver WHERE smiles IS NOT NULL
    UNION ALL
    SELECT id, NULL, inchi, source, 'inchi', 'inchi'
    FROM silver WHERE inchi IS NOT NULL
),
name_identifiers AS (
    SELECT id, NULL, name, source, 'name', 'name'
    FROM silver WHERE name IS NOT NULL
),
synonym_identifiers AS (
    SELECT id, NULL, syn, source, 'synonym', 'synonym'
    FROM silver, UNNEST(synonyms) AS t(syn)
)
SELECT
    ROW_NUMBER() OVER (ORDER BY entity_evidence_id, identifier_kind, identifier) AS id,
    entity_evidence_id,
    entity_id,
    identifier,
    source_id,
    identifier_type_name,
    identifier_kind
FROM (
    SELECT * FROM source_accession_identifiers
    UNION ALL SELECT * FROM cross_reference_identifiers
    UNION ALL SELECT * FROM structural_identifiers
    UNION ALL SELECT * FROM name_identifiers
    UNION ALL SELECT * FROM synonym_identifiers
);

COPY entity_identifier TO 'entity_identifier.parquet' (FORMAT 'parquet');
