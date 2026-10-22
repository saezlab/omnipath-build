-- The coverage rows of the report: for each class and namespace, how many seeds
-- found an entity, how many found only a name-identified one, and which found
-- nothing.
--
-- The seed counts come from the resolution, which runs in Python so that one
-- spelling rule serves both the build and its tests. This file writes them.

INSERT INTO exclusion_metabolite_report
  (kind, exclusion_class, namespace, resolved, fallback_only, unresolved, detail, build_id)
SELECT 'seed_coverage',
       c.exclusion_class,
       c.namespace,
       c.resolved,
       c.fallback_only,
       c.unresolved,
       c.detail,
       %(build_id)s
FROM jsonb_to_recordset(%(coverage)s::jsonb)
  AS c(exclusion_class text, namespace text, resolved integer, fallback_only integer,
       unresolved integer, detail jsonb)
ORDER BY c.exclusion_class, c.namespace;
