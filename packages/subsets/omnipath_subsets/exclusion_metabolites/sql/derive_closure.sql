-- Ions and atoms from the chemical ontology: every `subclass_of` descendant of
-- the class's root, the root included.
--
-- The seeds of MetaProViz are a floor. The ontology knows more, and it moves
-- with each release, so the descendants are read here and not typed in. A member
-- the seeds also name gets its own row for this source.
--
-- The depth guard stops a cycle in the source from running away, as in the
-- ClassyFire extraction. The shortest path to each entity wins.

DROP TABLE IF EXISTS exclusion_subclass_edge;

CREATE TEMP TABLE exclusion_subclass_edge AS
SELECT r.subject_entity_id AS child, r.object_entity_id AS parent
FROM entity_ontology_relation r
JOIN vocab_relation_predicate p
  ON p.relation_predicate_id = r.predicate_id AND p.name = 'subclass_of';

CREATE INDEX ON exclusion_subclass_edge (parent);
ANALYZE exclusion_subclass_edge;

INSERT INTO exclusion_metabolite
  (entity_id, exclusion_class, assignment_source, origin, match_kind, seed_namespace,
   seed_identifier, evidence, via_entity_id, list_version, build_id)
WITH RECURSIVE root (exclusion_class, entity_id, curie) AS (
  SELECT * FROM unnest(%(root_classes)s::text[], %(root_entities)s::uuid[], %(root_curies)s::text[])
),
walk (exclusion_class, entity_id, curie, depth) AS (
  SELECT exclusion_class, entity_id, curie, 0 FROM root
  UNION ALL
  SELECT w.exclusion_class, e.child, w.curie, w.depth + 1
  FROM walk w
  JOIN exclusion_subclass_edge e ON e.parent = w.entity_id
  WHERE w.depth < 20
),
nearest AS (
  SELECT DISTINCT ON (exclusion_class, entity_id)
         exclusion_class, entity_id, curie, depth
  FROM walk
  ORDER BY exclusion_class, entity_id, depth, curie
)
SELECT n.entity_id,
       n.exclusion_class,
       'chebi_hierarchy',
       'derived',
       'entity',
       NULL,
       NULL,
       CASE WHEN n.depth = 0 THEN 'the root ' || n.curie
            ELSE 'subclass_of descendant of ' || n.curie || ' at depth ' || n.depth END,
       NULL,
       %(list_version)s,
       %(build_id)s
FROM nearest n
JOIN entity e ON e.entity_id = n.entity_id
JOIN vocab_entity_type vt
  ON vt.entity_type_id = e.entity_type_id AND vt.name = ANY(%(entity_types)s::text[]);

DROP TABLE IF EXISTS exclusion_subclass_edge;
