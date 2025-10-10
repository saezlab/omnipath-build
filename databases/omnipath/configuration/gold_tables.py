__all__ = [
    'ENTITY_IDENTIFIER_PROVENANCE_UNIONS',
    'ENTITY_IDENTIFIER_PROVENANCE_UNION_SELECTS',
    'ENTITY_IDENTIFIER_UNIONS',
    'ENTITY_IDENTIFIER_UNION_SELECTS',
    'IDENTIFIER_COLUMNS',
    'fk',
]

# helper for readability
def fk(id_col, link_text, null_equal_columns: tuple[str, ...] | None = None):
    fk_def = {"id": id_col, "link": link_text}
    if null_equal_columns:
        fk_def["null_equal_columns"] = tuple(null_equal_columns)
    return fk_def

IDENTIFIER_COLUMNS: tuple[tuple[str, str], ...] = (
    ("name", "'name'"),
    ("inchikey", "'inchikey'"),
    ("hmdb_id", "'hmdb'"),
    ("chebi_id", "'chebi'"),
    ("pubchem_cid", "'pubchem'"),
    ("lipidmaps_id", "'lipidmaps'"),
    ("swisslipids_id", "'swisslipids'"),
    ("metanetx_id", "'metanetx'"),
    ("ramp_id", "'ramp'"),
    ("kegg_id", "'kegg'"),
    ("drugbank_id", "'drugbank'"),
    ("cas_number", "'cas'"),
)

ENTITY_IDENTIFIER_UNION_SELECTS = [
    f"""SELECT DISTINCT
                {column} AS identifier,
                dedup_identifier AS entity_deduplication_identifier,
                dedup_identifier_type AS entity_deduplication_identifier_type,
                'OmniPath' AS identifier_type_namespace_name,
                {label} AS identifier_type_name
            FROM silver_entities
            WHERE {column} IS NOT NULL
              AND dedup_identifier IS NOT NULL
              AND dedup_identifier_type IS NOT NULL"""
    for column, label in IDENTIFIER_COLUMNS
]

ENTITY_IDENTIFIER_UNION_SELECTS.append(
    """SELECT DISTINCT
                json_extract_string(synonym_json.unnest, '$') AS identifier,
                dedup_identifier AS entity_deduplication_identifier,
                dedup_identifier_type AS entity_deduplication_identifier_type,
                'OmniPath' AS identifier_type_namespace_name,
                'synonym' AS identifier_type_name
            FROM silver_entities,
                 unnest(json_extract(name_variants, '$[*]')) AS synonym_json
            WHERE name_variants IS NOT NULL
              AND name_variants != '[]'
              AND json_extract_string(synonym_json.unnest, '$') IS NOT NULL
              AND dedup_identifier IS NOT NULL
              AND dedup_identifier_type IS NOT NULL"""
)

ENTITY_IDENTIFIER_UNIONS = "\n        UNION ALL\n        ".join(ENTITY_IDENTIFIER_UNION_SELECTS)

ENTITY_IDENTIFIER_PROVENANCE_UNION_SELECTS = [
    f"""SELECT DISTINCT
                {column} AS identifier,
                'OmniPath' AS identifier_type_namespace_name,
                {label} AS identifier_type_name,
                source_database AS source_name,
                NULL::VARCHAR AS reference_value
            FROM silver_entities
            WHERE {column} IS NOT NULL"""
    for column, label in IDENTIFIER_COLUMNS
]

ENTITY_IDENTIFIER_PROVENANCE_UNION_SELECTS.append(
    """SELECT DISTINCT
                json_extract_string(synonym_json.unnest, '$') AS identifier,
                'OmniPath' AS identifier_type_namespace_name,
                'synonym' AS identifier_type_name,
                source_database AS source_name,
                NULL::VARCHAR AS reference_value
            FROM silver_entities,
                 unnest(json_extract(name_variants, '$[*]')) AS synonym_json
            WHERE name_variants IS NOT NULL
              AND name_variants != '[]'
              AND json_extract_string(synonym_json.unnest, '$') IS NOT NULL"""
)

