from __future__ import annotations

from pathlib import Path
from typing import Iterable

import duckdb
import pyarrow as pa

from omnipath_build.gold.utils.cv_terms import CV_LABELS, format_cv_term
from omnipath_build.gold.utils.schema import (
    ASSOCIATION_CATEGORY,
    ASSOCIATION_PREDICATE,
    CV_TERM_ENTITY_TYPE,
    EVIDENCE_IDENTIFIER_TERMS,
    INTERACTION_LIKE_TYPES,
    MEMBERSHIP_RULES,
    ONTOLOGY_IDENTIFIER_TERM,
    SIGN_NEGATIVE_TERMS,
    SIGN_POSITIVE_TERMS,
    SOURCE_ROLE_ACCESSIONS,
    STOICHIOMETRY_TERM,
    TAXONOMY_IDENTIFIER_TERM,
    TARGET_ROLE_ACCESSIONS,
)
from omnipath_build.rewrite.gold_config import GoldPartitionConfig

UNIPROT_TYPE = 'MI:1097:Uniprot'
STANDARD_INCHI_TYPE = 'MI:2010:Standard Inchi'
PROTEIN_ENTITY_TYPES = frozenset({'MI:0326:Protein'})
CHEMICAL_ENTITY_TYPES = frozenset({'MI:0328:Small Molecule', 'OM:0011:Lipid'})
PROTEIN_REFERENCE_TYPES = frozenset({
    'MI:0476:Ensembl',
    'MI:0477:Entrez',
    'MI:1097:Uniprot',
    'OM:0200:Gene Name Primary',
    'OM:0201:Gene Name Synonym',
    'OM:0221:Uniprot Entry Name',
})
CHEMICAL_ID_TYPE_TO_SOURCE = {
    'MI:0474:Chebi': 'chebi',
    'OM:0004:Hmdb': 'hmdb',
    'OM:0003:Lipidmaps': 'lipidmaps',
    'OM:0009:Swisslipids': 'swisslipids',
}
FALLBACK_ENTITY_ID_TYPE = 'omnipath:local_entity'
ONTOLOGY_ENTITY_TYPE_LABEL = format_cv_term(CV_TERM_ENTITY_TYPE)
ONTOLOGY_IDENTIFIER_TYPE_LABEL = format_cv_term(ONTOLOGY_IDENTIFIER_TERM)


