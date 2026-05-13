from __future__ import annotations

import csv
import importlib
from pathlib import Path
import sys
from typing import Any

import duckdb

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'pypath'))
sys.path.insert(0, str(ROOT / 'download-manager'))
sys.path.insert(0, str(ROOT / 'cache-manager'))
sys.path.insert(0, str(ROOT / '.venv' / 'lib' / 'python3.12' / 'site-packages'))
sys.path.insert(0, str(ROOT / 'pypath' / '.venv' / 'lib' / 'python3.12' / 'site-packages'))

from omnipath_build.rewrite.bronze import materialize_bronze_duckdb, source_state_path
from omnipath_build.rewrite.combine import materialize_combined_duckdb
import omnipath_build.rewrite.combine_duckdb as combine_duckdb
from omnipath_build.rewrite.combine_duckdb import CombinedRewriteConfig
from omnipath_build.rewrite.gold import materialize_gold_duckdb
from omnipath_build.rewrite.gold_config import GoldPartitionConfig
from omnipath_build.rewrite.silver import materialize_silver_duckdb
from omnipath_build.silver.build import ResourceFunction


FIXTURES = Path(__file__).parent / 'fixtures' / 'rewrite_pipeline'
GOLD_CFG = GoldPartitionConfig(
    bucket_count=64,
    part_count=4,
    min_part_size_bytes=0,
    duckdb_memory_limit='512MB',
    duckdb_threads=1,
)
COMBINED_CFG = CombinedRewriteConfig(
    bucket_count=64,
    part_count=4,
    duckdb_memory_limit='512MB',
    duckdb_threads=1,
)


def _tsv_records(path: Path, *, signor_interactions: bool = False) -> list[dict[str, Any]]:
    with path.open(newline='', encoding='utf-8') as handle:
        rows = list(csv.DictReader(handle, delimiter='\t'))
    if signor_interactions:
        for row in rows:
            row['\ufeff#ID(s) interactor A'] = row.pop('#ID(s) interactor A')
    return rows


def _resource_functions(source: str, *function_names: str) -> list[Any]:
    from pypath.inputs_v2.base import ArtifactDataset, OntologyDataset

    module = importlib.import_module(f'pypath.inputs_v2.{source}')
    resource = module.resource
    datasets = resource.datasets()
    wanted = set(function_names)
    functions = []
    for dataset_name, dataset_obj in datasets.items():
        if wanted and dataset_name not in wanted:
            continue
        output_kind = 'entity'
        ontology_id = None
        if isinstance(dataset_obj, OntologyDataset):
            output_kind = 'ontology'
            ontology_id = dataset_obj.ontology_id
        elif isinstance(dataset_obj, ArtifactDataset):
            output_kind = 'artifact'

        def dataset_call(dataset_obj=dataset_obj) -> Any:
            return dataset_obj()

        dataset_call._raw_dataset = dataset_obj
        functions.append(
            ResourceFunction(
                source=source,
                function_name=dataset_name,
                qualified_module=f'pypath.inputs_v2.{source}',
                call=dataset_call,
                resource_id=source,
                output_kind=output_kind,
                ontology_id=ontology_id,
            )
        )
    return functions


def _table_count(con: duckdb.DuckDBPyConnection, table: str) -> int:
    if not con.execute(
        """
        select count(*)
        from information_schema.tables
        where table_schema = 'main'
          and table_name = ?
        """,
        [table],
    ).fetchone()[0]:
        return 0
    return int(con.execute(f'select count(*) from "{table}"').fetchone()[0] or 0)


def _source_work(data_root: Path, source: str) -> dict[str, int]:
    con = duckdb.connect(str(source_state_path(data_root, source)), read_only=True)
    try:
        return {
            'raw_scope': _table_count(con, 'source_run_scope_raw_record'),
            'occurrence_scope': _table_count(con, 'source_run_scope_occurrence'),
            'gold_entity_scope': _table_count(con, 'source_run_scope_entity'),
            'gold_relation_scope': _table_count(con, 'source_run_scope_relation'),
            'gold_entity': _table_count(con, 'gold_entity'),
            'gold_relation': _table_count(con, 'gold_entity_relation'),
        }
    finally:
        con.close()


def _empty_relation_object_count(data_root: Path, source: str) -> int:
    con = duckdb.connect(str(source_state_path(data_root, source)), read_only=True)
    try:
        return int(
            con.execute(
                """
                select count(*)
                from gold_entity_relation_evidence
                where object_entity_key is null
                   or object_entity_key = ''
                """
            ).fetchone()[0]
            or 0
        )
    finally:
        con.close()


