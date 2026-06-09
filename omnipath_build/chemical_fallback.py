"""Non-lipid chemical fallback resolution (US1 T020, research R22).

When a chemical mention has no usable **structure** (InChIKey), the legacy
resolver dropped it to the opaque ``unresolved_entity_key`` md5 hash — ~44% of
chemicals. R22 replaces the distrusted transitive co-occurrence clustering with
a **per-record, priority-ordered pick** (no transitivity, no false chain-merges):
each structure-less chemical canonicalises to its single best identifier by a
fixed priority, recording the producing ``resolution_mechanism``.

Priority (this module — STAGE 1):

  SMILES → ChEBI → ChEMBL → PubChem → SwissLipids → HMDB → LIPID MAPS
  → keep-original (any other real external id, e.g. KEGG/FooDB/MetaNetX/CAS…)
  → name (exact, basic collision handling)

A mention's primary id is therefore never a lower-priority id when a higher one
is present (a ChEBI+PubChem record anchors on ChEBI). Same id across resources
merges; different ids stay distinct. InChIKey is intentionally absent — those
mentions resolve through the existing direct InChIKey path and never reach here.

Deferred to a follow-up (R22 steps 4–5, the ~3.4% non-priority bulk): the
**within-resource id merge** (FooDB membership→compound, lifting structure-less
membership rows onto the structured compound that shares their FooDB id) and the
**ChEBI-xref 1:1 translation** (CAS/KEGG→ChEBI, abort on 1→many). UniChem is not
ingested. Until then those ids resolve via *keep-original* (a stable id, the
``original_id`` mechanism — still far better than the md5 hash).

The result table ``chemical_fallback_resolution`` is consumed by
``entity_resolution_base``'s unresolved branch: it is applied only to chemical
mentions the structure + resolver-candidate paths left unresolved.
"""

from __future__ import annotations

from pypath.internals.cv_terms import (
    IdentifierNamespaceCv,
    cv_term_label_accession,
)

from omnipath_build.cv_terms import CHEMICAL_ENTITY_TYPE

NAME_TYPE = cv_term_label_accession(IdentifierNamespaceCv.NAME)

# (identifier_type label, tier [lower=preferred], resolution_mechanism).
# Tiers are distinct so the per-mention pick is fully deterministic.
_TIERS: tuple[tuple[str, int, str], ...] = (
    ('Smiles:MI:0239', 1, 'smiles'),
    ('Chebi:MI:0474', 2, 'chebi'),
    ('Chembl Compound:MI:0967', 3, 'chembl'),
    ('Pubchem Compound:OM:0002', 4, 'pubchem'),
    ('Pubchem:MI:0730', 5, 'pubchem'),
    ('Swisslipids:OM:0009', 6, 'swisslipids'),
    ('Hmdb:OM:0004', 7, 'hmdb'),
    ('Lipidmaps:OM:0003', 8, 'lipidmaps'),
    # keep-original: any other real external id → its own id is the canonical
    # identity (stable, mergeable across resources), mechanism 'original_id'.
    ('Kegg Compound:MI:2012', 20, 'original_id'),
    ('Metanetx:OM:0005', 21, 'original_id'),
    ('Bigg Metabolite:OM:0233', 22, 'original_id'),
    ('Human Gem Metabolite:OM:0243', 23, 'original_id'),
    ('Refmet:OM:0137', 24, 'original_id'),
    ('Cas:MI:2011', 25, 'original_id'),
    ('Drugbank:MI:2002', 26, 'original_id'),
    ('Drugcentral:OM:0242', 27, 'original_id'),
    ('Guidetopharma:OM:0008', 28, 'original_id'),
    ('Bindingdb:OM:0006', 29, 'original_id'),
    ('Reactome Stable Id:OM:0130', 30, 'original_id'),
    ('Ramp Id:OM:0132', 31, 'original_id'),
    ('Phenol Explorer:OM:0247', 32, 'original_id'),
    ('Foodb:OM:0213', 33, 'original_id'),
    ('Ptfi:OM:0214', 34, 'original_id'),
    ('Pubchem Substance:OM:0028', 35, 'original_id'),
    # name (no id at all): merge by exact name. Basic guard only — drop
    # empty/InChIKey-shaped/pure-numeric values; the unambiguous-name guard is
    # part of the deferred step.
    ('Name:OM:0202', 40, 'name'),
    ('Synonym:OM:0203', 41, 'name'),
)


def _values(rows: tuple) -> str:
    parts = []
    for name, tier, mech in rows:
        n = name.replace("'", "''")
        m = mech.replace("'", "''")
        parts.append(f"('{n}', {tier}, '{m}')")
    return ', '.join(parts)


def build_chemical_fallback_resolution(con, *, log=lambda *_: None) -> int:
    """Build ``chemical_fallback_resolution`` (one best-id row per chemical
    mention with a usable non-structure identifier). Returns the row count."""

    chem = CHEMICAL_ENTITY_TYPE.replace("'", "''")
    name_re = "'^[A-Z]{14}-[A-Z]{10}-[A-Z]$'"  # InChIKey-shaped guard for names
    con.execute(
        f"""
        CREATE OR REPLACE TABLE chemical_fallback_resolution AS
        WITH tier(type_name, tier, mechanism) AS (VALUES {_values(_TIERS)}),
        candidate AS (
          SELECT
            ee.source,
            ee.entity_evidence_id,
            t.tier,
            t.mechanism,
            it.identifier_type_id AS canonical_identifier_type_id,
            trim(ei.identifier) AS canonical_identifier
          FROM entity_evidence_raw ee
          JOIN entity_identifier_raw ei
            ON ei.source = ee.source
           AND ei.entity_evidence_id = ee.entity_evidence_id
          JOIN tier t ON t.type_name = ei.identifier_type
          JOIN identifier_type_all it ON it.name = ei.identifier_type
          WHERE ee.entity_type = '{chem}'
            AND ei.identifier IS NOT NULL
            AND trim(ei.identifier) <> ''
            -- name tiers: drop junk (InChIKey-shaped / pure-numeric)
            AND NOT (
              t.mechanism = 'name'
              AND (
                trim(ei.identifier) ~ {name_re}
                OR trim(ei.identifier) ~ '^[0-9]+$'
                OR length(trim(ei.identifier)) < 2
              )
            )
        ),
        ranked AS (
          SELECT
            source, entity_evidence_id, mechanism,
            canonical_identifier_type_id, canonical_identifier,
            row_number() OVER (
              PARTITION BY source, entity_evidence_id
              ORDER BY tier, canonical_identifier
            ) AS rk
          FROM candidate
        )
        SELECT
          source, entity_evidence_id,
          canonical_identifier_type_id, canonical_identifier, mechanism
        FROM ranked
        WHERE rk = 1
        """
    )
    rows = con.execute(
        'SELECT count(*) FROM chemical_fallback_resolution'
    ).fetchone()[0]
    log(f'chemical fallback: {rows} chemical mentions with a fallback id')
    return int(rows)
