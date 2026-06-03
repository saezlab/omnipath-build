"""The networks onboarded into the framework this cycle: MetalinksDB + LIANA.

Each is a :class:`NetworkDefinition` — declarative metadata plus the curated SQL
files that materialise it. A future network (e.g. COSMOS-in-Postgres) is added
the same way: a definition here, served by the same uniform API.
"""

from __future__ import annotations

from omnipath_build.network_views._framework import NetworkDefinition

# MetalinksDB: compound↔protein relations from 7 interaction sources, each a
# per-source matview, unioned into the `metalinksdb_relations` combined contract,
# plus protein/compound annotation matviews. (`metalinksdb` the multi-resource
# network is distinct from `mrclinksdb` the single source.)
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
        'metalinksdb_relations',
        'metalinksdb_protein_annotations',
        'metalinksdb_compound_annotations',
    ),
    sql_files=('metalinksdb.sql', 'metalinksdb_annotations.sql'),
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
        'connectomedb',
        'icellnet',
        'nichenet',
    ),
    combined_relation='liana_ligand_receptor_pairs',
    matviews=('liana_ligand_receptor_pairs',),
    sql_files=('liana.sql',),
)

NETWORKS: list[NetworkDefinition] = [METALINKSDB, LIANA]
