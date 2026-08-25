-- Project the staged pairs onto the canonical metabolite layer, derive
-- `set_size`, and upsert one resource into the substrate.
--
-- Every resource reaches the table through this file. The extraction differs
-- per resource; the publication does not.

DROP TABLE IF EXISTS metsigdb_projection;

CREATE TEMP TABLE metsigdb_projection AS
SELECT e.entity_id,
       -- The row contract requires a label. Measured coverage is 100%, and the
       -- fallback keeps the column honest rather than empty if that changes.
       coalesce(e.label, e.canonical_identifier) AS metabolite_label,
       vt.name                                   AS metabolite_entity_type,
       max(v.value) FILTER (WHERE it.name = 'Standard Inchi Key:MI:1101') AS inchikey,
       max(v.value) FILTER (WHERE it.name = 'Smiles:MI:0239')             AS smiles,
       max(v.value) FILTER (WHERE it.name = 'Hmdb:OM:0004')               AS hmdb,
       max(v.value) FILTER (WHERE it.name = 'Pubchem Compound:OM:0002')   AS pubchem,
       max(v.value) FILTER (WHERE it.name = 'Chebi:MI:0474')              AS chebi,
       max(v.value) FILTER (WHERE it.name = 'Kegg Compound:MI:2012')      AS kegg
FROM (SELECT DISTINCT metabolite_entity_id FROM metsigdb_stage) s
JOIN entity e ON e.entity_id = s.metabolite_entity_id
JOIN vocab_entity_type vt ON vt.entity_type_id = e.entity_type_id
LEFT JOIN entity_identifier_lookup l ON l.entity_id = e.entity_id
LEFT JOIN identifier_evidence v ON v.identifier_id = l.identifier_id
LEFT JOIN vocab_identifier_type it ON it.identifier_type_id = v.identifier_type_id
GROUP BY e.entity_id, coalesce(e.label, e.canonical_identifier), vt.name;

CREATE INDEX ON metsigdb_projection (entity_id);

ANALYZE metsigdb_projection;
