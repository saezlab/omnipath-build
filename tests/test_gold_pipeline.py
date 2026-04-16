from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import polars as pl

from omnipath_build.gold.combined import build_combined_parquets
from omnipath_build.gold.convert import INTERACTIONS_SCHEMA, SourceConverter
from omnipath_build.gold.dedup import deduplicate_target_schema_dir
from omnipath_build.pipeline.cli import build_parser
from omnipath_build.pipeline.dag import (
    _has_gold_buildable_dataset,
    build_task_graph,
    run_pipeline,
)
from omnipath_build.silver.build import ResourceFunction


class GoldPipelineTests(unittest.TestCase):
    def test_interactions_schema_keeps_annotation_terms_out_of_materialized_columns(self) -> None:
        field_names = INTERACTIONS_SCHEMA.names
        self.assertNotIn('mechanism_term', field_names)
        self.assertNotIn('statement_term', field_names)

    def test_attribute_units_are_normalized_like_terms(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            converter = SourceConverter(
                source='test',
                silver_dir=Path(tmp),
                output_dir=Path(tmp),
            )
            try:
                rows = converter._annotations_to_attributes([
                    {'term': 'MI:0643', 'value': '0.99', 'units': 'OM:0722'},
                ])
            finally:
                converter.close()

        self.assertEqual(len(rows or []), 1)
        self.assertNotEqual(rows[0]['term'], 'MI:0643')
        self.assertNotEqual(rows[0]['unit'], 'OM:0722')

    def test_build_task_graph_matches_plan(self) -> None:
        tasks = build_task_graph(['signor', 'reactome'], include_mappings=True, include_sources=True)
        keys = [task.key for task in tasks]
        self.assertEqual(
            keys,
            [
                'resolver_mappings',
                'silver:signor',
                'gold:signor',
                'silver:reactome',
                'gold:reactome',
            ],
        )
        gold = {task.key: task for task in tasks}['gold:signor']
        self.assertEqual(gold.deps, ('silver:signor', 'resolver_mappings'))

    def test_dedup_creates_public_aggregate_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pl.DataFrame({
                'entity_id': ['P1', 'P1', 'P2'],
                'entity_id_type': ['uniprot', 'uniprot', 'uniprot'],
                'entity_type': ['protein', 'protein', 'protein'],
                'entity_attributes': [None, None, None],
                'taxonomy_id': ['9606', '9606', '9606'],
                'sources': [['signor'], ['signor'], ['signor']],
            }).write_parquet(root / 'entity.parquet')
            pl.DataFrame({
                'entity_id': ['P1'],
                'entity_id_type': ['uniprot'],
                'identifier': ['P1'],
                'identifier_type': ['uniprot'],
                'is_canonical': [True],
                'sources': [['source:signor']],
            }).write_parquet(root / 'entity_identifiers.parquet')
            pl.DataFrame({
                'source': ['signor', 'signor'],
                'interaction_id': [1, 2],
                'entity_a_id': ['P1', 'P2'],
                'entity_a_id_type': ['uniprot', 'uniprot'],
                'entity_b_id': ['P2', 'P1'],
                'entity_b_id_type': ['uniprot', 'uniprot'],
                'direction': [None, None],
                'sign': [1, 1],
                'record_attributes': [None, None],
                'entity_a_attributes': [None, None],
                'entity_b_attributes': [None, None],
                'evidence': [None, None],
            }).write_parquet(root / 'interaction_evidence.parquet')
            pl.DataFrame({
                'source': ['signor'],
                'association_id': [1],
                'parent_entity_id': ['P1'],
                'parent_entity_id_type': ['uniprot'],
                'member_entity_id': ['P2'],
                'member_entity_id_type': ['uniprot'],
                'role_term_id': [None],
                'stoichiometry': [None],
                'record_attributes': [None],
                'parent_attributes': [None],
                'member_attributes': [None],
                'evidence': [None],
            }).write_parquet(root / 'association_evidence.parquet')
            pl.DataFrame({
                'subject_type': ['entity', 'interaction'],
                'subject_id': ['P1', '1'],
                'subject_id_type': ['uniprot', None],
                'cv_term': ['OM:1:test', 'OM:2:test'],
                'source': ['signor', 'signor'],
            }).write_parquet(root / 'annotations.parquet')

            deduplicate_target_schema_dir(root)

            self.assertTrue((root / 'interaction.parquet').exists())
            self.assertTrue((root / 'association.parquet').exists())
            self.assertTrue((root / 'entity_annotation.parquet').exists())
            self.assertTrue((root / 'interaction_annotation.parquet').exists())
            self.assertFalse((root / 'annotations.parquet').exists())

            self.assertFalse((root / 'entity_identifiers.parquet').exists())

            entity = pl.read_parquet(root / 'entity.parquet')
            self.assertIn('entity_pk', entity.columns)
            self.assertIn('canonical_identifier', entity.columns)
            self.assertIn('identifiers', entity.columns)
            self.assertEqual(entity.height, 2)

            interaction = pl.read_parquet(root / 'interaction.parquet')
            self.assertEqual(interaction.height, 1)
            self.assertIn('interaction_pk', interaction.columns)
            self.assertEqual(interaction['evidence_count'].to_list(), [2])

            interaction_evidence = pl.read_parquet(root / 'interaction_evidence.parquet')
            self.assertIn('interaction_pk', interaction_evidence.columns)
            self.assertNotIn('entity_a_id', interaction_evidence.columns)

            association = pl.read_parquet(root / 'association.parquet')
            self.assertIn('association_pk', association.columns)
            self.assertIn('parent_entity_pk', association.columns)
            self.assertIn('member_entity_pk', association.columns)

            entity_annotation = pl.read_parquet(root / 'entity_annotation.parquet')
            self.assertEqual(entity_annotation.height, 1)
            self.assertIn('entity_pk', entity_annotation.columns)

            interaction_annotation = pl.read_parquet(root / 'interaction_annotation.parquet')
            self.assertEqual(interaction_annotation.height, 1)
            self.assertIn('interaction_pk', interaction_annotation.columns)

    def test_sign_implies_direction_in_deduplicated_interactions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pl.DataFrame({
                'entity_id': ['P1', 'P2'],
                'entity_id_type': ['uniprot', 'uniprot'],
                'entity_type': ['protein', 'protein'],
                'entity_attributes': [None, None],
                'taxonomy_id': ['9606', '9606'],
                'sources': [['signor'], ['signor']],
            }).write_parquet(root / 'entity.parquet')
            pl.DataFrame({
                'entity_id': ['P1', 'P2'],
                'entity_id_type': ['uniprot', 'uniprot'],
                'identifier': ['P1', 'P2'],
                'identifier_type': ['uniprot', 'uniprot'],
                'is_canonical': [True, True],
                'sources': [['source:signor'], ['source:signor']],
            }).write_parquet(root / 'entity_identifiers.parquet')
            pl.DataFrame({
                'source': ['signor'],
                'interaction_id': [1],
                'entity_a_id': ['P1'],
                'entity_a_id_type': ['uniprot'],
                'entity_b_id': ['P2'],
                'entity_b_id_type': ['uniprot'],
                'direction': [None],
                'sign': [1],
                'record_attributes': [None],
                'entity_a_attributes': [None],
                'entity_b_attributes': [None],
                'evidence': [None],
            }).write_parquet(root / 'interaction_evidence.parquet')

            deduplicate_target_schema_dir(root)

            interaction = pl.read_parquet(root / 'interaction.parquet')
            interaction_evidence = pl.read_parquet(root / 'interaction_evidence.parquet')
            self.assertEqual(interaction['direction'].to_list(), [1])
            self.assertEqual(interaction_evidence['direction'].to_list(), [1])

    def test_combined_builder_uses_public_aligned_names(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_dir = root / 'gold' / 'signor' / '1'
            source_dir.mkdir(parents=True, exist_ok=True)

            pl.DataFrame({
                'entity_pk': [1],
                'canonical_identifier': ['P1'],
                'canonical_identifier_type': ['uniprot'],
                'identifiers': [[{'identifier': 'QP1', 'identifier_type': 'refseq'}]],
                'entity_type': ['protein'],
                'entity_attributes': [None],
                'taxonomy_id': ['9606'],
                'sources': [['signor']],
            }).write_parquet(source_dir / 'entity.parquet')
            pl.DataFrame({
                'interaction_pk': [1],
                'entity_a_pk': [1],
                'entity_b_pk': [1],
                'direction': [1],
                'sign': [1],
                'evidence_count': [2],
                'sources': [['signor']],
            }).write_parquet(source_dir / 'interaction.parquet')
            pl.DataFrame({
                'entity_pk': [1],
                'cv_term': ['OM:1:test'],
                'sources': [['signor']],
            }).write_parquet(source_dir / 'entity_annotation.parquet')

            output_dir = root / 'combined'
            summary = build_combined_parquets(gold_root=root / 'gold', output_dir=output_dir)

            self.assertTrue((output_dir / 'entity.parquet').exists())
            self.assertFalse((output_dir / 'entity_identifiers.parquet').exists())
            self.assertTrue((output_dir / 'interaction.parquet').exists())
            self.assertTrue((output_dir / 'entity_annotation.parquet').exists())
            self.assertFalse((output_dir / 'entity_identifier.parquet').exists())
            self.assertEqual(summary['row_counts']['entity.parquet'], 1)
            self.assertEqual(summary['row_counts']['interaction.parquet'], 1)

            combined_entity = pl.read_parquet(output_dir / 'entity.parquet')
            self.assertIn('entity_pk', combined_entity.columns)
            self.assertIn('canonical_identifier', combined_entity.columns)

    def test_has_gold_buildable_dataset_filters_ontology_only_sources(self) -> None:
        def stub(function_name: str, output_kind: str) -> ResourceFunction:
            return ResourceFunction(
                source='omnipath_ontology',
                function_name=function_name,
                qualified_module='fake.module',
                call=lambda: [],
                resource_id='omnipath_ontology',
                output_kind=output_kind,
            )

        self.assertFalse(_has_gold_buildable_dataset([stub('resource', 'entity')]))
        self.assertFalse(
            _has_gold_buildable_dataset([
                stub('resource', 'entity'),
                stub('ontology', 'ontology'),
            ])
        )
        self.assertTrue(
            _has_gold_buildable_dataset([
                stub('resource', 'entity'),
                stub('interactions', 'entity'),
            ])
        )

    def test_run_pipeline_autodiscovers_sources_when_omitted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            def fake_build_mappings(output_dir: Path) -> dict[str, int]:
                output_dir.mkdir(parents=True, exist_ok=True)
                return {'rows': 1}

            def fake_build_silver_source(*, source: str, output_dir: Path, inputs_package: str, batch_size: int, test_mode: bool):
                output_dir.mkdir(parents=True, exist_ok=True)
                (output_dir / 'resource.parquet').write_text(source, encoding='utf-8')
                return {'source': source}

            def fake_build_gold_source(*, source: str, silver_dir: Path, output_dir: Path, mapping_dir: Path, batch_size: int):
                output_dir.mkdir(parents=True, exist_ok=True)
                (output_dir / 'entity.parquet').write_text(source, encoding='utf-8')
                return {'source': source}

            with (
                patch('omnipath_build.pipeline.dag._discover_all_sources', return_value=['a', 'b']),
                patch('omnipath_build.pipeline.dag.build_resolver_mappings', side_effect=fake_build_mappings),
                patch('omnipath_build.pipeline.dag.build_silver_source', side_effect=fake_build_silver_source),
                patch('omnipath_build.pipeline.dag.build_gold_source', side_effect=fake_build_gold_source),
                patch('omnipath_build.pipeline.dag.build_resources_parquet', return_value=root / 'gold' / 'resources.parquet'),
            ):
                report = run_pipeline(
                    command='source',
                    sources=[],
                    data_root=root,
                    inputs_package='fake.inputs',
                    jobs=2,
                    resolver_mapping_dir=None,
                )

            self.assertEqual(report['selected_sources'], ['a', 'b'])
            self.assertIn('gold:a', report['tasks'])
            self.assertIn('gold:b', report['tasks'])

    def test_run_pipeline_continues_after_source_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            def fake_build_mappings(output_dir: Path) -> dict[str, int]:
                output_dir.mkdir(parents=True, exist_ok=True)
                return {'rows': 1}

            def fake_build_silver_source(*, source: str, output_dir: Path, inputs_package: str, batch_size: int, test_mode: bool):
                output_dir.mkdir(parents=True, exist_ok=True)
                (output_dir / 'resource.parquet').write_text(source, encoding='utf-8')
                return {'source': source}

            def fake_build_gold_source(*, source: str, silver_dir: Path, output_dir: Path, mapping_dir: Path, batch_size: int):
                if source == 'bad':
                    raise RuntimeError('boom')
                output_dir.mkdir(parents=True, exist_ok=True)
                (output_dir / 'entity.parquet').write_text(source, encoding='utf-8')
                return {'source': source}

            with (
                patch('omnipath_build.pipeline.dag.build_resolver_mappings', side_effect=fake_build_mappings),
                patch('omnipath_build.pipeline.dag.build_silver_source', side_effect=fake_build_silver_source),
                patch('omnipath_build.pipeline.dag.build_gold_source', side_effect=fake_build_gold_source),
                patch('omnipath_build.pipeline.dag.build_resources_parquet', return_value=root / 'gold' / 'resources.parquet'),
            ):
                report = run_pipeline(
                    command='source',
                    sources=['bad', 'good'],
                    data_root=root,
                    inputs_package='fake.inputs',
                    jobs=2,
                    resolver_mapping_dir=None,
                )

            self.assertEqual(report['tasks']['gold:bad']['status'], 'failed')
            self.assertEqual(report['tasks']['gold:good']['status'], 'executed')
            self.assertEqual(
                report['tasks']['gold:bad']['metadata']['error']['message'],
                'boom',
            )

    def test_cli_overwrite_flag_parses_supported_forms(self) -> None:
        parser = build_parser()

        args = parser.parse_args(['source', 'signor'])
        self.assertIsNone(args.overwrite)

        args = parser.parse_args(['source', 'signor', '--overwrite'])
        self.assertEqual(args.overwrite, 'both')

        args = parser.parse_args(['source', 'signor', '--overwrite', 'gold'])
        self.assertEqual(args.overwrite, 'gold')

        args = parser.parse_args(['source', 'signor', '--overwrite', 'silver'])
        self.assertEqual(args.overwrite, 'silver')

    def test_run_pipeline_reuses_existing_outputs_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            calls: dict[str, int] = {
                'mappings': 0,
                'silver': 0,
                'gold': 0,
            }

            def fake_build_mappings(output_dir: Path) -> dict[str, int]:
                calls['mappings'] += 1
                output_dir.mkdir(parents=True, exist_ok=True)
                (output_dir / 'mappings.json').write_text('{}\n', encoding='utf-8')
                return {'rows': 1}

            def fake_build_silver_source(*, source: str, output_dir: Path, inputs_package: str, batch_size: int, test_mode: bool):
                calls['silver'] += 1
                output_dir.mkdir(parents=True, exist_ok=True)
                (output_dir / 'resource.parquet').write_text(source, encoding='utf-8')
                return {'source': source, 'inputs_package': inputs_package, 'batch_size': batch_size, 'test_mode': test_mode}

            def fake_build_gold_source(*, source: str, silver_dir: Path, output_dir: Path, mapping_dir: Path, batch_size: int):
                calls['gold'] += 1
                output_dir.mkdir(parents=True, exist_ok=True)
                (output_dir / 'entity.parquet').write_text(f'{source}:{silver_dir.name}:{mapping_dir.name}', encoding='utf-8')
                return {'source': source, 'silver_dir': str(silver_dir), 'mapping_dir': str(mapping_dir), 'batch_size': batch_size}

            with (
                patch('omnipath_build.pipeline.dag.build_resolver_mappings', side_effect=fake_build_mappings),
                patch('omnipath_build.pipeline.dag.build_silver_source', side_effect=fake_build_silver_source),
                patch('omnipath_build.pipeline.dag.build_gold_source', side_effect=fake_build_gold_source),
                patch('omnipath_build.pipeline.dag.build_resources_parquet', return_value=root / 'gold' / 'resources.parquet'),
            ):
                first = run_pipeline(
                    command='source',
                    sources=['signor'],
                    data_root=root,
                    inputs_package='fake.inputs',
                    batch_size=123,
                    test_mode=True,
                    jobs=2,
                    resolver_mapping_dir=None,
                )
                second = run_pipeline(
                    command='source',
                    sources=['signor'],
                    data_root=root,
                    inputs_package='fake.inputs',
                    batch_size=123,
                    test_mode=True,
                    jobs=2,
                    resolver_mapping_dir=None,
                )

            self.assertEqual(calls['mappings'], 0)
            self.assertEqual(calls['silver'], 1)
            self.assertEqual(calls['gold'], 1)

            self.assertEqual(first['resolver_mapping_version'], second['resolver_mapping_version'])
            second_tasks = second['tasks']
            self.assertEqual(second_tasks['resolver_mappings']['status'], 'reused')
            self.assertEqual(second_tasks['silver:signor']['status'], 'reused')
            self.assertEqual(second_tasks['gold:signor']['status'], 'reused')

            latest_report = json.loads((root / 'reports' / 'latest.json').read_text(encoding='utf-8'))
            self.assertEqual(latest_report['run_id'], second['run_id'])
            self.assertEqual(latest_report['selected_sources'], ['signor'])

            silver_latest = json.loads((root / 'silver' / 'signor' / 'latest').read_text(encoding='utf-8'))
            gold_latest = json.loads((root / 'gold' / 'signor' / 'latest').read_text(encoding='utf-8'))
            self.assertEqual(silver_latest['version'], second_tasks['silver:signor']['version'])
            self.assertEqual(gold_latest['version'], second_tasks['gold:signor']['version'])

    def test_run_pipeline_overwrite_gold_reruns_only_gold(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            calls: dict[str, int] = {
                'mappings': 0,
                'silver': 0,
                'gold': 0,
            }

            def fake_build_mappings(output_dir: Path) -> dict[str, int]:
                calls['mappings'] += 1
                output_dir.mkdir(parents=True, exist_ok=True)
                (output_dir / 'mappings.json').write_text('{}\n', encoding='utf-8')
                return {'rows': 1}

            def fake_build_silver_source(*, source: str, output_dir: Path, inputs_package: str, batch_size: int, test_mode: bool):
                calls['silver'] += 1
                output_dir.mkdir(parents=True, exist_ok=True)
                (output_dir / 'resource.parquet').write_text(source, encoding='utf-8')
                return {'source': source}

            def fake_build_gold_source(*, source: str, silver_dir: Path, output_dir: Path, mapping_dir: Path, batch_size: int):
                calls['gold'] += 1
                output_dir.mkdir(parents=True, exist_ok=True)
                (output_dir / 'entity.parquet').write_text(f'{source}:{silver_dir.name}', encoding='utf-8')
                return {'source': source, 'silver_dir': str(silver_dir)}

            with (
                patch('omnipath_build.pipeline.dag.build_resolver_mappings', side_effect=fake_build_mappings),
                patch('omnipath_build.pipeline.dag.build_silver_source', side_effect=fake_build_silver_source),
                patch('omnipath_build.pipeline.dag.build_gold_source', side_effect=fake_build_gold_source),
                patch('omnipath_build.pipeline.dag.build_resources_parquet', return_value=root / 'gold' / 'resources.parquet'),
            ):
                run_pipeline(
                    command='source',
                    sources=['signor'],
                    data_root=root,
                    inputs_package='fake.inputs',
                    jobs=2,
                    resolver_mapping_dir=None,
                )
                second = run_pipeline(
                    command='source',
                    sources=['signor'],
                    data_root=root,
                    inputs_package='fake.inputs',
                    jobs=2,
                    overwrite='gold',
                    resolver_mapping_dir=None,
                )

            self.assertEqual(calls['mappings'], 0)
            self.assertEqual(calls['silver'], 1)
            self.assertEqual(calls['gold'], 2)
            self.assertEqual(second['tasks']['resolver_mappings']['status'], 'reused')
            self.assertEqual(second['tasks']['silver:signor']['status'], 'reused')
            self.assertEqual(second['tasks']['gold:signor']['status'], 'executed')
            self.assertEqual(second['overwrite'], 'gold')

    def test_run_pipeline_overwrite_silver_reruns_silver_and_gold(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            calls: dict[str, int] = {
                'mappings': 0,
                'silver': 0,
                'gold': 0,
            }

            def fake_build_mappings(output_dir: Path) -> dict[str, int]:
                calls['mappings'] += 1
                output_dir.mkdir(parents=True, exist_ok=True)
                (output_dir / 'mappings.json').write_text('{}\n', encoding='utf-8')
                return {'rows': 1}

            def fake_build_silver_source(*, source: str, output_dir: Path, inputs_package: str, batch_size: int, test_mode: bool):
                calls['silver'] += 1
                output_dir.mkdir(parents=True, exist_ok=True)
                (output_dir / 'resource.parquet').write_text(f'{source}:{calls["silver"]}', encoding='utf-8')
                return {'source': source}

            def fake_build_gold_source(*, source: str, silver_dir: Path, output_dir: Path, mapping_dir: Path, batch_size: int):
                calls['gold'] += 1
                output_dir.mkdir(parents=True, exist_ok=True)
                (output_dir / 'entity.parquet').write_text(f'{source}:{silver_dir.name}', encoding='utf-8')
                return {'source': source, 'silver_dir': str(silver_dir)}

            with (
                patch('omnipath_build.pipeline.dag.build_resolver_mappings', side_effect=fake_build_mappings),
                patch('omnipath_build.pipeline.dag.build_silver_source', side_effect=fake_build_silver_source),
                patch('omnipath_build.pipeline.dag.build_gold_source', side_effect=fake_build_gold_source),
                patch('omnipath_build.pipeline.dag.build_resources_parquet', return_value=root / 'gold' / 'resources.parquet'),
            ):
                run_pipeline(
                    command='source',
                    sources=['signor'],
                    data_root=root,
                    inputs_package='fake.inputs',
                    jobs=2,
                    resolver_mapping_dir=None,
                )
                second = run_pipeline(
                    command='source',
                    sources=['signor'],
                    data_root=root,
                    inputs_package='fake.inputs',
                    jobs=2,
                    overwrite='silver',
                    resolver_mapping_dir=None,
                )

            self.assertEqual(calls['mappings'], 0)
            self.assertEqual(calls['silver'], 2)
            self.assertEqual(calls['gold'], 2)
            self.assertEqual(second['tasks']['resolver_mappings']['status'], 'reused')
            self.assertEqual(second['tasks']['silver:signor']['status'], 'executed')
            self.assertEqual(second['tasks']['gold:signor']['status'], 'executed')
            self.assertEqual(second['overwrite'], 'silver')


if __name__ == '__main__':
    unittest.main()
