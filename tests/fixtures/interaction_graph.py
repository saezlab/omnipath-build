"""A small hand-built canonical graph for the interaction-projection tests.

The interaction derive step projects ``relation`` and its
``relation_evidence`` provenance into ``interaction``, ``interaction_party`` and
``interaction_fact_resource``. Asserting its semantics against the full build
would make the assertions depend on whatever the resources happen to say this
week, so the projection tests build this fixture instead: a dozen relations in
a throwaway schema, each one carrying exactly the situation a requirement talks
about.

What the graph is built to exercise:

* ``ligand_receptor`` from **participant-role** evidence (tier 1),
* ``allosteric`` and ``orthosteric`` from **interaction-level** annotation
  (tier 2), ``signaling``/``transport`` from the **predicate** (tier 3) and
  ``other`` as the fallback,
* a pair whose resources **disagree** on sign (both flags true, cross-resource),
* a pair where **one** resource asserts both signs (single-resource conflict),
* a contributor asserting **neither** sign nor direction, so
  ``sign_source_count <= cardinality(sources)`` is a real inequality,
* a pair with **no sign at all**, so the sign columns stay NULL, and
* an **opposite-direction pair**, which must stay two rows.

Beside those single-situation rows the graph carries a **coverage pair per
interaction class**: one ordered endpoint pair for every class the graph can
evidence, each reported by two resources that both publish a reference. Those
are what let the dedup and provenance rule be asserted class by class instead
of on ``signaling`` alone, and they are the reason a class reached only by a
verb no rule maps cannot pass unnoticed. One endpoint pair carries **two**
classes at once, which turns the class into a claim about rows rather than
about a column.

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


# The predicate miRBase publishes its pre-miRNA to mature-miRNA processing
# under. The build stores the bare accession, because the controlled-vocabulary
# term is an (accession, label) pair and the loader writes the accession.
MATURATION_PREDICATE = 'OM:1257'

# One ordered endpoint pair per interaction class the graph can evidence:
# the class slug, the predicate the pair is reported under, and the
# interaction-level annotation that names the class where the verb cannot.
# `tf_target` is absent on purpose — no resource in the graph asserts it, and
# a fixture that invented one would hide the gap instead of surfacing it.
CLASS_PAIRS = (
    ('ligand_receptor', 'interacts_with', ()),
    ('signaling', 'controls', ()),
    ('transport', 'transports', ()),
    ('orthosteric', 'interacts_with', ('Agonist:OM:1001',)),
    ('allosteric', 'interacts_with', ('Allosteric Modulator:OM:1005',)),
    ('maturation', MATURATION_PREDICATE, ()),
    ('other', 'has_member', ()),
)

# The classes the coverage pairs above evidence, in the order they are built.
COVERED_CLASSES = tuple(slug for slug, _predicate, _terms in CLASS_PAIRS)

# The participants. The single-situation rows name theirs by the letter the
# docstrings above use. The coverage pairs name theirs after the class they
# carry, so a failure names the class that broke. Only hex digits are
# legal in a uuid, so the name indexes the entity and an ordinal carries it.
ENTITY_NAMES = (
    *'abcdefghijklmnopqr',
    *(
        f'{slug}_{side}'
        for slug in COVERED_CLASSES
        for side in ('s', 'o')
    ),
    'dual_s',
    'dual_o',
)

ENTITY = {
    name: f'e0000000-0000-4000-8000-{index:012d}'
    for index, name in enumerate(ENTITY_NAMES, start=1)
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
    # One coverage pair per class, the maturation pair reversed so the ordered
    # key holds outside `signaling` too, and an endpoint pair carrying two
    # classes at once.
    *(
        (f'pair_{slug}', f'{slug}_s', predicate, f'{slug}_o')
        for slug, predicate, _terms in CLASS_PAIRS
    ),
    ('pair_maturation_reverse', 'maturation_o', MATURATION_PREDICATE,
     'maturation_s'),
    ('dual_signaling', 'dual_s', 'positively_regulates', 'dual_o'),
    ('dual_orthosteric', 'dual_s', 'interacts_with', 'dual_o'),
)

# Evidence: (relation key, source id, relation-level annotation terms).
EVIDENCE = (
    ('lr', SOURCE_LR, ()),
    ('sig_pos', SOURCE_A, ('Activation:OM:0930',)),
    ('sig_neg', SOURCE_B, ('Inhibition:OM:0931',)),
    # The third resource on the same endpoint pair asserts neither sign nor
    # anything else — only a reference. It stays in `sources` all the same.
    ('sig_plain', SOURCE_C, ()),
    ('unsigned', SOURCE_C, ()),
    ('forward', SOURCE_A, ()),
    ('reverse', SOURCE_A, ()),
    # One resource asserting both signs, under two predicates that share the
    # `signaling` class. 93 per cent of both-flags rows are this rather than
    # a disagreement between resources.
    ('self_pos', SOURCE_A, ('Activation:OM:0930',)),
    ('self_neg', SOURCE_A, ('Inhibition:OM:0931',)),
    ('orthosteric', SOURCE_A, ('Agonist:OM:1001',)),
    ('allosteric', SOURCE_A, ('Allosteric Modulator:OM:1005',)),
    ('transport', SOURCE_A, ()),
    ('other', SOURCE_A, ()),
    # Two resources on every coverage pair, so folding one is folding a group
    # rather than copying a row. The annotation that names the class rides on
    # the first resource alone — it resolves the relation, not the record.
    *(
        (f'pair_{slug}', source, terms if source == SOURCE_A else ())
        for slug, _predicate, terms in CLASS_PAIRS
        for source in (SOURCE_A, SOURCE_B)
    ),
    ('pair_maturation_reverse', SOURCE_A, ()),
    ('dual_signaling', SOURCE_A, ()),
    ('dual_orthosteric', SOURCE_B, ('Agonist:OM:1001',)),
)

# Which evidence rows carry a PubMed reference, and which id.
REFERENCES = {
    ('sig_pos', SOURCE_A): '11111111',
    ('sig_plain', SOURCE_C): '33333333',
    ('lr', SOURCE_LR): '44444444',
    # Both contributors to a coverage pair publish a reference, and they
    # publish different ones, so a collapsed row reporting two of them is
    # reporting a collection rather than one resource's list.
    **{
        (f'pair_{slug}', source): f'{prefix}{index:07d}'
        for index, (slug, _predicate, _terms) in enumerate(CLASS_PAIRS, start=1)
        for source, prefix in ((SOURCE_A, '7'), (SOURCE_B, '8'))
    },
}

PREDICATES = (
    'interacts_with',
    'positively_regulates',
    'negatively_regulates',
    'controls',
    'transports',
    'has_member',
    MATURATION_PREDICATE,
)

# Participant-role annotations, and the endpoint evidence rows they hang off:
# (relation key, source, side, term). Role evidence is the one tier that lives
# at the entity-evidence grain, so a relation reaching a class through it needs
# an evidence row per endpoint rather than a bare entity id.
ROLE_EVIDENCE = (
    ('lr', SOURCE_LR, 'subject', 'Ligand:OM:7777'),
    ('lr', SOURCE_LR, 'object', 'Receptor:OM:7778'),
    ('pair_ligand_receptor', SOURCE_A, 'subject', 'Ligand:OM:7777'),
    ('pair_ligand_receptor', SOURCE_A, 'object', 'Receptor:OM:7778'),
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
    # Endpoint evidence ids exist only for the ligand/receptor relations: those
    # are the ones whose class comes from participant-role annotations, which
    # hang off the entity-evidence grain.
    endpoint_ids = {
        (key, source, side): _uuid5('33333333', index)
        for index, (key, source, side, _term) in enumerate(
            ROLE_EVIDENCE,
            start=1,
        )
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
                    source,
                    endpoint_ids[(key, source, side)],
                    annotation_key(term),
                )
                for key, source, side, term in ROLE_EVIDENCE
            ],
        )
    conn.commit()

    return {
        'entities': ENTITY,
        'relations': relation_ids,
        'evidence': evidence_ids,
        'sources': SOURCE_NAMES,
    }
