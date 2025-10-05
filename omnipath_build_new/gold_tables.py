__all__ = [
    'fk',
]

# helper for readability
def fk(id_col, link_text):
    return {"id": id_col, "link": link_text}

gold_tables = {
    "cv_namespace": {
        "columns": {
            "main": {
                "name": "VARCHAR(255)"
            },
            "temp": {}
        },
        "foreign_keys": [],
        "constraints": {
            "pass1": ["unique on (name)"],
            "pass2": ["unique on (name)"]
        }
    },

    "cv_term": {
        "columns": {
            "main": {
                "name": "VARCHAR(255)",
                "accession": "VARCHAR(100)",
                "description": "TEXT",
                "is_obsolete": "BOOLEAN"
            },
            "temp": {
                "namespace_name": "VARCHAR(255)",
                "replaces_accession": "VARCHAR(100)",
                "replaced_by_accession": "VARCHAR(100)"
            }
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
            "main": {
                "name": "VARCHAR(255)",
                "url": "VARCHAR(500)",
                "description": "TEXT"
            },
            "temp": {}
        },
        "foreign_keys": [],
        "constraints": {
            "pass1": ["unique on (name)"],
            "pass2": ["unique on (name)"]
        }
    },

    "reference": {
        "columns": {
            "main": {
                "identifier": "TEXT",
                "citation": "TEXT",
                "published_year": "INT",
                "journal": "TEXT",
                "title": "TEXT"
            },
            "temp": {
                "type_namespace_name": "VARCHAR(255)",
                "type_name": "VARCHAR(255)"
            }
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
            "main": {},
            "temp": {
                "source_name": "VARCHAR(255)",
                "primary_source_name": "VARCHAR(255)",
                "reference_value": "TEXT"
            }
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
            "main": {},
            "temp": {
                "deduplication_identifier": "TEXT",
                "deduplication_identifier_type": "VARCHAR(255)",
                "entity_type_namespace_name": "VARCHAR(255)",
                "entity_type_name": "VARCHAR(255)"
            }
        },
        "foreign_keys": [
            fk("type_id", "links to cv_term via (cv_term.namespace_name = entity_type_namespace_name AND cv_term.name = entity_type_name)")
        ],
        "constraints": {
            "pass1": ["unique on (deduplication_identifier, deduplication_identifier_type)"],
            "pass2": []  # add FK-based ones later if desired
        }
    },

    "entity_identifier": {
        "columns": {
            "main": {
                "identifier": "TEXT",
                "is_canonical": "BOOLEAN"
            },
            "temp": {
                "entity_deduplication_identifier": "TEXT",
                "entity_deduplication_identifier_type": "VARCHAR(255)",
                "identifier_type_namespace_name": "VARCHAR(255)",
                "identifier_type_name": "VARCHAR(255)",
                "source_name": "VARCHAR(255)",
                "reference_value": "TEXT"
            }
        },
        "foreign_keys": [
            fk("entity_id", "links to entity via (entity.deduplication_identifier = entity_deduplication_identifier AND entity.deduplication_identifier_type = entity_deduplication_identifier_type)"),
            fk("type_id", "links to cv_term via (cv_term.namespace_name = identifier_type_namespace_name AND cv_term.name = identifier_type_name)"),
            fk("provenance_id", "links to provenance via (provenance.source_name = source_name AND provenance.reference_value = reference_value)")
        ],
        "constraints": {
            "pass1": [],
            "pass2": []  # define later if you want canonical/uniqueness per entity+type
        }
    },

    "compound": {
        "columns": {
            "main": {
                "formula": "VARCHAR(255)",
                "molecular_weight": "FLOAT",
                "exact_mass": "FLOAT",
                "tpsa": "FLOAT",
                "logp": "FLOAT",
                "hbd": "INT",
                "hba": "INT",
                "rotatable_bonds": "INT",
                "aromatic_rings": "INT",
                "heavy_atoms": "INT"
            },
            "temp": {
                "entity_deduplication_identifier": "TEXT",
                "entity_deduplication_identifier_type": "VARCHAR(255)"
            }
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

silver_gold_map = {
    'cv_namespace': {
        'source_table': 'silver_cv_terms',
        'select': 'SELECT DISTINCT namespace as name FROM silver_cv_terms'
    },
    'cv_term': {
        'source_table': 'silver_cv_terms',
        'select': '''SELECT DISTINCT
            term_name as name,
            term_accession as accession,
            term_definition as description,
            FALSE as is_obsolete,
            namespace as namespace_name,
            NULL::VARCHAR as replaces_accession,
            NULL::VARCHAR as replaced_by_accession
        FROM silver_cv_terms'''
    },
    'source': {
        'source_table': 'silver_entities',
        'select': '''SELECT DISTINCT
            source_database as name,
            NULL as url,
            NULL as description
        FROM silver_entities'''
    },
    'reference': {
        'source_table': 'silver_interactions',
        'select': '''SELECT DISTINCT
            reference_value as identifier,
            NULL as citation,
            NULL as published_year,
            NULL as journal,
            NULL as title,
            'OmniPath' as type_namespace_name,
            reference_type as type_name
        FROM silver_interactions
        WHERE reference_value IS NOT NULL'''
    },
    'provenance_from_interactions': {
        'source_table': 'silver_interactions',
        'target_gold_table': 'provenance',
        'select': '''SELECT DISTINCT
            source as source_name,
            primary_source as primary_source_name,
            reference_value
        FROM silver_interactions'''
    },
    'provenance_from_entities': {
        'source_table': 'silver_entities',
        'target_gold_table': 'provenance',
        'select': '''SELECT DISTINCT
            source_database as source_name,
            source_database as primary_source_name,
            NULL::VARCHAR as reference_value
        FROM silver_entities'''
    },
    'entity': {
        'source_table': 'silver_entities',
        'select': '''SELECT DISTINCT
            identifier as deduplication_identifier,
            identifier_type as deduplication_identifier_type,
            'OmniPath' as entity_type_namespace_name,
            entity_type as entity_type_name
        FROM silver_entities'''
    },
    'entity_identifier': {
        'source_table': 'silver_entities',
        'select': '''
            -- Main identifier (canonical)
            SELECT DISTINCT
                identifier,
                TRUE as is_canonical,
                identifier as entity_deduplication_identifier,
                identifier_type as entity_deduplication_identifier_type,
                'OmniPath' as identifier_type_namespace_name,
                identifier_type as identifier_type_name,
                source_database as source_name,
                NULL::VARCHAR as reference_value
            FROM silver_entities

            UNION ALL

            -- Additional identifiers (unnested from JSON array)
            SELECT DISTINCT
                CAST(json_extract_string(unnest(json_extract(additional_identifiers, '$[*]')), 'value') AS VARCHAR) as identifier,
                FALSE as is_canonical,
                identifier as entity_deduplication_identifier,
                identifier_type as entity_deduplication_identifier_type,
                'OmniPath' as identifier_type_namespace_name,
                CAST(json_extract_string(unnest(json_extract(additional_identifiers, '$[*]')), 'type') AS VARCHAR) as identifier_type_name,
                source_database as source_name,
                NULL::VARCHAR as reference_value
            FROM silver_entities
            WHERE additional_identifiers IS NOT NULL
              AND additional_identifiers != '[]'
        '''
    }
}
