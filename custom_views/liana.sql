CREATE SCHEMA IF NOT EXISTS custom_views;

DO $$
DECLARE
  existing_kind "char";
BEGIN
  SELECT relkind
    INTO existing_kind
  FROM pg_class cls
  JOIN pg_namespace ns
    ON ns.oid = cls.relnamespace
  WHERE ns.nspname = 'custom_views'
    AND cls.relname = 'liana_ligand_receptor_pairs';

  IF existing_kind = 'v' THEN
    DROP VIEW custom_views.liana_ligand_receptor_pairs;
  ELSIF existing_kind = 'm' THEN
    DROP MATERIALIZED VIEW custom_views.liana_ligand_receptor_pairs;
  ELSIF existing_kind IS NOT NULL THEN
    DROP TABLE custom_views.liana_ligand_receptor_pairs;
  END IF;
END $$;

CREATE TABLE custom_views.liana_ligand_receptor_pairs AS
WITH included_source(source_name) AS (
  VALUES
    ('cellchat'),
    ('cellphonedb'),
    ('connectomedb'),
    ('icellnet'),
    ('nichenet')
),
identifier_label(identifier_type, label) AS (
  VALUES
    ('Uniprot:MI:1097', 'UniProt'),
    ('Gene Name Primary:OM:0200', 'Gene'),
    ('Gene Name Synonym:OM:0201', 'Synonym'),
    ('Hgnc:MI:1095', 'HGNC'),
    ('Entrez:MI:0477', 'Entrez'),
    ('Name:OM:0202', 'Name'),
    ('Uniprot Entry Name:OM:0221', 'UniProtEntry')
),
entity_identifier_raw AS (
  SELECT
    e.entity_id,
    item->>'identifier_type' AS identifier_type,
    item->>'identifier' AS identifier
  FROM public.entity e
  CROSS JOIN LATERAL jsonb_array_elements(e.identifiers) AS item
  WHERE item->>'identifier' IS NOT NULL
    AND item->>'identifier' <> ''

  UNION ALL

  SELECT
    e.entity_id,
    it.name AS identifier_type,
    e.canonical_identifier AS identifier
  FROM public.entity e
  JOIN public.vocab_identifier_type it
    ON it.identifier_type_id = e.canonical_identifier_type_id
  WHERE e.canonical_identifier IS NOT NULL
    AND e.canonical_identifier <> ''
    AND it.name NOT IN (
      'Fallback',
      'Ensembl:MI:0476',
      'Name:OM:0202',
      'omnipath:unresolved_entity_key',
      'omnipath:complex_member_hash'
    )
    AND it.name !~ '^omnipath:'
),
entity_identifier AS (
  SELECT
    raw.entity_id,
    coalesce(label.label, split_part(raw.identifier_type, ':', 1))
      AS identifier_type,
    raw.identifier
  FROM entity_identifier_raw raw
  LEFT JOIN identifier_label label
    ON label.identifier_type = raw.identifier_type
  WHERE raw.identifier_type <> 'omnipath:unresolved_entity_key'
    AND raw.identifier_type <> 'omnipath:complex_member_hash'
    AND raw.identifier_type <> 'Ensembl:MI:0476'
),
entity_identifier_summary AS (
  SELECT
    entity_id,
    string_agg(
      DISTINCT identifier_type || ':' || identifier,
      '|' ORDER BY identifier_type || ':' || identifier
    ) AS identifiers
  FROM entity_identifier
  GROUP BY entity_id
),
entity_summary AS (
  SELECT
    e.entity_id,
    split_part(et.name, ':', 1) AS entity_type,
    e.taxonomy_id,
    coalesce(identifier_summary.identifiers, '') AS identifiers
  FROM public.entity e
  JOIN public.vocab_entity_type et
    ON et.entity_type_id = e.entity_type_id
  LEFT JOIN entity_identifier_summary identifier_summary
    ON identifier_summary.entity_id = e.entity_id
),
evidence_side AS (
  SELECT
    re.source_id,
    re.relation_evidence_id,
    rer.relation_id,
    side.side_name,
    side.entity_evidence_id,
    eer.entity_id,
    bool_or(a.term = 'Ligand:OM:7777') AS is_ligand,
    bool_or(a.term = 'Receptor:OM:7778') AS is_receptor,
    array_remove(
      array_agg(
      DISTINCT CASE
        WHEN a.term IN (
          'Membrane:OM:7779',
          'Cytoplasm:OM:7780',
          'Secreted:OM:7781'
        ) THEN split_part(a.term, ':', 1)
        ELSE NULL
      END
      ORDER BY CASE
        WHEN a.term IN (
          'Membrane:OM:7779',
          'Cytoplasm:OM:7780',
          'Secreted:OM:7781'
        ) THEN split_part(a.term, ':', 1)
        ELSE NULL
      END
    ) FILTER (
        WHERE a.term IN (
          'Membrane:OM:7779',
          'Cytoplasm:OM:7780',
          'Secreted:OM:7781'
        )
      ),
      NULL
    ) AS annotations
  FROM public.relation_evidence re
  JOIN public.vocab_relation_predicate rp
    ON rp.relation_predicate_id = re.predicate_id
   AND rp.name = 'interacts_with'
  JOIN public.relation_evidence_relation rer
    ON rer.source_id = re.source_id
   AND rer.relation_evidence_id = re.relation_evidence_id
  CROSS JOIN LATERAL (
    VALUES
      ('subject', re.subject_entity_evidence_id),
      ('object', re.object_entity_evidence_id)
  ) AS side(side_name, entity_evidence_id)
  JOIN public.entity_evidence_resolution eer
    ON eer.source_id = re.source_id
   AND eer.entity_evidence_id = side.entity_evidence_id
  LEFT JOIN public.entity_evidence_annotation eea
    ON eea.source_id = re.source_id
   AND eea.entity_evidence_id = side.entity_evidence_id
  LEFT JOIN public.annotation a
    ON a.annotation_key = eea.annotation_key
  GROUP BY
    re.source_id,
    re.relation_evidence_id,
    rer.relation_id,
    side.side_name,
    side.entity_evidence_id,
    eer.entity_id
),
oriented_evidence AS (
  SELECT
    ligand.source_id,
    ligand.relation_evidence_id,
    ligand.relation_id,
    ligand.entity_id AS ligand_entity_id,
    receptor.entity_id AS receptor_entity_id,
    ligand.annotations AS ligand_annotations,
    receptor.annotations AS receptor_annotations
  FROM evidence_side ligand
  JOIN evidence_side receptor
    ON receptor.source_id = ligand.source_id
   AND receptor.relation_evidence_id = ligand.relation_evidence_id
   AND receptor.side_name <> ligand.side_name
  WHERE ligand.is_ligand
    AND receptor.is_receptor
),
relation_annotation AS (
  WITH annotation_value AS (
    SELECT
      rea.source_id,
      rea.relation_evidence_id,
      a.term,
      a.value
    FROM public.relation_evidence_annotation rea
    JOIN public.annotation a
      ON a.annotation_key = rea.annotation_key
  ),
  reference_value AS (
    SELECT
      source_id,
      relation_evidence_id,
      'PMID:' || value AS reference
    FROM annotation_value
    WHERE term = 'Pubmed:MI:0446'
      AND value IS NOT NULL
      AND value <> ''

    UNION ALL

    SELECT
      source_id,
      relation_evidence_id,
      'PMCID:' || regexp_replace(value, '^PMC', '', 'i') AS reference
    FROM annotation_value
    WHERE term = 'Pubmed Central:MI:1042'
      AND value IS NOT NULL
      AND value <> ''

    UNION ALL

    SELECT
      source_id,
      relation_evidence_id,
      'DOI:' || value AS reference
    FROM annotation_value
    WHERE term = 'Doi:MI:0574'
      AND value IS NOT NULL
      AND value <> ''
  ),
  reference_summary AS (
    SELECT
      source_id,
      relation_evidence_id,
      string_agg(
        DISTINCT reference,
        '|' ORDER BY reference
      ) AS references
    FROM reference_value
    GROUP BY source_id, relation_evidence_id
  ),
  interaction_annotation_value AS (
    SELECT
      source_id,
      relation_evidence_id,
      split_part(term, ':', 1) AS annotation
    FROM annotation_value
    WHERE term = 'Neurotransmitter Interaction:OM:1215'

    UNION ALL

    SELECT
      source_id,
      relation_evidence_id,
      value AS annotation
    FROM annotation_value
    WHERE term = 'Interaction Directness:OM:1216'
      AND value IN ('Direct', 'Inferred')
  ),
  interaction_annotation_summary AS (
    SELECT
      source_id,
      relation_evidence_id,
      string_agg(
        DISTINCT annotation,
        '|' ORDER BY annotation
      ) AS interaction_annotations
    FROM interaction_annotation_value
    GROUP BY source_id, relation_evidence_id
  )
  SELECT
    av.source_id,
    av.relation_evidence_id,
    reference_summary.references,
    interaction_annotation_summary.interaction_annotations
  FROM annotation_value av
  LEFT JOIN reference_summary
    ON reference_summary.source_id = av.source_id
   AND reference_summary.relation_evidence_id = av.relation_evidence_id
  LEFT JOIN interaction_annotation_summary
    ON interaction_annotation_summary.source_id = av.source_id
   AND interaction_annotation_summary.relation_evidence_id = av.relation_evidence_id
  GROUP BY
    av.source_id,
    av.relation_evidence_id,
    reference_summary.references,
    interaction_annotation_summary.interaction_annotations
),
complex_member AS (
  SELECT
    complex.entity_id AS complex_entity_id,
    string_agg(
      DISTINCT '(' || member_summary.identifiers || ')',
      ',' ORDER BY '(' || member_summary.identifiers || ')'
    ) AS members
  FROM public.entity complex
  JOIN public.relation r
    ON r.subject_entity_id = complex.entity_id
  JOIN public.vocab_relation_predicate rp
    ON rp.relation_predicate_id = r.predicate_id
   AND rp.name = 'has_member'
  JOIN entity_summary member_summary
    ON member_summary.entity_id = r.object_entity_id
  GROUP BY complex.entity_id
)
SELECT
  ligand_summary.identifiers AS ligand_identifiers,
  ligand_summary.entity_type AS ligand_type,
  ligand_summary.taxonomy_id AS ligand_taxonomy_id,
  coalesce(
    string_agg(
      DISTINCT ligand_annotation.value,
      '|' ORDER BY ligand_annotation.value
    )
      FILTER (
        WHERE ligand_annotation.value IS NOT NULL
          AND ligand_annotation.value <> ''
      ),
    ''
  ) AS ligand_annotations,
  coalesce(ligand_members.members, '') AS ligand_members,
  receptor_summary.identifiers AS receptor_identifiers,
  receptor_summary.entity_type AS receptor_type,
  receptor_summary.taxonomy_id AS receptor_taxonomy_id,
  coalesce(
    string_agg(
      DISTINCT receptor_annotation.value,
      '|' ORDER BY receptor_annotation.value
    )
      FILTER (
        WHERE receptor_annotation.value IS NOT NULL
          AND receptor_annotation.value <> ''
      ),
    ''
  ) AS receptor_annotations,
  coalesce(receptor_members.members, '') AS receptor_members,
  string_agg(DISTINCT ds.name, '|' ORDER BY ds.name) AS sources,
  count(DISTINCT (oe.source_id, oe.relation_evidence_id)) AS evidence_count,
  coalesce(
    string_agg(
      DISTINCT ra.references,
      '|' ORDER BY ra.references
    ) FILTER (
      WHERE ra.references IS NOT NULL
        AND ra.references <> ''
    ),
    ''
  ) AS references,
  coalesce(
    string_agg(
      DISTINCT ra.interaction_annotations,
      '|' ORDER BY ra.interaction_annotations
    ) FILTER (
      WHERE ra.interaction_annotations IS NOT NULL
        AND ra.interaction_annotations <> ''
    ),
    ''
  ) AS interaction_annotations
