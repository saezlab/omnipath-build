"""A small hand-built canonical graph for the interaction-projection tests.

The interaction derive step (008 T013/T014) folds ``relation`` and its
``relation_evidence`` provenance into ``interaction``, ``interaction_party`` and
``interaction_fact``. Asserting its semantics against the full build would make
the assertions depend on whatever the resources happen to say this week, so the
three projection tests build this fixture instead: a dozen relations in a
throwaway schema, each one carrying exactly the situation a requirement talks
about.

What the graph is built to exercise:

* ``ligand_receptor`` from **participant-role** evidence (research R18 tier 1),
* ``allosteric`` and ``orthosteric`` from **interaction-level** annotation
  (tier 2), ``signaling``/``transport`` from the **predicate** (tier 3) and
  ``other`` as the fallback,
* a pair whose resources **disagree** on sign (both flags true, cross-resource),
* a pair where **one** resource asserts both signs (single-resource conflict),
* a contributor asserting **neither** sign nor direction, so
  ``sign_source_count <= cardinality(sources)`` is a real inequality,
* a pair with **no sign at all**, so the sign columns stay NULL, and
* an **opposite-direction pair**, which must stay two rows.

Every id is fixed, so a test can name the row it means.
"""

from __future__ import annotations

from psycopg2 import sql
import psycopg2.extensions

# Sources. The ids are explicit so the partitioned evidence tables land in their
# default partition and the test can name a source by number.
SOURCE_A = 9001
SOURCE_B = 9002
SOURCE_C = 9003
SOURCE_LR = 9004
SOURCE_NAMES = {
    SOURCE_A: 'fixture_res_a',
    SOURCE_B: 'fixture_res_b',
    SOURCE_C: 'fixture_res_c',
    SOURCE_LR: 'fixture_res_lr',
}


# The participants, by the letter the docstrings above use. Only hex digits are
# legal in a uuid, so the letter names the entity and an ordinal carries it.
ENTITY = {
    letter: f'e0000000-0000-4000-8000-{index:012d}'
    for index, letter in enumerate('abcdefghijklmnopqr', start=1)
}

# Relations: (key, subject letter, predicate, object letter).
RELATIONS = (
    ('lr', 'a', 'interacts_with', 'b'),
    ('sig_pos', 'c', 'positively_regulates', 'd'),
    ('sig_neg', 'c', 'negatively_regulates', 'd'),
    ('sig_plain', 'c', 'controls', 'd'),
    ('unsigned', 'e', 'interacts_with', 'f'),
    ('forward', 'g', 'controls', 'h'),
    ('reverse', 'h', 'controls', 'g'),
    ('self_pos', 'i', 'positively_regulates', 'j'),
    ('self_neg', 'i', 'negatively_regulates', 'j'),
    ('orthosteric', 'k', 'interacts_with', 'l'),
    ('allosteric', 'm', 'interacts_with', 'n'),
    ('transport', 'o', 'transports', 'p'),
    ('other', 'q', 'has_member', 'r'),
)

# Evidence: (relation key, source id, relation-level annotation terms).
EVIDENCE = (
    ('lr', SOURCE_LR, ()),
    ('sig_pos', SOURCE_A, ('Activation:OM:0930',)),
    ('sig_neg', SOURCE_B, ('Inhibition:OM:0931',)),
    # The third resource on the same endpoint pair asserts neither sign nor
    # anything else — only a reference. FR-044c keeps it in `sources`.
    ('sig_plain', SOURCE_C, ()),
    ('unsigned', SOURCE_C, ()),
    ('forward', SOURCE_A, ()),
    ('reverse', SOURCE_A, ()),
    # One resource asserting both signs, under two predicates that share the
    # `signaling` class — the 93% case research R15 measured.
    ('self_pos', SOURCE_A, ('Activation:OM:0930',)),
    ('self_neg', SOURCE_A, ('Inhibition:OM:0931',)),
    ('orthosteric', SOURCE_A, ('Agonist:OM:1001',)),
    ('allosteric', SOURCE_A, ('Allosteric Modulator:OM:1005',)),
    ('transport', SOURCE_A, ()),
    ('other', SOURCE_A, ()),
)

# Which evidence rows carry a PubMed reference, and which id.
REFERENCES = {
    ('sig_pos', SOURCE_A): '11111111',
    ('sig_plain', SOURCE_C): '33333333',
    ('lr', SOURCE_LR): '44444444',
}

