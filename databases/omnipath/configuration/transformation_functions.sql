-- Transformation functions for metabo silver layer
-- Define SQL functions that will be used to transform data from bronze to silver

-- =====================================================
-- CORE NORMALIZATION FUNCTIONS
-- =====================================================

CREATE OR REPLACE MACRO normalize_id(ns, raw) AS (
    CASE
        WHEN raw IS NULL OR TRIM(raw) = '' THEN NULL

        WHEN lower(ns) = 'hmdb' THEN
            CASE
                WHEN regexp_extract(TRIM(raw), '(?i)^(?:HMDB:)?(?:HMDB)?(\d{5,7})$', 1) IS NOT NULL
                     AND regexp_extract(TRIM(raw), '(?i)^(?:HMDB:)?(?:HMDB)?(\d{5,7})$', 1) != ''
                    THEN 'HMDB:' || regexp_extract(TRIM(raw), '(?i)^(?:HMDB:)?(?:HMDB)?(\d{5,7})$', 1)
            END

        WHEN lower(ns) = 'chebi' THEN
            CASE
                WHEN regexp_extract(TRIM(raw), '(?i)^(?:CHEBI:)?(\d+)$', 1) IS NOT NULL
                     AND regexp_extract(TRIM(raw), '(?i)^(?:CHEBI:)?(\d+)$', 1) != ''
                    THEN 'CHEBI:' || regexp_extract(TRIM(raw), '(?i)^(?:CHEBI:)?(\d+)$', 1)
            END

        WHEN lower(ns) = 'pubchem' THEN
            CASE
                WHEN regexp_extract(TRIM(raw), '(?i)^(?:PUBCHEM:|CID:?)?(\d+)$', 1) IS NOT NULL
                     AND regexp_extract(TRIM(raw), '(?i)^(?:PUBCHEM:|CID:?)?(\d+)$', 1) != ''
                    THEN 'PUBCHEM:' || regexp_extract(TRIM(raw), '(?i)^(?:PUBCHEM:|CID:?)?(\d+)$', 1)
            END

        WHEN lower(ns) = 'kegg' THEN
            CASE
                WHEN regexp_extract(TRIM(raw), '(?i)^(?:KEGG:)?([CD]\d{5})$', 1) IS NOT NULL
                     AND regexp_extract(TRIM(raw), '(?i)^(?:KEGG:)?([CD]\d{5})$', 1) != ''
                    THEN 'KEGG:' || upper(regexp_extract(TRIM(raw), '(?i)^(?:KEGG:)?([CD]\d{5})$', 1))
            END

        WHEN lower(ns) = 'drugbank' THEN
            CASE
                WHEN regexp_extract(TRIM(raw), '(?i)^(?:DRUGBANK:)?(DB\d{5})$', 1) IS NOT NULL
                     AND regexp_extract(TRIM(raw), '(?i)^(?:DRUGBANK:)?(DB\d{5})$', 1) != ''
                    THEN 'DRUGBANK:' || upper(regexp_extract(TRIM(raw), '(?i)^(?:DRUGBANK:)?(DB\d{5})$', 1))
            END

        WHEN lower(ns) = 'cas' THEN
            CASE
                WHEN regexp_extract(TRIM(raw), '^(\d{2,7}-\d{2}-\d)$', 1) IS NOT NULL
                     AND regexp_extract(TRIM(raw), '^(\d{2,7}-\d{2}-\d)$', 1) != ''
                    THEN regexp_extract(TRIM(raw), '^(\d{2,7}-\d{2}-\d)$', 1)
            END

        WHEN lower(ns) = 'inchikey' THEN
            CASE
                WHEN regexp_extract(upper(TRIM(raw)), '^(?:INCHIKEY=)?([A-Z]{14}-[A-Z]{10}-[A-Z])$', 1) IS NOT NULL
                     AND regexp_extract(upper(TRIM(raw)), '^(?:INCHIKEY=)?([A-Z]{14}-[A-Z]{10}-[A-Z])$', 1) != ''
                    THEN regexp_extract(upper(TRIM(raw)), '^(?:INCHIKEY=)?([A-Z]{14}-[A-Z]{10}-[A-Z])$', 1)
            END

        WHEN lower(ns) = 'inchi' THEN
            CASE
                WHEN upper(TRIM(raw)) IN ('INCHI=NONE','NONE','N/A','NA') OR TRIM(raw) = 'InChI=' THEN NULL
                WHEN regexp_extract(TRIM(raw), '(?i)^InChI=(.+)$', 1) IS NOT NULL
                    THEN 'InChI=' || regexp_extract(TRIM(raw), '(?i)^InChI=(.+)$', 1)
                ELSE TRIM(raw)
            END

        WHEN lower(ns) = 'lipidmaps' THEN
            CASE
                WHEN regexp_extract(upper(TRIM(raw)), '(?i)^(?:LIPIDMAPS:)?(LM[A-Z]{2}\d{8})$', 1) IS NOT NULL
                     AND regexp_extract(upper(TRIM(raw)), '(?i)^(?:LIPIDMAPS:)?(LM[A-Z]{2}\d{8})$', 1) != ''
                    THEN 'LIPIDMAPS:' || regexp_extract(upper(TRIM(raw)), '(?i)^(?:LIPIDMAPS:)?(LM[A-Z]{2}\d{8})$', 1)
            END

        WHEN lower(ns) = 'swisslipids' THEN
            CASE
                WHEN regexp_extract(TRIM(raw), '(?i)^(?:SWISSLIPIDS:|SLM:)?(\d{6,})$', 1) IS NOT NULL
                     AND regexp_extract(TRIM(raw), '(?i)^(?:SWISSLIPIDS:|SLM:)?(\d{6,})$', 1) != ''
                    THEN 'SWISSLIPIDS:' || regexp_extract(TRIM(raw), '(?i)^(?:SWISSLIPIDS:|SLM:)?(\d{6,})$', 1)
            END

        WHEN lower(ns) = 'metanetx' THEN
            CASE
                WHEN regexp_extract(TRIM(raw), '(?i)^(?:METANETX:)?(MNXM\d+)$', 1) IS NOT NULL
                     AND regexp_extract(TRIM(raw), '(?i)^(?:METANETX:)?(MNXM\d+)$', 1) != ''
                    THEN 'METANETX:' || upper(regexp_extract(TRIM(raw), '(?i)^(?:METANETX:)?(MNXM\d+)$', 1))
            END

        WHEN lower(ns) = 'ramp' THEN
            CASE
                WHEN TRIM(raw) = '' THEN NULL
                ELSE TRIM(raw)
            END
    END
);

