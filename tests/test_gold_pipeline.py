from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from omnipath_build.gold.convert import INTERACTIONS_SCHEMA, SourceConverter
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
                (output_dir / 'entities.parquet').write_text(source, encoding='utf-8')
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
                (output_dir / 'entities.parquet').write_text(source, encoding='utf-8')
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
                (output_dir / 'entities.parquet').write_text(f'{source}:{silver_dir.name}:{mapping_dir.name}', encoding='utf-8')
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
                (output_dir / 'entities.parquet').write_text(f'{source}:{silver_dir.name}', encoding='utf-8')
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
                (output_dir / 'entities.parquet').write_text(f'{source}:{silver_dir.name}', encoding='utf-8')
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
