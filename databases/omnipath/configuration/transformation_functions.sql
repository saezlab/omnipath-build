-- Transformation functions for metabo silver layer
-- Define SQL functions that will be used to transform data from bronze to silver

-- =====================================================
-- CORE NORMALIZATION FUNCTIONS
-- =====================================================

-- Normalize separators: collapse any of [ , ; | ] (with spaces) into a single |
CREATE OR REPLACE MACRO normalize_separators(s) AS
    NULLIF(regexp_replace(TRIM(s), '\s*[,;|]\s*', '|', 'g'), '');

-- Normalize a single identifier by namespace with regex
CREATE OR REPLACE MACRO normalize_id(ns, raw) AS (
    CASE
        WHEN raw IS NULL OR TRIM(raw) = '' THEN NULL

        WHEN lower(ns) = 'hmdb' THEN
            CASE
                WHEN regexp_extract(TRIM(raw), '(?i)^(?:HMDB:)?(?:HMDB)?(\d{5,7})$', 1) IS NOT NULL
                     AND regexp_extract(TRIM(raw), '(?i)^(?:HMDB:)?(?:HMDB)?(\d{5,7})$', 1) != ''
                    THEN struct_pack(
                        type := 'hmdb',
                        value := 'HMDB:' || regexp_extract(TRIM(raw), '(?i)^(?:HMDB:)?(?:HMDB)?(\d{5,7})$', 1)
                    )
            END

        WHEN lower(ns) = 'chebi' THEN
            CASE
                WHEN regexp_extract(TRIM(raw), '(?i)^(?:CHEBI:)?(\d+)$', 1) IS NOT NULL
                     AND regexp_extract(TRIM(raw), '(?i)^(?:CHEBI:)?(\d+)$', 1) != ''
                    THEN struct_pack(
                        type := 'chebi',
                        value := 'CHEBI:' || regexp_extract(TRIM(raw), '(?i)^(?:CHEBI:)?(\d+)$', 1)
                    )
            END

        WHEN lower(ns) = 'pubchem' THEN
            CASE
                WHEN regexp_extract(TRIM(raw), '(?i)^(?:PUBCHEM:|CID:?)?(\d+)$', 1) IS NOT NULL
                     AND regexp_extract(TRIM(raw), '(?i)^(?:PUBCHEM:|CID:?)?(\d+)$', 1) != ''
                    THEN struct_pack(
                        type := 'pubchem',
                        value := 'PUBCHEM:' || regexp_extract(TRIM(raw), '(?i)^(?:PUBCHEM:|CID:?)?(\d+)$', 1)
                    )
            END

        WHEN lower(ns) = 'kegg' THEN
            CASE
                WHEN regexp_extract(TRIM(raw), '(?i)^(?:KEGG:)?([CD]\d{5})$', 1) IS NOT NULL
                     AND regexp_extract(TRIM(raw), '(?i)^(?:KEGG:)?([CD]\d{5})$', 1) != ''
                    THEN struct_pack(
                        type := 'kegg',
                        value := 'KEGG:' || upper(regexp_extract(TRIM(raw), '(?i)^(?:KEGG:)?([CD]\d{5})$', 1))
                    )
            END

        WHEN lower(ns) = 'drugbank' THEN
            CASE
                WHEN regexp_extract(TRIM(raw), '(?i)^(?:DRUGBANK:)?(DB\d{5})$', 1) IS NOT NULL
                     AND regexp_extract(TRIM(raw), '(?i)^(?:DRUGBANK:)?(DB\d{5})$', 1) != ''
                    THEN struct_pack(
                        type := 'drugbank',
                        value := 'DRUGBANK:' || upper(regexp_extract(TRIM(raw), '(?i)^(?:DRUGBANK:)?(DB\d{5})$', 1))
                    )
            END

        WHEN lower(ns) = 'cas' THEN
            CASE
                WHEN regexp_extract(TRIM(raw), '^(\d{2,7}-\d{2}-\d)$', 1) IS NOT NULL
                     AND regexp_extract(TRIM(raw), '^(\d{2,7}-\d{2}-\d)$', 1) != ''
                    THEN struct_pack(
                        type := 'cas',
                        value := regexp_extract(TRIM(raw), '^(\d{2,7}-\d{2}-\d)$', 1)
                    )
            END

        WHEN lower(ns) = 'inchikey' THEN
            CASE
                WHEN regexp_extract(upper(TRIM(raw)), '^(?:INCHIKEY=)?([A-Z]{14}-[A-Z]{10}-[A-Z])$', 1) IS NOT NULL
                     AND regexp_extract(upper(TRIM(raw)), '^(?:INCHIKEY=)?([A-Z]{14}-[A-Z]{10}-[A-Z])$', 1) != ''
                    THEN struct_pack(
                        type := 'inchikey',
                        value := regexp_extract(upper(TRIM(raw)), '^(?:INCHIKEY=)?([A-Z]{14}-[A-Z]{10}-[A-Z])$', 1)
                    )
            END

        WHEN lower(ns) = 'inchi' THEN
            CASE
                WHEN upper(TRIM(raw)) IN ('INCHI=NONE','NONE','N/A','NA') OR TRIM(raw) = 'InChI=' THEN NULL
                WHEN regexp_extract(TRIM(raw), '(?i)^InChI=(.+)$', 1) IS NOT NULL
                    THEN struct_pack(
                        type := 'inchi',
                        value := 'InChI=' || regexp_extract(TRIM(raw), '(?i)^InChI=(.+)$', 1)
                    )
                ELSE struct_pack(type := 'inchi', value := TRIM(raw))
            END

        WHEN lower(ns) = 'lipidmaps' THEN
            CASE
                WHEN regexp_extract(upper(TRIM(raw)), '(?i)^(?:LIPIDMAPS:)?(LM[A-Z]{2}\d{8})$', 1) IS NOT NULL
                     AND regexp_extract(upper(TRIM(raw)), '(?i)^(?:LIPIDMAPS:)?(LM[A-Z]{2}\d{8})$', 1) != ''
                    THEN struct_pack(
                        type := 'lipidmaps',
                        value := 'LIPIDMAPS:' || regexp_extract(upper(TRIM(raw)), '(?i)^(?:LIPIDMAPS:)?(LM[A-Z]{2}\d{8})$', 1)
                    )
            END

        WHEN lower(ns) = 'swisslipids' THEN
            CASE
                WHEN regexp_extract(TRIM(raw), '(?i)^(?:SWISSLIPIDS:|SLM:)?(\d{6,})$', 1) IS NOT NULL
                     AND regexp_extract(TRIM(raw), '(?i)^(?:SWISSLIPIDS:|SLM:)?(\d{6,})$', 1) != ''
                    THEN struct_pack(
                        type := 'swisslipids',
                        value := 'SWISSLIPIDS:' || regexp_extract(TRIM(raw), '(?i)^(?:SWISSLIPIDS:|SLM:)?(\d{6,})$', 1)
                    )
            END

        WHEN lower(ns) = 'metanetx' THEN
            CASE
                WHEN regexp_extract(TRIM(raw), '(?i)^(?:METANETX:)?(MNXM\d+)$', 1) IS NOT NULL
                     AND regexp_extract(TRIM(raw), '(?i)^(?:METANETX:)?(MNXM\d+)$', 1) != ''
                    THEN struct_pack(
                        type := 'metanetx',
                        value := 'METANETX:' || upper(regexp_extract(TRIM(raw), '(?i)^(?:METANETX:)?(MNXM\d+)$', 1))
                    )
            END

        WHEN lower(ns) = 'ramp' THEN
            CASE WHEN TRIM(raw) != '' THEN struct_pack(type := 'ramp', value := TRIM(raw)) END
    END
);