def build_gold_temp_tables_sql(
    *,
    con: duckdb.DuckDBPyConnection,
    source: str,
    mapping_dir: Path,
    cfg: GoldPartitionConfig,
    scope: object,
) -> None:
    """Build source gold temp tables from generic silver tables using DuckDB SQL."""
    del scope
    _register_metadata(con, cfg)
    _create_resolver_mapping_tables(con, mapping_dir)
    _drop_temp(con)

    s = _sql_literal(source)
    ontology_type = _sql_literal(ONTOLOGY_ENTITY_TYPE_LABEL or '')
    ontology_id_type = _sql_literal(ONTOLOGY_IDENTIFIER_TYPE_LABEL or '')
    evidence_terms = _sql_string_list(EVIDENCE_IDENTIFIER_TERMS)
    interaction_types = _sql_string_list(INTERACTION_LIKE_TYPES)
    positive_terms = _sql_string_list(SIGN_POSITIVE_TERMS)
    negative_terms = _sql_string_list(SIGN_NEGATIVE_TERMS)
    source_roles = _sql_string_list(SOURCE_ROLE_ACCESSIONS)
    target_roles = _sql_string_list(TARGET_ROLE_ACCESSIONS)
    membership_predicate = _membership_predicate_case_sql('parent.entity_type')

    con.execute(f"""
        create temp table _gold_occurrence_base as
        select
            o.*,
            coalesce(type_label.formatted, o.entity_type) as entity_type_fmt,
            case
                when o.entity_type is null
                 and not exists (select 1 from silver_entity_identifier i where i.source={s} and i.occurrence_id=o.occurrence_id and nullif(i.identifier,'') is not null)
                 and not exists (select 1 from silver_membership m where m.source={s} and m.parent_occurrence_id=o.occurrence_id)
                    then 'ignored'
                when o.entity_type in ({interaction_types})
                 and exists (select 1 from silver_membership m where m.source={s} and m.parent_occurrence_id=o.occurrence_id)
                    then 'interaction_relation'
                when o.entity_type = {_sql_literal(CV_TERM_ENTITY_TYPE)}
                    then 'ontology_term_only'
                when exists (
                    select 1 from silver_entity_identifier i
                    where i.source={s}
                      and i.occurrence_id=o.occurrence_id
                      and i.identifier_type={_sql_literal(ONTOLOGY_IDENTIFIER_TERM)}
                      and nullif(i.identifier,'') is not null
                )
                    then 'entity_with_ontology_backing'
                when exists (select 1 from silver_membership m where m.source={s} and m.parent_occurrence_id=o.occurrence_id)
                    then 'membership_relation'
                when o.entity_type is not null
                    then 'entity_only'
                else 'ignored'
            end as record_class
        from silver_entity_occurrence o
        left join _gold_cv_label type_label on type_label.accession = o.entity_type
        where o.source = {s}
    """)
    con.execute(f"""
        create temp table _gold_identifier_fmt as
        select
            i.occurrence_id,
            nullif(i.identifier, '') as identifier,
            i.identifier_type,
            coalesce(label.formatted, i.identifier_type) as identifier_type_fmt
        from silver_entity_identifier i
        left join _gold_cv_label label on label.accession = i.identifier_type
        where i.source = {s}
          and nullif(i.identifier, '') is not null
          and i.identifier_type is not null
    """)
    con.execute(f"""
        create temp table _gold_annotation_fmt as
        select
            a.occurrence_id,
            a.term,
            nullif(a.value, '') as value,
            a.unit,
            case
                when a.term is not null and not starts_with(a.term, 'http') and regexp_matches(a.term, '^[^:]+:[^ ]+$')
                    then coalesce(term_label.formatted, a.term)
                else a.term
            end as term_fmt,
            case
                when a.unit is not null and not starts_with(a.unit, 'http') and regexp_matches(a.unit, '^[^:]+:[^ ]+$')
                    then coalesce(unit_label.formatted, a.unit)
                else a.unit
            end as unit_fmt
        from silver_entity_annotation a
        left join _gold_cv_label term_label on term_label.accession = a.term
        left join _gold_cv_label unit_label on unit_label.accession = a.unit
        where a.source = {s}
    """)
    con.execute(f"""
        create temp table _gold_direct_taxonomy as
        select occurrence_id, min(taxonomy_id) as taxonomy_id
        from (
            select occurrence_id, identifier as taxonomy_id from _gold_identifier_fmt where identifier_type = {_sql_literal(TAXONOMY_IDENTIFIER_TERM)}
            union all
            select occurrence_id, value as taxonomy_id from _gold_annotation_fmt where term = {_sql_literal(TAXONOMY_IDENTIFIER_TERM)} and value is not null
        )
        group by occurrence_id
    """)
    con.execute(f"""
        create temp table _gold_member_taxonomy as
        select m.parent_occurrence_id as occurrence_id, min(t.taxonomy_id) as taxonomy_id
        from silver_membership m
        join _gold_direct_taxonomy t on t.occurrence_id = m.member_occurrence_id
        where m.source = {s}
        group by m.parent_occurrence_id
        having count(distinct t.taxonomy_id) = 1
    """)
    con.execute("""
        create temp table _gold_occ_identifier_agg as
        select
            occurrence_id,
            list(struct_pack(type := identifier_type_fmt, value := identifier) order by identifier_type_fmt, identifier) as identifiers,
            string_agg(identifier_type_fmt || chr(31) || identifier, chr(30) order by identifier_type_fmt, identifier) as identifier_key
        from _gold_identifier_fmt
        group by occurrence_id
    """)
    con.execute(f"""
        create temp table _gold_entity_attribute_agg as
        select
            occurrence_id,
            list(struct_pack(term := term_fmt, value := value, unit := unit_fmt) order by term_fmt, value, unit_fmt) as entity_attributes
        from _gold_annotation_fmt
        where term is not null
          and term != {_sql_literal(TAXONOMY_IDENTIFIER_TERM)}
          and term not in ({evidence_terms})
          and not (term = {_sql_literal(ONTOLOGY_IDENTIFIER_TERM)} and unit is null)
        group by occurrence_id
    """)
    con.execute(f"""
        create temp table _gold_candidate_all as
        select
            row_number() over(order by o.raw_record_bucket, o.occurrence_id)::bigint * 10000 as candidate_order,
            substr(sha256(coalesce(o.entity_type_fmt, '') || '|' || coalesce(ids.identifier_key, '')), 1, 32) as fingerprint,
            o.occurrence_id,
            o.entity_type_fmt as entity_type,
            coalesce(dt.taxonomy_id, mt.taxonomy_id) as taxonomy_id,
            attrs.entity_attributes,
            ids.identifiers,
            [{s}]::varchar[] as sources
        from _gold_occurrence_base o
        left join _gold_occ_identifier_agg ids using(occurrence_id)
        left join _gold_direct_taxonomy dt using(occurrence_id)
        left join _gold_member_taxonomy mt using(occurrence_id)
        left join _gold_entity_attribute_agg attrs using(occurrence_id)
        where o.record_class not in ('ignored', 'interaction_relation')
        union all
        select
            row_number() over(order by value)::bigint * 10000 as candidate_order,
            substr(sha256({ontology_type} || '|' || {ontology_id_type} || chr(31) || value), 1, 32) as fingerprint,
            null::varchar as occurrence_id,
            {ontology_type} as entity_type,
            null::varchar as taxonomy_id,
            null as entity_attributes,
            [struct_pack(type := {ontology_id_type}, value := value)] as identifiers,
            [{s}]::varchar[] as sources
        from (select distinct value from _gold_annotation_fmt where term={_sql_literal(ONTOLOGY_IDENTIFIER_TERM)} and unit is null and value is not null)
    """)
    con.execute("""
        create temp table _candidate_entities as
        select
            row_number() over(order by candidate_order, fingerprint)::bigint as entity_pk,
            fingerprint as _fingerprint,
            occurrence_id,
            entity_type,
            taxonomy_id,
            coalesce(entity_attributes, []::struct(term varchar, value varchar, unit varchar)[]) as entity_attributes,
            coalesce(identifiers, []::struct(type varchar, value varchar)[]) as identifiers,
            sources
        from _gold_candidate_all
        qualify row_number() over(partition by fingerprint order by candidate_order, fingerprint) = 1
    """)
    con.execute("""
        create temp table _occurrence_fingerprint_map as
        select distinct occurrence_id, fingerprint as _fingerprint
        from _gold_candidate_all
        where occurrence_id is not null
    """)
    con.execute(f"""
        create temp table _source_identifiers as
        select distinct c.entity_pk, i.identifier, i.identifier_type_fmt as identifier_type, {s} as source
        from _occurrence_fingerprint_map om
        join _candidate_entities c on c._fingerprint = om._fingerprint
        join _gold_identifier_fmt i on i.occurrence_id = om.occurrence_id
        union
        select distinct c.entity_pk, u.value as identifier, u.type as identifier_type, {s} as source
        from _candidate_entities c, unnest(c.identifiers) as t(u)
    """)
    _build_canonical_and_entity_tables(con, source, cfg)
    _build_entity_evidence_tables(con, source, cfg)
    _build_relation_tables(
        con,
        source,
        cfg,
        ontology_type=ONTOLOGY_ENTITY_TYPE_LABEL or '',
        ontology_identifier_type=ONTOLOGY_IDENTIFIER_TYPE_LABEL or '',
        membership_predicate=membership_predicate,
        source_roles=source_roles,
        target_roles=target_roles,
        positive_terms=positive_terms,
        negative_terms=negative_terms,
    )


