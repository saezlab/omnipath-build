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

Beside the ordered endpoint pairs the graph carries three **stars**, the shape a
metabolic resource publishes a reaction in: a parent entity and one
``has_participant`` relation per member, with the member's role, stoichiometry
and compartment riding on the relation evidence at **object** scope, and the
catalyst arriving as a sibling ``controls`` edge that points *at* the parent.

* two reaction parents with the **same** member multiset, reported by two
  different resources, so the hyperedge projection is asserted to merge them
  into one header rather than to mint one per resource,
* a transport parent whose members carry a membrane side rather than a
  subcellular location, and
* a **pathway** parent, which uses the same ``has_participant`` verb and must
  **not** become a hyperedge — the reaction projection keys on the parent's
  entity type, not on the predicate, and a pathway with two hundred members
  would otherwise mint a two-hundred-way interaction, and
* a reaction parent whose **whole** participant set is the two entities an
  ordinary pair already joins, which is the one shape where the two readings
  hash to the same header id.

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
# The entity types the star rows need. Everything else in the graph is a
# protein, which is what the pair rows have always been.
PROTEIN_TYPE = 'protein:MI:0326'
CHEMICAL_TYPE = 'small molecule:MI:0328'
REACTION_TYPE = 'Reaction:OM:0015'
TRANSPORT_TYPE = 'Transport:OM:0035'
PATHWAY_TYPE = 'Pathway:OM:0014'

# The star participants. `rxn_a` and `rxn_b` are two **distinct** reaction
# entities over the same members — what two resources produce whenever the
# load side's reaction hash cannot merge them, which it cannot for a reaction
# that names only one side. The projection has to merge them anyway.
STAR_NAMES = (
    'rxn_a',
    'rxn_b',
    'trn_a',
    'pathway_a',
    'met_x',
    'met_y',
    'met_z',
    'met_in',
    'met_out',
    'enz_p',
    'enz_t',
    'pw_m1',
    'pw_m2',
    'pw_m3',
    'rxn_c',
)

ENTITY_NAMES = (
    *'abcdefghijklmnopqr',
    *(
        f'{slug}_{side}'
        for slug in COVERED_CLASSES
        for side in ('s', 'o')
    ),
    'dual_s',
    'dual_o',
    # Appended, never inserted into: every id above is an index into this
    # tuple, and a test elsewhere names the row it means.
    *STAR_NAMES,
)

ENTITY_TYPES = {
    'rxn_a': REACTION_TYPE,
    'rxn_b': REACTION_TYPE,
    'rxn_c': REACTION_TYPE,
    'trn_a': TRANSPORT_TYPE,
    'pathway_a': PATHWAY_TYPE,
    'met_x': CHEMICAL_TYPE,
    'met_y': CHEMICAL_TYPE,
    'met_z': CHEMICAL_TYPE,
    'met_in': CHEMICAL_TYPE,
    'met_out': CHEMICAL_TYPE,
}

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
    # The stars. The parent is the **subject** of `has_participant` and the
    # member its object, which is the direction the membership projection
    # writes. The catalyst is the subject of a `controls` edge pointing at
    # the parent, which is the direction `predicate_for_membership` flips a
    # catalytic membership into.
    ('rxn_a_x', 'rxn_a', 'has_participant', 'met_x'),
    ('rxn_a_y', 'rxn_a', 'has_participant', 'met_y'),
    ('rxn_a_z', 'rxn_a', 'has_participant', 'met_z'),
    ('rxn_a_enzyme', 'enz_p', 'controls', 'rxn_a'),
    ('rxn_b_x', 'rxn_b', 'has_participant', 'met_x'),
    ('rxn_b_y', 'rxn_b', 'has_participant', 'met_y'),
    ('rxn_b_z', 'rxn_b', 'has_participant', 'met_z'),
    ('rxn_b_enzyme', 'enz_p', 'controls', 'rxn_b'),
    ('trn_a_in', 'trn_a', 'has_participant', 'met_in'),
    ('trn_a_out', 'trn_a', 'has_participant', 'met_out'),
    ('trn_a_enzyme', 'enz_t', 'controls', 'trn_a'),
    ('pathway_a_1', 'pathway_a', 'has_participant', 'pw_m1'),
    ('pathway_a_2', 'pathway_a', 'has_participant', 'pw_m2'),
    ('pathway_a_3', 'pathway_a', 'has_participant', 'pw_m3'),
    # A reaction whose whole participant set is the two entities the `other`
    # coverage pair already joins, so the shared identity scheme hands both
    # readings the same header id. The projection has to resolve that rather
    # than write four party rows under an `arity` of two.
    ('rxn_c_q', 'rxn_c', 'has_participant', 'q'),
    ('rxn_c_r', 'rxn_c', 'has_participant', 'r'),
)

