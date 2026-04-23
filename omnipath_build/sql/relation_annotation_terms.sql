DROP MATERIALIZED VIEW IF EXISTS {schema}.relation_annotation_term;

CREATE MATERIALIZED VIEW {schema}.relation_annotation_term AS
WITH interaction_relation_evidence AS (
    SELECT
        er.relation_pk,
        er.subject_entity_pk,
        er.object_entity_pk,
        ere.relation_evidence_pk,
        ere.source,
        ere.record_attributes
    FROM {schema}.entity_relation er
    JOIN {schema}.entity_relation_evidence ere
      ON ere.relation_pk = er.relation_pk
    WHERE er.relation_category = 'interaction'
),
interaction_terms AS (
    SELECT DISTINCT
        ire.relation_pk,
        ire.relation_evidence_pk,
        ire.source,
        'interaction'::text AS scope,
        ot.term_id
    FROM interaction_relation_evidence ire
    CROSS JOIN LATERAL jsonb_to_recordset(COALESCE(ire.record_attributes, '[]'::jsonb))
      AS attr(term text, value text, unit text)
    JOIN {schema}.ontology_term ot
      ON ot.term_id = split_part(attr.term, ':', 1) || ':' || split_part(attr.term, ':', 2)
    WHERE attr.term IS NOT NULL
      AND split_part(attr.term, ':', 2) <> ''
      AND attr.value IS NULL
      AND attr.unit IS NULL
),
participant_term_candidates AS (
    SELECT
        ire.relation_pk,
        ire.relation_evidence_pk,
        ire.source,
        ann.object_entity_pk AS term_entity_pk
    FROM interaction_relation_evidence ire
    JOIN {schema}.entity_relation ann
      ON ann.relation_category = 'annotation'
     AND ann.subject_entity_pk = ire.subject_entity_pk

    UNION ALL

    SELECT
        ire.relation_pk,
        ire.relation_evidence_pk,
        ire.source,
        ann.object_entity_pk AS term_entity_pk
    FROM interaction_relation_evidence ire
    JOIN {schema}.entity_relation ann
      ON ann.relation_category = 'annotation'
     AND ann.subject_entity_pk = ire.object_entity_pk
),
participant_terms AS (
    SELECT DISTINCT
        ptc.relation_pk,
        ptc.relation_evidence_pk,
        ptc.source,
        'participant'::text AS scope,
        ot.term_id
    FROM participant_term_candidates ptc
    JOIN {schema}.entity term_entity
      ON term_entity.entity_pk = ptc.term_entity_pk
    JOIN {schema}.ontology_term ot
      ON ot.term_id = term_entity.canonical_identifier
)
SELECT * FROM interaction_terms
UNION
SELECT * FROM participant_terms;
