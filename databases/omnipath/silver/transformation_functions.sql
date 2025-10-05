-- Transformation functions for metabo silver layer
-- Define SQL functions that will be used to transform data from bronze to silver

-- Normalize RaMP source IDs to consistent separator format
CREATE OR REPLACE MACRO normalize_ramp_source_ids(sources_field) AS
    CASE
        WHEN sources_field IS NULL OR sources_field = '' THEN NULL
        ELSE replace(replace(sources_field, ', ', '|'), ',', '|')
    END;

CREATE OR REPLACE MACRO extract_ids(field, prefix) AS
    CASE
        WHEN field IS NULL THEN NULL
        WHEN field LIKE '%' || prefix || ':%' THEN
            list_distinct(
                regexp_extract_all(
                    field,
                    prefix || ':([A-Za-z0-9_-]+)',
                    1
                )
            )
        ELSE NULL
    END;

-- =====================================================
-- ID CONSOLIDATION FUNCTIONS
-- =====================================================

-- Resource-specific compound ID combination functions
-- Each takes exactly the number of fields that resource provides

CREATE OR REPLACE MACRO combine_compound_ids_hmdb(chebi_id, accession, pubchem_compound_id, kegg_id, drugbank_id, cas_registry_number, synonyms) AS
    CONCAT_WS('|',
        CASE WHEN chebi_id IS NOT NULL AND chebi_id != '' THEN 'CHEBI:' || chebi_id ELSE NULL END,
        CASE WHEN accession IS NOT NULL AND accession != '' THEN 'HMDB:' || accession ELSE NULL END,
        CASE WHEN pubchem_compound_id IS NOT NULL AND pubchem_compound_id != '' THEN 'PUBCHEM:' || pubchem_compound_id ELSE NULL END,
        CASE WHEN kegg_id IS NOT NULL AND kegg_id != '' THEN 'KEGG:' || kegg_id ELSE NULL END,
        CASE WHEN drugbank_id IS NOT NULL AND drugbank_id != '' THEN 'DRUGBANK:' || drugbank_id ELSE NULL END,
        CASE WHEN cas_registry_number IS NOT NULL AND cas_registry_number != '' THEN 'CAS:' || cas_registry_number ELSE NULL END,
        CASE WHEN synonyms IS NOT NULL AND len(synonyms) > 0 THEN 'SYNONYM:' || replace(array_to_string(synonyms, ','), ',', '|SYNONYM:') ELSE NULL END
    );

CREATE OR REPLACE MACRO build_identifier_list_hmdb(
    chebi_id,
    pubchem_compound_id,
    kegg_id,
    drugbank_id,
    cas_registry_number,
    inchikey
) AS (
    WITH cleaned AS (
        SELECT
            chebi_id,
            pubchem_compound_id,
            kegg_id,
            drugbank_id,
            cas_registry_number,
            clean_inchikey(inchikey) AS clean_inchikey_val
    )
    SELECT CASE
        WHEN len(identifier_list) = 0 THEN NULL
        ELSE to_json(list_distinct(identifier_list))
    END
    FROM (
        SELECT list_filter([
            CASE
                WHEN chebi_id IS NOT NULL AND TRIM(chebi_id) != '' THEN struct_pack(
                    type := 'chebi',
                    value := CASE
                        WHEN UPPER(chebi_id) LIKE 'CHEBI:%' THEN chebi_id
                        ELSE 'CHEBI:' || chebi_id
                    END
                )
            END,
            CASE
                WHEN pubchem_compound_id IS NOT NULL AND TRIM(pubchem_compound_id) != '' THEN struct_pack(
                    type := 'pubchem',
                    value := CASE
                        WHEN UPPER(pubchem_compound_id) LIKE 'PUBCHEM:%' THEN pubchem_compound_id
                        ELSE 'PUBCHEM:' || pubchem_compound_id
                    END
                )
            END,
            CASE
                WHEN kegg_id IS NOT NULL AND TRIM(kegg_id) != '' THEN struct_pack(
                    type := 'kegg',
                    value := CASE
                        WHEN UPPER(kegg_id) LIKE 'KEGG:%' THEN kegg_id
                        ELSE 'KEGG:' || kegg_id
                    END
                )
            END,
            CASE
                WHEN drugbank_id IS NOT NULL AND TRIM(drugbank_id) != '' THEN struct_pack(
                    type := 'drugbank',
                    value := CASE
                        WHEN UPPER(drugbank_id) LIKE 'DRUGBANK:%' THEN drugbank_id
                        ELSE 'DRUGBANK:' || drugbank_id
                    END
                )
            END,
            CASE
                WHEN cas_registry_number IS NOT NULL AND TRIM(cas_registry_number) != '' THEN struct_pack(
                    type := 'cas',
                    value := TRIM(cas_registry_number)
                )
            END,
            CASE
                WHEN clean_inchikey_val IS NOT NULL THEN struct_pack(
                    type := 'inchikey',
                    value := clean_inchikey_val
                )
            END
        ], x -> x IS NOT NULL) AS identifier_list
        FROM cleaned
    )
);

