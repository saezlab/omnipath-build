"""Unit test for the non-lipid chemical fallback resolution (US1 T020, R22).

Synthetic DuckDB raw tables → ``build_chemical_fallback_resolution`` → assert the
per-record priority pick (no transitive clustering): SMILES → ChEBI → ChEMBL →
PubChem → SwissLipids → HMDB → LIPID MAPS → keep-original → name, with the
producing ``resolution_mechanism`` recorded. Needs only ``duckdb``.
"""

from __future__ import annotations

import pytest

duckdb = pytest.importorskip('duckdb')

from omnipath_build.chemical_fallback import (  # noqa: E402
    build_chemical_fallback_resolution,
)

CHEM = 'Chemical:OM:0037'
PROTEIN = 'Protein:MI:0326'
# (type label -> id) for identifier_type_all
TYPES = {
    'Smiles:MI:0239': 1,
    'Chebi:MI:0474': 2,
    'Chembl Compound:MI:0967': 3,
    'Pubchem Compound:OM:0002': 4,
    'Hmdb:OM:0004': 5,
    'Foodb:OM:0213': 6,
    'Name:OM:0202': 7,
    'Standard Inchi Key:MI:1101': 8,
}


def _con(rows):
    con = duckdb.connect(':memory:')
    con.execute('CREATE TABLE identifier_type_all (identifier_type_id BIGINT, name VARCHAR)')
    con.executemany('INSERT INTO identifier_type_all VALUES (?, ?)',
                    [(v, k) for k, v in TYPES.items()])
    con.execute("""CREATE TABLE entity_evidence_raw (
      source VARCHAR, dataset VARCHAR, row_id BIGINT, entity_evidence_id VARCHAR,
      parent_entity_evidence_id VARCHAR, entity_role VARCHAR, entity_type VARCHAR,
      taxonomy_id VARCHAR)""")
    con.execute("""CREATE TABLE entity_identifier_raw (
      source VARCHAR, entity_evidence_id VARCHAR, identifier_id VARCHAR,
      identifier_type VARCHAR, identifier VARCHAR)""")
    # rows: (ev_id, entity_type, [(id_type, id_value), ...])
    for ev, etype, ids in rows:
        con.execute('INSERT INTO entity_evidence_raw VALUES (?,?,?,?,?,?,?,?)',
                    ('s', 'd', 1, ev, None, 'r', etype, None))
        for it, val in ids:
            con.execute('INSERT INTO entity_identifier_raw VALUES (?,?,?,?,?)',
                        ('s', ev, ev + it, it, val))
    return con


def _pick(con, ev):
    r = con.execute(
        'SELECT canonical_identifier_type_id, canonical_identifier, mechanism '
        'FROM chemical_fallback_resolution WHERE entity_evidence_id = ?', [ev],
    ).fetchall()
    return r[0] if r else None


def test_priority_pick_and_mechanisms():
    con = _con([
        ('m_chebi_pubchem', CHEM, [('Chebi:MI:0474', '15365'), ('Pubchem Compound:OM:0002', '2244')]),
        ('m_pubchem', CHEM, [('Pubchem Compound:OM:0002', '999')]),
        ('m_foodb', CHEM, [('Foodb:OM:0213', 'FDB000123')]),
        ('m_smiles_chebi', CHEM, [('Smiles:MI:0239', 'CCO'), ('Chebi:MI:0474', '16236')]),
        ('m_name', CHEM, [('Name:OM:0202', 'aspirin')]),
    ])
    build_chemical_fallback_resolution(con)
    # ChEBI beats PubChem (higher priority), not both → no clustering.
    assert _pick(con, 'm_chebi_pubchem') == (TYPES['Chebi:MI:0474'], '15365', 'chebi')
    assert _pick(con, 'm_pubchem') == (TYPES['Pubchem Compound:OM:0002'], '999', 'pubchem')
    # FooDB-only → keep-original (stable id, not a hash).
    assert _pick(con, 'm_foodb') == (TYPES['Foodb:OM:0213'], 'FDB000123', 'original_id')
    # SMILES (structure-ish) beats ChEBI.
    assert _pick(con, 'm_smiles_chebi') == (TYPES['Smiles:MI:0239'], 'CCO', 'smiles')
    # name-only → name.
    assert _pick(con, 'm_name') == (TYPES['Name:OM:0202'], 'aspirin', 'name')


def test_inchikey_and_junk_excluded():
    con = _con([
        # InChIKey present: not a fallback tier → no fallback row (handled by the
        # direct InChIKey path upstream).
        ('m_inchikey_only', CHEM, [('Standard Inchi Key:MI:1101', 'BSYNRYMUTXBXSQ-UHFFFAOYSA-N')]),
        # junk name (InChIKey-shaped / numeric) → dropped, no row.
        ('m_junk_name', CHEM, [('Name:OM:0202', 'BSYNRYMUTXBXSQ-UHFFFAOYSA-N')]),
        ('m_numeric_name', CHEM, [('Name:OM:0202', '12345')]),
        # non-chemical → ignored entirely.
        ('p_protein', PROTEIN, [('Chebi:MI:0474', '1')]),
    ])
    build_chemical_fallback_resolution(con)
    for ev in ('m_inchikey_only', 'm_junk_name', 'm_numeric_name', 'p_protein'):
        assert _pick(con, ev) is None, f'{ev} should have no fallback row'


def test_name_falls_below_real_ids():
    con = _con([
        ('m_id_and_name', CHEM, [('Foodb:OM:0213', 'FDB1'), ('Name:OM:0202', 'foo')]),
    ])
    build_chemical_fallback_resolution(con)
    # a real id (even keep-original FooDB) beats a name.
    assert _pick(con, 'm_id_and_name') == (TYPES['Foodb:OM:0213'], 'FDB1', 'original_id')