# `has_participant` is a membership, and the build files memberships under the
# association category rather than the interaction one. Every other relation in
# the graph is an interaction, which is what the default says.
RELATION_CATEGORY = {
    key: 1
    for key, _subject, predicate, _object in RELATIONS
    if predicate == 'has_participant'
}

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
    # One resource per reaction parent, so the merge the projection performs is
    # a merge across resources rather than a fold inside one.
    ('rxn_a_x', SOURCE_A, ()),
    ('rxn_a_y', SOURCE_A, ()),
    ('rxn_a_z', SOURCE_A, ()),
    ('rxn_a_enzyme', SOURCE_A, ()),
    ('rxn_b_x', SOURCE_B, ()),
    ('rxn_b_y', SOURCE_B, ()),
    ('rxn_b_z', SOURCE_B, ()),
    ('rxn_b_enzyme', SOURCE_B, ()),
    ('trn_a_in', SOURCE_A, ()),
    ('trn_a_out', SOURCE_A, ()),
    ('trn_a_enzyme', SOURCE_A, ()),
    ('pathway_a_1', SOURCE_A, ()),
    ('pathway_a_2', SOURCE_A, ()),
    ('pathway_a_3', SOURCE_A, ()),
    ('rxn_c_q', SOURCE_C, ()),
    ('rxn_c_r', SOURCE_C, ()),
)