def _build_canonical_and_entity_tables(con: duckdb.DuckDBPyConnection, source: str, cfg: GoldPartitionConfig) -> None:
    s = _sql_literal(source)
    con.execute(f"""
        create temp table _resolver_entities as
        select entity_pk, entity_pk % {cfg.bucket_count} as entity_bucket, entity_type, taxonomy_id
        from _candidate_entities
    """)
    con.execute("""
        create temp table _resolver_identifiers as
        select
            entity_pk,
            identifier,
            identifier_type,
            source,
            case
                when identifier_type = 'MI:1097:Uniprot' then 0
                when identifier_type = 'MI:0476:Ensembl' then 10
                when identifier_type = 'MI:0477:Entrez' then 20
                else 100
            end::bigint as priority_rank
        from _source_identifiers
        where identifier is not null and identifier_type is not null
    """)
    con.execute('create temp table _resolver_canonical(entity_pk bigint, canonical_identifier varchar, canonical_identifier_type varchar)')
    con.execute(_resolver_insert_sql())
    con.execute(f"""
        create temp table _entity_export_keys as
        select
            c.entity_pk as local_entity_pk,
            coalesce(r.canonical_identifier, {s} || ':entity:' || c.entity_pk::varchar) as entity_id,
            coalesce(r.canonical_identifier_type, {_sql_literal(FALLBACK_ENTITY_ID_TYPE)}) as entity_id_type,
            r.canonical_identifier is not null as resolved
        from _candidate_entities c
        left join _resolver_canonical r using(entity_pk)
    """)
    con.execute("""
        create temp table _canonicalized_entities as
        select c.entity_pk, e.entity_id, e.entity_id_type, c.entity_type, c.entity_attributes, c.taxonomy_id, c.sources, c._fingerprint
        from _candidate_entities c
        join _entity_export_keys e on e.local_entity_pk = c.entity_pk
    """)
    con.execute("""
        create temp table _identifier_rows_raw as
        select e.entity_id, e.entity_id_type, i.identifier, i.identifier_type,
               i.identifier = e.entity_id and i.identifier_type = e.entity_id_type as is_canonical,
               ['source:' || i.source]::varchar[] as sources
        from _source_identifiers i
        join _entity_export_keys e on e.local_entity_pk = i.entity_pk
        union all
        select e.entity_id, e.entity_id_type, e.entity_id, e.entity_id_type, true,
               [case when e.resolved then 'resolver:canonicalization' else 'pipeline:unresolved_fallback' end]::varchar[]
        from _entity_export_keys e
        where not exists (
            select 1 from _source_identifiers i
            where i.entity_pk=e.local_entity_pk and i.identifier=e.entity_id and i.identifier_type=e.entity_id_type
        )
    """)
    con.execute("""
        create temp table _identifier_rows as
        select entity_id, entity_id_type, identifier, identifier_type, bool_or(is_canonical) as is_canonical,
               list_sort(list_distinct(flatten(list(sources)))) as sources
        from _identifier_rows_raw
        group by entity_id, entity_id_type, identifier, identifier_type
    """)
    con.execute("""
        create temp table _final_entity_key_map as
        select entity_id, entity_id_type, row_number() over(order by entity_id_type, entity_id)::bigint as entity_pk
        from (select distinct entity_id, entity_id_type from _canonicalized_entities)
    """)
    con.execute("""
        create temp table _final_entity_raw as
        select
            map.entity_pk,
            map.entity_id as canonical_identifier,
            map.entity_id_type as canonical_identifier_type,
            sha256(coalesce(map.entity_id, '') || '|' || coalesce(map.entity_id_type, '') || '|' || coalesce(any_value(c.taxonomy_id), '')) as entity_key,
            list(struct_pack(identifier := i.identifier, identifier_type := i.identifier_type) order by i.identifier_type, i.identifier)
                filter (where not i.is_canonical) as identifiers,
            any_value(c.entity_type) as entity_type,
            any_value(c.taxonomy_id) as taxonomy_id,
            any_value(c.entity_attributes) as entity_attributes,
            list_sort(list_distinct(flatten(list(c.sources)))) as sources
        from _final_entity_key_map map
        join _canonicalized_entities c using(entity_id, entity_id_type)
        left join _identifier_rows i using(entity_id, entity_id_type)
        group by map.entity_pk, map.entity_id, map.entity_id_type
    """)
    con.execute("""
        create temp table _entity_registry_out as
        select entity_key, row_number() over(order by entity_key)::bigint as entity_pk,
               _gold_bucket(entity_key)::bigint as entity_bucket,
               _gold_part(entity_key)::bigint as entity_part
        from (select distinct entity_key from _final_entity_raw)
    """)
    con.execute("""
        create temp table _gold_entity_out as
        select registry.entity_pk, final.entity_key, registry.entity_bucket, registry.entity_part,
               final.canonical_identifier, final.canonical_identifier_type,
               coalesce(final.identifiers, []::struct(identifier varchar, identifier_type varchar)[]) as identifiers,
               final.entity_type, final.taxonomy_id,
               coalesce(final.entity_attributes, []::struct(term varchar, value varchar, unit varchar)[]) as entity_attributes,
               final.sources
        from _final_entity_raw final
        join _entity_registry_out registry using(entity_key)
        order by registry.entity_pk
    """)
    con.execute("""
        create temp table _gold_entity_map_out as
        select distinct c._fingerprint, registry.entity_pk, final.entity_key,
               _gold_bucket(c._fingerprint)::bigint as fingerprint_bucket,
               _gold_part(c._fingerprint)::bigint as fingerprint_part
        from _canonicalized_entities c
        join _final_entity_key_map map using(entity_id, entity_id_type)
        join _final_entity_raw final on final.entity_pk = map.entity_pk
        join _entity_registry_out registry using(entity_key)
        order by c._fingerprint
    """)
    con.execute("""
        create temp table _gold_entity_occurrence_map_out as
        select distinct om.occurrence_id, om._fingerprint, em.entity_pk, em.entity_key,
               _gold_bucket(om.occurrence_id)::bigint as occ_bucket,
               _gold_part(om.occurrence_id)::bigint as occ_part
        from _occurrence_fingerprint_map om
        join _gold_entity_map_out em using(_fingerprint)
        order by om.occurrence_id
    """)


