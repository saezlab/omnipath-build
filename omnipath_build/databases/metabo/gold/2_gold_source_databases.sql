-- Gold layer source databases table
-- Populated from metadata.resources

-- 1) Source databases reference table
CREATE TABLE IF NOT EXISTS gold.source_databases (
  database_id   SERIAL PRIMARY KEY,
  database_name VARCHAR(50) UNIQUE NOT NULL,
  database_url  VARCHAR(255),
  description   TEXT,
  created_at    TIMESTAMP DEFAULT NOW()
);

-- 2) Ingestion: Populate from metadata.resources
INSERT INTO gold.source_databases (database_name, description)
SELECT
  name,
  description
FROM metadata.resources
ON CONFLICT (database_name) DO UPDATE SET
  description = EXCLUDED.description;

-- 3) Indexes
