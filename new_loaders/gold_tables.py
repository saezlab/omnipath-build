__all__ = [
    'fk',
]

# helper for readability
def fk(id_col, link_text):
    return {"id": id_col, "link": link_text}

plan = {
    "cv_namespace": {
        "columns": {
            "main": ["name"],
            "temp": []
        },
        "foreign_keys": [],
        "constraints": {
            "pass1": ["unique on (name)"],
            "pass2": ["unique on (name)"]
        }
    },

    "cv_term": {
        "columns": {
            "main": ["name", "accession", "description", "is_obsolete"],
            "temp": ["namespace_name", "replaces_accession", "replaced_by_accession"]
        },
        "foreign_keys": [
            fk("namespace_id", "links to cv_namespace via cv_namespace.name = namespace_name"),
            fk("replaces_id", "links to cv_term via cv_term.accession = replaces_accession"),
            fk("replaced_by_id", "links to cv_term via cv_term.accession = replaced_by_accession"),
        ],
        "constraints": {
            "pass1": ["unique on (namespace_name, name)"],   # enforceable without FKs
            "pass2": ["unique on (namespace_id, name)"]      # final form
        }
    },

    "source": {
        "columns": {
            "main": ["name", "url", "description"],
            "temp": []
        },
        "foreign_keys": [],
        "constraints": {
            "pass1": ["unique on (name)"],
            "pass2": ["unique on (name)"]
        }
    },

    "reference": {
        "columns": {
            "main": ["identifier", "citation", "published_year", "journal", "title"],
            "temp": ["type_namespace_name", "type_name"]
        },
        "foreign_keys": [
            fk("type_id", "links to cv_term via (cv_namespace.name = type_namespace_name AND cv_term.name = type_name)")
        ],
        "constraints": {
            "pass1": ["unique on (identifier)"],
            "pass2": ["unique on (identifier)"]
        }
    },

    "provenance": {
        "columns": {
            "main": [],
            "temp": ["source_name", "primary_source_name", "reference_value"]
        },
        "foreign_keys": [
            fk("source_id", "links to source via source.name = source_name"),
            fk("primary_source_id", "links to source via source.name = primary_source_name"),
            fk("reference_id", "links to reference via reference.identifier = reference_value")
        ],
        "constraints": {
            "pass1": [],
            "pass2": ["unique on (source_id, reference_id)"]
        }
    },

    "entity": {
        "columns": {
            "main": [],
            "temp": ["deduplication_identifier", "deduplication_identifier_type", "entity_type_namespace_name", "entity_type_name"]
        },
        "foreign_keys": [
            fk("type_id", "links to cv_term via (cv_namespace.name = entity_type_namespace_name AND cv_term.name = entity_type_name)")
        ],
        "constraints": {
            "pass1": ["unique on (deduplication_identifier, deduplication_identifier_type)"],
            "pass2": []  # add FK-based ones later if desired
        }
    },

    "entity_identifier": {
        "columns": {
            "main": ["identifier", "is_canonical"],
            "temp": [
                "entity_deduplication_identifier", "entity_deduplication_identifier_type",
                "identifier_type_namespace_name", "identifier_type_name",
                "source_name", "reference_value"
            ]
        },
        "foreign_keys": [
            fk("entity_id", "links to entity via (entity.deduplication_identifier = entity_deduplication_identifier AND entity.deduplication_identifier_type = entity_deduplication_identifier_type)"),
            fk("type_id", "links to cv_term via (cv_namespace.name = identifier_type_namespace_name AND cv_term.name = identifier_type_name)"),
            fk("provenance_id", "links to provenance via (source.name = source_name AND reference.identifier = reference_value)")
        ],
        "constraints": {
            "pass1": [],
            "pass2": []  # define later if you want canonical/uniqueness per entity+type
        }
    },

    "compound": {
        "columns": {
            "main": ["formula", "molecular_weight", "exact_mass", "tpsa", "logp",
                     "hbd", "hba", "rotatable_bonds", "aromatic_rings", "heavy_atoms"],
            "temp": ["entity_deduplication_identifier", "entity_deduplication_identifier_type"]
        },
        "foreign_keys": [
            fk("entity_id", "links to entity via (entity.deduplication_identifier = entity_deduplication_identifier AND entity.deduplication_identifier_type = entity_deduplication_identifier_type)")
        ],
        "constraints": {
            "pass1": [],
            "pass2": ["unique on (entity_id)"]  # if you enforce 1:1
        }
    }
}