# Participant-descriptive annotations: (relation key, source, term, value).
# These ride on the **relation** evidence at `object` scope, because the thing
# they describe is the object of `has_participant` — the member — and not the
# relation as a whole. The catalyst's role annotation is written at the same
# scope even though the party it describes is the *subject* of the `controls`
# edge: the membership projection stamps the scope before the catalytic flip
# swaps the endpoints, so the column says `object` on every one of the 810,240
# catalyst annotations in the build. The fixture reproduces that rather than
# correcting it, so a projection that trusted the scope column would fail here
# exactly as it fails on real data.
PARTICIPANT_EVIDENCE = (
    ('rxn_a_x', SOURCE_A, 'Reactant:OM:0310', None),
    ('rxn_a_x', SOURCE_A, 'Stoichiometry:OM:1226', '2'),
    ('rxn_a_x', SOURCE_A, 'Subcellular Location:OM:0604', 'c'),
    ('rxn_a_y', SOURCE_A, 'Reactant:OM:0310', None),
    ('rxn_a_y', SOURCE_A, 'Stoichiometry:OM:1226', '1'),
    ('rxn_a_y', SOURCE_A, 'Subcellular Location:OM:0604', 'c'),
    ('rxn_a_z', SOURCE_A, 'Product:OM:0311', None),
    ('rxn_a_z', SOURCE_A, 'Stoichiometry:OM:1226', '1'),
    ('rxn_a_z', SOURCE_A, 'Subcellular Location:OM:0604', 'm'),
    ('rxn_a_enzyme', SOURCE_A, 'Enzyme:MI:0501', None),
    ('rxn_b_x', SOURCE_B, 'Reactant:OM:0310', None),
    ('rxn_b_x', SOURCE_B, 'Stoichiometry:OM:1226', '2'),
    ('rxn_b_x', SOURCE_B, 'Subcellular Location:OM:0604', 'c'),
    # The second resource states the role and nothing else for this member, so
    # the merged party can only carry a stoichiometry and a compartment if the
    # merge reaches across the two resources.
    ('rxn_b_y', SOURCE_B, 'Reactant:OM:0310', None),
    ('rxn_b_z', SOURCE_B, 'Product:OM:0311', None),
    ('rxn_b_z', SOURCE_B, 'Stoichiometry:OM:1226', '1'),
    ('rxn_b_z', SOURCE_B, 'Subcellular Location:OM:0604', 'm'),
    ('rxn_b_enzyme', SOURCE_B, 'Enzyme:MI:0501', None),
    # A transport states where each side of the membrane is, not which
    # organelle the member sits in.
    ('trn_a_in', SOURCE_A, 'Reactant:OM:0310', None),
    ('trn_a_in', SOURCE_A, 'Membrane Side:OM:1231', 'in'),
    ('trn_a_out', SOURCE_A, 'Product:OM:0311', None),
    ('trn_a_out', SOURCE_A, 'Membrane Side:OM:1231', 'out'),
    ('trn_a_enzyme', SOURCE_A, 'Enzyme:MI:0501', None),
    # The pathway's members carry the pathway-component role, which is not a
    # reaction role and names no side of an arrow.
    ('pathway_a_1', SOURCE_A, 'Pathway Component:OM:0315', None),
    ('pathway_a_2', SOURCE_A, 'Pathway Component:OM:0315', None),
    ('pathway_a_3', SOURCE_A, 'Pathway Component:OM:0315', None),
    ('rxn_c_q', SOURCE_C, 'Reactant:OM:0310', None),
    ('rxn_c_q', SOURCE_C, 'Stoichiometry:OM:1226', '3'),
    ('rxn_c_r', SOURCE_C, 'Product:OM:0311', None),
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
    'has_participant',
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
        type_names = sorted({PROTEIN_TYPE, *ENTITY_TYPES.values()})
        cur.executemany(
            q('INSERT INTO {}.vocab_entity_type (name) VALUES (%s) '
              'ON CONFLICT (name) DO NOTHING').as_string(cur.connection),
            [(name,) for name in type_names],
        )
        cur.execute(q('SELECT name, entity_type_id FROM {}.vocab_entity_type'))
        entity_type_ids = dict(cur.fetchall())
        entity_type_id = entity_type_ids[PROTEIN_TYPE]

        cur.executemany(
            q(
                'INSERT INTO {}.entity (entity_id, entity_type_id, '
                'taxonomy_id, canonical_identifier, resolution_status_id) '
                'VALUES (%s, %s, %s, %s, 1) ON CONFLICT DO NOTHING'
            ).as_string(cur.connection),
            [
                (
                    entity_id,
                    entity_type_ids[ENTITY_TYPES.get(letter, PROTEIN_TYPE)],
                    TAXONOMY_ID,
                    f'FIXTURE_{letter}',
                )
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
                'VALUES (%s, %s, %s, %s, %s) ON CONFLICT DO NOTHING'
            ).as_string(cur.connection),
            [
                (
                    relation_ids[key],
                    ENTITY[subject],
                    predicate_ids[predicate],
                    ENTITY[obj],
                    RELATION_CATEGORY.get(key, 2),
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
                    RELATION_CATEGORY.get(key, 2),
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
        # Scope 3 is `object` in `vocab_annotation_scope`. Everything above is
        # scope 1, `relation`.
        for key, source, term, value in PARTICIPANT_EVIDENCE:
            relation_annotations.append(
                (
                    source,
                    evidence_ids[(key, source)],
                    annotation_key(term, value),
                    3,
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