CREATE OR REPLACE MACRO combine_compound_ids_lipidmaps(id, chebi, pubchem, name, synonyms) AS
    CONCAT_WS('|',
        CASE WHEN id IS NOT NULL AND id != '' THEN 'LIPIDMAPS:' || id ELSE NULL END,
        CASE WHEN chebi IS NOT NULL AND chebi != '' THEN
            CASE WHEN chebi LIKE 'CHEBI:%' THEN chebi ELSE 'CHEBI:' || chebi END
        ELSE NULL END,
        CASE WHEN pubchem IS NOT NULL AND pubchem != '' THEN 'PUBCHEM:' || pubchem ELSE NULL END,
        CASE WHEN name IS NOT NULL AND name != '' THEN 'NAME:' || name ELSE NULL END,
        CASE WHEN synonyms IS NOT NULL AND synonyms != '' THEN 'SYNONYM:' || replace(synonyms, '; ', '|SYNONYM:') ELSE NULL END
    );

CREATE OR REPLACE MACRO combine_compound_ids_ramp(ramp_id, sources, common_name, synonyms) AS
    CONCAT_WS('|',
        CASE WHEN ramp_id IS NOT NULL AND ramp_id != '' THEN 'RAMP:' || ramp_id ELSE NULL END,
        CASE WHEN sources IS NOT NULL AND sources != '' THEN
            normalize_ramp_source_ids(sources)
        ELSE NULL END,
        CASE WHEN common_name IS NOT NULL AND common_name != '' THEN 'NAME:' || common_name ELSE NULL END,
        CASE WHEN synonyms IS NOT NULL AND synonyms != '' THEN 'SYNONYM:' || replace(synonyms, ', ', '|SYNONYM:') ELSE NULL END
    );

CREATE OR REPLACE MACRO combine_compound_ids_swisslipids(id, chebi, lipidmaps, hmdb, metanetx, name, synonyms) AS
    CONCAT_WS('|',
        CASE WHEN id IS NOT NULL AND id != '' THEN 'SWISSLIPIDS:' || id ELSE NULL END,
        CASE WHEN chebi IS NOT NULL AND chebi != '' THEN
            CASE WHEN chebi LIKE 'CHEBI:%' THEN chebi ELSE 'CHEBI:' || chebi END
        ELSE NULL END,
        CASE WHEN lipidmaps IS NOT NULL AND lipidmaps != '' THEN 'LIPIDMAPS:' || lipidmaps ELSE NULL END,
        CASE WHEN hmdb IS NOT NULL AND hmdb != '' THEN
            CASE WHEN hmdb LIKE 'HMDB:%' THEN hmdb ELSE 'HMDB:' || hmdb END
        ELSE NULL END,
        CASE WHEN metanetx IS NOT NULL AND metanetx != '' THEN 'METANETX:' || metanetx ELSE NULL END,
        CASE WHEN name IS NOT NULL AND name != '' THEN 'NAME:' || name ELSE NULL END,
        CASE WHEN synonyms IS NOT NULL AND synonyms != '' THEN 'SYNONYM:' || replace(synonyms, ', ', '|SYNONYM:') ELSE NULL END
    );

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