CREATE OR REPLACE MACRO normalize_hmdb_id(raw) AS normalize_id('hmdb', raw);
CREATE OR REPLACE MACRO normalize_chebi_id(raw) AS normalize_id('chebi', raw);
CREATE OR REPLACE MACRO normalize_pubchem_cid(raw) AS normalize_id('pubchem', raw);
CREATE OR REPLACE MACRO normalize_kegg_id(raw) AS normalize_id('kegg', raw);
CREATE OR REPLACE MACRO normalize_drugbank_id(raw) AS normalize_id('drugbank', raw);
CREATE OR REPLACE MACRO normalize_cas_number(raw) AS normalize_id('cas', raw);
CREATE OR REPLACE MACRO normalize_inchi_value(raw) AS normalize_id('inchi', raw);
CREATE OR REPLACE MACRO normalize_lipidmaps_id(raw) AS normalize_id('lipidmaps', raw);
CREATE OR REPLACE MACRO normalize_swisslipids_id(raw) AS normalize_id('swisslipids', raw);
CREATE OR REPLACE MACRO normalize_metanetx_id(raw) AS normalize_id('metanetx', raw);
CREATE OR REPLACE MACRO normalize_ramp_id(raw) AS normalize_id('ramp', raw);
CREATE OR REPLACE MACRO normalize_inchikey_value(raw) AS normalize_id('inchikey', raw);

-- =====================================================
-- SILVER ENTITY HELPER FUNCTIONS
-- =====================================================

CREATE OR REPLACE MACRO split_string_to_json(value, delimiter) AS
    CASE
        WHEN value IS NULL OR TRIM(value) = '' THEN NULL
        ELSE to_json(
            list_filter(
                list_transform(
                    str_split(value, delimiter),
                    x -> TRIM(x)
                ),
                x -> x IS NOT NULL AND x != ''
            )
        )
    END;