def _empty_relation_endpoint_count(data_root: Path, source: str) -> int:
    con = duckdb.connect(str(source_state_path(data_root, source)), read_only=True)
    try:
        return int(
            con.execute(
                """
                select count(*)
                from gold_entity_relation
                where subject_entity_pk is null
                   or object_entity_pk is null
                """
            ).fetchone()[0]
            or 0
        )
    finally:
        con.close()


def _bronze_delta(data_root: Path, source: str) -> dict[str, int]:
    con = duckdb.connect(str(source_state_path(data_root, source)), read_only=True)
    try:
        latest_snapshot_id = con.execute(
            """
            select snapshot_id
            from bronze_dataset_snapshot
            where source = ?
            order by created_at desc
            limit 1
            """,
            [source],
        ).fetchone()[0]
        return {
            str(kind): int(count)
            for kind, count in con.execute(
                """
                select change_type, count(distinct raw_record_key)
                from bronze_raw_record_change
                where source_run_id = ?
                group by change_type
                order by change_type
                """,
                [latest_snapshot_id],
            ).fetchall()
        }
    finally:
        con.close()


def _latest_combined_scope(data_root: Path) -> dict[str, int]:
    con = duckdb.connect(str(data_root / 'state' / 'combined.duckdb'), read_only=True)
    try:
        run_id = con.execute(
            'select combined_run_id from combined_run order by started_at desc limit 1'
        ).fetchone()[0]
        return {
            'combined_entity_scope': int(
                con.execute(
                    'select count(*) from combined_run_scope_entity where combined_run_id = ?',
                    [run_id],
                ).fetchone()[0]
            ),
            'combined_relation_scope': int(
                con.execute(
                    'select count(*) from combined_run_scope_relation where combined_run_id = ?',
                    [run_id],
                ).fetchone()[0]
            ),
        }
    finally:
        con.close()


def _latest_combined_scope_sources(data_root: Path) -> dict[str, set[str | None]]:
    con = duckdb.connect(str(data_root / 'state' / 'combined.duckdb'), read_only=True)
    try:
        run_id = con.execute(
            'select combined_run_id from combined_run order by started_at desc limit 1'
        ).fetchone()[0]
        return {
            'entity_sources': {
                row[0]
                for row in con.execute(
                    'select distinct source from combined_run_scope_entity where combined_run_id = ?',
                    [run_id],
                ).fetchall()
            },
            'relation_sources': {
                row[0]
                for row in con.execute(
                    'select distinct source from combined_run_scope_relation where combined_run_id = ?',
                    [run_id],
                ).fetchall()
            },
        }
    finally:
        con.close()


def _minimal_resources_parquet(
    *,
    gold_root: str | Path,
    output_path: str | Path,
    inputs_package: str = 'pypath.inputs_v2',
) -> Path:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    try:
        con.execute(
            f"""
            copy (
                select 'signor'::varchar as resource_id,
                       'SIGNOR'::varchar as resource_name,
                       'fixture'::varchar as resource_kind,
                       'success'::varchar as build_status
                union all
                select 'uniprot'::varchar,
                       'UniProt'::varchar,
                       'fixture'::varchar,
                       'success'::varchar
            ) to '{str(output_path).replace("'", "''")}' (format parquet)
            """,
        )
    finally:
        con.close()
    return output_path