-- Parse a prefixed list like "hmdb:123, pubchem:45 | chebi:7"
CREATE OR REPLACE MACRO parse_prefixed_identifier_list(text) AS (
    CASE
        WHEN text IS NULL OR TRIM(text) = '' THEN CAST(list_value() AS STRUCT(type VARCHAR, value VARCHAR)[])
        ELSE list_filter(
            list_transform(
                str_split(normalize_separators(text), '|'),
                entry -> CASE
                    WHEN TRIM(entry) = '' THEN NULL
                    WHEN strpos(entry, ':') > 0 THEN
                        normalize_id(split_part(entry, ':', 1), substring(entry, strpos(entry, ':') + 1))
                    ELSE NULL
                END
            ),
            x -> x IS NOT NULL
        )
    END
);

-- =====================================================
-- ID CONSOLIDATION FUNCTIONS
-- =====================================================

CREATE OR REPLACE MACRO build_identifier_list_hmdb(
    accession,
    chebi_id,
    pubchem_compound_id,
    kegg_id,
    drugbank_id,
    cas_registry_number,
    smiles,
    iupac_name,
    traditional_iupac,
    synonyms
) AS (
    SELECT CASE
        WHEN len(identifier_list) = 0 THEN NULL
        ELSE to_json(list_distinct(identifier_list))
    END
    FROM (
        SELECT list_filter(
            list_concat(
                list_filter([
                    normalize_id('hmdb', accession),
                    normalize_id('chebi', chebi_id),
                    normalize_id('pubchem', pubchem_compound_id),
                    normalize_id('kegg', kegg_id),
                    normalize_id('drugbank', drugbank_id),
                    normalize_id('cas', cas_registry_number)
                ], x -> x IS NOT NULL),
                list_concat(
                    CASE
                        WHEN smiles IS NOT NULL AND TRIM(smiles) != '' THEN
                            list_value(struct_pack(type := 'canonical_smiles', value := TRIM(smiles)))
                        ELSE CAST(list_value() AS STRUCT(type VARCHAR, value VARCHAR)[])
                    END,
                    list_concat(
                        CASE
                            WHEN COALESCE(iupac_name, traditional_iupac) IS NOT NULL
                                 AND TRIM(COALESCE(iupac_name, traditional_iupac)) != '' THEN
                                list_value(struct_pack(
                                    type := 'name',
                                    value := TRIM(COALESCE(iupac_name, traditional_iupac))
                                ))
                            ELSE CAST(list_value() AS STRUCT(type VARCHAR, value VARCHAR)[])
                        END,
                        CASE
                            WHEN synonyms IS NOT NULL THEN
                                list_filter(
                                    list_transform(
                                        CAST(synonyms AS VARCHAR[]),
                                        s -> CASE
                                            WHEN s IS NOT NULL AND TRIM(s) != '' THEN
                                                struct_pack(type := 'synonym', value := TRIM(s))
                                        END
                                    ),
                                    x -> x IS NOT NULL
                                )
                            ELSE CAST(list_value() AS STRUCT(type VARCHAR, value VARCHAR)[])
                        END
                    )
                )
            ),
            x -> x IS NOT NULL
        ) AS identifier_list
    )
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

CREATE OR REPLACE MACRO build_identifier_list_lipidmaps(
    lipidmaps_id,
    chebi,
    pubchem,
    inchi,
    smiles,
    name,
    synonyms,
    abbreviation
) AS (
    SELECT CASE
        WHEN len(identifier_list) = 0 THEN NULL
        ELSE to_json(list_distinct(identifier_list))
    END
    FROM (
        SELECT list_filter(
            list_concat(
                list_filter([
                    normalize_id('lipidmaps', lipidmaps_id),
                    normalize_id('chebi', chebi),
                    normalize_id('pubchem', pubchem),
                    normalize_id('inchi', inchi)
                ], x -> x IS NOT NULL),
                list_concat(
                    CASE
                        WHEN smiles IS NOT NULL AND TRIM(smiles) != '' THEN
                            list_value(struct_pack(type := 'canonical_smiles', value := TRIM(smiles)))
                        ELSE CAST(list_value() AS STRUCT(type VARCHAR, value VARCHAR)[])
                    END,
                    list_concat(
                        CASE
                            WHEN name IS NOT NULL AND TRIM(name) != '' THEN
                                list_value(struct_pack(type := 'name', value := TRIM(name)))
                            ELSE CAST(list_value() AS STRUCT(type VARCHAR, value VARCHAR)[])
                        END,
                        list_concat(
                            CASE
                                WHEN abbreviation IS NOT NULL AND TRIM(abbreviation) != '' THEN
                                    list_value(struct_pack(type := 'synonym', value := TRIM(abbreviation)))
                                ELSE CAST(list_value() AS STRUCT(type VARCHAR, value VARCHAR)[])
                            END,
                            CASE
                                WHEN synonyms IS NOT NULL AND TRIM(synonyms) != '' THEN
                                    list_filter(
                                        list_transform(
                                            str_split(synonyms, '; '),
                                            s -> CASE
                                                WHEN s IS NOT NULL AND TRIM(s) != '' THEN
                                                    struct_pack(type := 'synonym', value := TRIM(s))
                                            END
                                        ),
                                        x -> x IS NOT NULL
                                    )
                                ELSE CAST(list_value() AS STRUCT(type VARCHAR, value VARCHAR)[])
                            END
                        )
                    )
                )
            ),
            x -> x IS NOT NULL
        ) AS identifier_list
    )
);

CREATE OR REPLACE MACRO build_identifier_list_ramp(
    ramp_id,
    sources,
    chem_data_source,
    chem_source_id,
    inchi,
    smiles,
    common_name,
    synonyms
) AS (
    SELECT CASE
        WHEN len(identifier_list) = 0 THEN NULL
        ELSE to_json(list_distinct(identifier_list))
    END
    FROM (
        SELECT list_filter(
            list_concat(
                list_filter(
                    list_concat([
                        normalize_id('ramp', ramp_id),
                        normalize_id(chem_data_source, chem_source_id),
                        normalize_id('inchi', inchi)
                    ], parse_prefixed_identifier_list(sources)),
                    x -> x IS NOT NULL
                ),
                list_concat(
                    CASE
                        WHEN smiles IS NOT NULL AND TRIM(smiles) != '' THEN
                            list_value(struct_pack(type := 'canonical_smiles', value := TRIM(smiles)))
                        ELSE CAST(list_value() AS STRUCT(type VARCHAR, value VARCHAR)[])
                    END,
                    list_concat(
                        CASE
                            WHEN common_name IS NOT NULL AND TRIM(common_name) != '' THEN
                                list_value(struct_pack(type := 'name', value := TRIM(common_name)))
                            ELSE CAST(list_value() AS STRUCT(type VARCHAR, value VARCHAR)[])
                        END,
                        CASE
                            WHEN synonyms IS NOT NULL AND TRIM(synonyms) != '' THEN
                                list_filter(
                                    list_transform(
                                        str_split(COALESCE(normalize_separators(synonyms), ''), '|'),
                                        s -> CASE
                                            WHEN s IS NOT NULL AND TRIM(s) != '' THEN
                                                struct_pack(type := 'synonym', value := TRIM(s))
                                        END
                                    ),
                                    x -> x IS NOT NULL
                                )
                            ELSE CAST(list_value() AS STRUCT(type VARCHAR, value VARCHAR)[])
                        END
                    )
                )
            ),
            x -> x IS NOT NULL
        ) AS identifier_list
    )
);

CREATE OR REPLACE MACRO build_identifier_list_swisslipids(
    swisslipids_id,
    chebi,
    lipidmaps,
    hmdb,
    metanetx,
    inchi,
    smiles,
    name,
    synonyms,
    abbreviation
) AS (
    SELECT CASE
        WHEN len(identifier_list) = 0 THEN NULL
        ELSE to_json(list_distinct(identifier_list))
    END
    FROM (
        SELECT list_filter(
            list_concat(
                list_filter([
                    normalize_id('swisslipids', swisslipids_id),
                    normalize_id('chebi', chebi),
                    normalize_id('lipidmaps', lipidmaps),
                    normalize_id('hmdb', hmdb),
                    normalize_id('metanetx', metanetx),
                    normalize_id('inchi', inchi)
                ], x -> x IS NOT NULL),
                list_concat(
                    CASE
                        WHEN smiles IS NOT NULL AND TRIM(smiles) != '' THEN
                            list_value(struct_pack(type := 'canonical_smiles', value := TRIM(smiles)))
                        ELSE CAST(list_value() AS STRUCT(type VARCHAR, value VARCHAR)[])
                    END,
                    list_concat(
                        CASE
                            WHEN name IS NOT NULL AND TRIM(name) != '' THEN
                                list_value(struct_pack(type := 'name', value := TRIM(name)))
                            ELSE CAST(list_value() AS STRUCT(type VARCHAR, value VARCHAR)[])
                        END,
                        list_concat(
                            CASE
                                WHEN abbreviation IS NOT NULL AND TRIM(abbreviation) != '' THEN
                                    list_value(struct_pack(type := 'synonym', value := TRIM(abbreviation)))
                                ELSE CAST(list_value() AS STRUCT(type VARCHAR, value VARCHAR)[])
                            END,
                            CASE
                                WHEN synonyms IS NOT NULL AND TRIM(synonyms) != '' THEN
                                    list_filter(
                                        list_transform(
                                            str_split(synonyms, '; '),
                                            s -> CASE
                                                WHEN s IS NOT NULL AND TRIM(s) != '' THEN
                                                    struct_pack(type := 'synonym', value := TRIM(s))
                                            END
                                        ),
                                        x -> x IS NOT NULL
                                    )
                                ELSE CAST(list_value() AS STRUCT(type VARCHAR, value VARCHAR)[])
                            END
                        )
                    )
                )
            ),
            x -> x IS NOT NULL
        ) AS identifier_list
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

-- Clean InChIKey by removing InChIKey= prefix
CREATE OR REPLACE MACRO clean_inchikey(inchikey_value) AS
    CASE
        WHEN inchikey_value IS NULL THEN NULL
        WHEN TRIM(inchikey_value) = '' THEN NULL
        WHEN UPPER(TRIM(inchikey_value)) LIKE 'INCHIKEY=%' THEN SUBSTRING(TRIM(inchikey_value), 10)
        ELSE TRIM(inchikey_value)
    END;
