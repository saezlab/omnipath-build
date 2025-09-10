-- This query will be executed by DuckDB and written to gold/data/network_metrics.parquet
-- Gold network metrics table - Overall network statistics
-- Provides high-level metrics about the interaction network


WITH entity_metrics AS (
    SELECT
        COUNT(DISTINCT id) AS total_entities,
        COUNT(DISTINCT CASE WHEN entity_type_id IN (
            SELECT id FROM read_parquet('gold/data/cv_term.parquet') WHERE accession = 'MI:0326'  -- protein
        ) THEN id END) AS protein_count,
        COUNT(DISTINCT CASE WHEN entity_type_id IN (
            SELECT id FROM read_parquet('gold/data/cv_term.parquet') WHERE accession = 'MI:0314'  -- complex
        ) THEN id END) AS complex_count,
        COUNT(DISTINCT CASE WHEN entity_type_id IN (
            SELECT id FROM read_parquet('gold/data/cv_term.parquet') WHERE accession = 'MI:0328'  -- small molecule
        ) THEN id END) AS small_molecule_count,
        COUNT(DISTINCT CASE WHEN entity_type_id IN (
            SELECT id FROM read_parquet('gold/data/cv_term.parquet') WHERE accession = 'MI:0250'  -- gene
        ) THEN id END) AS gene_count
    FROM read_parquet('gold/data/entity.parquet') AS entity
),

interaction_metrics AS (
    SELECT
        COUNT(DISTINCT ic.id) AS total_interactions,
        COUNT(DISTINCT CASE WHEN ie.is_directed THEN ic.id END) AS directed_interactions,
        AVG(evidence_per_interaction) AS avg_evidence_per_interaction,
        AVG(sources_per_interaction) AS avg_sources_per_interaction,
        AVG(publications_per_interaction) AS avg_publications_per_interaction
    FROM read_parquet('gold/data/interaction_canonical.parquet') AS ic
    LEFT JOIN (
        SELECT 
            interaction_id,
            COUNT(*) as evidence_per_interaction,
            COUNT(DISTINCT data_source_id) as sources_per_interaction,
            COUNT(DISTINCT reference_id) as publications_per_interaction,
            BOOL_OR(is_directed) as is_directed
        FROM read_parquet('gold/data/interaction_evidence.parquet')
        GROUP BY interaction_id
    ) ie ON ic.id = ie.interaction_id
),

source_metrics AS (
    SELECT
        COUNT(DISTINCT data_source) AS total_data_sources,
        STRING_AGG(DISTINCT data_source, '|' ORDER BY data_source) AS data_sources_list
    FROM read_parquet('silver/data/interactions/*.parquet') AS interactions
),

reference_metrics AS (
    SELECT
        COUNT(DISTINCT id) AS total_references
    FROM read_parquet('gold/data/reference.parquet') AS reference
),

quality_metrics AS (
    SELECT
        COUNT(DISTINCT e.id) AS entities_without_interactions
    FROM read_parquet('gold/data/entity.parquet') AS e
    LEFT JOIN read_parquet('gold/data/entity_interaction_stats.parquet') AS eis ON e.id = eis.entity_id
    WHERE eis.interaction_count = 0 OR eis.interaction_count IS NULL
)

SELECT
    1 AS id,
    -- Entity metrics
    em.total_entities,
    em.protein_count,
    em.complex_count,
    em.small_molecule_count,
    em.gene_count,
    -- Interaction metrics
    im.total_interactions,
    im.directed_interactions,
    ROUND(im.avg_evidence_per_interaction, 2) AS avg_evidence_per_interaction,
    ROUND(im.avg_sources_per_interaction, 2) AS avg_sources_per_interaction,
    ROUND(im.avg_publications_per_interaction, 2) AS avg_publications_per_interaction,
    -- Source and reference metrics
    sm.total_data_sources,
    sm.data_sources_list,
    rm.total_references,
    -- Quality metrics
    qm.entities_without_interactions,
    ROUND(100.0 * qm.entities_without_interactions / em.total_entities, 2) AS percent_entities_without_interactions,
    -- Calculated metrics
    ROUND(2.0 * im.total_interactions / em.total_entities, 2) AS avg_degree,
    CURRENT_TIMESTAMP AS last_updated
FROM entity_metrics em
CROSS JOIN interaction_metrics im
CROSS JOIN source_metrics sm
CROSS JOIN reference_metrics rm
CROSS JOIN quality_metrics qm;