FROM oriented_evidence oe
JOIN public.data_source ds
  ON ds.source_id = oe.source_id
JOIN included_source included
  ON included.source_name = ds.name
JOIN entity_summary ligand_summary
  ON ligand_summary.entity_id = oe.ligand_entity_id
JOIN entity_summary receptor_summary
  ON receptor_summary.entity_id = oe.receptor_entity_id
LEFT JOIN relation_annotation ra
  ON ra.source_id = oe.source_id
 AND ra.relation_evidence_id = oe.relation_evidence_id
LEFT JOIN complex_member ligand_members
  ON ligand_members.complex_entity_id = oe.ligand_entity_id
LEFT JOIN complex_member receptor_members
  ON receptor_members.complex_entity_id = oe.receptor_entity_id
LEFT JOIN LATERAL unnest(oe.ligand_annotations) AS ligand_annotation(value) ON true
LEFT JOIN LATERAL unnest(oe.receptor_annotations) AS receptor_annotation(value) ON true
GROUP BY
  ligand_summary.entity_type,
  ligand_summary.taxonomy_id,
  ligand_summary.identifiers,
  ligand_members.members,
  receptor_summary.entity_type,
  receptor_summary.taxonomy_id,
  receptor_summary.identifiers,
  receptor_members.members;

CREATE INDEX IF NOT EXISTS liana_ligand_receptor_pairs_taxonomy_idx
ON custom_views.liana_ligand_receptor_pairs (
  ligand_taxonomy_id,
  receptor_taxonomy_id
);
