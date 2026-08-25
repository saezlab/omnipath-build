"""The datasets onboarded into the framework: MetalinksDB + LIANA.

Each is a :class:`NetworkDefinition`, and both generations of definition live
here side by side:

* a **preset** (cycle 008) carries only metadata — contributing sources,
  interaction-class scope, evidence scope, default and mandatory attributes,
  labels, curation thresholds, attribute sources, the mode it collapses the
  per-resource record with and the license levels a resource must meet to
  contribute — and is served by filtering the interaction fact table.
  Registering it is the whole build step, and it materialises nothing of its
  own: a preset restricted to a resource subset collapses the record at query
  time.
* a **matview network** carries a schema, a combined relation and
  the curated SQL files that materialise it. MetaLinksDB below is still of this
  kind. LIANA is not: it became a preset over the fact table, and the matview it
  used to own is left standing, unmanaged, as the rollback path until the
  retirement step drops it. The ``schema``/``combined_relation`` fields — with
  the registry columns behind them — retire with the last bespoke matview.

Adding a dataset stays a declarative change: a definition here, served by the
same uniform API.
"""

from __future__ import annotations

from omnipath_build.network_views._framework import NetworkDefinition

# MetalinksDB: compound↔protein relations from 12 interaction/transport/signaling
# sources, each a per-source matview, unioned into the single `metalinksdb_relations`
# combined contract (004-metalinksdb-view: now also carrying protein/compound
# annotations inline via LEFT JOIN, so the two former standalone annotation
# matviews are upstream inputs rather than a separate public contract).
# Human-GEM is loaded under the pypath/data_source name 'metatlas'; its per-source
# matview and combined-view `source` label are both 'humangem' to match this
# spec's naming (see metalinksdb.sql header comment).
# (`metalinksdb` the multi-resource network is distinct from `mrclinksdb` the
# single source.)
METALINKSDB = NetworkDefinition(
    name='metalinksdb',
    kind='compound_protein',
    schema='custom_views',
    included_sources=(
        'chembl',
        'bindingdb',
        'cellinker',
        'guidetopharma',
        'mrclinksdb',
        'stitch',
        'tcdb',
        'recon3d',
        'rhea',
        'humangem',
        'cellphonedb',
        'neuronchat',
    ),
    combined_relation='metalinksdb_relations',
    matviews=(
        'metalinksdb_chembl_relations',
        'metalinksdb_bindingdb_relations',
        'metalinksdb_cellinker_relations',
        'metalinksdb_guidetopharma_relations',
        'metalinksdb_mrclinksdb_relations',
        'metalinksdb_stitch_relations',
        'metalinksdb_tcdb_relations',
        'metalinksdb_recon3d_relations',
        'metalinksdb_rhea_relations',
        'metalinksdb_humangem_relations',
        'metalinksdb_cellphonedb_relations',
        'metalinksdb_neuronchat_relations',
        'metalinksdb_protein_annotations',
        'metalinksdb_compound_annotations',
        'metalinksdb_relations',
    ),
    sql_files=('metalinksdb_annotations.sql', 'metalinksdb.sql'),
)

# LIANA: ligand↔receptor pairs, a preset over the interaction fact table. It was
# a matview network over 5 cell-cell-communication resources; the reduced drop
# scopes to ConnectomeDB2025 alone, so the other four are out of scope here.
# Nothing materialises: registering this row is the whole build step, and the
# old matview `custom_views.liana_ligand_receptor_pairs` is deliberately left in
# place — unmanaged and unrefreshed — as the rollback path until it is retired.
#
# Organism scope: all 14 taxa the resource loads, not human alone. That is a
# decision, not an oversight. Organism is a dimension a caller queries on, not a
# property of a preset: `subject_organism` and `object_organism` are populated on
# every one of the 44,455 rows and never disagree, so asking for organism 9606
# yields the human drop at no cost, whereas a human-scoped preset could not be
# widened later without registering a second one. Scoping to human here would
# throw away 92 per cent of a resource the build already holds. The consequence
# is worth stating plainly: **this preset's default result is all taxa**, so a
# consumer expecting the legacy human-only LIANA meets a result roughly twelve
# times larger, and should pass an organism filter to get the old shape.
#
# `references` stays among the default attributes even though it returns empty
# today. ConnectomeDB2025 does publish PubMed ids, but the loader that produced
# this build (`pypath/inputs_v2/connectomedb.py`) never reads the `AI summary`
# column they sit in, so there is no reference annotation for the projection to
# collect and nothing to serve. That is an ingest gap, tracked as a follow-up in
# this cycle's task list, not a property of the dataset. Keeping the attribute in
# the defaults keeps the response shape stable — the field is present and empty —
# and it fills in once the loader is fixed and the resource re-ingested. Nothing
# here claims otherwise: `attribute_sources` names no source for references.
LIANA = NetworkDefinition(
    name='liana',
    kind='ligand_receptor',
    included_sources=('connectomedb2025',),
    interaction_class_scope=('ligand_receptor',),
    default_attributes=('endpoints', 'label', 'references', 'evidence'),
)

NETWORKS: list[NetworkDefinition] = [METALINKSDB, LIANA]