CREATE OR REPLACE MACRO parse_prefixed_identifier_list(list_text) AS (
    CASE
        WHEN list_text IS NULL OR TRIM(list_text) = '' THEN CAST(list_value() AS STRUCT(type VARCHAR, value VARCHAR)[])
        ELSE list_filter(
            list_transform(
                str_split(replace(list_text, ', ', ','), ','),
                entry -> CASE
                    WHEN TRIM(entry) = '' THEN NULL
                    ELSE struct_pack(
                        type := lower(split_part(TRIM(entry), ':', 1)),
                        value := CASE
                            WHEN split_part(TRIM(entry), ':', 2) = '' THEN TRIM(entry)
                            ELSE UPPER(split_part(TRIM(entry), ':', 1)) || ':' || split_part(TRIM(entry), ':', 2)
                        END
                    )
                END
            ),
            x -> x IS NOT NULL
        )
    END
);

CREATE OR REPLACE MACRO build_identifier_list_lipidmaps(
    chebi,
    pubchem,
    inchikey,
    inchi
) AS (
    WITH cleaned AS (
        SELECT
            chebi,
            pubchem,
            clean_inchikey(inchikey) AS clean_inchikey_val,
            clean_inchi(inchi) AS clean_inchi_val
    )
    SELECT CASE
        WHEN len(identifier_list) = 0 THEN NULL
        ELSE to_json(list_distinct(identifier_list))
    END
    FROM (
        SELECT list_filter([
            CASE
                WHEN chebi IS NOT NULL AND TRIM(chebi) != '' THEN struct_pack(
                    type := 'chebi',
                    value := CASE WHEN UPPER(chebi) LIKE 'CHEBI:%' THEN chebi ELSE 'CHEBI:' || chebi END
                )
            END,
            CASE
                WHEN pubchem IS NOT NULL AND TRIM(pubchem) != '' THEN struct_pack(
                    type := 'pubchem',
                    value := CASE WHEN UPPER(pubchem) LIKE 'PUBCHEM:%' THEN pubchem ELSE 'PUBCHEM:' || pubchem END
                )
            END,
            CASE
                WHEN clean_inchikey_val IS NOT NULL THEN struct_pack(
                    type := 'inchikey',
                    value := clean_inchikey_val
                )
            END,
            CASE
                WHEN clean_inchi_val IS NOT NULL THEN struct_pack(
                    type := 'inchi',
                    value := clean_inchi_val
                )
            END
        ], x -> x IS NOT NULL) AS identifier_list
        FROM cleaned
    )
);

