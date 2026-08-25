"""The datasets onboarded into the framework: MetalinksDB + LIANA.

Both are now **presets**: metadata over the interaction fact table — the
contributing sources, the interaction-class scope, the evidence scope, the
default and mandatory attributes, the labels, the curation thresholds, the
attribute sources, the mode the per-resource record folds under, the license a
resource must meet, and — where the dataset is not one query — the recipe that
assembles it. Registering the row is the whole build step, and it materialises
nothing at all.

The framework still carries the older **matview network** shape: a schema, a
combined relation and the curated SQL that fills them. No definition here uses
it any more, and the ``schema`` / ``combined_relation`` fields retire with the
registry columns behind them once the standing views are dropped.

**Both datasets' old views are still on disk, and both are unmanaged.**
Converting a dataset stops the framework *managing* its views and does not drop
them: ``apply_network`` returns early for a preset, and the only ``DROP`` for
each relation sits inside the SQL file the derive has stopped executing. So
they survive, frozen at their last refresh, serving data the build has since
corrected. They are a rollback path and not a live contract — a fresh
``init-db --drop-existing`` takes them and nothing recreates them.

Adding a dataset stays a declarative change: a definition here, served by the
same uniform API.
"""

from __future__ import annotations

from omnipath_build.network_views._framework import NetworkDefinition

# MetalinksDB: metabolite↔protein relations, a preset over the interaction
# fact table. It was fifteen materialized views and 1,571 lines of SQL; the
# recipe below is the whole of what that SQL said, expressed as parameters.
#
# **The resource names are the loaded ones, not the published ones.** Human-GEM
# loads under `metatlas` and the retiring views only read `humangem` because
# they label their own rows so by hand. A preset naming `humangem` would
# resolve it to nothing, and an empty contribution reads exactly like a
# resource that failed to load. `labels` carries the published name for output.
#
# **Two of the twelve contribute nothing, for two different reasons, and both
# are worth stating so neither reads as a defect.** BindingDB is excluded by
# the recipe: its assertions stay in the record for any other query to find,
# and no row of this dataset counts them. ChEMBL survives the recipe and then
# meets the metabolite gate, which removes it entirely — a mechanism-of-action
# compound is a drug. The retiring view delivers exactly this: ten resources
# reach its combined contract, and ChEMBL is not among them.
#
# **The reaction-grain components are declared and currently empty.** Rhea,
# Recon3D and Human-GEM contribute metabolite↔enzyme pairs that the record
# holds as two hops through a reaction entity — gene → reaction and reaction →
# metabolite — because the load binarised the reaction rather than keeping it
# as a hyperedge. Until the reaction projection lands, this component selects
# the handful of pairs already stated directly, and the dataset is short of the
# view's rhea, recon3d and humangem rows. That is a known gap with a named
# cause, declared here rather than left as a silent difference.
_CURATED_SOURCES = (
    'cellinker',
    'guidetopharma',
    'mrclinksdb',
    'stitch',
    'tcdb',
    'cellphonedb',
    'neuronchat',
)

# The classes the transport component scopes to. Named rather than left open
# because the reaction-derived contribution is a transport statement, and
# widening it would pull in the signaling rows of the same resources.
_TRANSPORT_CLASSES = ('transport', 'ligand_receptor')

# The gate every component carries. It is on the entity, so no resource can
# route around it: a compound is a metabolite or it is not.
_GATE = {'chemical_classes': ['metabolite']}

METALINKSDB = NetworkDefinition(
    name='metalinksdb',
    kind='compound_protein',
    included_sources=(
        'chembl',
        'bindingdb',
        *_CURATED_SOURCES,
        'recon3d',
        'rhea',
        'metatlas',
    ),
    interaction_class_scope=(),
    default_attributes=('endpoints', 'label', 'references', 'evidence'),
    # The node classification the delivered contract carries inline. Mandatory
    # rather than default: a consumer of this dataset reads the compound and
    # protein annotations, and a request naming another attribute must not
    # take them away.
    mandatory_attributes=('intercell',),
    labels={
        'preset': 'MetaLinksDB',
        'resources': {'metatlas': 'humangem'},
    },
    curation={
        # Every threshold the retiring SQL held inline, as configuration.
        'chemical_class_gate': 'metabolite',
        'chembl_curation': 'mechanism_of_action',
        'excluded_from_combined': ['bindingdb'],
    },
    attribute_sources={
        # Provenance for the node classification, and it says which stage
        # answered. The interim stage is the annotation content the resources
        # already publish; the rebuild replaces the vocabulary behind the same
        # output names, and this field is how a caller tells them apart.
        'intercell': {
            'stage': 'interim',
            'source': 'loaded resource annotations',
            'note': (
                'role and location terms as the contributing resources publish '
                'them, not a cross-resource consensus'
            ),
        },
    },
    composition={
        'operation': 'union',
        'components': [
            {
                'parameters': {
                    'filters': {
                        'resources': ['chembl'],
                        # Presence of the mechanism annotation, folded onto the
                        # record as a flag. An affinity threshold is the
                        # alternative and it floods the set: pChEMBL above 6 is
                        # 1.6 M pairs, and mechanism and pChEMBL sit in
                        # different ChEMBL tables, so their conjunction is empty.
                        'curation_flags': ['mechanism_of_action'],
                        **_GATE,
                    },
                },
            },
            {
                'parameters': {
                    'filters': {
                        'resources': list(_CURATED_SOURCES),
                        **_GATE,
                    },
                },
            },
            {
                'parameters': {
                    'filters': {
                        'resources': ['recon3d', 'rhea', 'metatlas'],
                        'interaction_classes': list(_TRANSPORT_CLASSES),
                        **_GATE,
                    },
                },
            },
        ],
        'steps': [
            # Before the fold, so the dropped resource contributes no row and
            # no count. After it, its assertions would stay inside
            # source_count, the references and the sign flags.
            {'operation': 'exclude', 'resources': ['bindingdb']},
            {'operation': 'collapse'},
            {'operation': 'annotate', 'layer': 'intercell'},
        ],
    },
)

# LIANA: ligand↔receptor pairs, a preset over the interaction fact table. It was
# a matview network over 5 cell-cell-communication resources; the reduced drop
# scopes to ConnectomeDB2025 alone, so the other four are out of scope here.
# Nothing materialises: registering this row is the whole build step. The SQL
# that built the old matview is deleted, so no build recreates it, and a
# database still holding one holds a relation nothing owns — frozen at its last
# refresh, its `sources` column reading a resource name the build has since
# corrected. Dropping that leftover is a one-line statement against the
# database and not a code change, which is why it is not here.
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
