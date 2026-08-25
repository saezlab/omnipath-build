"""Per-resource MetSigDB extraction rules (cycle 010).

One rule per v1 resource. The rule says which build-database source it reads,
which extraction shape that source needs, which semantic it contributes, and
how its organism is derived. `contracts/mapping-rules.md` freezes all four.

The rules hold no identifier translation of their own. The core build already
resolved these evidences, and the extraction reads that result, so the
metabolite side stays identical to every other consumer of the canonical entity
layer.
"""

from __future__ import annotations

from dataclasses import dataclass

# The canonical metabolite entity type. Every published membership resolves to
# a `Chemical:OM:0037` entity, and to nothing else.
CHEMICAL_ENTITY_TYPE = 'Chemical:OM:0037'

# The set side is a pathway for three resources and a controlled-vocabulary
# term for two. Named here rather than by numeric id, because the build resolves
# the id from `vocab_entity_type` at run time.
PATHWAY_ENTITY_TYPE = 'Pathway:OM:0014'
CV_TERM_ENTITY_TYPE = 'Cv Term:OM:0012'

# The default projection, in the priority order the row contract publishes. The
# key is the column, the value the `vocab_identifier_type` name behind it.
#
# `inchi` is absent by design: no InChI identifier type exists in the schema.
PROJECTION_IDENTIFIERS: tuple[tuple[str, str], ...] = (
    ('inchikey', 'Standard Inchi Key:MI:1101'),
    ('smiles', 'Smiles:MI:0239'),
    ('hmdb', 'Hmdb:OM:0004'),
    ('pubchem', 'Pubchem Compound:OM:0002'),
    ('chebi', 'Chebi:MI:0474'),
    ('kegg', 'Kegg Compound:MI:2012'),
)


@dataclass(frozen=True)
class ResourceRule:
    """How one v1 resource becomes MetSigDB memberships.

    ``organism_sql`` is a SQL expression over the staged ``set_source_id``, not
    a bind parameter: the value is derived from the set identifier, and it is a
    constant of this module rather than anything a caller supplies. No set
    entity in the build database carries a taxonomy id, so a resource whose
    identifiers name no species gets ``NULL``.
    """

    name: str
    source_name: str
    set_type: str
    set_entity_type: str
    extraction: str
    organism_sql: str = 'NULL'
    # ClassyFire alone reads a second source: HMDB assigns the class, and
    # ChemOnt supplies the hierarchy the assignment expands over.
    hierarchy_source_name: str | None = None


REACTOME = ResourceRule(
    name='Reactome',
    source_name='reactome',
    set_type='pathway',
    set_entity_type=PATHWAY_ENTITY_TYPE,
    extraction='extract_onehop.sql',
    # Every Reactome pathway in this build is R-HSA-*, so the species sits in
    # the identifier. The CASE returns NULL for any other prefix rather than
    # claiming human, so a non-human pathway would publish an honest null.
    organism_sql="CASE WHEN s.set_source_id LIKE 'R-HSA-%%' THEN 9606 END",
)

WIKIPATHWAYS = ResourceRule(
    name='WikiPathways',
    source_name='wikipathways',
    set_type='pathway',
    set_entity_type=PATHWAY_ENTITY_TYPE,
    extraction='extract_onehop.sql',
)

KEGG = ResourceRule(
    name='KEGG',
    source_name='kegg',
    set_type='pathway',
    set_entity_type=PATHWAY_ENTITY_TYPE,
    # KEGG publishes no compound-to-pathway edge. A compound reaches a pathway
    # through a reaction assigned to it, so this resource alone is two hops.
    extraction='extract_kegg.sql',
)

MACDB = ResourceRule(
    name='MACdb',
    source_name='macdb',
    set_type='disease',
    set_entity_type=CV_TERM_ENTITY_TYPE,
    # MACdb resolves its trait on the evidence side, so the subject needs the
    # same resolution join as the object.
    extraction='extract_macdb.sql',
)

CLASSYFIRE = ResourceRule(
    name='ClassyFire',
    source_name='hmdb',
    set_type='chemical_class',
    set_entity_type=CV_TERM_ENTITY_TYPE,
    extraction='extract_classyfire.sql',
    hierarchy_source_name='chemont',
)

RESOURCES: tuple[ResourceRule, ...] = (
    REACTOME,
    WIKIPATHWAYS,
    KEGG,
    MACDB,
    CLASSYFIRE,
)

_BY_NAME = {rule.name: rule for rule in RESOURCES}


def rule_for(name: str) -> ResourceRule:
    """The rule for one contract resource name.

    Raises ``KeyError`` for anything outside the v1 scope, because the substrate
    constrains `resource` to the same five names.
    """
    return _BY_NAME[name]