def _build_entity_evidence_tables(con: duckdb.DuckDBPyConnection, source: str, cfg: GoldPartitionConfig) -> None:
    del cfg
    s = _sql_literal(source)
    evidence_terms = _sql_string_list(EVIDENCE_IDENTIFIER_TERMS)
    ontology_type = _sql_literal(ONTOLOGY_ENTITY_TYPE_LABEL or '')
    ontology_id_type = _sql_literal(ONTOLOGY_IDENTIFIER_TYPE_LABEL or '')
    con.execute(f"""
        create temp table _gold_entity_evidence_attr as
        select occurrence_id,
               list(struct_pack(term := term, value := value, unit := unit) order by term, value, unit) as evidence
        from _gold_annotation_fmt
        where term in ({evidence_terms}) and value is not null
        group by occurrence_id
    """)
    con.execute(f"""
        create temp table _gold_entity_evidence_out as
        select distinct
            em.entity_pk, {s} as source, em.entity_key, ge.canonical_identifier, ge.canonical_identifier_type,
            occ.record_id as raw_record_id, om.occurrence_id, om._fingerprint as fingerprint,
            ge.entity_type, ge.taxonomy_id, ge.identifiers, ge.entity_attributes,
            coalesce(ev.evidence, []::struct(term varchar, value varchar, unit varchar)[]) as evidence,
            ge.entity_bucket, ge.entity_part,
            _gold_bucket(om.occurrence_id)::bigint as occ_bucket,
            _gold_part(om.occurrence_id)::bigint as occ_part
        from _gold_entity_occurrence_map_out om
        join _gold_entity_map_out em using(_fingerprint)
        join _gold_entity_out ge on ge.entity_pk = em.entity_pk
        join _gold_occurrence_base occ using(occurrence_id)
        left join _gold_entity_evidence_attr ev using(occurrence_id)
        where occ.record_id is not null
        union
        select distinct
            em.entity_pk, {s} as source, em.entity_key, ge.canonical_identifier, ge.canonical_identifier_type,
            occ.record_id as raw_record_id, a.occurrence_id, em._fingerprint as fingerprint,
            ge.entity_type, ge.taxonomy_id, ge.identifiers, ge.entity_attributes,
            []::struct(term varchar, value varchar, unit varchar)[] as evidence,
            ge.entity_bucket, ge.entity_part,
            _gold_bucket(a.occurrence_id)::bigint as occ_bucket,
            _gold_part(a.occurrence_id)::bigint as occ_part
        from _gold_annotation_fmt a
        join _gold_occurrence_base occ using(occurrence_id)
        join _gold_entity_map_out em
          on em._fingerprint = substr(sha256({ontology_type} || '|' || {ontology_id_type} || chr(31) || a.value), 1, 32)
        join _gold_entity_out ge on ge.entity_pk = em.entity_pk
        where a.term = {_sql_literal(ONTOLOGY_IDENTIFIER_TERM)}
          and a.unit is null
          and a.value is not null
          and occ.record_id is not null
    """)


