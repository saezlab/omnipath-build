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
