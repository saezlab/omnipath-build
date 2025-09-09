-- Transformation functions for metabo silver layer
-- Define SQL functions that will be used to transform data from bronze to silver

-- Example function:
-- CREATE OR REPLACE FUNCTION clean_protein_name(raw_name TEXT)
-- RETURNS TEXT AS $$
-- BEGIN
--     RETURN TRIM(UPPER(raw_name));
-- END;
-- $$ LANGUAGE plpgsql;

-- =====================================================
-- PATTERN EXTRACTION
-- =====================================================

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
