"""Stream unchanged published Parquets into the current main relational model.

This module has no PostgreSQL connection and performs no entity resolution.
The caller owns the DuckDB connection (prefer an on-disk database for a release),
PostgreSQL schema, transactions, partitions and COPY.  Only the small dimension
dictionaries cross the Python boundary; biological rows stay in DuckDB SQL.

Main's canonical relation is an endpoint/predicate triple.  Published qualified
statements remain distinct in ``parquet_statement`` and their evidence links.
Entity occurrences absent from the published contract are explicitly represented
as aggregate evidence, with status ``published`` rather than an invented match.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from omnipath_postgres.duckdb_projection import _literal, _scan, validate_resource
from omnipath_postgres.releases import PinnedRelease, ResourceSelection


PUBLISHED_STATUS_ID = 5
ENTITY_DATASET = "omnipath:published_entities"
CLAIM_DATASET = "omnipath:published_statement"
PUBLISHED_FALLBACK_NAMESPACE = "omnipath:unresolved_entity_key"

# Known outer CURIE prefixes only. Intrinsic identifier prefixes (CHEMBL,
# HMDB, SLM, C/R in KEGG IDs) remain part of the resulting identifier. Neither
# unknown namespaces nor arbitrary colon-containing strings are shortened.
BARE_IDENTIFIER_RULES = (
    ("chebi", "(?i)^chebi:([0-9]+)$"),
    ("pubchem", "(?i)^(?:pubchem|pubchem.compound|cid):([0-9]+)$"),
    ("chembl", "(?i)^chembl:(CHEMBL[0-9]+)$"),
    ("hmdb", "(?i)^hmdb:(HMDB[0-9]+)$"),
    ("kegg", "(?i)^(?:kegg|kegg.compound|cpd):(C[0-9]{5})$"),
    ("kegg_reaction", "(?i)^(?:kegg.reaction|rn):(R[0-9]{5})$"),
    ("lipidmaps", "(?i)^lipidmaps:(LM[A-Z0-9]+)$"),
    ("swisslipids", "(?i)^swisslipids:(SLM:[0-9]+)$"),
    ("entrez", "(?i)^(?:ncbigene|entrez):([0-9]+)$"),
    ("hgnc", "(?i)^hgnc:([0-9]+)$"),
    ("reactome", "(?i)^reactome:(R-[A-Z]{3}-[0-9]+(?:[.][0-9]+)?)$"),
)

# Explicit choices in inputs_v2. Namespace alone does not imply that a protein,
# pathway or activity typed as an ontology_class is an ontology term.
ONTOLOGY_SCOPES = (
    ("go", "go", "gene_ontology"), ("hpo", "hpo", "hpo"),
    ("mondo", "mondo", "mondo"), ("chebi", "chebi", "chebi"),
    ("chemont", "chemont", "chemont"), ("psi_mi", "mi", "psi-mi"),
    ("omnipath_ontology", "om", "omnipath"),
    ("reactome", "reactome", "reactome_pathways"),
    ("kegg", "kegg_pathway", "kegg_pathways"),
    ("brenda", "ec", "enzyme_classification"),
    ("uniprot", "uniprot_keyword", "uniprot_keywords"),
    ("swisslipids", "swisslipids", "swisslipids"),
)


@dataclass(frozen=True)
class CopyQuery:
    table: str
    columns: tuple[str, ...]
    query: str


@dataclass(frozen=True)
class AlignedCopyPlan:
    queries: tuple[CopyQuery, ...]
    dimensions: Mapping[str, tuple[tuple[Any, ...], ...]]
    counts: Mapping[str, int]
    sources: tuple[tuple[int, str], ...]
    compatibility: Mapping[str, Any]


# These are additive provenance/quantity tables, not replacement main tables.
# DDL takes a quoted schema token supplied by the loader, not user input.
COMPANION_DDL = {
    "parquet_entity": """resource text NOT NULL, version text NOT NULL,
        entity_key text NOT NULL, entity_id uuid NOT NULL,
        entity_evidence_id uuid NOT NULL, label text, namespace text,
        identifier text, entity_type text, taxon text,
        has_hierarchy boolean, parent_count bigint, child_count bigint,
        identifiers_present boolean NOT NULL, annotations_present boolean NOT NULL,
        PRIMARY KEY(resource, version, entity_key)""",
    "parquet_statement": """resource text NOT NULL, version text NOT NULL,
        relation_key text NOT NULL, relation_id uuid, statement_kind text,
        subject_entity_id uuid NOT NULL, object_entity_id uuid NOT NULL,
        predicate_id bigint NOT NULL, subject_label text, subject_type text,
        object_label text, object_type text, taxon text, is_directed boolean,
        sign integer, category text, interaction_class text, evidence_count bigint,
        sources text[], evidence_present boolean NOT NULL,
        annotations_present boolean NOT NULL,
        PRIMARY KEY(resource, version, relation_key)""",
    "parquet_evidence": """resource text NOT NULL, version text NOT NULL,
        relation_key text NOT NULL, ordinal bigint NOT NULL,
        source_id bigint NOT NULL, relation_evidence_id uuid NOT NULL,
        source text, dataset text, original_row_id text, upstream_id text,
        annotations_present boolean NOT NULL, synthetic boolean NOT NULL,
        PRIMARY KEY(resource, version, relation_key, ordinal)""",
    "parquet_identifier_occurrence": """resource text NOT NULL, version text NOT NULL,
        entity_key text NOT NULL, ordinal bigint NOT NULL, identifier_id uuid,
        ns text, identifier text, is_canonical boolean, source text,
        PRIMARY KEY(resource, version, entity_key, ordinal)""",
    "parquet_annotation_occurrence": """resource text NOT NULL, version text NOT NULL,
        owner_kind text NOT NULL CHECK(owner_kind IN ('entity','relation','evidence')),
        owner_key text NOT NULL, evidence_ordinal bigint NOT NULL,
        ordinal bigint NOT NULL, annotation_key uuid NOT NULL,
        term text, untyped_value text, source text, dataset text, scope text,
        PRIMARY KEY(resource, version, owner_kind, owner_key, evidence_ordinal, ordinal)""",
    "annotation_quantity": """annotation_key uuid PRIMARY KEY,
        has_numeric_value double precision, has_unit text, has_unit_prefix text,
        has_binary_relation text, source_field text, comparator text, published_value text""",
}


def companion_ddl(schema: str) -> tuple[str, ...]:
    """Return safely quoted CREATE TABLE statements for narrow companion tables."""
    if not isinstance(schema, str) or not schema or "\x00" in schema:
        raise ValueError("schema must be nonempty text without NUL")
    quoted = '"' + schema.replace('"', '""') + '"'
    return tuple(
        f'CREATE TABLE {quoted}."{name}" ({definition})'
        for name, definition in COMPANION_DDL.items()
    )


def _resources(release: PinnedRelease | Iterable[ResourceSelection]) -> tuple[ResourceSelection, ...]:
    resources = release.resources if isinstance(release, PinnedRelease) else tuple(release)
    if not resources:
        raise ValueError("An aligned projection requires at least one pinned resource")
    if len({resource.source for resource in resources}) != len(resources):
        raise ValueError("A release must select exactly one version per resource")
    return tuple(sorted(resources, key=lambda resource: resource.source))


def _uuid(payload: str) -> str:
    return f"ap_uuid({payload})"


def _key(*expressions: str) -> str:
    # JSON arrays are unambiguous even when data includes separators or quotes.
    return "to_json(list_value(" + ",".join(f"{item}::VARCHAR" for item in expressions) + "))"


def _create_inputs(con: Any, resources: tuple[ResourceSelection, ...]) -> None:
    for resource in resources:
        validate_resource(con, resource.directory)
    con.execute("""CREATE OR REPLACE MACRO ap_uuid(value) AS (
        (substr(md5(value),1,8)||'-'||substr(md5(value),9,4)||'-'||
         substr(md5(value),13,4)||'-'||substr(md5(value),17,4)||'-'||
         substr(md5(value),21,12))::UUID)""")
    for table, filename in (("ap_entity_raw", "entities.parquet"),
                            ("ap_statement_raw", "relations.parquet")):
        selects = [
            f"SELECT {_literal(resource.source)}::VARCHAR AS resource, "
            f"{_literal(resource.version)}::VARCHAR AS \"version\", p.* "
            f"FROM {_scan(resource.directory, filename)} p"
            for resource in resources
        ]
        con.execute(f"CREATE OR REPLACE TABLE {table} AS " + " UNION ALL ".join(selects))


def _create_flat_inputs(con: Any) -> None:
    con.execute("""CREATE OR REPLACE TABLE ap_identifier_raw AS
        SELECT e.resource, e.version, e.entity_key,
            unnest(e.identifiers) item,
            (generate_subscripts(e.identifiers,1)-1)::BIGINT ordinal
        FROM ap_entity_raw e""")
    con.execute("""CREATE OR REPLACE TABLE ap_evidence_raw AS
        SELECT r.resource, r.version, r.relation_key,
            unnest(r.evidence) item,
            (generate_subscripts(r.evidence,1)-1)::BIGINT ordinal
        FROM ap_statement_raw r""")
    con.execute("""CREATE OR REPLACE TABLE ap_annotation_raw AS
        SELECT resource,version,'entity'::VARCHAR owner_kind,entity_key owner_key,
            -1::BIGINT evidence_ordinal,
            ordinal,
            a.term,a.value,a.quantity,a.source,a.dataset,NULL::VARCHAR AS "scope"
        FROM (SELECT resource,version,entity_key,
                     (generate_subscripts(annotations,1)-1)::BIGINT ordinal,
                     unnest(annotations) a
              FROM ap_entity_raw)
        UNION ALL
        SELECT resource,version,'relation',relation_key,-1::BIGINT,
            ordinal,
            a.term,a.value,a.quantity,a.source,a.dataset,a.scope
        FROM (SELECT resource,version,relation_key,
                     (generate_subscripts(annotations,1)-1)::BIGINT ordinal,
                     unnest(annotations) a
              FROM ap_statement_raw)
        UNION ALL
        SELECT resource,version,'evidence',relation_key,evidence_ordinal,
            ordinal,
            a.term,a.value,a.quantity,a.source,a.dataset,a.scope
        FROM (SELECT resource,version,relation_key,ordinal evidence_ordinal,
                     (generate_subscripts(item.annotations,1)-1)::BIGINT ordinal,
                     unnest(item.annotations) a
              FROM ap_evidence_raw)""")


def _namespace_names() -> dict[str, str]:
    """Namespace spelling adapters only; no identifier translation/resolution."""
    from pypath.internals.cv_terms import IdentifierNamespaceCv, cv_term_label_accession
    members = {
        "uniprot": "UNIPROT", "uniprot_entry": "UNIPROT_ENTRY_NAME",
        "genesymbol": "GENE_NAME_PRIMARY", "genesymbol-syn": "GENE_NAME_SYNONYM",
        "entrez": "ENTREZ", "hgnc": "HGNC", "ensembl": "ENSEMBL",
        "ensg": "ENSEMBL", "ensp": "ENSEMBL", "enst": "ENSEMBL",
        "chebi": "CHEBI", "hmdb": "HMDB", "lipidmaps": "LIPIDMAPS",
        "swisslipids": "SWISSLIPIDS", "pubchem": "PUBCHEM_COMPOUND",
        "inchikey": "STANDARD_INCHI_KEY", "chembl": "CHEMBL_COMPOUND",
        "kegg": "KEGG_COMPOUND", "kegg_reaction": "KEGG_REACTION",
        "cas": "CAS", "name": "NAME",
        "synonym": "SYNONYM", "cv_term": "CV_TERM_ACCESSION", "ramp": "RAMP_ID",
        "refmet": "REFMET", "mirbase_precursor": "MIRBASE_PRECURSOR",
        "mirbase_mature": "MIRBASE_MATURE", "drugbank": "DRUGBANK",
        "reactome": "REACTOME_STABLE_ID", "lipid_name": "LIPID_NAME",
    }
    return {
        name: cv_term_label_accession(getattr(IdentifierNamespaceCv, member))
        for name, member in members.items()
        if hasattr(IdentifierNamespaceCv, member)
    }


def _dimension(con: Any, table: str, required: Iterable[str], existing: Iterable[tuple]) -> tuple:
    rows = tuple(existing)
    mapping = {str(row[1]): int(row[0]) for row in rows}
    if len(mapping) != len(rows) or len({row[0] for row in rows}) != len(rows):
        raise ValueError(f"Duplicate seeded dimension name or ID in {table}")
    next_id = max(mapping.values(), default=0)
    for name in sorted(set(required) - mapping.keys()):
        next_id += 1
        mapping[name] = next_id
    con.execute(f"CREATE OR REPLACE TABLE ap_{table} (id BIGINT,name VARCHAR)")
    result = tuple(sorted((identifier, name) for name, identifier in mapping.items()))
    if result:
        con.executemany(f"INSERT INTO ap_{table} VALUES (?,?)", result)
    return result


def _prepare_dimensions(con: Any, existing: Mapping[str, Iterable[tuple]]) -> dict[str, tuple]:
    aliases = _namespace_names()
    con.execute("CREATE OR REPLACE TABLE ap_namespace_alias (raw VARCHAR,name VARCHAR)")
    if aliases:
        con.executemany("INSERT INTO ap_namespace_alias VALUES (?,?)", sorted(aliases.items()))
    con.execute("CREATE OR REPLACE TABLE ap_ontology_scope (resource VARCHAR,namespace VARCHAR,ontology_id VARCHAR)")
    con.executemany("INSERT INTO ap_ontology_scope VALUES (?,?,?)", ONTOLOGY_SCOPES)
    required_sql = {
        "data_source": """SELECT resource FROM ap_entity_raw UNION SELECT resource FROM
            ap_statement_raw UNION SELECT item.source FROM ap_evidence_raw
            WHERE item.source IS NOT NULL""",
        "vocab_identifier_type": f"""SELECT coalesce(a.name,e.namespace) FROM ap_entity_raw e
            LEFT JOIN ap_namespace_alias a ON a.raw=e.namespace
            UNION SELECT coalesce(a.name,i.item.ns) FROM ap_identifier_raw i
            LEFT JOIN ap_namespace_alias a ON a.raw=i.item.ns WHERE i.item.ns IS NOT NULL
            UNION SELECT {_literal(PUBLISHED_FALLBACK_NAMESPACE)}""",
        "vocab_entity_type": "SELECT entity_type FROM ap_entity_raw WHERE entity_type IS NOT NULL",
        "vocab_relation_predicate": "SELECT predicate FROM ap_statement_raw WHERE predicate IS NOT NULL",
        "vocab_relation_category": "SELECT coalesce(category,'omnipath:unspecified') FROM ap_statement_raw",
        "vocab_annotation_scope": "SELECT scope FROM ap_annotation_raw WHERE scope IS NOT NULL AND scope<>'evidence'",
    }
    result = {}
    seeds = {"vocab_entity_role": ((1, "parent"), (2, "member")),
             "vocab_annotation_scope": ((1, "relation"), (2, "subject"), (3, "object")),
             "vocab_resolution_status": ((1, "resolved"), (2, "unresolved"),
                                         (3, "ambiguous"), (4, "unsupported"),
                                         (PUBLISHED_STATUS_ID, "published"))}
    for table, query in required_sql.items():
        # Only vocabulary names cross into Python. Some source SELECTs contain
        # millions of biological rows; deduplicating in _dimension after
        # fetchall would first allocate those repeated names in Python memory.
        names_query = (
            f"SELECT DISTINCT dimension_name FROM ({query}) "
            "required(dimension_name) WHERE dimension_name IS NOT NULL"
        )
        required = [row[0] for row in con.execute(names_query).fetchall()]
        result[table] = _dimension(con, table, required, existing.get(table, seeds.get(table, ())))
    for table in ("vocab_entity_role", "vocab_resolution_status"):
        result[table] = _dimension(con, table, (row[1] for row in seeds[table]),
                                   existing.get(table, seeds[table]))
    status = dict((name, identifier) for identifier, name in result["vocab_resolution_status"])
    if status["published"] != PUBLISHED_STATUS_ID:
        raise ValueError("Published status must have the main-compatible explicit ID 5")
    datasets = tuple(existing.get("dataset", ()))
    dataset_map = {(int(row[1]), str(row[2])): int(row[0]) for row in datasets}
    source_ids = {name: identifier for identifier, name in result["data_source"]}
    required = con.execute(f"""SELECT resource,{_literal(ENTITY_DATASET)} FROM ap_entity_raw
        UNION SELECT coalesce(item.source,resource),coalesce(item.dataset,{_literal(CLAIM_DATASET)})
              FROM ap_evidence_raw
        UNION SELECT resource,{_literal(CLAIM_DATASET)} FROM ap_statement_raw""").fetchall()
    next_id = max(dataset_map.values(), default=0)
    for source, name in sorted(required):
        key = (source_ids[source], name)
        if key not in dataset_map:
            next_id += 1
            dataset_map[key] = next_id
    con.execute("CREATE OR REPLACE TABLE ap_dataset (id BIGINT,source_id BIGINT,name VARCHAR)")
    result["dataset"] = tuple(sorted((identifier, *key) for key, identifier in dataset_map.items()))
    if result["dataset"]:
        con.executemany("INSERT INTO ap_dataset VALUES (?,?,?)", result["dataset"])
    return result


def _checked(con: Any, query: str, message: str) -> None:
    count = con.execute(f"SELECT count(*) FROM ({query}) invalid").fetchone()[0]
    if count:
        # Deliberately aggregate only: diagnostics must not dump biological rows.
        raise ValueError(f"{message}: {count} conflicting rows/groups")


def _validate_canonical_inputs(con: Any) -> None:
    _checked(con, """SELECT entity_key FROM ap_entity_raw WHERE entity_type IS NULL
        OR namespace IS NULL OR identifier IS NULL""",
        "Main canonical entities require published type, namespace and identifier")
    _checked(con, """SELECT entity_key FROM ap_entity_raw GROUP BY entity_key
        HAVING count(DISTINCT to_json(struct_pack(entity_type:=entity_type,
               namespace:=namespace,identifier:=identifier)))>1""",
        "A published entity identity has conflicting canonical fields across resources")
    _checked(con, """SELECT resource,version,entity_key FROM ap_entity_raw
        GROUP BY resource,version,entity_key HAVING count(*)>1""",
        "Duplicate published entity identity within one resource")
    _checked(con, """SELECT resource,version,relation_key FROM ap_statement_raw
        GROUP BY resource,version,relation_key HAVING count(*)>1""",
        "Duplicate published statement identity within one resource")
    # The publisher hashes endpoints, predicate, kind and Biolink qualifiers.
    # Its scalar taxon is a per-resource consensus, not part of that key (see
    # writer._relations_sql). Keep that consensus and the exact in_taxon claims
    # scoped to resource/version; globally equating them rejects valid sources.
    # Retain the existing derived-field checks until a demonstrated publisher
    # version difference warrants a separately reviewed presentation policy.
    _checked(con, """SELECT relation_key FROM ap_statement_raw GROUP BY relation_key
        HAVING count(DISTINCT to_json(struct_pack(subject:=subject_entity_key,
            object:=object_entity_key,predicate:=predicate,kind:=statement_kind,
            directed:=is_directed,sign:=sign,category:=category,
            interaction_class:=interaction_class)))>1""",
        "A published statement identity has conflicting scientific fields")
    _checked(con, """SELECT e.entity_key FROM ap_entity_raw e
        WHERE nullif(e.taxon,'') IS NOT NULL AND try_cast(e.taxon AS BIGINT) IS NULL""",
        "Published taxonomy cannot be represented in main bigint taxonomy_id")
    _checked(con, """SELECT r.relation_key FROM ap_statement_raw r
        LEFT JOIN ap_entity_raw s ON s.resource=r.resource AND s.version=r.version
             AND s.entity_key=r.subject_entity_key
        LEFT JOIN ap_entity_raw o ON o.resource=r.resource AND o.version=r.version
             AND o.entity_key=r.object_entity_key
        WHERE s.entity_key IS NULL OR o.entity_key IS NULL OR r.predicate IS NULL""",
        "Published statement has missing canonical endpoint or predicate")


def _create_entity_projection(con: Any) -> None:
    # Published identity is authoritative. A null/known taxon disagreement does
    # not mint a second entity; two distinct known taxa make canonical taxonomy
    # unknown. Every exact published occurrence remains in evidence/crosswalk.
    con.execute("""CREATE OR REPLACE TABLE ap_entity_canonical_input AS SELECT
        entity_key,entity_type,namespace,identifier,
        CASE WHEN min(try_cast(taxon AS BIGINT))=max(try_cast(taxon AS BIGINT))
             THEN min(try_cast(taxon AS BIGINT)) ELSE NULL END taxonomy_id
        FROM ap_entity_raw GROUP BY entity_key,entity_type,namespace,identifier""")
    con.execute("""CREATE OR REPLACE TABLE ap_entity_natural_conflict AS
        SELECT e.entity_type,e.taxonomy_id,coalesce(a.name,e.namespace) namespace_name,e.identifier
        FROM ap_entity_canonical_input e LEFT JOIN ap_namespace_alias a ON a.raw=e.namespace
        GROUP BY ALL HAVING count(*)>1""")
    _checked(con, """SELECT e.entity_key FROM ap_entity_canonical_input e
        JOIN ap_entity_natural_conflict c ON c.entity_type=e.entity_type
            AND c.taxonomy_id IS NOT DISTINCT FROM e.taxonomy_id AND c.identifier=e.identifier
        LEFT JOIN ap_namespace_alias a ON a.raw=e.namespace
        WHERE c.namespace_name=coalesce(a.name,e.namespace)
          AND e.namespace NOT IN ('name','synonym')""",
        "Distinct published entities violate main canonical natural-key uniqueness")
    con.execute(f"""CREATE OR REPLACE TABLE ap_entity AS SELECT
        {_uuid(_key(_literal('published-entity'), 'e.entity_key'))} entity_id,
        et.id entity_type_id,e.taxonomy_id,
        CASE WHEN c.identifier IS NOT NULL THEN fallback.id ELSE it.id END canonical_identifier_type_id,
        CASE WHEN c.identifier IS NOT NULL THEN e.entity_key ELSE e.identifier END canonical_identifier,
        {PUBLISHED_STATUS_ID}::SMALLINT resolution_status_id,
        'published_parquet'::VARCHAR resolution_mechanism
        FROM ap_entity_canonical_input e
        JOIN ap_vocab_entity_type et ON et.name=e.entity_type
        LEFT JOIN ap_namespace_alias a ON a.raw=e.namespace
        JOIN ap_vocab_identifier_type it ON it.name=coalesce(a.name,e.namespace)
        JOIN ap_vocab_identifier_type fallback ON fallback.name={_literal(PUBLISHED_FALLBACK_NAMESPACE)}
        LEFT JOIN ap_entity_natural_conflict c ON c.entity_type=e.entity_type
            AND c.taxonomy_id IS NOT DISTINCT FROM e.taxonomy_id AND c.identifier=e.identifier
            AND c.namespace_name=coalesce(a.name,e.namespace)""")
    _checked(con, """SELECT entity_type_id,taxonomy_id,canonical_identifier_type_id,
        canonical_identifier FROM ap_entity GROUP BY ALL HAVING count(*)>1""",
        "Distinct published entities violate main canonical natural-key uniqueness")
    con.execute(f"""CREATE OR REPLACE VIEW ap_entity_occurrence AS SELECT e.*,
        {_uuid(_key(_literal('published-entity'), 'e.entity_key'))} entity_id,
        {_uuid(_key(_literal('aggregate-entity-evidence'),'e.resource','e.version','e.entity_key'))}
            entity_evidence_id,
        ds.id source_id,d.id dataset_id,et.id entity_type_id,
        hash({_key('e.resource','e.entity_key')})::UBIGINT % 9223372036854775807 row_id
        FROM ap_entity_raw e JOIN ap_data_source ds ON ds.name=e.resource
        JOIN ap_dataset d ON d.source_id=ds.id AND d.name={_literal(ENTITY_DATASET)}
        JOIN ap_vocab_entity_type et ON et.name=e.entity_type""")
    # Dictionary deduplication mirrors main. Original null structs/occurrences
    # remain in the narrow companion rather than acquiring invented namespaces.
    con.execute("""CREATE OR REPLACE TABLE ap_identifier_input AS
        SELECT namespace ns,identifier FROM ap_entity_raw
        UNION SELECT item.ns,item.id FROM ap_identifier_raw
        UNION SELECT it.name,e.canonical_identifier FROM ap_entity e
              JOIN ap_vocab_identifier_type it ON it.id=e.canonical_identifier_type_id""")
    con.execute("CREATE OR REPLACE TABLE ap_bare_identifier_rule (namespace VARCHAR,pattern VARCHAR)")
    con.executemany("INSERT INTO ap_bare_identifier_rule VALUES (?,?)", BARE_IDENTIFIER_RULES)
    con.execute(f"""CREATE OR REPLACE TABLE ap_identifier_alias AS SELECT DISTINCT
        {_uuid(_key(_literal('identifier'),'coalesce(a.name,i.ns)','i.identifier'))} original_identifier_id,
        {_uuid(_key(_literal('identifier'),'coalesce(a.name,i.ns)',"regexp_extract(i.identifier,rule.pattern,1)"))} alias_identifier_id,
        coalesce(a.name,i.ns) namespace_name,
        regexp_extract(i.identifier,rule.pattern,1) alias_value
        FROM ap_identifier_input i JOIN ap_bare_identifier_rule rule ON rule.namespace=i.ns
        LEFT JOIN ap_namespace_alias a ON a.raw=i.ns
        WHERE regexp_full_match(i.identifier,rule.pattern)""")
    con.execute(f"""CREATE OR REPLACE TABLE ap_identifier AS SELECT DISTINCT
        {_uuid(_key(_literal('identifier'),'coalesce(a.name,i.ns)','i.identifier'))} identifier_id,
        it.id identifier_type_id,i.identifier AS "value",
        NULL::VARCHAR value_normalized
        FROM (SELECT ns,identifier FROM ap_identifier_input
              UNION SELECT namespace_name,alias_value FROM ap_identifier_alias) i
        LEFT JOIN ap_namespace_alias a ON a.raw=i.ns
        JOIN ap_vocab_identifier_type it ON it.name=coalesce(a.name,i.ns)
        WHERE i.identifier IS NOT NULL""")
    con.execute(f"""CREATE OR REPLACE VIEW ap_identifier_occurrence AS SELECT i.*,
        CASE WHEN item.ns IS NOT NULL AND item.id IS NOT NULL THEN
            {_uuid(_key(_literal('identifier'),'coalesce(a.name,i.item.ns)','i.item.id'))}
        END identifier_id
        FROM ap_identifier_raw i LEFT JOIN ap_namespace_alias a ON a.raw=i.item.ns""")


def _create_statement_projection(con: Any) -> None:
    con.execute(f"""CREATE OR REPLACE VIEW ap_statement AS SELECT r.*,
        s.entity_id subject_entity_id,o.entity_id object_entity_id,rp.id predicate_id,
        rc.id relation_category_id,
        coalesce(os.ontology_id,'omnipath:published-scope:'||r.resource||':'||s.namespace)
            ontology_id,
        os.ontology_id IS NOT NULL ontology_scope_recovered,
        CASE WHEN r.statement_kind='relation' THEN
            {_uuid(_key(_literal('canonical-relation'),'s.entity_id','rp.name','o.entity_id'))}
        END relation_id
        FROM ap_statement_raw r
        JOIN ap_entity_occurrence s ON s.resource=r.resource AND s.version=r.version
             AND s.entity_key=r.subject_entity_key
        JOIN ap_entity_occurrence o ON o.resource=r.resource AND o.version=r.version
             AND o.entity_key=r.object_entity_key
        JOIN ap_vocab_relation_predicate rp ON rp.name=r.predicate
        JOIN ap_vocab_relation_category rc ON rc.name=coalesce(r.category,'omnipath:unspecified')
        LEFT JOIN ap_ontology_scope os ON os.resource=lower(r.resource) AND os.namespace=s.namespace
        AND (s.namespace<>'reactome' OR s.entity_type='pathway')""")
    con.execute("""CREATE OR REPLACE TABLE ap_relation AS SELECT relation_id,
        subject_entity_id,predicate_id,object_entity_id,
        CASE WHEN count(DISTINCT relation_category_id)=1 THEN min(relation_category_id)
             ELSE NULL END relation_category_id
        FROM ap_statement WHERE relation_id IS NOT NULL
        GROUP BY relation_id,subject_entity_id,predicate_id,object_entity_id""")
    con.execute(f"""CREATE OR REPLACE TABLE ap_evidence AS
        SELECT e.resource,e.version,e.relation_key,e.ordinal,
            {_uuid(_key(_literal('published-relation-evidence'),'e.resource','e.version',
                        'e.relation_key','e.ordinal'))} relation_evidence_id,
            ds.id source_id,d.id dataset_id,
            coalesce(try_cast(e.item.row_id AS BIGINT),
                     (hash({_key('e.resource','e.item.source','e.item.dataset','e.item.row_id')})
                      % 9223372036854775807)::BIGINT) row_id,
            e.item.source AS "source",e.item.dataset AS dataset,e.item.row_id original_row_id,
            e.item.upstream_id upstream_id,e.item.annotations IS NOT NULL annotations_present,
            false synthetic
        FROM ap_evidence_raw e
        JOIN ap_data_source ds ON ds.name=coalesce(e.item.source,e.resource)
        JOIN ap_dataset d ON d.source_id=ds.id
             AND d.name=coalesce(e.item.dataset,{_literal(CLAIM_DATASET)})
        UNION ALL
        SELECT r.resource,r.version,r.relation_key,-1::BIGINT,
            {_uuid(_key(_literal('published-statement-evidence'),'r.resource','r.version',
                        'r.relation_key'))}, ds.id,d.id,
            (hash({_key('r.resource','r.relation_key')}) % 9223372036854775807)::BIGINT,
            NULL::VARCHAR,NULL::VARCHAR,NULL::VARCHAR,NULL::VARCHAR,false,true
        FROM ap_statement r
        JOIN ap_data_source ds ON ds.name=r.resource
        JOIN ap_dataset d ON d.source_id=ds.id AND d.name={_literal(CLAIM_DATASET)}
        WHERE coalesce(len(r.evidence),0)=0""")
    _checked(con, """SELECT source_id,dataset_id,row_id FROM ap_evidence
        WHERE NOT synthetic AND original_row_id IS NOT NULL
        GROUP BY source_id,dataset_id,row_id
        HAVING count(DISTINCT coalesce(try_cast(original_row_id AS BIGINT)::VARCHAR,
                                      original_row_id))>1""",
        "Original source row identities collide in the main bigint row representation")


def _create_annotation_projection(con: Any) -> None:
    con.execute(f"""CREATE OR REPLACE TABLE ap_annotation_occurrence AS SELECT a.*,
        {_uuid("to_json(struct_pack(kind:='annotation',term:=term,value:=value,quantity:=quantity))")}
            annotation_key
        FROM ap_annotation_raw a""")
    con.execute("""CREATE OR REPLACE TABLE ap_annotation AS SELECT DISTINCT
        annotation_key,term,
        CASE WHEN quantity.has_numeric_value IS NOT NULL THEN
             quantity.has_numeric_value::VARCHAR ELSE value END AS "value",
        quantity.has_unit unit
        FROM ap_annotation_occurrence WHERE term IS NOT NULL""")
    con.execute("""CREATE OR REPLACE TABLE ap_annotation_quantity AS SELECT DISTINCT
        annotation_key,quantity.has_numeric_value has_numeric_value,
        quantity.has_unit has_unit,quantity.has_unit_prefix has_unit_prefix,
        quantity.has_binary_relation has_binary_relation,quantity.source_field source_field,
        quantity.comparator comparator,value published_value
        FROM ap_annotation_occurrence WHERE quantity IS NOT NULL""")
    _checked(con, "SELECT annotation_key FROM ap_annotation GROUP BY annotation_key HAVING count(*)>1",
             "Annotation dictionary UUID collision")
    _checked(con, "SELECT annotation_key FROM ap_annotation_quantity GROUP BY annotation_key HAVING count(*)>1",
             "Quantity dictionary UUID collision")


def _pg_array(expression: str) -> str:
    # The COPY cell is PostgreSQL array input, including quoting/NULL elements.
    return f"""CASE WHEN {expression} IS NULL THEN NULL ELSE '{{' || coalesce(
        array_to_string(list_transform({expression}, x -> CASE WHEN x IS NULL THEN 'NULL'
            ELSE chr(34)||replace(replace(x,chr(92),chr(92)||chr(92)),
                 chr(34),chr(92)||chr(34))||chr(34) END), ','),'') || '}}' END"""


def _prepare_relation_evidence_annotation_copy(
    connection: Any,
    *,
    shard_count: int = 128,
    on_progress: Callable[[Mapping[str, Any]], None] | None = None,
) -> None:
    """Materialize exact annotation links without one release-wide anti-join.

    Persist only primitive link inputs, clustered by a complete owner's shard.
    Values, quantities and arrays already have their own tables and never enter
    these joins. A published evidence UUID belongs to one resource/version/owner,
    so four-column deduplication within an owner-complete shard is also global.
    """
    if isinstance(shard_count, bool) or not isinstance(shard_count, int) or shard_count < 1:
        raise ValueError("shard_count must be a positive integer")

    def report(stage: str, state: str, **details: Any) -> None:
        if on_progress is not None:
            on_progress({"stage": stage, "state": state,
                         "shard_count": shard_count, **details})

    report("annotation_input", "start")
    connection.execute(f"""CREATE OR REPLACE TABLE ap_annotation_link_input AS
        SELECT resource,version,owner_key,evidence_ordinal,annotation_key,source,dataset,
            CASE WHEN scope='evidence' THEN 'relation'
                 ELSE coalesce(scope,'relation') END normalized_scope,
            owner_kind='evidence' is_evidence,term IS NOT NULL is_typed,
            (hash(resource,version,owner_key)%{shard_count})::INTEGER owner_shard
        FROM ap_annotation_occurrence
        WHERE owner_kind IN ('relation','evidence')
        ORDER BY owner_shard""")
    report("annotation_input", "done")
    report("evidence_input", "start")
    connection.execute(f"""CREATE OR REPLACE TABLE ap_evidence_link_input AS
        SELECT resource,version,relation_key owner_key,ordinal,source_id,
            relation_evidence_id,source,dataset,synthetic,
            (hash(resource,version,relation_key)%{shard_count})::INTEGER owner_shard
        FROM ap_evidence ORDER BY owner_shard""")
    report("evidence_input", "done")
    connection.execute("""CREATE OR REPLACE TABLE ap_copy_relation_evidence_annotation (
        source_id BIGINT,relation_evidence_id UUID,annotation_key UUID,
        annotation_scope_id SMALLINT)""")
    for shard in range(shard_count):
        report("shard", "start", shard=shard)
        connection.execute(f"""INSERT INTO ap_copy_relation_evidence_annotation
            WITH annotations AS MATERIALIZED (
                SELECT resource,version,owner_key,evidence_ordinal,annotation_key,
                    source,dataset,normalized_scope,is_evidence,is_typed
                FROM ap_annotation_link_input WHERE owner_shard={shard}
            ), evidence AS MATERIALIZED (
                SELECT resource,version,owner_key,ordinal,source_id,
                    relation_evidence_id,source,dataset,synthetic
                FROM ap_evidence_link_input WHERE owner_shard={shard}
            ), observed AS MATERIALIZED (
                SELECT DISTINCT resource,version,owner_key,annotation_key,
                    source,dataset,normalized_scope
                FROM annotations WHERE is_evidence
            ), true_statement_annotation AS (
                SELECT a.resource,a.version,a.owner_key,a.annotation_key,
                    a.source,a.dataset,a.normalized_scope
                FROM annotations a WHERE NOT a.is_evidence AND a.is_typed
                  AND NOT EXISTS (
                    SELECT 1 FROM observed o
                    WHERE o.resource=a.resource AND o.version=a.version
                      AND o.owner_key=a.owner_key AND o.annotation_key=a.annotation_key
                      AND o.source IS NOT DISTINCT FROM a.source
                      AND o.dataset IS NOT DISTINCT FROM a.dataset
                      AND o.normalized_scope=a.normalized_scope
                  )
            )
            SELECT e.source_id,e.relation_evidence_id,a.annotation_key,
                sc.id::SMALLINT annotation_scope_id
            FROM annotations a JOIN evidence e
              ON a.resource=e.resource AND a.version=e.version AND a.owner_key=e.owner_key
             AND a.evidence_ordinal=e.ordinal
            JOIN ap_vocab_annotation_scope sc ON sc.name=a.normalized_scope
            WHERE a.is_evidence AND a.is_typed
            UNION
            SELECT e.source_id,e.relation_evidence_id,a.annotation_key,
                sc.id::SMALLINT annotation_scope_id
            FROM true_statement_annotation a JOIN evidence e
              ON a.resource=e.resource AND a.version=e.version AND a.owner_key=e.owner_key
             AND (e.synthetic OR
                  ((a.source IS NULL OR a.source=e.source)
                   AND (a.dataset IS NULL OR a.dataset=e.dataset)))
            JOIN ap_vocab_annotation_scope sc ON sc.name=a.normalized_scope""")
        report("shard", "done", shard=shard)
    connection.execute("DROP TABLE ap_annotation_link_input")
    connection.execute("DROP TABLE ap_evidence_link_input")


def _queries() -> tuple[CopyQuery, ...]:
    result: list[CopyQuery] = []
    def add(table: str, columns: str, query: str) -> None:
        result.append(CopyQuery(table, tuple(columns.split()), query))

    add("entity", "entity_id entity_type_id taxonomy_id canonical_identifier_type_id "
        "canonical_identifier resolution_status_id resolution_mechanism", "SELECT * FROM ap_entity")
    add("identifier_evidence", "identifier_id identifier_type_id value value_normalized",
        "SELECT * FROM ap_identifier")
    add("annotation", "annotation_key term value unit", "SELECT * FROM ap_annotation")
    add("entity_evidence", "source_id entity_evidence_id dataset_id row_id "
        "parent_entity_evidence_id entity_role_id entity_type_id taxonomy_id", """
        SELECT source_id,entity_evidence_id,dataset_id,row_id::BIGINT,NULL::UUID,
            1::SMALLINT,entity_type_id,try_cast(taxon AS BIGINT)
        FROM ap_entity_occurrence""")
    add("entity_evidence_resolution", "source_id entity_evidence_id status_id entity_id "
        "reason_id molecular_type_id", f"""SELECT source_id,entity_evidence_id,
            {PUBLISHED_STATUS_ID}::SMALLINT,entity_id,NULL::SMALLINT,NULL::SMALLINT
        FROM ap_entity_occurrence""")
    add("entity_identifier", "source_id entity_id identifier_id", f"""WITH published_links AS (
        SELECT DISTINCT e.source_id,e.entity_id,i.identifier_id
        FROM ap_identifier_occurrence i JOIN ap_entity_occurrence e
        USING(resource,version,entity_key) WHERE i.identifier_id IS NOT NULL
        UNION SELECT DISTINCT e.source_id,e.entity_id,
            {_uuid(_key(_literal('identifier'),'coalesce(a.name,e.namespace)','e.identifier'))}
        FROM ap_entity_occurrence e LEFT JOIN ap_namespace_alias a ON a.raw=e.namespace
        UNION SELECT DISTINCT occurrence.source_id,e.entity_id,
            {_uuid(_key(_literal('identifier'),'it.name','e.canonical_identifier'))}
        FROM ap_entity_occurrence occurrence JOIN ap_entity e ON e.entity_id=occurrence.entity_id
        JOIN ap_vocab_identifier_type it ON it.id=e.canonical_identifier_type_id)
        SELECT * FROM published_links
        UNION SELECT links.source_id,links.entity_id,a.alias_identifier_id
        FROM published_links links JOIN ap_identifier_alias a
             ON a.original_identifier_id=links.identifier_id""")
    # Computed lookup aliases do not claim source evidence and consequently do
    # not acquire main's authority/reference role from an occurrence join.
    add("entity_evidence_identifier", "source_id entity_evidence_id identifier_id", """
        SELECT DISTINCT e.source_id,e.entity_evidence_id,i.identifier_id
        FROM ap_identifier_occurrence i JOIN ap_entity_occurrence e
        USING(resource,version,entity_key) WHERE i.identifier_id IS NOT NULL""")
    add("entity_evidence_annotation", "source_id entity_evidence_id annotation_key", """
        SELECT DISTINCT e.source_id,e.entity_evidence_id,a.annotation_key
        FROM ap_annotation_occurrence a JOIN ap_entity_occurrence e
        ON a.resource=e.resource AND a.version=e.version AND a.owner_key=e.entity_key
        WHERE a.owner_kind='entity' AND a.term IS NOT NULL""")
    add("relation", "relation_id subject_entity_id predicate_id object_entity_id relation_category_id",
        "SELECT * FROM ap_relation")
    add("relation_evidence", "source_id relation_evidence_id dataset_id row_id "
        "subject_entity_evidence_id subject_entity_id predicate_id object_entity_evidence_id "
        "object_entity_id relation_category_id", """SELECT e.source_id,e.relation_evidence_id,
        e.dataset_id,e.row_id,NULL::UUID,r.subject_entity_id,r.predicate_id,NULL::UUID,
        r.object_entity_id,r.relation_category_id
        FROM ap_evidence e JOIN ap_statement r USING(resource,version,relation_key)""")
    add("relation_evidence_relation", "source_id relation_id relation_evidence_id", """
        SELECT e.source_id,r.relation_id,e.relation_evidence_id FROM ap_evidence e
        JOIN ap_statement r USING(resource,version,relation_key) WHERE r.relation_id IS NOT NULL""")
    add("relation_evidence_annotation", "source_id relation_evidence_id annotation_key "
        "annotation_scope_id", """WITH true_statement_annotation AS (
            SELECT a.* FROM ap_annotation_occurrence a
            WHERE a.owner_kind='relation' AND a.term IS NOT NULL AND NOT EXISTS (
                SELECT 1 FROM ap_annotation_occurrence observed
                WHERE observed.owner_kind='evidence' AND observed.resource=a.resource
                  AND observed.version=a.version AND observed.owner_key=a.owner_key
                  AND observed.annotation_key=a.annotation_key
                  AND observed.source IS NOT DISTINCT FROM a.source
                  AND observed.dataset IS NOT DISTINCT FROM a.dataset
                  AND (CASE WHEN observed.scope='evidence' THEN 'relation'
                            ELSE coalesce(observed.scope,'relation') END)
                    = (CASE WHEN a.scope='evidence' THEN 'relation'
                            ELSE coalesce(a.scope,'relation') END)
            )
        ), own_annotation AS (
            SELECT * FROM ap_annotation_occurrence WHERE owner_kind='evidence' AND term IS NOT NULL
            UNION ALL SELECT * FROM true_statement_annotation
        )
        SELECT DISTINCT e.source_id,e.relation_evidence_id,
        a.annotation_key,sc.id::SMALLINT
        FROM own_annotation a JOIN ap_evidence e
        ON a.resource=e.resource AND a.version=e.version AND a.owner_key=e.relation_key
        AND (a.owner_kind='relation' OR (a.owner_kind='evidence' AND a.evidence_ordinal=e.ordinal))
        AND (a.owner_kind='evidence' OR e.synthetic OR
             ((a.source IS NULL OR a.source=e.source) AND (a.dataset IS NULL OR a.dataset=e.dataset)))
        JOIN ap_vocab_annotation_scope sc ON sc.name=CASE WHEN a.scope='evidence' THEN 'relation'
                                                       ELSE coalesce(a.scope,'relation') END
        WHERE a.term IS NOT NULL""")
    add("entity_ontology_relation", "source_id subject_entity_id predicate_id object_entity_id ontology_id",
        """SELECT DISTINCT ds.id,r.subject_entity_id,r.predicate_id,r.object_entity_id,r.ontology_id
        FROM ap_statement r JOIN ap_data_source ds ON ds.name=r.resource
        WHERE r.statement_kind='ontology_axiom'""")
    add("ontology_terms", "source_id term_entity_id term_id ontology_prefix label definition "
        "ontology_id synonyms synonyms_text sources", f"""
        WITH syn AS (
            SELECT resource,version,entity_key,list(item.id ORDER BY ordinal) synonyms
            FROM ap_identifier_occurrence WHERE item.ns='synonym' AND item.id IS NOT NULL
            GROUP BY resource,version,entity_key
        )
        SELECT e.source_id,e.entity_id,e.identifier,e.namespace,
               coalesce(e.label,e.identifier),first(a.value ORDER BY a.ordinal)
                   FILTER(WHERE a.term='description'),
               os.ontology_id,{_pg_array('coalesce(syn.synonyms,[]::VARCHAR[])')},
               coalesce(array_to_string(syn.synonyms,' '),''),{_pg_array('list_value(e.resource)')}
        FROM ap_entity_occurrence e
        JOIN ap_ontology_scope os ON os.resource=lower(e.resource) AND os.namespace=e.namespace
        LEFT JOIN syn ON syn.resource=e.resource AND syn.version=e.version AND syn.entity_key=e.entity_key
        LEFT JOIN ap_annotation_occurrence a
        ON a.resource=e.resource AND a.version=e.version AND a.owner_kind='entity'
           AND a.owner_key=e.entity_key
        WHERE (e.namespace<>'reactome' OR e.entity_type='pathway')
        GROUP BY e.source_id,e.entity_id,e.identifier,e.namespace,e.label,e.resource,
                 os.ontology_id,syn.synonyms""")
    add("parquet_entity", "resource version entity_key entity_id entity_evidence_id label namespace "
        "identifier entity_type taxon has_hierarchy parent_count child_count identifiers_present "
        "annotations_present", """SELECT resource,version,entity_key,entity_id,entity_evidence_id,
        label,namespace,identifier,entity_type,taxon,has_hierarchy,parent_count,child_count,
        identifiers IS NOT NULL,annotations IS NOT NULL FROM ap_entity_occurrence""")
    add("parquet_statement", "resource version relation_key relation_id statement_kind "
        "subject_entity_id object_entity_id predicate_id subject_label subject_type object_label "
        "object_type taxon is_directed sign category interaction_class evidence_count sources "
        "evidence_present annotations_present", f"""SELECT resource,version,relation_key,relation_id,
        statement_kind,subject_entity_id,object_entity_id,predicate_id,subject_label,subject_type,
        object_label,object_type,taxon,is_directed,sign,category,interaction_class,evidence_count,
        {_pg_array('sources')},evidence IS NOT NULL,annotations IS NOT NULL FROM ap_statement""")
    add("parquet_evidence", "resource version relation_key ordinal source_id relation_evidence_id "
        "source dataset original_row_id upstream_id annotations_present synthetic",
        """SELECT resource,version,relation_key,ordinal,source_id,relation_evidence_id,
        source,dataset,original_row_id,upstream_id,annotations_present,synthetic FROM ap_evidence""")
    add("parquet_identifier_occurrence", "resource version entity_key ordinal identifier_id "
        "ns identifier is_canonical source", """SELECT resource,version,entity_key,ordinal,
        identifier_id,item.ns,item.id,item.is_canonical,item.source FROM ap_identifier_occurrence""")
    add("parquet_annotation_occurrence", "resource version owner_kind owner_key evidence_ordinal "
        "ordinal annotation_key term untyped_value source dataset scope", """
        SELECT resource,version,owner_kind,owner_key,evidence_ordinal,ordinal,annotation_key,
        term,CASE WHEN term IS NULL THEN value END,source,dataset,scope FROM ap_annotation_occurrence""")
    add("annotation_quantity", "annotation_key has_numeric_value has_unit has_unit_prefix "
        "has_binary_relation source_field comparator published_value", "SELECT * FROM ap_annotation_quantity")
    return tuple(result)


def prepare_aligned_release(
    connection: Any,
    release: PinnedRelease | Iterable[ResourceSelection],
    *,
    dimension_rows: Mapping[str, Iterable[tuple]] | None = None,
    progress: Callable[[Mapping[str, Any]], None] | None = None,
) -> AlignedCopyPlan:
    """Prepare streaming COPY SELECTs, keeping main's strict identity contracts.

    ``dimension_rows`` contains existing ``(id,name)`` rows by main vocabulary
    table; dataset rows are ``(dataset_id,source_id,name)``. Returned dimensions
    include those seed rows; the caller inserts only new rows and advances its
    sequences. Data sources in the result tell it which source partitions to
    create. ``counts`` are exact prepared COPY row counts, not estimates.

    SQL work is cancellable through DuckDB.  Nothing mutates a PostgreSQL schema
    or an input artifact. Full raw payload Parquets are never scanned here.
    """
    def report(phase: str, **details: Any) -> None:
        if progress is not None:
            progress({"phase": phase, **details})

    resources = _resources(release)
    report("validate_and_stage", resources=len(resources))
    _create_inputs(connection, resources)
    report("flatten_published_arrays")
    _create_flat_inputs(connection)
    report("validate_canonical_inputs")
    _validate_canonical_inputs(connection)
    report("dimensions")
    dimensions = _prepare_dimensions(connection, dimension_rows or {})
    report("entities")
    _create_entity_projection(connection)
    report("statements_and_evidence")
    _create_statement_projection(connection)
    report("annotation_dictionary")
    _create_annotation_projection(connection)
    # Materialize each final SELECT once. DISTINCT link reductions and shared
    # joins must not execute independently for counting and PostgreSQL COPY.
    # Caller-owned on-disk DuckDB keeps this bounded by its memory limit.
    queries = []
    counts = {}
    for item in _queries():
        report("prepare_copy", table=item.table)
        names = ",".join('"' + column + '"' for column in item.columns)
        staged = f"ap_copy_{item.table}"
        if item.table == "relation_evidence_annotation":
            _prepare_relation_evidence_annotation_copy(
                connection,
                on_progress=lambda details: report("prepare_annotation_links", **details),
            )
        else:
            connection.execute(f"CREATE OR REPLACE TABLE {staged} ({names}) AS {item.query}")
        counts[item.table] = int(connection.execute(f"SELECT count(*) FROM {staged}").fetchone()[0])
        queries.append(CopyQuery(item.table, item.columns, f"SELECT {names} FROM {staged}"))
        report("copy_prepared", table=item.table, rows=counts[item.table])
    return AlignedCopyPlan(
        tuple(queries), dimensions, counts, dimensions["data_source"],
        {"resolution": "published identities; no original matched flag inferred",
         "entity_evidence": "one explicit aggregate per published resource/entity",
         "original_entity_occurrences_available": False,
         "original_resolution_diagnostics_available": False,
         "relation_identity": "main triple; qualified published statement crosswalk retained",
         "raw_payloads_loaded": False, "record_json_loaded": False,
         "safe_bare_identifier_alias_pairs": int(connection.execute(
             "SELECT count(*) FROM ap_identifier_alias").fetchone()[0]),
         "unknown_ontology_scope_statements": int(connection.execute(
             "SELECT count(*) FROM ap_statement WHERE statement_kind='ontology_axiom' "
             "AND NOT ontology_scope_recovered").fetchone()[0]),
         "source_scoped_name_fallback_entities": int(connection.execute(
             "SELECT count(*) FROM ap_entity e JOIN ap_vocab_identifier_type it "
             "ON it.id=e.canonical_identifier_type_id WHERE it.name=?",
             [PUBLISHED_FALLBACK_NAMESPACE]).fetchone()[0]),
         "shared_identity_taxonomy_conflicts": int(connection.execute(
             "SELECT count(*) FROM (SELECT entity_key FROM ap_entity_raw GROUP BY entity_key "
             "HAVING count(DISTINCT nullif(taxon,''))>1) q").fetchone()[0]),
         "shared_identity_taxonomy_null_and_known": int(connection.execute(
             "SELECT count(*) FROM (SELECT entity_key FROM ap_entity_raw GROUP BY entity_key "
             "HAVING count(DISTINCT nullif(taxon,''))=1 AND bool_or(nullif(taxon,'') IS NULL)) q").fetchone()[0]),
         "published_resolution_status_id": PUBLISHED_STATUS_ID},
    )
