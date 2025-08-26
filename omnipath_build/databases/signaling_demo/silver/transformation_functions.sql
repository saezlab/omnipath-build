-- =====================================================
-- SIGNALING DEMO TRANSFORMATION FUNCTIONS
-- =====================================================

-- Simple uppercase transformation for gene symbols
CREATE OR REPLACE MACRO uppercase(field) AS 
    UPPER(field);

-- Simple lowercase transformation
CREATE OR REPLACE MACRO lowercase(field) AS 
    LOWER(field);

-- Convert SPIKE integrity scores to simple confidence (0-1 scale)
CREATE OR REPLACE MACRO spike_confidence(field) AS 
    CAST(field AS DOUBLE) / 10.0;