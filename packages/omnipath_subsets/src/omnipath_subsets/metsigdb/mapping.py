"""The five existing MetSigDB products, expressed with native Biolink terms."""

from dataclasses import dataclass


CHEMICAL_TYPES = ("chemical_entity", "small_molecule")

KEGG_OVERVIEW_MAPS = (
    "rn01100",
    "rn01110",
    "rn01120",
    "rn01200",
    "rn01210",
    "rn01212",
    "rn01220",
    "rn01230",
    "rn01232",
    "rn01240",
    "rn01250",
)


@dataclass(frozen=True)
class ResourceRule:
    """A resource-native set and the published graph needed to extract it."""

    name: str
    source: str
    set_type: str
    set_entity_type: str
    set_namespace: str
    extraction: str
    hierarchy_source: str | None = None


RESOURCES = (
    ResourceRule("Reactome", "reactome", "pathway", "pathway", "reactome", "onehop"),
    ResourceRule("WikiPathways", "wikipathways", "pathway", "pathway", "wikipathways", "onehop"),
    ResourceRule("KEGG", "kegg", "pathway", "pathway", "kegg_pathway", "kegg"),
    ResourceRule("MACdb", "macdb", "disease", "ontology_class", "macdb_trait", "onehop"),
    ResourceRule(
        "ClassyFire", "hmdb", "chemical_class", "ontology_class", "chemont", "classyfire", "chemont"
    ),
)