def test_rewrite_pipeline_tracks_work_done_for_bootstrap_noop_and_delta(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    data_root = tmp_path / 'data_rewrite'
    monkeypatch.setattr(
        combine_duckdb,
        'build_resources_parquet',
        _minimal_resources_parquet,
    )

    materialize_bronze_duckdb(
        records=_tsv_records(FIXTURES / 'uniprot_proteins_initial.tsv'),
        source='uniprot',
        dataset='proteins',
        data_root=data_root,
    )
    uniprot_silver = materialize_silver_duckdb(
        source='uniprot',
        resource_functions=_resource_functions('uniprot', 'proteins'),
        data_root=data_root,
    )
    uniprot_gold = materialize_gold_duckdb(
        source='uniprot',
        data_root=data_root,
        partition_config=GOLD_CFG,
    )
    uniprot_combined = materialize_combined_duckdb(
        sources=['uniprot'],
        data_root=data_root,
        config=COMBINED_CFG,
    )

    assert _bronze_delta(data_root, 'uniprot') == {'added': 2}
    assert uniprot_silver.mapped_raw_record_count == 2
    assert uniprot_gold.gold_changed is True
    assert uniprot_gold.archive_written is True
    assert _empty_relation_object_count(data_root, 'uniprot') == 0
    assert uniprot_combined.mode == 'bootstrap'
    assert _latest_combined_scope(data_root)['combined_entity_scope'] > 0
    assert _source_work(data_root, 'uniprot')['raw_scope'] == 0

    materialize_bronze_duckdb(
        records=_tsv_records(
            FIXTURES / 'signor_interactions_initial.tsv',
            signor_interactions=True,
        ),
        source='signor',
        dataset='interactions',
        data_root=data_root,
    )
    signor_silver = materialize_silver_duckdb(
        source='signor',
        resource_functions=_resource_functions('signor', 'interactions'),
        data_root=data_root,
    )
    signor_gold = materialize_gold_duckdb(
        source='signor',
        data_root=data_root,
        partition_config=GOLD_CFG,
    )
    signor_work = _source_work(data_root, 'signor')
    signor_combined = materialize_combined_duckdb(
        sources=['uniprot', 'signor'],
        data_root=data_root,
        config=COMBINED_CFG,
    )

    assert _bronze_delta(data_root, 'signor') == {'added': 2}
    assert signor_silver.mapped_raw_record_count == 2
    assert signor_gold.gold_changed is True
    assert signor_combined.mode == 'incremental'
    assert signor_work['raw_scope'] == 2
    assert signor_work['occurrence_scope'] > 0
    assert signor_work['gold_entity_scope'] > 0
    assert signor_work['gold_relation_scope'] > 0
    assert _empty_relation_endpoint_count(data_root, 'signor') == 0
    assert _latest_combined_scope_sources(data_root)['entity_sources'] == {'signor'}
    assert 'uniprot' not in _latest_combined_scope_sources(data_root)['relation_sources']
    assert _source_work(data_root, 'signor')['raw_scope'] == 0

    materialize_bronze_duckdb(
        records=_tsv_records(
            FIXTURES / 'signor_interactions_initial.tsv',
            signor_interactions=True,
        ),
        source='signor',
        dataset='interactions',
        data_root=data_root,
    )
    signor_silver_noop = materialize_silver_duckdb(
        source='signor',
        resource_functions=_resource_functions('signor', 'interactions'),
        data_root=data_root,
    )
    signor_gold_noop = materialize_gold_duckdb(
        source='signor',
        data_root=data_root,
        partition_config=GOLD_CFG,
    )

    assert _bronze_delta(data_root, 'signor') == {}
    assert signor_silver_noop.mapped_raw_record_count == 0
    assert signor_gold_noop.gold_changed is False
    assert signor_gold_noop.archive_written is False
    assert _source_work(data_root, 'signor')['raw_scope'] == 0
    assert _source_work(data_root, 'signor')['gold_entity_scope'] == 0

    materialize_bronze_duckdb(
        records=_tsv_records(
            FIXTURES / 'signor_interactions_changed.tsv',
            signor_interactions=True,
        ),
        source='signor',
        dataset='interactions',
        data_root=data_root,
    )
    signor_silver_delta = materialize_silver_duckdb(
        source='signor',
        resource_functions=_resource_functions('signor', 'interactions'),
        data_root=data_root,
    )
    signor_gold_delta = materialize_gold_duckdb(
        source='signor',
        data_root=data_root,
        partition_config=GOLD_CFG,
    )
    signor_delta_work = _source_work(data_root, 'signor')
    signor_combined_delta = materialize_combined_duckdb(
        sources=['uniprot', 'signor'],
        data_root=data_root,
        config=COMBINED_CFG,
    )

    combined_delta_work = _latest_combined_scope(data_root)
    combined_delta_sources = _latest_combined_scope_sources(data_root)
    assert _bronze_delta(data_root, 'signor') == {'added': 2, 'removed': 2}
    assert signor_silver_delta.mapped_raw_record_count == 2
    assert signor_silver_delta.deleted_raw_record_count == 4
    assert signor_gold_delta.gold_changed is True
    assert signor_combined_delta.mode == 'incremental'
    assert signor_delta_work['raw_scope'] == 4
    assert signor_delta_work['gold_entity_scope'] > 0
    assert signor_delta_work['gold_relation_scope'] > 0
    assert combined_delta_work['combined_entity_scope'] > 0
    assert combined_delta_sources['entity_sources'] == {'signor'}
    assert 'uniprot' not in combined_delta_sources['relation_sources']
    assert _source_work(data_root, 'signor')['raw_scope'] == 0
