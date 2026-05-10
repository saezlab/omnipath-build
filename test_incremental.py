#!/usr/bin/env python3
"""End-to-end test of incremental combine + incremental postgres load with bitmap verification."""

import json
import tempfile
from pathlib import Path

import polars as pl

from omnipath_build.gold.combine import build_combined
from omnipath_build.postgres import load_combined_schema_to_postgres

POSTGRES_URI = 'postgresql://omnipath:omnipath@localhost:55432/omnipath'


def create_test_gold_data(gold_root: Path, entity_type: str = 'protein') -> None:
    """Create minimal gold data with 2 entities and 1 relation."""
    source_dir = gold_root / 'test_source'
    entities_dir = source_dir / 'entities'
    relations_dir = source_dir / 'relations'
    entities_dir.mkdir(parents=True)
    relations_dir.mkdir(parents=True)

    entity_df = pl.DataFrame({
        'entity_pk': [1, 2],
        'entity_key': ['key1', 'key2'],
        'canonical_identifier': ['P12345', 'P67890'],
        'canonical_identifier_type': ['uniprot', 'uniprot'],
        'identifiers': [[{'identifier': 'P12345', 'identifier_type': 'uniprot'}],
                        [{'identifier': 'P67890', 'identifier_type': 'uniprot'}]],
        'entity_type': [entity_type, entity_type],
        'taxonomy_id': ['9606', '9606'],
        'entity_attributes': [None, None],
        'sources': [['test_source'], ['test_source']],
    })
    entity_df.write_parquet(entities_dir / 'entity.parquet')

    entity_evidence_df = pl.DataFrame({
        'source': ['test_source', 'test_source'],
        'entity_key': ['key1', 'key2'],
        'raw_record_ids': [['r1'], ['r2']],
        'entity_type': [entity_type, entity_type],
        'taxonomy_id': ['9606', '9606'],
        'identifiers': [None, None],
        'entity_attributes': [None, None],
    })
    entity_evidence_df.write_parquet(entities_dir / 'entity_evidence.parquet')

    relation_df = pl.DataFrame({
        'relation_pk': [1],
        'relation_key': ['rel1'],
        'subject_entity_pk': [1],
        'subject_entity_key': ['key1'],
        'predicate': ['interacts_with'],
        'object_entity_pk': [2],
        'object_entity_key': ['key2'],
        'relation_category': ['interaction'],
        'evidence_count': [1],
        'sources': [['test_source']],
    })
    relation_df.write_parquet(relations_dir / 'entity_relation.parquet')

    record_attrs = pl.Series(
        [[{'term': None, 'value': None, 'unit': None}]],
        dtype=pl.List(pl.Struct([
            pl.Field('term', pl.String),
            pl.Field('value', pl.String),
            pl.Field('unit', pl.String),
        ]))
    )
    relation_evidence_df = pl.DataFrame({
        'source': ['test_source'],
        'relation_evidence_pk': [1],
        'relation_pk': [1],
        'relation_key': ['rel1'],
        'raw_record_id': ['r1'],
        'record_attributes': record_attrs,
        'subject_attributes': pl.Series([[]], dtype=pl.List(pl.Struct([
            pl.Field('term', pl.String),
            pl.Field('value', pl.String),
            pl.Field('unit', pl.String),
        ]))),
        'object_attributes': pl.Series([[]], dtype=pl.List(pl.Struct([
            pl.Field('term', pl.String),
            pl.Field('value', pl.String),
            pl.Field('unit', pl.String),
        ]))),
        'evidence': pl.Series([[]], dtype=pl.List(pl.Struct([
            pl.Field('term', pl.String),
            pl.Field('value', pl.String),
            pl.Field('unit', pl.String),
        ]))),
    })
    relation_evidence_df.write_parquet(relations_dir / 'entity_relation_evidence.parquet')


