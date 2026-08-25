"""The per-resource MetSigDB mapping rules (cycle 010).

Pure unit tests. The rules are a frozen contract, so they are checked without a
database: `contracts/mapping-rules.md` is the reference.
"""

from __future__ import annotations

import pytest

from omnipath_build.metsigdb.mapping import RESOURCES, rule_for

# The frozen per-resource set_type matrix. A resource contributes exactly one
# semantic, and no row's semantics come from source-side free text.
FROZEN_SET_TYPES = {
    'KEGG': 'pathway',
    'Reactome': 'pathway',
    'WikiPathways': 'pathway',
    'MACdb': 'disease',
    'ClassyFire': 'chemical_class',
}

# The `data_source.name` each rule reads from.
FROZEN_SOURCES = {
    'KEGG': 'kegg',
    'Reactome': 'reactome',
    'WikiPathways': 'wikipathways',
    'MACdb': 'macdb',
    'ClassyFire': 'hmdb',
}


def test_every_contract_resource_has_a_rule():
    assert {rule.name for rule in RESOURCES} == set(FROZEN_SET_TYPES)


def test_set_type_matches_the_frozen_matrix():
    assert {rule.name: rule.set_type for rule in RESOURCES} == FROZEN_SET_TYPES


def test_source_name_matches_the_frozen_matrix():
    assert {rule.name: rule.source_name for rule in RESOURCES} == FROZEN_SOURCES


def test_rules_are_unique():
    names = [rule.name for rule in RESOURCES]
    assert len(names) == len(set(names))


def test_classyfire_declares_its_hierarchy_source():
    """ClassyFire is two sources: HMDB assigns, ChemOnt supplies the hierarchy."""
    classyfire = rule_for('ClassyFire')
    assert classyfire.hierarchy_source_name == 'chemont'
    assert all(
        rule.hierarchy_source_name is None
        for rule in RESOURCES
        if rule.name != 'ClassyFire'
    )


def test_only_reactome_derives_an_organism():
    """No set entity carries a taxonomy id, so organism is derived or null."""
    for rule in RESOURCES:
        if rule.name == 'Reactome':
            assert 'R-HSA-' in rule.organism_sql
            assert '9606' in rule.organism_sql
        else:
            assert rule.organism_sql == 'NULL'


def test_rule_for_rejects_an_unknown_resource():
    with pytest.raises(KeyError):
        rule_for('SMPDB')


def test_extraction_files_carry_no_bare_percent_sign():
    """psycopg2 interpolates the extraction files, comments included.

    A bare `%` reads as a malformed placeholder and fails the whole load with
    "dict is not a sequence", which names neither the file nor the character.
    Twice was enough.
    """
    import re
    from pathlib import Path

    import omnipath_build.metsigdb.build as build

    named = re.compile(r'%\((?:source_id|set_entity_type_id|'
                       r'chemical_entity_type_id|hierarchy_source_id|'
                       r'max_records|resource|set_type|provenance_source|'
                       r'build_id)\)s')
    for rule in RESOURCES:
        path = Path(build._SQL_DIR) / rule.extraction
        if not path.exists():
            continue
        stripped = named.sub('', path.read_text(encoding='utf-8'))
        assert '%' not in stripped, (
            f'{rule.extraction} carries a bare percent sign; '
            f'psycopg2 reads it as a placeholder'
        )