CREATE OR REPLACE MACRO parse_bracketed_list(value) AS
    CASE
        WHEN value IS NULL OR TRIM(value) = '' THEN NULL
        ELSE split_string_to_json(
            CASE
                WHEN LEFT(TRIM(value), 1) = '[' AND RIGHT(TRIM(value), 1) = ']' THEN
                    CASE
                        WHEN LENGTH(TRIM(value)) <= 2 THEN ''
                        ELSE SUBSTRING(TRIM(value), 2, LENGTH(TRIM(value)) - 2)
                    END
                ELSE TRIM(value)
            END,
            ','
        )
    END;

CREATE OR REPLACE MACRO build_lipidmaps_annotations(category, main_class) AS (
    SELECT CASE
        WHEN len(annotation_list) = 0 THEN NULL
        ELSE to_json(annotation_list)
    END
    FROM (
        SELECT list_filter([
            CASE
                WHEN category IS NOT NULL AND TRIM(category) != '' THEN struct_pack(
                    term := 'lipidmaps_category',
                    value := TRIM(category),
                    units := NULL
                )
            END,
            CASE
                WHEN main_class IS NOT NULL AND TRIM(main_class) != '' THEN struct_pack(
                    term := 'lipidmaps_main_class',
                    value := TRIM(main_class),
                    units := NULL
                )
            END
        ], x -> x IS NOT NULL) AS annotation_list
    )
);

CREATE OR REPLACE MACRO build_ramp_annotations(classes) AS (
    SELECT CASE
        WHEN len(annotation_list) = 0 THEN NULL
        ELSE to_json(annotation_list)
    END
    FROM (
        SELECT list_filter(
            list_transform(
                str_split(coalesce(classes, ''), ', '),
                entry -> CASE
                    WHEN TRIM(entry) = '' THEN NULL
                    ELSE struct_pack(
                        term := 'ramp_classification',
                        value := TRIM(entry),
                        units := NULL
                    )
                END
            ),
            x -> x IS NOT NULL
        ) AS annotation_list
    )
);

CREATE OR REPLACE MACRO build_cross_references(
    chebi_id,
    pubchem_cid,
    kegg_id,
    drugbank_id,
    cas_number,
    lipidmaps_id,
    swisslipids_id,
    metanetx_id
) AS (
    SELECT CASE
        WHEN len(xref_list) = 0 THEN NULL
        ELSE to_json(xref_list)
    END
    FROM (
        SELECT list_filter([
            CASE
                WHEN normalize_chebi_id(chebi_id) IS NOT NULL
                    THEN struct_pack(type := 'chebi', value := normalize_chebi_id(chebi_id))
            END,
            CASE
                WHEN normalize_pubchem_cid(pubchem_cid) IS NOT NULL
                    THEN struct_pack(type := 'pubchem_cid', value := normalize_pubchem_cid(pubchem_cid))
            END,
            CASE
                WHEN normalize_kegg_id(kegg_id) IS NOT NULL
                    THEN struct_pack(type := 'kegg_compound', value := normalize_kegg_id(kegg_id))
            END,
            CASE
                WHEN normalize_drugbank_id(drugbank_id) IS NOT NULL
                    THEN struct_pack(type := 'drugbank', value := normalize_drugbank_id(drugbank_id))
            END,
            CASE
                WHEN normalize_cas_number(cas_number) IS NOT NULL
                    THEN struct_pack(type := 'cas', value := normalize_cas_number(cas_number))
            END,
            CASE
                WHEN normalize_lipidmaps_id(lipidmaps_id) IS NOT NULL
                    THEN struct_pack(type := 'lipidmaps', value := normalize_lipidmaps_id(lipidmaps_id))
            END,
            CASE
                WHEN normalize_swisslipids_id(swisslipids_id) IS NOT NULL
                    THEN struct_pack(type := 'swisslipids', value := normalize_swisslipids_id(swisslipids_id))
            END,
            CASE
                WHEN normalize_metanetx_id(metanetx_id) IS NOT NULL
                    THEN struct_pack(type := 'metanetx', value := normalize_metanetx_id(metanetx_id))
            END
        ], x -> x IS NOT NULL) AS xref_list
    )
);

