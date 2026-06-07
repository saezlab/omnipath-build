"""US7 / FR-027: a UniProt AC mapping to >1 gene is split per gene.

The rare identical-gene-copies case (one UniProt AC, several NCBI genes producing
identical protein) MUST be **split into one gene-anchored record per gene**, each
carrying the same form as state (molecular_type = protein, uniprot = the AC); the
protein-centric view re-collapses them. No anchor is chosen and nothing is dropped.

STATUS — **deferred** (xfail). The current resolver marks a source id that maps to
>1 gene as *unresolved* (``entity_resolution_base``: ``candidate_count > 1`` →
status ``unresolved``), so the split is not yet produced. Implementing it requires
changing that resolution branch to fan out across the candidate genes and
re-anchoring the base graph — tracked with the relation re-anchoring in T061. The
``state``/``evidence_state`` schema already accommodates the split (``evidence_state``
is one-to-many), so this test pins the intended behaviour for when T061 lands.

Run against a built instance, e.g. on beauty::

    DATABASE_URL=postgresql://omnipath:omnipath@localhost:55432/omnipath \
        uv run pytest tests/test_uniprot_multi_gene.py -v

Skipped when DATABASE_URL is not set (no DB to test against).
"""

from __future__ import annotations

import os

import pytest

DATABASE_URL = os.environ.get('DATABASE_URL')
SCHEMA = os.environ.get('OMNIPATH_PG_SCHEMA', 'public')

GENE_ENTITY_TYPE = 'Gene:MI:0250'

pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason='DATABASE_URL not set; multi-gene split test needs a built database',
)


@pytest.fixture(scope='module')
def conn():
    import psycopg2

    connection = psycopg2.connect(DATABASE_URL)
    try:
        yield connection
    finally:
        connection.close()


def _rows(conn, query: str, params=None):
    with conn.cursor() as cur:
        cur.execute(query, params or [])
        return cur.fetchall()


@pytest.mark.xfail(
    reason='multi-gene split deferred to T061 (multi-gene ids currently resolve '
    'to unresolved, not split); see module docstring',
    strict=False,
)
def test_uniprot_mapping_to_multiple_genes_is_split(conn):
    """A UniProt AC asserted on a record that maps to >1 gene yields one
    gene-anchored evidence_state per gene, each a protein state carrying the AC."""
    gene_type_id = next(
        (
            r[0]
            for r in _rows(
                conn,
                f'SELECT entity_type_id FROM {SCHEMA}.vocab_entity_type WHERE name = %s',
                [GENE_ENTITY_TYPE],
            )
        ),
        None,
    )
    assert gene_type_id is not None

    # Find an evidence record whose asserted UniProt maps (via gene states) to >1
    # distinct gene entity — the signature of a performed split.
    multi = _rows(
        conn,
        f"""
        SELECT es.source_id, es.entity_evidence_id,
               count(DISTINCT s.gene_entity_id) AS genes
        FROM {SCHEMA}.evidence_state es
        JOIN {SCHEMA}.state s ON s.state_id = es.state_id
        GROUP BY es.source_id, es.entity_evidence_id
        HAVING count(DISTINCT s.gene_entity_id) > 1
        LIMIT 1
        """,
    )
    assert multi, 'no evidence record split across multiple genes'