def _build_relation_tables(
    con: duckdb.DuckDBPyConnection,
    source: str,
    cfg: GoldPartitionConfig,
    *,
    ontology_type: str,
    ontology_identifier_type: str,
    membership_predicate: str,
    source_roles: str,
    target_roles: str,
    positive_terms: str,
    negative_terms: str,
) -> None:
    del cfg
    s = _sql_literal(source)
    evidence_terms = _sql_string_list(EVIDENCE_IDENTIFIER_TERMS)
    ontology_type_sql = _sql_literal(ontology_type)
    ontology_id_type_sql = _sql_literal(ontology_identifier_type)
    empty_attrs = '[]::struct(term varchar, value varchar, unit varchar)[]'
    con.execute(f"""
        create temp table _gold_membership_annotation_fmt as
        select
            ma.membership_id,
            ma.parent_occurrence_id,
            ma.member_occurrence_id,
            ma.term,
            nullif(ma.value, '') as value,
            ma.unit,
            case
                when ma.term is not null and not starts_with(ma.term, 'http') and regexp_matches(ma.term, '^[^:]+:[^ ]+$')
                    then coalesce(term_label.formatted, ma.term)
                else ma.term
            end as term_fmt,
            case
                when ma.unit is not null and not starts_with(ma.unit, 'http') and regexp_matches(ma.unit, '^[^:]+:[^ ]+$')
                    then coalesce(unit_label.formatted, ma.unit)
                else ma.unit
            end as unit_fmt
        from silver_membership_annotation ma
        left join _gold_cv_label term_label on term_label.accession = ma.term
        left join _gold_cv_label unit_label on unit_label.accession = ma.unit
        where ma.source = {s}
    """)
    con.execute(f"""
        create temp table _gold_membership_annotations as
        select
            ma.membership_id,
            bool_or(ma.term in ({source_roles})) as has_source_role,
            bool_or(ma.term in ({target_roles})) as has_target_role,
            bool_or(ma.term in ({positive_terms}) or upper(coalesce(ma.value, '')) like '%ACTIV%') as positive_sign,
            bool_or(ma.term in ({negative_terms}) or regexp_matches(upper(coalesce(ma.value, '')), 'INHIB|NEGATIVE|REPRESS')) as negative_sign
        from _gold_membership_annotation_fmt ma
        group by ma.membership_id
    """)
    con.execute(f"""
        create temp table _gold_record_relation_attrs as
        select
            occurrence_id,
            list(struct_pack(term := term_fmt, value := value, unit := unit_fmt) order by term_fmt, value, unit_fmt)
                filter (
                    where term is not null
                      and term != {_sql_literal(TAXONOMY_IDENTIFIER_TERM)}
                      and term not in ({evidence_terms})
                      and not (term = {_sql_literal(ONTOLOGY_IDENTIFIER_TERM)} and unit is null)
                ) as record_attributes,
            list(struct_pack(term := term, value := value, unit := unit) order by term, value, unit)
                filter (where term in ({evidence_terms}) and value is not null) as evidence
        from _gold_annotation_fmt
        group by occurrence_id
    """)
    con.execute(f"""
        create temp table _gold_membership_relation_attrs as
        select
            membership_id,
            list(struct_pack(term := term_fmt, value := value, unit := unit_fmt) order by term_fmt, value, unit_fmt)
                filter (
                    where term is not null
                      and term != {_sql_literal(TAXONOMY_IDENTIFIER_TERM)}
                      and term != {_sql_literal(STOICHIOMETRY_TERM)}
                      and term not in ({evidence_terms})
                      and not (term = {_sql_literal(ONTOLOGY_IDENTIFIER_TERM)} and unit is null)
                ) as subject_attributes,
            list(struct_pack(term := term_fmt, value := value, unit := unit_fmt) order by term_fmt, value, unit_fmt)
                filter (
                    where term is not null
                      and term != {_sql_literal(TAXONOMY_IDENTIFIER_TERM)}
                      and term not in ({evidence_terms})
                      and not (term = {_sql_literal(ONTOLOGY_IDENTIFIER_TERM)} and unit is null)
                ) as object_attributes,
            list(struct_pack(term := term, value := value, unit := unit) order by term, value, unit)
                filter (where term in ({evidence_terms}) and value is not null) as evidence
        from _gold_membership_annotation_fmt
        group by membership_id
    """)
    con.execute(f"""
        create temp table _gold_record_sign as
        select occurrence_id,
               bool_or(term in ({positive_terms}) or upper(coalesce(value, '')) like '%ACTIV%') as positive_sign,
               bool_or(term in ({negative_terms}) or regexp_matches(upper(coalesce(value, '')), 'INHIB|NEGATIVE|REPRESS')) as negative_sign
        from _gold_annotation_fmt
        group by occurrence_id
    """)
    con.execute(f"""
        create temp table _gold_membership_participants as
        select
            m.membership_id, m.parent_occurrence_id, m.member_occurrence_id, m.is_parent,
            member_map.entity_pk, member_map.entity_key,
            coalesce(ma.has_source_role, false) as has_source_role,
            coalesce(ma.has_target_role, false) as has_target_role,
            coalesce(ma.positive_sign, false) as positive_sign,
            coalesce(ma.negative_sign, false) as negative_sign,
            row_number() over(partition by m.parent_occurrence_id order by m.membership_id, m.member_occurrence_id) as member_order
        from silver_membership m
        join _gold_entity_occurrence_map_out member_map on member_map.occurrence_id = m.member_occurrence_id
        left join _gold_membership_annotations ma using(membership_id)
        where m.source = {s}
    """)
    con.execute(f"""
        create temp table _relation_evidence_raw as
        with membership_rel as (
            select
                'membership' as relation_kind,
                m.parent_occurrence_id,
                case when coalesce(m.is_parent, false) then member.entity_key else parent_map.entity_key end as subject_key,
                {membership_predicate} as predicate,
                case when coalesce(m.is_parent, false) then parent_map.entity_key else member.entity_key end as object_key,
                {_sql_literal(ASSOCIATION_CATEGORY)} as relation_category,
                case when coalesce(m.is_parent, false) then m.membership_id else null end as subject_membership_id,
                case when coalesce(m.is_parent, false) then null else m.membership_id end as object_membership_id
            from silver_membership m
            join _gold_occurrence_base parent on parent.occurrence_id = m.parent_occurrence_id
            join _gold_entity_occurrence_map_out parent_map on parent_map.occurrence_id = m.parent_occurrence_id
            join _gold_entity_occurrence_map_out member on member.occurrence_id = m.member_occurrence_id
            where m.source = {s}
              and parent.record_class = 'membership_relation'
        ),
        interaction_rel as (
            select
                'interaction' as relation_kind,
                parent.occurrence_id as parent_occurrence_id,
                case
                    when count(*) filter(where p.has_source_role) = 1 and count(*) filter(where p.has_target_role) = 1
                        then max(p.entity_key) filter(where p.has_source_role)
                    else min(p.entity_key) filter(where p.member_order = 1)
                end as subject_key,
                case
                    when parent.entity_type = 'MI:0915' then {_sql_literal(ASSOCIATION_PREDICATE)}
                    when coalesce(sign.positive_sign, false) or bool_or(p.positive_sign) then 'positively_regulates'
                    when coalesce(sign.negative_sign, false) or bool_or(p.negative_sign) then 'negatively_regulates'
                    when parent.entity_type in ('MI:2247', 'MI:2248', 'MI:2249') then 'regulates'
                    when parent.entity_type = 'MI:0414'
                     and count(*) filter(where p.has_source_role) = 1
                     and count(*) filter(where p.has_target_role) = 1 then 'transforms_to'
                    when parent.entity_type in ('MI:0190', 'MI:0414') then 'interacts_with'
                    else 'related_to'
                end as predicate,
                case
                    when count(*) filter(where p.has_source_role) = 1 and count(*) filter(where p.has_target_role) = 1
                        then max(p.entity_key) filter(where p.has_target_role)
                    else min(p.entity_key) filter(where p.member_order = 2)
                end as object_key,
                case when parent.entity_type = 'MI:0915' then {_sql_literal(ASSOCIATION_CATEGORY)} else 'interaction' end as relation_category,
                case
                    when count(*) filter(where p.has_source_role) = 1 and count(*) filter(where p.has_target_role) = 1
                        then max(p.membership_id) filter(where p.has_source_role)
                    else min(p.membership_id) filter(where p.member_order = 1)
                end as subject_membership_id,
                case
                    when count(*) filter(where p.has_source_role) = 1 and count(*) filter(where p.has_target_role) = 1
                        then max(p.membership_id) filter(where p.has_target_role)
                    else min(p.membership_id) filter(where p.member_order = 2)
                end as object_membership_id
            from _gold_occurrence_base parent
            join _gold_membership_participants p on p.parent_occurrence_id = parent.occurrence_id
            left join _gold_record_sign sign on sign.occurrence_id = parent.occurrence_id
            where parent.record_class = 'interaction_relation'
            group by parent.occurrence_id, parent.entity_type, sign.positive_sign, sign.negative_sign
            having count(*) = 2
        ),
        annotation_rel as (
            select
                'annotation' as relation_kind,
                a.occurrence_id as parent_occurrence_id,
                subject_map.entity_key as subject_key,
                case
                    when upper(split_part(a.value, ':', 1)) in ('REACTOME', 'WP')
                      or upper(a.value) like 'WP%'
                      or upper(a.value) like 'R-%'
                        then 'involved_in'
                    else {_sql_literal(ASSOCIATION_PREDICATE)}
                end as predicate,
                object_map.entity_key as object_key,
                {_sql_literal(ASSOCIATION_CATEGORY)} as relation_category,
                null::varchar as subject_membership_id,
                null::varchar as object_membership_id
            from _gold_annotation_fmt a
            join _gold_entity_occurrence_map_out subject_map on subject_map.occurrence_id = a.occurrence_id
            join _gold_entity_map_out object_map
              on object_map._fingerprint = substr(sha256({ontology_type_sql} || '|' || {ontology_id_type_sql} || chr(31) || a.value), 1, 32)
            join _gold_occurrence_base occ on occ.occurrence_id = a.occurrence_id
            where a.term = {_sql_literal(ONTOLOGY_IDENTIFIER_TERM)}
              and a.unit is null
              and a.value is not null
              and occ.record_class not in ('ignored', 'ontology_term_only')
        ),
        rel as (
            select * from membership_rel
            union all select * from interaction_rel
            union all select * from annotation_rel
        )
        select
            {s} as source,
            _gold_relation_key(subject.entity_key, rel.predicate, object.entity_key, rel.relation_category) as relation_key,
            subject.entity_key as subject_entity_key,
            rel.predicate,
            object.entity_key as object_entity_key,
            rel.relation_category,
            coalesce(parent.record_id, '') as raw_record_id,
            case
                when rel.relation_kind = 'interaction'
                    then coalesce(record_attrs.record_attributes, {empty_attrs})
                else {empty_attrs}
            end as record_attributes,
            coalesce(subject_attrs.subject_attributes, {empty_attrs}) as subject_attributes,
            coalesce(object_attrs.object_attributes, {empty_attrs}) as object_attributes,
            case
                when rel.relation_kind in ('interaction', 'membership')
                    then list_concat(
                        coalesce(record_attrs.evidence, {empty_attrs}),
                        coalesce(subject_attrs.evidence, {empty_attrs}),
                        coalesce(object_attrs.evidence, {empty_attrs})
                    )
                else {empty_attrs}
            end as evidence,
            _gold_bucket(_gold_relation_key(subject.entity_key, rel.predicate, object.entity_key, rel.relation_category))::bigint as relation_bucket,
            _gold_part(_gold_relation_key(subject.entity_key, rel.predicate, object.entity_key, rel.relation_category))::bigint as relation_part
        from rel
        join _entity_registry_out subject on subject.entity_key = rel.subject_key
        join _entity_registry_out object on object.entity_key = rel.object_key
        left join _gold_occurrence_base parent on parent.occurrence_id = rel.parent_occurrence_id
        left join _gold_record_relation_attrs record_attrs on record_attrs.occurrence_id = rel.parent_occurrence_id
        left join _gold_membership_relation_attrs subject_attrs on subject_attrs.membership_id = rel.subject_membership_id
        left join _gold_membership_relation_attrs object_attrs on object_attrs.membership_id = rel.object_membership_id
    """)
    con.execute("""
        create temp table _relation_registry_out as
        select relation_key, row_number() over(order by relation_key)::bigint as relation_pk,
               any_value(relation_bucket)::bigint as relation_bucket,
               any_value(relation_part)::bigint as relation_part
        from _relation_evidence_raw
        group by relation_key
    """)
    con.execute("""
        create temp table _relation_out as
        with grouped as (
            select relation_key, any_value(subject_entity_key) as subject_entity_key, any_value(predicate) as predicate,
                   any_value(object_entity_key) as object_entity_key, any_value(relation_category) as relation_category,
                   count(*)::bigint as evidence_count,
                   list_sort(list_distinct(list(source) filter (where source is not null))) as sources
            from _relation_evidence_raw
            group by relation_key
        )
        select registry.relation_pk, grouped.relation_key,
               subject.entity_pk as subject_entity_pk, grouped.subject_entity_key, grouped.predicate,
               object.entity_pk as object_entity_pk, grouped.object_entity_key, grouped.relation_category,
               grouped.evidence_count, coalesce(grouped.sources, []::varchar[]) as sources,
               registry.relation_bucket, registry.relation_part
        from grouped
        join _relation_registry_out registry using(relation_key)
        left join _entity_registry_out subject on grouped.subject_entity_key = subject.entity_key
        left join _entity_registry_out object on grouped.object_entity_key = object.entity_key
        order by registry.relation_pk
    """)
    con.execute("""
        create temp table _relation_evidence_out as
        select row_number() over(order by raw.relation_key, raw.source, raw.raw_record_id)::bigint as relation_evidence_pk,
               registry.relation_pk, raw.relation_key, raw.source, raw.raw_record_id,
               raw.record_attributes, raw.subject_attributes, raw.object_attributes, raw.evidence,
               raw.subject_entity_key, raw.predicate, raw.object_entity_key, raw.relation_category,
               registry.relation_bucket, registry.relation_part
        from _relation_evidence_raw raw
        join _relation_registry_out registry using(relation_key)
        order by relation_evidence_pk
    """)


