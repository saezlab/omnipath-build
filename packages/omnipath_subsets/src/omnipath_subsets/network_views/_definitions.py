"""Current legacy preset recipes, with chemical gates in Biolink storage names."""

from __future__ import annotations

from ._registry import NetworkDefinition

_CURATED_SOURCES = (
    "cellinker",
    "guidetopharma",
    "mrclinksdb",
    "stitch",
    "tcdb",
    "cellphonedb",
    "neuronchat",
)

_TRANSPORT_CLASSES = ("transport", "ligand_receptor")

_GATE = {"entity_types": ["chemical_entity", "small_molecule"]}


METALINKSDB = NetworkDefinition(
    name="metalinksdb",
    kind="compound_protein",
    included_sources=(
        "chembl",
        "bindingdb",
        *_CURATED_SOURCES,
        "recon3d",
        "rhea",
        "metatlas",
    ),
    interaction_class_scope=(),
    default_attributes=("endpoints", "label", "references", "evidence"),
    mandatory_attributes=("intercell",),
    labels={
        "preset": "MetaLinksDB",
        "resources": {"metatlas": "humangem"},
    },
    curation={
        "entity_type_gate": "biolink:ChemicalEntity",
        "chembl_curation": "mechanism_of_action",
        "excluded_from_combined": ["bindingdb"],
    },
    attribute_sources={
        "intercell": {
            "stage": "interim",
            "source": "loaded resource annotations",
            "note": (
                "role and location terms as the contributing resources publish "
                "them, not a cross-resource consensus"
            ),
        },
    },
    composition={
        "operation": "union",
        "components": [
            {
                "parameters": {
                    "filters": {
                        "resources": ["chembl"],
                        "curation_flags": ["mechanism_of_action"],
                        **_GATE,
                    },
                },
            },
            {
                "parameters": {
                    "filters": {
                        "resources": list(_CURATED_SOURCES),
                        **_GATE,
                    },
                },
            },
            {
                "parameters": {
                    "filters": {
                        "resources": ["recon3d", "rhea", "metatlas"],
                        "interaction_classes": list(_TRANSPORT_CLASSES),
                        **_GATE,
                    },
                },
            },
        ],
        "steps": [
            {"operation": "exclude", "resources": ["bindingdb"]},
            {"operation": "collapse"},
            {"operation": "annotate", "layer": "intercell"},
        ],
    },
)

LIANA = NetworkDefinition(
    name="liana",
    kind="ligand_receptor",
    included_sources=("connectomedb2025",),
    interaction_class_scope=("ligand_receptor",),
    default_attributes=("endpoints", "label", "references", "evidence"),
)

REACTIONS = NetworkDefinition(
    name="reactions",
    kind="reaction",
    included_sources=("kegg", "rhea", "metatlas", "recon3d"),
    interaction_class_scope=(),
    default_attributes=("endpoints", "label", "references", "evidence"),
    mandatory_attributes=("role", "side", "stoichiometry", "compartment"),
    grain="participant",
    collapse_mode="none",
    labels={
        "preset": "Reactions",
        "resources": {"metatlas": "humangem"},
    },
    curation={
        "participant_grain": "hyperedge",
        "excluded_from_scope": ["reactome"],
        "never_loaded": ["mouse-gem", "imm1415"],
    },
    attribute_sources={
        "role": {
            "sources": ["kegg", "rhea", "metatlas", "recon3d"],
            "note": "every participant of every reaction carries one",
        },
        "side": {
            "sources": ["kegg", "rhea", "metatlas", "recon3d"],
            "note": "derived from the reactant or product role",
        },
        "stoichiometry": {
            "sources": ["kegg", "metatlas", "recon3d"],
            "note": (
                "complete on every reaction these three publish; rhea states "
                "none on any of its reactions, and serves null"
            ),
        },
        "compartment": {
            "sources": ["metatlas", "recon3d"],
            "note": (
                "per-participant subcellular location, complete on both; kegg "
                "states none, and rhea states membrane sides that sit on "
                "transport records rather than on reactions"
            ),
        },
    },
)

NETWORKS: list[NetworkDefinition] = [METALINKSDB, LIANA, REACTIONS]
