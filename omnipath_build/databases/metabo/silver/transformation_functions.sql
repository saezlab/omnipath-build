-- Transformation functions for metabo silver layer
-- Define SQL functions that will be used to transform data from bronze to silver

-- Example function:
-- CREATE OR REPLACE FUNCTION clean_protein_name(raw_name TEXT)
-- RETURNS TEXT AS $$
-- BEGIN
--     RETURN TRIM(UPPER(raw_name));
-- END;
-- $$ LANGUAGE plpgsql;