def _register_metadata(con: duckdb.DuckDBPyConnection, cfg: GoldPartitionConfig) -> None:
    con.execute('drop table if exists _gold_cv_label')
    rows = [{'accession': key, 'label': value, 'formatted': f'{key}:{value}'} for key, value in CV_LABELS.items()]
    arrow = pa.Table.from_pylist(rows, schema=pa.schema([
        pa.field('accession', pa.string()),
        pa.field('label', pa.string()),
        pa.field('formatted', pa.string()),
    ]))
    con.register('_gold_cv_label_arrow', arrow)
    try:
        con.execute('create temp table _gold_cv_label as select * from _gold_cv_label_arrow')
    finally:
        con.unregister('_gold_cv_label_arrow')
    con.execute(f'create or replace macro _gold_bucket(v) as (hash(coalesce(v, \'\')) % {cfg.bucket_count})')
    con.execute(f'create or replace macro _gold_part(v) as ((hash(coalesce(v, \'\')) % {cfg.bucket_count}) % {cfg.part_count})')
    con.execute("create or replace macro _gold_relation_key(s, p, o, c) as (sha256(coalesce(s, '') || '|' || coalesce(p, '') || '|' || coalesce(o, '') || '|' || coalesce(c, '')))")


def _drop_temp(con: duckdb.DuckDBPyConnection) -> None:
    for table in [
        '_gold_occurrence_base',
        '_gold_identifier_fmt',
        '_gold_annotation_fmt',
        '_gold_direct_taxonomy',
        '_gold_member_taxonomy',
        '_gold_occ_identifier_agg',
        '_gold_entity_attribute_agg',
        '_gold_candidate_all',
        '_candidate_entities',
        '_occurrence_fingerprint_map',
        '_source_identifiers',
        '_resolver_entities',
        '_resolver_identifiers',
        '_resolver_canonical',
        '_entity_export_keys',
        '_canonicalized_entities',
        '_identifier_rows_raw',
        '_identifier_rows',
        '_final_entity_key_map',
        '_final_entity_raw',
        '_entity_registry_out',
        '_gold_entity_out',
        '_gold_entity_map_out',
        '_gold_entity_occurrence_map_out',
        '_gold_entity_evidence_attr',
        '_gold_entity_evidence_out',
        '_gold_membership_annotation_fmt',
        '_gold_membership_annotations',
        '_gold_record_relation_attrs',
        '_gold_membership_relation_attrs',
        '_gold_record_sign',
        '_gold_membership_participants',
        '_relation_evidence_raw',
        '_relation_registry_out',
        '_relation_out',
        '_relation_evidence_out',
    ]:
        con.execute(f'drop table if exists {_quote_identifier(table)}')