ENTITY_IDENTIFIER_PROVENANCE_UNIONS = "\n        UNION ALL\n        ".join(ENTITY_IDENTIFIER_PROVENANCE_UNION_SELECTS)

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
                "identifier": "TEXT"
            },
            "temp": {
                "entity_deduplication_identifier": "TEXT",
                "entity_deduplication_identifier_type": "VARCHAR(255)",
                "identifier_type_namespace_name": "VARCHAR(255)",
                "identifier_type_name": "VARCHAR(255)"
            }
        },
        "foreign_keys": [
            fk("entity_id", "links to entity via (entity.deduplication_identifier = entity_deduplication_identifier AND entity.deduplication_identifier_type = entity_deduplication_identifier_type)"),
            fk("type_id", "links to cv_term via (cv_term.namespace_name = identifier_type_namespace_name AND cv_term.name = identifier_type_name)")
        ],
        "constraints": {
            "pass1": [],
            "pass2": ["unique on (identifier, type_id)"]
        }
    },

    "entity_identifier_provenance": {
        "columns": {
            "main": {},
            "temp": {
                "identifier": "TEXT",
                "identifier_type_namespace_name": "VARCHAR(255)",
                "identifier_type_name": "VARCHAR(255)",
                "source_name": "VARCHAR(255)",
                "reference_value": "TEXT"
            }
        },
        "foreign_keys": [
            fk("entity_identifier_id", "links to entity_identifier via (entity_identifier.identifier = identifier AND entity_identifier.identifier_type_namespace_name = identifier_type_namespace_name AND entity_identifier.identifier_type_name = identifier_type_name)"),
            fk(
                "provenance_id",
                "links to provenance via (provenance.source_name = source_name AND provenance.reference_value = reference_value)",
                null_equal_columns=("reference_value",)
            )
        ],
        "constraints": {
            "pass1": [],
            "pass2": ["unique on (entity_identifier_id, provenance_id)"]
        }
    },

    "entity_evidence": {
        "columns": {
            "main": {
                "annotations": "JSON"   # All annotations from this source: {"gene_name": "EGFR", "tissue": ["liver"], ...}
            },
            "temp": {
                "entity_deduplication_identifier": "TEXT",
                "entity_deduplication_identifier_type": "VARCHAR(255)",
                "source_name": "VARCHAR(255)",
                "reference_value": "TEXT"
            }
        },
        "foreign_keys": [
            fk("entity_id", "links to entity via (entity.deduplication_identifier = entity_deduplication_identifier AND entity.deduplication_identifier_type = entity_deduplication_identifier_type)"),
            fk(
                "provenance_id",
                "links to provenance via (provenance.source_name = source_name AND provenance.reference_value = reference_value)",
                null_equal_columns=("reference_value",)
            )
        ],
        "constraints": {
            "pass1": [],
            "pass2": ["unique on (entity_id, provenance_id)"]
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
    },

    "membership": {
        "columns": {
            "main": {
                "stoichiometry": "FLOAT"
            },
            "temp": {
                "parent_deduplication_identifier": "TEXT",
                "parent_deduplication_identifier_type": "VARCHAR(255)",
                "member_deduplication_identifier": "TEXT",
                "member_deduplication_identifier_type": "VARCHAR(255)",
                "role_namespace_name": "VARCHAR(255)",
                "role_name": "VARCHAR(255)",
                "source_name": "VARCHAR(255)",
                "reference_value": "TEXT"
            }
        },
        "foreign_keys": [
            fk("parent_id", "links to entity via (entity.deduplication_identifier = parent_deduplication_identifier AND entity.deduplication_identifier_type = parent_deduplication_identifier_type)"),
            fk("member_id", "links to entity via (entity.deduplication_identifier = member_deduplication_identifier AND entity.deduplication_identifier_type = member_deduplication_identifier_type)"),
            fk("role_id", "links to cv_term via (cv_term.namespace_name = role_namespace_name AND cv_term.name = role_name)"),
            fk(
                "provenance_id",
                "links to provenance via (provenance.source_name = source_name AND provenance.reference_value = reference_value)",
                null_equal_columns=("reference_value",)
            )
        ],
        "constraints": {
            "pass1": [],
            "pass2": ["unique on (parent_id, member_id, role_id, provenance_id)"]
        }
    },

    "interaction": {
        "columns": {
            "main": {
            },
            "temp": {
                "entity_a_deduplication_identifier": "TEXT",
                "entity_a_deduplication_identifier_type": "VARCHAR(255)",
                "entity_b_deduplication_identifier": "TEXT",
                "entity_b_deduplication_identifier_type": "VARCHAR(255)",
                "type_namespace_name": "VARCHAR(255)",
                "type_name": "VARCHAR(255)"
            }
        },
        "foreign_keys": [
            fk("entity_a_id", "links to entity via (entity.deduplication_identifier = entity_a_deduplication_identifier AND entity.deduplication_identifier_type = entity_a_deduplication_identifier_type)"),
            fk("entity_b_id", "links to entity via (entity.deduplication_identifier = entity_b_deduplication_identifier AND entity.deduplication_identifier_type = entity_b_deduplication_identifier_type)"),
                fk("type_id", "links to cv_term via (cv_term.namespace_name = type_namespace_name AND cv_term.name = type_name)")
            ],
            "constraints": {
                "pass1": [],
            "pass2": ["unique on (entity_a_id, entity_b_id, type_id)"]
        }
    },

    "interaction_evidence": {
        "columns": {
            "main": {
                "detection_method": "VARCHAR(255)",
                "causal_statement": "TEXT",
                "sentence": "TEXT",
                "is_directed": "BOOLEAN",
                "annotations": "JSON",        # General interaction annotations
                "entity_a_context": "JSON",   # Context annotations for entity A
                "entity_b_context": "JSON"    # Context annotations for entity B
            },
            "temp": {
                "entity_a_deduplication_identifier": "TEXT",
                "entity_a_deduplication_identifier_type": "VARCHAR(255)",
                "entity_b_deduplication_identifier": "TEXT",
                "entity_b_deduplication_identifier_type": "VARCHAR(255)",
                "type_namespace_name": "VARCHAR(255)",
                "type_name": "VARCHAR(255)",
                "source_name": "VARCHAR(255)",
                "reference_value": "TEXT"
            }
        },
        "foreign_keys": [
            fk("interaction_id", "links to interaction via (interaction.entity_a_deduplication_identifier = entity_a_deduplication_identifier AND interaction.entity_a_deduplication_identifier_type = entity_a_deduplication_identifier_type AND interaction.entity_b_deduplication_identifier = entity_b_deduplication_identifier AND interaction.entity_b_deduplication_identifier_type = entity_b_deduplication_identifier_type AND interaction.type_namespace_name = type_namespace_name AND interaction.type_name = type_name)"),
            fk(
                "provenance_id",
                "links to provenance via (provenance.source_name = source_name AND provenance.reference_value = reference_value)",
                null_equal_columns=("reference_value",)
            )
        ],
        "constraints": {
            "pass1": [],
            "pass2": ["unique on (interaction_id, provenance_id, detection_method)"]
        }
    },
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
            dedup_identifier AS deduplication_identifier,
            dedup_identifier_type AS deduplication_identifier_type,
            'OmniPath' AS entity_type_namespace_name,
            entity_type AS entity_type_name
        FROM silver_entities
        WHERE dedup_identifier IS NOT NULL
          AND dedup_identifier_type IS NOT NULL'''
    },
    'entity_identifier': {
        'source_table': 'silver_entities',
        'select': f'''
            {ENTITY_IDENTIFIER_UNIONS}
        '''
    },
    'entity_identifier_provenance': {
        'source_table': 'silver_entities',
        'select': f'''
            {ENTITY_IDENTIFIER_PROVENANCE_UNIONS}
        '''
    },
    'entity_evidence': {
        'source_table': 'silver_entities',
        'select': '''
            SELECT DISTINCT
                dedup_identifier AS entity_deduplication_identifier,
                dedup_identifier_type AS entity_deduplication_identifier_type,
                source_database AS source_name,
                NULL::VARCHAR AS reference_value,
                annotations
            FROM silver_entities
            WHERE annotations IS NOT NULL
              AND annotations::VARCHAR != '{}'
              AND dedup_identifier IS NOT NULL
              AND dedup_identifier_type IS NOT NULL
        '''
    },
    'membership': {
        'source_table': 'silver_entities',
        'select': '''
            SELECT DISTINCT
                CAST(json_extract_string(member, 'member_id') AS VARCHAR) as member_deduplication_identifier,
                dedup_identifier_type as member_deduplication_identifier_type,
                dedup_identifier as parent_deduplication_identifier,
                dedup_identifier_type as parent_deduplication_identifier_type,
                'OmniPath' as role_namespace_name,
                COALESCE(CAST(json_extract_string(member, 'role') AS VARCHAR), 'member') as role_name,
                CAST(json_extract_string(member, 'stoichiometry') AS FLOAT) as stoichiometry,
                source_database as source_name,
                NULL::VARCHAR as reference_value
            FROM silver_entities,
                 unnest(json_extract(complex_members, '$[*]')) as member
            WHERE complex_members IS NOT NULL
              AND complex_members != '[]'
              AND dedup_identifier IS NOT NULL
              AND dedup_identifier_type IS NOT NULL
              AND CAST(json_extract_string(member, 'member_id') AS VARCHAR) IS NOT NULL
        '''
    },
    'interaction': {
        'source_table': 'silver_interactions',
        'select': '''
            SELECT DISTINCT
                entity_a_identifier as entity_a_deduplication_identifier,
                entity_a_identifier_type as entity_a_deduplication_identifier_type,
                entity_b_identifier as entity_b_deduplication_identifier,
                entity_b_identifier_type as entity_b_deduplication_identifier_type,
                'OmniPath' as type_namespace_name,
                interaction_type as type_name,
            FROM silver_interactions
        '''
    },
    'interaction_evidence': {
        'source_table': 'silver_interactions',
        'select': '''
            SELECT DISTINCT
                entity_a_identifier as entity_a_deduplication_identifier,
                entity_a_identifier_type as entity_a_deduplication_identifier_type,
                entity_b_identifier as entity_b_deduplication_identifier,
                entity_b_identifier_type as entity_b_deduplication_identifier_type,
                'OmniPath' as type_namespace_name,
                interaction_type as type_name,
                detection_method,
                causal_statement,
                sentence,
                is_directed,
                interaction_annotations as annotations,
                entity_a_context,
                entity_b_context,
                source_name,
                reference_value
            FROM silver_interactions
        '''
    },
}