CREATE OR REPLACE MACRO build_identifier_list_ramp(
    ramp_id,
    sources,
    chem_data_source,
    chem_source_id,
    inchi_key,
    inchi
) AS (
    WITH cleaned AS (
        SELECT
            ramp_id,
            sources,
            chem_data_source,
            chem_source_id,
            clean_inchikey(inchi_key) AS clean_inchikey_val,
            clean_inchi(inchi) AS clean_inchi_val
    )
    SELECT CASE
        WHEN len(identifier_list) = 0 THEN NULL
        ELSE to_json(list_distinct(identifier_list))
    END
    FROM (
        SELECT list_filter(
            list_concat(
                base_entries,
                parse_prefixed_identifier_list(sources)
            ),
            x -> x IS NOT NULL
        ) AS identifier_list
        FROM (
            SELECT list_filter([
                CASE
                    WHEN ramp_id IS NOT NULL AND TRIM(ramp_id) != '' THEN struct_pack(
                        type := 'ramp',
                        value := TRIM(ramp_id)
                    )
                END,
                CASE
                    WHEN chem_data_source IS NOT NULL AND TRIM(chem_data_source) != ''
                         AND chem_source_id IS NOT NULL AND TRIM(chem_source_id) != '' THEN struct_pack(
                        type := lower(TRIM(chem_data_source)),
                        value := CASE
                            WHEN UPPER(TRIM(chem_data_source)) LIKE 'HMDB' THEN 'HMDB:' || TRIM(chem_source_id)
                            WHEN UPPER(TRIM(chem_data_source)) LIKE 'CHEBI' THEN 'CHEBI:' || TRIM(chem_source_id)
                            WHEN UPPER(TRIM(chem_data_source)) LIKE 'PUBCHEM' THEN 'PUBCHEM:' || TRIM(chem_source_id)
                            ELSE TRIM(chem_source_id)
                        END
                    )
                END,
                CASE
                    WHEN clean_inchikey_val IS NOT NULL THEN struct_pack(
                        type := 'inchikey',
                        value := clean_inchikey_val
                    )
                END,
                CASE
                    WHEN clean_inchi_val IS NOT NULL THEN struct_pack(
                        type := 'inchi',
                        value := clean_inchi_val
                    )
                END
            ], x -> x IS NOT NULL) AS base_entries
            FROM cleaned
        )
    )
);

CREATE OR REPLACE MACRO build_identifier_list_swisslipids(
    chebi,
    lipidmaps,
    hmdb,
    metanetx,
    inchikey,
    inchi
) AS (
    WITH cleaned AS (
        SELECT
            chebi,
            lipidmaps,
            hmdb,
            metanetx,
            clean_inchikey(inchikey) AS clean_inchikey_val,
            clean_inchi(inchi) AS clean_inchi_val
    )
    SELECT CASE
        WHEN len(identifier_list) = 0 THEN NULL
        ELSE to_json(list_distinct(identifier_list))
    END
    FROM (
        SELECT list_filter([
            CASE
                WHEN chebi IS NOT NULL AND chebi != '' AND TRIM(chebi) NOT IN ('', 'CHEBI:', 'chebi:') THEN struct_pack(
                    type := 'chebi',
                    value := CASE WHEN UPPER(chebi) LIKE 'CHEBI:%' THEN chebi ELSE 'CHEBI:' || chebi END
                )
            END,
            CASE
                WHEN lipidmaps IS NOT NULL AND TRIM(lipidmaps) != '' THEN struct_pack(
                    type := 'lipidmaps',
                    value := CASE WHEN UPPER(lipidmaps) LIKE 'LIPIDMAPS:%' THEN lipidmaps ELSE 'LIPIDMAPS:' || lipidmaps END
                )
            END,
            CASE
                WHEN hmdb IS NOT NULL AND TRIM(hmdb) != '' THEN struct_pack(
                    type := 'hmdb',
                    value := CASE WHEN UPPER(hmdb) LIKE 'HMDB:%' THEN hmdb ELSE 'HMDB:' || hmdb END
                )
            END,
            CASE
                WHEN metanetx IS NOT NULL AND TRIM(metanetx) != '' THEN struct_pack(
                    type := 'metanetx',
                    value := CASE WHEN UPPER(metanetx) LIKE 'METANETX:%' THEN metanetx ELSE 'METANETX:' || metanetx END
                )
            END,
            CASE
                WHEN clean_inchikey_val IS NOT NULL THEN struct_pack(
                    type := 'inchikey',
                    value := clean_inchikey_val
                )
            END,
            CASE
                WHEN clean_inchi_val IS NOT NULL THEN struct_pack(
                    type := 'inchi',
                    value := clean_inchi_val
                )
            END
        ], x -> x IS NOT NULL) AS identifier_list
        FROM cleaned
    )
);