def _create_resolver_mapping_tables(con: duckdb.DuckDBPyConnection, mapping_dir: Path) -> None:
    protein_path = mapping_dir / 'proteins' / 'protein_identifier_lookup.parquet'
    chemical_path = mapping_dir / 'chemicals' / 'chemical_identifier_lookup.parquet'
    con.execute(f"""
        create or replace table _resolver_protein_lookup as
        select key_type::varchar as key_type, key_value::varchar as key_value,
               nullif(taxonomy_id::varchar, '') as taxonomy_id,
               primary_uniprot::varchar as primary_uniprot,
               mapping_type::varchar as mapping_type
        from read_parquet({_sql_literal(str(protein_path))})
    """)
    con.execute(f"""
        create or replace table _resolver_chemical_lookup as
        select key_type::varchar as key_type, key_value::varchar as key_value,
               standard_inchi::varchar as standard_inchi, source::varchar as source
        from read_parquet({_sql_literal(str(chemical_path))})
    """)


def _resolver_insert_sql() -> str:
    protein_types = _sql_string_list(PROTEIN_ENTITY_TYPES)
    chemical_types = _sql_string_list(CHEMICAL_ENTITY_TYPES)
    protein_reference_types = _sql_string_list(PROTEIN_REFERENCE_TYPES)
    chemical_source_case = _chemical_source_case_sql('i.identifier_type')
    return f"""
        insert into _resolver_canonical
        with ontology_canonical as (
            select e.entity_pk, i.identifier as canonical_identifier, i.identifier_type as canonical_identifier_type, 0 as rank
            from _resolver_entities e
            join _resolver_identifiers i using(entity_pk)
            where e.entity_type = {_sql_literal(ONTOLOGY_ENTITY_TYPE_LABEL or '')}
              and i.identifier_type = {_sql_literal(ONTOLOGY_IDENTIFIER_TYPE_LABEL or '')}
              and i.identifier is not null
            qualify row_number() over(partition by e.entity_pk order by i.identifier) = 1
        ),
        protein_resolved as (
            select e.entity_pk, i.identifier as resolved_id
            from _resolver_entities e
            join _resolver_identifiers i using(entity_pk)
            join (select distinct primary_uniprot from _resolver_protein_lookup where mapping_type != 'uniprot_secondary') p
              on i.identifier_type = {_sql_literal(UNIPROT_TYPE)} and i.identifier = p.primary_uniprot
            where e.entity_type in ({protein_types})
            union all
            select e.entity_pk, regexp_replace(i.identifier, '-[0-9]+$', '') as resolved_id
            from _resolver_entities e
            join _resolver_identifiers i using(entity_pk)
            join (select distinct primary_uniprot from _resolver_protein_lookup where mapping_type != 'uniprot_secondary') p
              on i.identifier_type = {_sql_literal(UNIPROT_TYPE)}
             and regexp_replace(i.identifier, '-[0-9]+$', '') = p.primary_uniprot
            where e.entity_type in ({protein_types})
              and regexp_replace(i.identifier, '-[0-9]+$', '') != i.identifier
            union all
            select e.entity_pk, p.primary_uniprot
            from _resolver_entities e
            join _resolver_identifiers i using(entity_pk)
            join _resolver_protein_lookup p
              on p.mapping_type = 'uniprot_secondary'
             and i.identifier_type = {_sql_literal(UNIPROT_TYPE)}
             and i.identifier = p.key_value
            where e.entity_type in ({protein_types})
            union all
            select e.entity_pk, p.primary_uniprot
            from _resolver_entities e
            join _resolver_identifiers i using(entity_pk)
            join _resolver_protein_lookup p
              on p.mapping_type != 'uniprot_secondary'
             and i.identifier_type = p.key_type
             and i.identifier = p.key_value
             and ((e.taxonomy_id is not null and e.taxonomy_id = p.taxonomy_id) or e.taxonomy_id is null)
            where e.entity_type in ({protein_types})
              and i.identifier_type in ({protein_reference_types})
        ),
        protein_canonical as (
            select entity_pk, min(resolved_id) as canonical_identifier, {_sql_literal(UNIPROT_TYPE)} as canonical_identifier_type, 1 as rank
            from protein_resolved
            where resolved_id is not null
            group by entity_pk
            having count(distinct resolved_id) = 1
        ),
        chemical_resolved as (
            select e.entity_pk, i.identifier as resolved_id
            from _resolver_entities e
            join _resolver_identifiers i using(entity_pk)
            where e.entity_type in ({chemical_types})
              and i.identifier_type = {_sql_literal(STANDARD_INCHI_TYPE)}
              and i.identifier is not null
            union all
            select e.entity_pk, c.standard_inchi
            from _resolver_entities e
            join _resolver_identifiers i using(entity_pk)
            join _resolver_chemical_lookup c
              on c.key_type = i.identifier_type
             and c.key_value = i.identifier
             and c.source = {chemical_source_case}
            where e.entity_type in ({chemical_types})
              and {chemical_source_case} is not null
        ),
        chemical_canonical as (
            select entity_pk, min(resolved_id) as canonical_identifier, {_sql_literal(STANDARD_INCHI_TYPE)} as canonical_identifier_type, 2 as rank
            from chemical_resolved
            where resolved_id is not null
            group by entity_pk
            having count(distinct resolved_id) = 1
        ),
        fallback_canonical as (
            select entity_pk, identifier as canonical_identifier, identifier_type as canonical_identifier_type, 3 as rank
            from _resolver_identifiers
            where identifier is not null and identifier_type is not null
            qualify row_number() over(partition by entity_pk order by priority_rank, identifier_type, identifier) = 1
        ),
        chosen as (
            select * from ontology_canonical
            union all select * from protein_canonical
            union all select * from chemical_canonical
            union all select * from fallback_canonical
        )
        select entity_pk, canonical_identifier, canonical_identifier_type
        from chosen
        qualify row_number() over(partition by entity_pk order by rank, canonical_identifier_type, canonical_identifier) = 1
    """


def _membership_predicate_case_sql(parent_type_sql: str) -> str:
    clauses = [
        f'when {parent_type_sql} = {_sql_literal(parent_type)} then {_sql_literal(predicate)}'
        for parent_type, predicate in sorted(MEMBERSHIP_RULES.items())
    ]
    return 'case ' + ' '.join(clauses) + f' else {_sql_literal("has_member")} end'


def _chemical_source_case_sql(identifier_type_sql: str) -> str:
    clauses = [
        f'when {identifier_type_sql} = {_sql_literal(identifier_type)} then {_sql_literal(source)}'
        for identifier_type, source in sorted(CHEMICAL_ID_TYPE_TO_SOURCE.items())
    ]
    return 'case ' + ' '.join(clauses) + ' else null end'


def _sql_string_list(values: Iterable[str]) -> str:
    values_sql = ', '.join(_sql_literal(str(value)) for value in sorted(values))
    return values_sql or "''"


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"