PREDICATES = (
    'interacts_with',
    'positively_regulates',
    'negatively_regulates',
    'controls',
    'transports',
    'has_member',
)

TAXONOMY_ID = 9606


def _uuid5(prefix: str, index: int) -> str:
    """A stable uuid: a hex prefix in the first block, the index in the last."""
    return f'{prefix}-0000-4000-8000-{index:012d}'


def build_interaction_fixture(
    conn: psycopg2.extensions.connection,
    schema: str,
) -> dict[str, object]:
    """Populate ``schema`` with the fixture graph; return its named ids."""
    schema_id = sql.Identifier(schema)

    def q(statement: str) -> sql.Composed:
        return sql.SQL(statement).format(schema_id)

    relation_ids = {
        key: _uuid5('11111111', index)
        for index, (key, *_rest) in enumerate(RELATIONS, start=1)
    }
    evidence_ids = {
        (key, source): _uuid5('22222222', index)
        for index, (key, source, _terms) in enumerate(EVIDENCE, start=1)
    }
    # Endpoint evidence ids exist only for the ligand/receptor relation: that is
    # the one whose class comes from participant-role annotations, which hang off
    # the entity-evidence grain.
    endpoint_ids = {
        ('lr', SOURCE_LR, 'subject'): _uuid5('33333333', 1),
        ('lr', SOURCE_LR, 'object'): _uuid5('33333333', 2),
    }

    with conn.cursor() as cur:
        cur.execute(
            q('INSERT INTO {}.vocab_entity_type (name) VALUES (%s) '
              'ON CONFLICT (name) DO NOTHING'),
            ['protein:MI:0326'],
        )
        cur.execute(
            q('SELECT entity_type_id FROM {}.vocab_entity_type '
              'WHERE name = %s'),
            ['protein:MI:0326'],
        )
        entity_type_id = cur.fetchone()[0]

        cur.executemany(
            q(
                'INSERT INTO {}.entity (entity_id, entity_type_id, '
                'taxonomy_id, canonical_identifier, resolution_status_id) '
                'VALUES (%s, %s, %s, %s, 1) ON CONFLICT DO NOTHING'
            ).as_string(cur.connection),
            [
                (entity_id, entity_type_id, TAXONOMY_ID, f'FIXTURE_{letter}')
                for letter, entity_id in ENTITY.items()
            ],
        )
        cur.executemany(
            q(
                'INSERT INTO {}.data_source (source_id, name) VALUES (%s, %s) '
                'ON CONFLICT (source_id) DO NOTHING'
            ).as_string(cur.connection),
            list(SOURCE_NAMES.items()),
        )
        cur.executemany(
            q(
                'INSERT INTO {}.dataset (dataset_id, source_id, name) '
                'VALUES (%s, %s, %s) ON CONFLICT (dataset_id) DO NOTHING'
            ).as_string(cur.connection),
            [
                (source_id, source_id, f'{name}_default')
                for source_id, name in SOURCE_NAMES.items()
            ],
        )
        cur.executemany(
            q(
                'INSERT INTO {}.vocab_relation_category '
                '(relation_category_id, name) VALUES (%s, %s) '
                'ON CONFLICT (relation_category_id) DO NOTHING'
            ).as_string(cur.connection),
            [(1, 'association'), (2, 'interaction')],
        )
        cur.executemany(
            q(
                'INSERT INTO {}.vocab_relation_predicate (name) VALUES (%s) '
                'ON CONFLICT (name) DO NOTHING'
            ).as_string(cur.connection),
            [(name,) for name in PREDICATES],
        )
        cur.execute(
            q('SELECT name, relation_predicate_id '
              'FROM {}.vocab_relation_predicate')
        )
        predicate_ids = dict(cur.fetchall())

        cur.executemany(
            q(
                'INSERT INTO {}.relation (relation_id, subject_entity_id, '
                'predicate_id, object_entity_id, relation_category_id) '
                'VALUES (%s, %s, %s, %s, 2) ON CONFLICT DO NOTHING'
            ).as_string(cur.connection),
            [
                (
                    relation_ids[key],
                    ENTITY[subject],
                    predicate_ids[predicate],
                    ENTITY[obj],
                )
                for key, subject, predicate, obj in RELATIONS
            ],
        )

        predicate_of = {key: predicate for key, _s, predicate, _o in RELATIONS}
        evidence_rows = []
        for row_id, (key, source, _terms) in enumerate(EVIDENCE, start=1):
            subject_evidence = endpoint_ids.get((key, source, 'subject'))
            object_evidence = endpoint_ids.get((key, source, 'object'))
            subject_letter = next(s for k, s, _p, _o in RELATIONS if k == key)
            object_letter = next(o for k, _s, _p, o in RELATIONS if k == key)
            evidence_rows.append(
                (
                    source,
                    evidence_ids[(key, source)],
                    source,
                    row_id,
                    subject_evidence,
                    None if subject_evidence else ENTITY[subject_letter],
                    predicate_ids[predicate_of[key]],
                    object_evidence,
                    None if object_evidence else ENTITY[object_letter],
                    2,
                )
            )
        # The endpoint evidence rows the ligand/receptor annotations hang off.
        # `relation_evidence` carries a foreign key onto them.
        cur.executemany(
            q(
                'INSERT INTO {}.entity_evidence (source_id, '
                'entity_evidence_id, dataset_id, row_id, entity_role_id, '
                'entity_type_id, taxonomy_id) '
                'VALUES (%s, %s, %s, %s, 1, %s, %s) ON CONFLICT DO NOTHING'
            ).as_string(cur.connection),
            [
                (
                    source,
                    endpoint_id,
                    source,
                    ordinal,
                    entity_type_id,
                    TAXONOMY_ID,
                )
                for ordinal, ((_key, source, _side), endpoint_id) in enumerate(
                    endpoint_ids.items(),
                    start=1,
                )
            ],
        )
        cur.executemany(
            q(
                'INSERT INTO {}.relation_evidence (source_id, '
                'relation_evidence_id, dataset_id, row_id, '
                'subject_entity_evidence_id, subject_entity_id, predicate_id, '
                'object_entity_evidence_id, object_entity_id, '
                'relation_category_id) '
                'VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) '
                'ON CONFLICT DO NOTHING'
            ).as_string(cur.connection),
            evidence_rows,
        )
        cur.executemany(
            q(
                'INSERT INTO {}.relation_evidence_relation '
                '(source_id, relation_id, relation_evidence_id) '
                'VALUES (%s, %s, %s) ON CONFLICT DO NOTHING'
            ).as_string(cur.connection),
            [
                (source, relation_ids[key], evidence_ids[(key, source)])
                for key, source, _terms in EVIDENCE
            ],
        )

        # Annotations. `annotation` is content-addressed by `annotation_key`, so
        # the fixture mints one key per (term, value) pair it needs.
        annotation_keys: dict[tuple[str, str | None], str] = {}

        def annotation_key(term: str, value: str | None = None) -> str:
            existing = annotation_keys.get((term, value))
            if existing is not None:
                return existing
            key = _uuid5('44444444', len(annotation_keys) + 1)
            annotation_keys[(term, value)] = key
            cur.execute(
                q(
                    'INSERT INTO {}.annotation (annotation_key, term, value) '
                    'VALUES (%s, %s, %s) ON CONFLICT DO NOTHING'
                ),
                [key, term, value],
            )
            return key

        relation_annotations = []
        for key, source, terms in EVIDENCE:
            for term in terms:
                relation_annotations.append(
                    (source, evidence_ids[(key, source)], annotation_key(term), 1)
                )
        for (key, source), pubmed in REFERENCES.items():
            relation_annotations.append(
                (
                    source,
                    evidence_ids[(key, source)],
                    annotation_key('Pubmed:MI:0446', pubmed),
                    1,
                )
            )
        cur.executemany(
            q(
                'INSERT INTO {}.relation_evidence_annotation (source_id, '
                'relation_evidence_id, annotation_key, annotation_scope_id) '
                'VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING'
            ).as_string(cur.connection),
            relation_annotations,
        )
        cur.executemany(
            q(
                'INSERT INTO {}.entity_evidence_annotation (source_id, '
                'entity_evidence_id, annotation_key) VALUES (%s, %s, %s) '
                'ON CONFLICT DO NOTHING'
            ).as_string(cur.connection),
            [
                (
                    SOURCE_LR,
                    endpoint_ids[('lr', SOURCE_LR, 'subject')],
                    annotation_key('Ligand:OM:7777'),
                ),
                (
                    SOURCE_LR,
                    endpoint_ids[('lr', SOURCE_LR, 'object')],
                    annotation_key('Receptor:OM:7778'),
                ),
            ],
        )
    conn.commit()

    return {
        'entities': ENTITY,
        'relations': relation_ids,
        'evidence': evidence_ids,
        'sources': SOURCE_NAMES,
    }