CREATE OR REPLACE MACRO build_lipidmaps_name_variants(synonyms, abbreviation) AS (
    SELECT CASE
        WHEN len(name_list) = 0 THEN NULL
        ELSE to_json(list_distinct(name_list))
    END
    FROM (
        SELECT list_filter(
            list_concat(
                CASE
                    WHEN abbreviation IS NOT NULL AND TRIM(abbreviation) != '' THEN list_value(TRIM(abbreviation))
                    ELSE CAST(list_value() AS VARCHAR[])
                END,
                CASE
                    WHEN synonyms IS NOT NULL AND TRIM(synonyms) != '' THEN list_filter(
                        list_transform(str_split(synonyms, '; '), x -> TRIM(x)),
                        x -> x IS NOT NULL AND x != ''
                    )
                    ELSE CAST(list_value() AS VARCHAR[])
                END
            ),
            x -> x IS NOT NULL AND x != ''
        ) AS name_list
    )
);

CREATE OR REPLACE MACRO build_swisslipids_name_variants(synonyms, abbreviation) AS (
    SELECT CASE
        WHEN len(name_list) = 0 THEN NULL
        ELSE to_json(list_distinct(name_list))
    END
    FROM (
        SELECT list_filter(
            list_concat(
                CASE
                    WHEN abbreviation IS NOT NULL AND TRIM(abbreviation) != '' THEN list_value(TRIM(abbreviation))
                    ELSE CAST(list_value() AS VARCHAR[])
                END,
                CASE
                    WHEN synonyms IS NOT NULL AND TRIM(synonyms) != '' THEN list_filter(
                        list_transform(str_split(synonyms, '; '), x -> TRIM(x)),
                        x -> x IS NOT NULL AND x != ''
                    )
                    ELSE CAST(list_value() AS VARCHAR[])
                END
            ),
            x -> x IS NOT NULL AND x != ''
        ) AS name_list
    )
);

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
-- IDENTIFIER CLEANING FUNCTIONS
-- =====================================================

-- Clean and validate InChI identifiers
CREATE OR REPLACE MACRO clean_inchi(inchi_value) AS
    CASE
        WHEN inchi_value IS NULL THEN NULL
        WHEN TRIM(inchi_value) = '' THEN NULL
        WHEN UPPER(TRIM(inchi_value)) IN ('INCHI=NONE', 'NONE', 'N/A', 'NA') THEN NULL
        WHEN TRIM(inchi_value) = 'InChI=' THEN NULL
        ELSE TRIM(inchi_value)
    END;

-- Clean and validate InChIKey identifiers
CREATE OR REPLACE MACRO clean_inchikey(inchikey_value) AS
    CASE
        WHEN inchikey_value IS NULL THEN NULL
        WHEN TRIM(inchikey_value) = '' THEN NULL
        WHEN UPPER(TRIM(inchikey_value)) IN ('NONE', 'N/A', 'NA') THEN NULL
        ELSE TRIM(inchikey_value)
    END;

-- =====================================================
-- PMID FORMATTING FUNCTIONS
-- =====================================================

-- Format PMIDs from various formats to pipe-delimited string or NULL
-- Handles both array types and string types
CREATE OR REPLACE MACRO format_pmids(pmids_field) AS
    CASE
        -- Handle NULL input
        WHEN pmids_field IS NULL THEN NULL
        -- Check if it's an array by trying to get its length
        WHEN len(pmids_field) >= 0 THEN
            CASE
                -- Empty array
                WHEN len(pmids_field) = 0 THEN NULL
                -- Array with single empty string or NULL
                WHEN len(pmids_field) = 1 AND (pmids_field[1] = '' OR pmids_field[1] IS NULL) THEN NULL
                -- Valid array - filter and join
                ELSE
                    CASE
                        WHEN len(list_filter(pmids_field, x -> x IS NOT NULL AND x != '')) = 0 THEN NULL
                        ELSE array_to_string(list_filter(pmids_field, x -> x IS NOT NULL AND x != ''), '|')
                    END
            END
        ELSE NULL
    END;