def query_bitmaps(postgres_uri: str, schema: str = 'public') -> dict:
    """Query current bitmap state from Postgres."""
    import psycopg2
    result = {}
    with psycopg2.connect(postgres_uri) as conn:
        with conn.cursor() as cur:
            # facet_entity_bitmap
            cur.execute(
                f"SELECT facet_name, facet_value, entity_count FROM {schema}.facet_entity_bitmap ORDER BY facet_name, facet_value"
            )
            result['facet_entity'] = cur.fetchall()

            # facet_relation_bitmap
            cur.execute(
                f"SELECT facet_name, facet_value, relation_count FROM {schema}.facet_relation_bitmap ORDER BY facet_name, facet_value"
            )
            result['facet_relation'] = cur.fetchall()

            # annotation_term_entity_bitmap
            cur.execute(
                f"SELECT term_entity_id, global_count FROM {schema}.annotation_term_entity_bitmap ORDER BY term_entity_id"
            )
            result['annotation_entity'] = cur.fetchall()

            # annotation_term_relation_bitmap
            cur.execute(
                f"SELECT term_entity_id, global_count FROM {schema}.annotation_term_relation_bitmap ORDER BY term_entity_id"
            )
            result['annotation_relation'] = cur.fetchall()
    return result


def run_test():
    with tempfile.TemporaryDirectory() as td:
        gold_root = Path(td) / 'gold'
        output_dir = Path(td) / 'combined'

        print("=== STEP 1: Create initial gold data ===")
        create_test_gold_data(gold_root, entity_type='protein')

        print("=== STEP 2: Full combine ===")
        build_combined(gold_root=gold_root, output_dir=output_dir)
        latest_dir = output_dir / 'latest'
        assert latest_dir.exists()
        print(f"  Combined output in {latest_dir}")

        print("=== STEP 3: Full Postgres load ===")
        load_combined_schema_to_postgres(
            output_dir=output_dir,
            postgres_uri=POSTGRES_URI,
            schema='public',
            drop_existing=True,
            batch_size=10_000,
        )

        print("=== STEP 4: Verify initial bitmaps ===")
        bitmaps_before = query_bitmaps(POSTGRES_URI)
        print(f"  facet_entity: {bitmaps_before['facet_entity']}")
        print(f"  facet_relation: {bitmaps_before['facet_relation']}")

        # Expected: 1 protein entity type facet with 2 entities
        assert any(f == 'entity_type' and v == 'protein' and c == 2
                   for f, v, c in bitmaps_before['facet_entity']), \
            f"Expected protein facet with 2 entities, got {bitmaps_before['facet_entity']}"
        print("  PASS: Initial bitmaps correct")

        print("=== STEP 5: Simulate source update (change entity type) ===")
        # Change entity 1 from protein -> gene, add entity 3 (chemical)
        entity_df = pl.DataFrame({
            'entity_pk': [1, 2, 3],
            'entity_key': ['key1', 'key2', 'key3'],
            'canonical_identifier': ['P12345', 'P67890', 'C00001'],
            'canonical_identifier_type': ['uniprot', 'uniprot', 'pubchem'],
            'identifiers': [[{'identifier': 'P12345', 'identifier_type': 'uniprot'}],
                            [{'identifier': 'P67890', 'identifier_type': 'uniprot'}],
                            [{'identifier': 'C00001', 'identifier_type': 'pubchem'}]],
            'entity_type': ['gene', 'protein', 'chemical'],
            'taxonomy_id': ['9606', '9606', '9606'],
            'entity_attributes': [None, None, None],
            'sources': [['test_source'], ['test_source'], ['test_source']],
        })
        source_dir = gold_root / 'test_source'
        entity_df.write_parquet(source_dir / 'entities' / 'entity.parquet')

        # Update evidence too
        entity_evidence_df = pl.DataFrame({
            'source': ['test_source', 'test_source', 'test_source'],
            'entity_key': ['key1', 'key2', 'key3'],
            'raw_record_ids': [['r1'], ['r2'], ['r3']],
            'entity_type': ['gene', 'protein', 'chemical'],
            'taxonomy_id': ['9606', '9606', '9606'],
            'identifiers': [None, None, None],
            'entity_attributes': [None, None, None],
        })
        entity_evidence_df.write_parquet(source_dir / 'entities' / 'entity_evidence.parquet')

        # Add a new relation
        relation_df = pl.DataFrame({
            'relation_pk': [1, 2],
            'relation_key': ['rel1', 'rel2'],
            'subject_entity_pk': [1, 3],
            'subject_entity_key': ['key1', 'key3'],
            'predicate': ['interacts_with', 'associated_with'],
            'object_entity_pk': [2, 1],
            'object_entity_key': ['key2', 'key1'],
            'relation_category': ['interaction', 'interaction'],
            'evidence_count': [1, 1],
            'sources': [['test_source'], ['test_source']],
        })
        relation_df.write_parquet(source_dir / 'relations' / 'entity_relation.parquet')

        record_attrs = pl.Series(
            [[{'term': None, 'value': None, 'unit': None}],
             [{'term': None, 'value': None, 'unit': None}]],
            dtype=pl.List(pl.Struct([
                pl.Field('term', pl.String),
                pl.Field('value', pl.String),
                pl.Field('unit', pl.String),
            ]))
        )
        empty_attrs = pl.Series(
            [[], []],
            dtype=pl.List(pl.Struct([
                pl.Field('term', pl.String),
                pl.Field('value', pl.String),
                pl.Field('unit', pl.String),
            ]))
        )
        relation_evidence_df = pl.DataFrame({
            'source': ['test_source', 'test_source'],
            'relation_evidence_pk': [1, 2],
            'relation_pk': [1, 2],
            'relation_key': ['rel1', 'rel2'],
            'raw_record_id': ['r1', 'r3'],
            'record_attributes': record_attrs,
            'subject_attributes': empty_attrs,
            'object_attributes': empty_attrs,
            'evidence': empty_attrs,
        })
        relation_evidence_df.write_parquet(source_dir / 'relations' / 'entity_relation_evidence.parquet')

        print("=== STEP 6: Incremental combine ===")
        build_combined(
            gold_root=gold_root,
            output_dir=output_dir,
            affected_entity_keys={'key1', 'key3'},
            affected_relation_keys={'rel1', 'rel2'},
            changed_source='test_source',
        )

        print("=== STEP 7: Incremental Postgres load ===")
        load_combined_schema_to_postgres(
            output_dir=output_dir,
            postgres_uri=POSTGRES_URI,
            schema='public',
            drop_existing=False,
            batch_size=10_000,
            mode='incremental',
            affected_entity_keys=['key1', 'key3'],
            affected_relation_keys=['rel1', 'rel2'],
            changed_source='test_source',
        )

        print("=== STEP 8: Verify updated bitmaps ===")
        bitmaps_after = query_bitmaps(POSTGRES_URI)
        print(f"  facet_entity: {bitmaps_after['facet_entity']}")
        print(f"  facet_relation: {bitmaps_after['facet_relation']}")

        # Verify entity facets
        entity_facets = {v: c for _, v, c in bitmaps_after['facet_entity']}
        assert entity_facets.get('protein') == 1, f"Expected protein=1, got {entity_facets}"
        assert entity_facets.get('gene') == 1, f"Expected gene=1, got {entity_facets}"
        assert entity_facets.get('chemical') == 1, f"Expected chemical=1, got {entity_facets}"
        print("  PASS: Entity facets correct after incremental update")

        # Verify relation facets
        relation_facets = {v: c for _, v, c in bitmaps_after['facet_relation']}
        assert relation_facets.get('interacts_with') == 1, f"Expected interacts_with=1, got {relation_facets}"
        assert relation_facets.get('associated_with') == 1, f"Expected associated_with=1, got {relation_facets}"
        print("  PASS: Relation facets correct after incremental update")

        # Count entities and relations in base tables
        import psycopg2
        with psycopg2.connect(POSTGRES_URI) as conn:
            with conn.cursor() as cur:
                cur.execute('SELECT COUNT(*) FROM public.entity')
                entity_count = cur.fetchone()[0]
                cur.execute('SELECT COUNT(*) FROM public.entity_relation')
                relation_count = cur.fetchone()[0]
                print(f"  Base tables: {entity_count} entities, {relation_count} relations")
                assert entity_count == 3, f"Expected 3 entities, got {entity_count}"
                assert relation_count == 2, f"Expected 2 relations, got {relation_count}"

        print("\n=== ALL TESTS PASSED ===")


if __name__ == '__main__':
    run_test()