CREATE OR REPLACE MACRO build_swisslipids_annotations(level, lipid_class, parent, components, pmids, charge) AS (
    SELECT CASE
        WHEN len(annotation_list) = 0 THEN NULL
        ELSE to_json(annotation_list)
    END
    FROM (
        SELECT list_filter(
            list_concat(
                base_annotations,
                CASE
                    WHEN pmids IS NULL THEN CAST(list_value() AS STRUCT(term VARCHAR, value VARCHAR, units VARCHAR)[])
                    ELSE list_filter(
                        list_transform(
                            pmids,
                            x -> CASE
                                WHEN x IS NULL OR x = '' THEN NULL
                                ELSE struct_pack(
                                    term := 'pmid',
                                    value := x,
                                    units := NULL
                                )
                            END
                        ),
                        x -> x IS NOT NULL
                    )
                END
            ),
            x -> x IS NOT NULL
        ) AS annotation_list
        FROM (
            SELECT list_filter([
                CASE
                    WHEN level IS NOT NULL AND TRIM(level) != '' THEN struct_pack(
                        term := 'swisslipids_level',
                        value := TRIM(level),
                        units := NULL
                    )
                END,
                CASE
                    WHEN lipid_class IS NOT NULL AND TRIM(lipid_class) != '' THEN struct_pack(
                        term := 'swisslipids_class',
                        value := TRIM(lipid_class),
                        units := NULL
                    )
                END,
                CASE
                    WHEN parent IS NOT NULL AND TRIM(parent) != '' THEN struct_pack(
                        term := 'swisslipids_parent',
                        value := TRIM(parent),
                        units := NULL
                    )
                END,
                CASE
                    WHEN components IS NOT NULL AND TRIM(components) != '' THEN struct_pack(
                        term := 'swisslipids_components',
                        value := TRIM(components),
                        units := NULL
                    )
                END,
                CASE
                    WHEN charge IS NOT NULL AND TRIM(charge) != '' THEN struct_pack(
                        term := 'charge',
                        value := TRIM(charge),
                        units := NULL
                    )
                END
            ], x -> x IS NOT NULL) AS base_annotations
        )
    )
);

-- =====================================================
-- GENERIC ID EXTRACTION FROM CURIE-FORMATTED LISTS
-- =====================================================

-- Extract all distinct IDs of a specific type from a comma-separated CURIE list
-- Returns an array of all unique IDs, or NULL if none found
--
-- Arguments:
--   field: String containing comma-separated CURIEs (e.g., "hmdb:HMDB0000001, chebi:123, hmdb:HMDB0000001")
--   prefix: ID type prefix to match (e.g., "hmdb", "chebi", "LIPIDMAPS")
--
-- Returns: Array of distinct ID values, or NULL if not found
--
-- Usage examples:
--   extract_ids('hmdb:HMDB0000001, chebi:123', 'hmdb') -> ['HMDB0000001']
--   extract_ids('hmdb:HMDB0000001, hmdb:HMDB0000002', 'hmdb') -> ['HMDB0000001', 'HMDB0000002']
--   extract_ids('hmdb:HMDB0000001, hmdb:HMDB0000001, chebi:123', 'hmdb') -> ['HMDB0000001'] (deduplicated)
--   extract_ids('swisslipids:SLM:000012241, chebi:123', 'swisslipids') -> ['SLM:000012241']
CREATE OR REPLACE MACRO extract_ids(field, prefix) AS (
    CASE
        WHEN field IS NULL OR TRIM(field) = '' THEN NULL
        WHEN field LIKE '%' || prefix || ':%' THEN (
            SELECT CASE
                WHEN ids IS NULL OR len(ids) = 0 THEN NULL
                ELSE ids  -- Return all distinct IDs as array
            END
            FROM (
                SELECT list_distinct(
                    list_transform(
                        regexp_extract_all(field, prefix || ':([^,]+)', 1),
                        id -> TRIM(id)
                    )
                ) AS ids
            )
        )
        ELSE NULL
    END
);

-- =====================================================
-- IDENTIFIER CLEANING FUNCTIONS
-- =====================================================

-- Clean InChIKey by removing InChIKey= prefix
CREATE OR REPLACE MACRO clean_inchikey(inchikey_value) AS
    CASE
        WHEN inchikey_value IS NULL THEN NULL
        WHEN TRIM(inchikey_value) = '' THEN NULL
        WHEN UPPER(TRIM(inchikey_value)) LIKE 'INCHIKEY=%' THEN SUBSTRING(TRIM(inchikey_value), 10)
        ELSE TRIM(inchikey_value)
    END;
