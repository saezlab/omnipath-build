"""The datasets onboarded into the framework: MetalinksDB + LIANA.

Each is a :class:`NetworkDefinition`, and both generations of definition live
here side by side:

* a **preset** (cycle 008) carries only metadata — contributing sources,
  interaction-class scope, evidence scope, default and mandatory attributes,
  labels, curation thresholds and attribute sources — and is served by filtering
  the interaction fact table. Registering it is the whole build step.
* a **matview network** (Milestone G) carries a schema, a combined relation and
  the curated SQL files that materialise it. The two below are still of this
  kind; they become presets over the fact table as their stories land (T044 for
  MetaLinksDB, T025 for the reduced LIANA), and the ``schema``/
  ``combined_relation`` fields — with the registry columns behind them — retire
  with the last bespoke matview (T046).

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

# LIANA: ligand↔receptor pairs from 5 cell-cell-communication resources, a single
# combined contract matview. Migrated from custom_views/liana.sql — proving a
# network is onboarded by a definition alone, with no bespoke API code.
LIANA = NetworkDefinition(
    name='liana',
    kind='ligand_receptor',
    schema='custom_views',
    included_sources=(
        'cellchat',
        'cellphonedb',
        'connectomedb2025',
        'icellnet',
        'nichenet',
    ),
    combined_relation='liana_ligand_receptor_pairs',
    matviews=('liana_ligand_receptor_pairs',),
    sql_files=('liana.sql',),
)

NETWORKS: list[NetworkDefinition] = [METALINKSDB, LIANA]
