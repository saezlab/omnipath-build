from __future__ import annotations

import os
import json
import shutil
from typing import Any
import hashlib
from pathlib import Path
from datetime import UTC, datetime
import tempfile
import importlib.util

import duckdb
import pyarrow.parquet as pq
import polars as pl

from omnipath_build.silver.build import run_silver_loader, discover_resources
from omnipath_build.silver.tables import (
    SILVER_TABLE_SCHEMAS,
    has_raw_keyed_silver_tables,
)
from omnipath_build.gold.utils.keys import compute_relation_key
from id_resolver.build.mapping_tables import (
    CHEMICAL_SOURCES,
    run_sources as materialize_resolver_tables,
)
from omnipath_build.gold.build_entities import (
    build_entities,
    reduce_entities_from_evidence,
)
from omnipath_build.gold.build_relations import (
    build_relations,
    reduce_relations_from_evidence,
)
from omnipath_build.gold.utils.partitioning import (
    ENTITY_BUCKET_COUNT,
    ENTITY_PART_COUNT,
    RELATION_BUCKET_COUNT,
    RELATION_PART_COUNT,
    add_entity_partition_columns,
    add_relation_partition_columns,
)
from omnipath_build.pipeline.resource_archives import build_resource_archive

REFERENCE_MAPPING_SOURCES = ['uniprot', *CHEMICAL_SOURCES]
TEST_MODE_REFERENCE_MAPPING_SOURCES = [
    'uniprot',
    'chebi',
]

INPUTS_MODULE_HASH_FILE = 'inputs_module_hash.json'
GOLD_SUCCESS_FILE = '_SUCCESS.json'
GOLD_DELTA_DIR = '_delta'
SILVER_STATE_DIR = 'state'
SILVER_DELTA_DIR = 'delta'
GOLD_BUCKET_ALGORITHM = 'stable_u64_sha256_mod_v1'
GOLD_ENTITY_KEY_ALGORITHM = 'sha256_v1'
GOLD_RELATION_KEY_ALGORITHM = 'sha256_v1'


def hash_inputs_module(inputs_package: str, source: str) -> dict[str, Any]:
    """Hash the Python files for the inputs_v2 module backing a source."""
    module_name = f'{inputs_package}.{source}'
    spec = importlib.util.find_spec(module_name)
    if spec is None:
        raise ModuleNotFoundError(f'Unable to find inputs module {module_name}')

    files: list[Path] = []
    if spec.origin and spec.origin not in {'built-in', 'namespace'}:
        origin = Path(spec.origin)
        if origin.exists() and origin.suffix == '.py':
            files.append(origin)

    for location in spec.submodule_search_locations or []:
        root = Path(location)
        if root.exists():
            files.extend(path for path in root.rglob('*.py') if path.is_file())

    files = sorted(set(files))
    if not files:
        raise FileNotFoundError(f'No Python files found for inputs module {module_name}')

    root = Path(spec.submodule_search_locations[0]).parent if spec.submodule_search_locations else files[0].parent
    digest = hashlib.sha256()
    entries: list[dict[str, str]] = []
    for path in files:
        content = path.read_bytes()
        file_hash = hashlib.sha256(content).hexdigest()
        try:
            rel_path = path.relative_to(root)
        except ValueError:
            rel_path = Path(path.name)
        digest.update(str(rel_path).encode('utf-8'))
        digest.update(b'\0')
        digest.update(file_hash.encode('ascii'))
        digest.update(b'\0')
        entries.append({'path': str(path), 'sha256': file_hash})

    return {
        'module': module_name,
        'sha256': digest.hexdigest(),
        'files': entries,
    }


def write_inputs_module_hash(output_dir: Path, hash_info: dict[str, Any]) -> None:
    path = output_dir / INPUTS_MODULE_HASH_FILE
    tmp_path = path.with_suffix(path.suffix + '.tmp')
    tmp_path.write_text(
        json.dumps(hash_info, indent=2, sort_keys=True) + '\n',
        encoding='utf-8',
    )
    tmp_path.replace(path)


def read_inputs_module_hash(output_dir: Path) -> dict[str, Any] | None:
    path = output_dir / INPUTS_MODULE_HASH_FILE
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except json.JSONDecodeError:
        return None


def resolver_mappings_ready(mapping_dir: Path) -> bool:
    required = [
        mapping_dir / 'proteins' / 'protein_identifier_lookup.parquet',
        mapping_dir / 'chemicals' / 'chemical_identifier_lookup.parquet',
    ]
    return all(path.exists() for path in required)


def build_resolver_mappings(output_dir: Path, *, test_mode: bool = False) -> dict[str, int]:
    output_dir.mkdir(parents=True, exist_ok=True)
    sources = (
        TEST_MODE_REFERENCE_MAPPING_SOURCES
        if test_mode else
        REFERENCE_MAPPING_SOURCES
    )
    return materialize_resolver_tables(
        sources=sources,
        output_dir=output_dir,
    )


def build_silver_source(
    *,
    source: str,
    output_dir: Path,
    inputs_package: str,
    batch_size: int,
    test_mode: bool,
) -> dict[str, Any]:
    inputs_hash = hash_inputs_module(inputs_package, source)
    source_root = output_dir.parent
    state_dir = source_root / SILVER_STATE_DIR
    previous_snapshot_dir = _latest_silver_snapshot_dir(source_root)
    previous_inputs_hash = (
        read_inputs_module_hash(previous_snapshot_dir)
        if previous_snapshot_dir is not None else None
    )
    inputs_compatible = (
        previous_inputs_hash is not None
        and previous_inputs_hash.get('sha256') == inputs_hash.get('sha256')
    )
    state_ready = has_raw_keyed_silver_tables(state_dir)
    supports_incremental = _source_supports_incremental_silver(source, inputs_package)
    incremental = (
        state_ready
        and supports_incremental
        and inputs_compatible
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    configured_cache = os.environ.get('PYPATH_DOWNLOAD_DATADIR')
    if configured_cache:
        configured_cache_path = Path(configured_cache).expanduser()
        download_cache = (
            configured_cache_path
            if configured_cache_path.is_absolute()
            else (Path.cwd() / configured_cache_path).resolve()
        )
    else:
        download_cache = Path(__file__).resolve().parents[2] / 'pypath-data'
    print(
        f'[{source}] silver loader: cache='
        f'{download_cache} test_mode={test_mode}'
    )
    with tempfile.TemporaryDirectory(prefix='op-pipeline-silver-') as tmp:
        stage_root = Path(tmp)
        _, _, selected_functions, outputs = run_silver_loader(
            database='.',
            base_path=stage_root,
            source=source,
            list_only=False,
            batch_size=batch_size,
            dry_run=False,
            override=True,
            test_mode=test_mode,
            inputs_package=inputs_package,
            silver_state_dir=state_dir if incremental else None,
        )
        staged_source_dir = stage_root / 'silver' / source.replace('.', '/')
        if not staged_source_dir.exists():
            raise FileNotFoundError(f'Silver output missing for {source}: {staged_source_dir}')

        if incremental and previous_snapshot_dir is not None and not _staged_has_silver_tables(staged_source_dir):
            shutil.rmtree(output_dir, ignore_errors=True)
            print(
                f'[{source}] silver no-op; reused previous snapshot '
                f'{previous_snapshot_dir}',
                flush=True,
            )
            return {
                'files': sorted(p.name for p in previous_snapshot_dir.iterdir() if p.is_file()),
                'functions': [f.function_name for f in (selected_functions or [])],
                'outputs': [str(output) for output in (outputs or []) if output is not None],
                'inputs_module_hash': inputs_hash,
                'incremental': True,
                'skipped': 'empty_bronze_delta',
                'output_dir': str(previous_snapshot_dir),
                'version': previous_snapshot_dir.name,
            }

        delta_summary = _write_silver_state_and_delta(
            source=source,
            staged_source_dir=staged_source_dir,
            output_dir=output_dir,
            inputs_hash=inputs_hash,
            write_lineage_delta=incremental,
            no_lineage_delta_reason=(
                None if incremental else
                'unsupported_source' if not supports_incremental else
                'missing_previous_state' if not state_ready else
                'inputs_changed_or_missing_previous_hash'
            ),
        )

        for item in sorted(staged_source_dir.iterdir()):
            target = output_dir / item.name
            if item.is_dir():
                shutil.copytree(item, target, dirs_exist_ok=True)
            else:
                shutil.copy2(item, target)

    write_inputs_module_hash(output_dir, inputs_hash)

    return {
        'files': sorted(p.name for p in output_dir.iterdir() if p.is_file()),
        'functions': [f.function_name for f in (selected_functions or [])],
        'outputs': [str(output) for output in (outputs or []) if output is not None],
        'inputs_module_hash': inputs_hash,
        'delta_summary': delta_summary,
        'incremental': incremental,
    }


def _latest_silver_snapshot_dir(source_root: Path) -> Path | None:
    latest_file = source_root / 'latest'
    if latest_file.exists():
        try:
            latest_data = json.loads(latest_file.read_text(encoding='utf-8'))
        except json.JSONDecodeError:
            latest_data = {}
        version = latest_data.get('version')
        if version:
            candidate = source_root / str(version)
            if candidate.exists():
                return candidate

    numeric_dirs: list[Path] = []
    if source_root.exists():
        for child in source_root.iterdir():
            if child.is_dir() and child.name.isdigit():
                numeric_dirs.append(child)
    if not numeric_dirs:
        return None
    return sorted(numeric_dirs, key=lambda path: int(path.name))[-1]


def _staged_has_silver_tables(staged_source_dir: Path) -> bool:
    return any((staged_source_dir / table_name).exists() for table_name in _silver_table_names())


def _source_supports_incremental_silver(source: str, inputs_package: str) -> bool:
    discovered, _ = discover_resources(
        database_name='.',
        base_path=None,
        inputs_package=inputs_package,
    )
    functions = discovered.get(source)
    if not functions:
        return False
    return all(
        fn.function_name == 'resource' or fn.output_kind in {'entity', 'ontology'}
        for fn in functions
    )


def _silver_table_names() -> list[str]:
    return list(SILVER_TABLE_SCHEMAS)


def _parquet_row_count(path: Path) -> int:
    if not path.exists():
        return 0
    if path.is_file():
        return int(pq.ParquetFile(path).metadata.num_rows)
    return sum(int(pq.ParquetFile(file).metadata.num_rows) for file in _parquet_table_files(path))


def _parquet_table_files(path: Path) -> list[Path]:
    if path.is_file():
        return [path]
    if path.is_dir():
        return sorted(child for child in path.rglob('*.parquet') if child.is_file())
    return []


def _resolve_parquet_table_path(*candidates: Path) -> Path | None:
    """Resolve a logical table as a partitioned dataset dir or legacy file."""
    expanded: list[Path] = []
    seen: set[Path] = set()
    for candidate in candidates:
        variants = (
            [candidate.with_suffix(''), candidate]
            if candidate.suffix == '.parquet' else
            [candidate, candidate.with_suffix('.parquet')]
        )
        for variant in variants:
            if variant not in seen:
                expanded.append(variant)
                seen.add(variant)

    for candidate in expanded:
        if candidate.is_dir() and _parquet_table_files(candidate):
            return candidate
    for candidate in expanded:
        if candidate.is_file():
            return candidate
    return None


def _gold_subtable_path(parent_dir: Path, table_name: str) -> Path | None:
    return _resolve_parquet_table_path(
        parent_dir / table_name,
        parent_dir / f'{table_name}.parquet',
    )


def _gold_table_path(output_dir: Path, group_name: str, table_name: str) -> Path | None:
    return _gold_subtable_path(output_dir / group_name, table_name)


def _require_parquet_table_path(path: Path | None, description: str) -> Path:
    if path is None:
        raise FileNotFoundError(f'missing parquet table: {description}')
    return path


def _polars_parquet_source(path: Path) -> str:
    if path.is_dir():
        return str(path / '**' / '*.parquet')
    return str(path)


def _read_parquet_table(path: Path, *, columns: list[str] | None = None) -> pl.DataFrame:
    return pl.read_parquet(
        _polars_parquet_source(path),
        columns=columns,
        hive_partitioning=False,
    )


def _scan_parquet_table(path: Path) -> pl.LazyFrame:
    return pl.scan_parquet(
        _polars_parquet_source(path),
        hive_partitioning=False,
    )


def _copy_parquet_table(source_path: Path, target_dir: Path, table_name: str) -> None:
    target_dir.mkdir(parents=True, exist_ok=True)
    target_dataset = target_dir / table_name
    target_file = target_dir / f'{table_name}.parquet'
    for target in (target_dataset, target_file):
        if target.is_dir():
            shutil.rmtree(target)
        elif target.exists():
            target.unlink()

    if source_path.is_dir():
        shutil.copytree(source_path, target_dataset)
    else:
        shutil.copy2(source_path, target_file)


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _select_columns_sql(columns: list[str], alias: str) -> str:
    return ',\n'.join(
        f'{alias}.{_quote_identifier(column)}'
        for column in columns
    )


def _copy_silver_table(source_path: Path, target_path: Path) -> None:
    target_path.parent.mkdir(parents=True, exist_ok=True)
    if target_path.exists():
        if target_path.is_dir():
            shutil.rmtree(target_path)
        else:
            target_path.unlink()
    if source_path.is_dir():
        shutil.copytree(source_path, target_path)
    else:
        shutil.copy2(source_path, target_path)


def _duckdb_read_parquet_table_sql(path: Path) -> str:
    if path.is_dir():
        value = str(path / '**' / '*.parquet')
        escaped = value.replace("'", "''")
        return (
            "read_parquet("
            f"'{escaped}', "
            "union_by_name=true, hive_partitioning=true)"
        )
    value = str(path)
    escaped = value.replace("'", "''")
    return f"read_parquet('{escaped}')"


def _write_lineage_delta_for_table(
    *,
    table_name: str,
    previous_path: Path,
    current_path: Path,
    delta_path: Path,
) -> dict[str, int]:
    """Write silver delta rows by raw-record lineage, not by full row diff."""
    columns = list(SILVER_TABLE_SCHEMAS[table_name].names)
    selected_current = _select_columns_sql(columns, 'c')
    selected_previous = _select_columns_sql(columns, 'p')
    current_sql = _duckdb_read_parquet_table_sql(current_path)
    previous_sql = _duckdb_read_parquet_table_sql(previous_path)
    output_file = delta_path / 'part=00000.parquet'
    output_sql = "'" + str(output_file).replace("'", "''") + "'"

    if delta_path.exists():
        if delta_path.is_dir():
            shutil.rmtree(delta_path)
        else:
            delta_path.unlink()
    delta_path.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    try:
        con.execute(
            f"""
            COPY (
                WITH
                previous_keys AS (
                    SELECT DISTINCT _raw_record_key
                    FROM {previous_sql}
                    WHERE _raw_record_key IS NOT NULL
                ),
                current_keys AS (
                    SELECT DISTINCT _raw_record_key
                    FROM {current_sql}
                    WHERE _raw_record_key IS NOT NULL
                )
                SELECT
                    {selected_current},
                    'added'::VARCHAR AS _change_type
                FROM {current_sql} AS c
                WHERE c._raw_record_key IS NOT NULL
                  AND c._raw_record_key NOT IN (SELECT _raw_record_key FROM previous_keys)
                UNION ALL
                SELECT
                    {selected_previous},
                    'removed'::VARCHAR AS _change_type
                FROM {previous_sql} AS p
                WHERE p._raw_record_key IS NOT NULL
                  AND p._raw_record_key NOT IN (SELECT _raw_record_key FROM current_keys)
            ) TO {output_sql} (FORMAT PARQUET, COMPRESSION ZSTD)
            """
        )
        counts = con.execute(
            """
            SELECT _change_type, count(*) AS count
            FROM read_parquet(?)
            GROUP BY _change_type
            """,
            [str(output_file)],
        ).fetchall()
    finally:
        con.close()

    by_type = {str(change_type): int(count) for change_type, count in counts}
    return {
        'added': by_type.get('added', 0),
        'removed': by_type.get('removed', 0),
    }


def _write_silver_state_and_delta(
    *,
    source: str,
    staged_source_dir: Path,
    output_dir: Path,
    inputs_hash: dict[str, Any],
    write_lineage_delta: bool,
    no_lineage_delta_reason: str | None,
) -> dict[str, Any]:
    source_root = output_dir.parent
    state_dir = source_root / SILVER_STATE_DIR
    delta_dir = output_dir / SILVER_DELTA_DIR
    state_dir.mkdir(parents=True, exist_ok=True)
    delta_dir.mkdir(parents=True, exist_ok=True)

    row_counts: dict[str, int] = {}
    delta_counts: dict[str, dict[str, int]] = {}
    for table_name in _silver_table_names():
        staged_path = staged_source_dir / table_name
        state_path = state_dir / table_name
        row_counts[table_name] = _parquet_row_count(staged_path)

        if write_lineage_delta:
            delta_counts[table_name] = _write_lineage_delta_for_table(
                table_name=table_name,
                previous_path=state_path,
                current_path=staged_path,
                delta_path=delta_dir / table_name,
            )
        else:
            delta_counts[table_name] = {'added': 0, 'removed': 0}

        _copy_silver_table(staged_path, state_path)

    manifest = {
        'layer': 'silver',
        'source': source,
        'snapshot_id': output_dir.name,
        'created_at': datetime.now(UTC).isoformat(),
        'state_dir': str(state_dir),
        'delta_dir': str(delta_dir),
        'inputs_module_hash': inputs_hash,
        'row_counts': row_counts,
        'delta_counts': delta_counts,
        'delta_strategy': (
            'raw_record_lineage'
            if write_lineage_delta else
            'no_per_row_delta'
        ),
        'no_per_row_delta_reason': no_lineage_delta_reason,
    }
    _write_json_atomic(output_dir / 'manifest.json', manifest)
    _write_json_atomic(state_dir / 'manifest.json', manifest)
    _write_json_atomic(source_root / 'latest.json', {
        'source': source,
        'snapshot_id': output_dir.name,
        'path': str(output_dir),
        'manifest': str(output_dir / 'manifest.json'),
    })
    return manifest


def resolve_silver_version(silver_source_dir: Path) -> Path:
    latest_file = silver_source_dir / 'latest'
    if latest_file.exists():
        latest_data = json.loads(latest_file.read_text(encoding='utf-8'))
        version = str(latest_data.get('version', '1'))
        version_dir = silver_source_dir / version
        if version_dir.exists():
            return version_dir

    for subdir in sorted(silver_source_dir.iterdir()):
        if subdir.is_dir() and subdir.name.isdigit():
            return subdir

    raise FileNotFoundError(f'No silver data found in {silver_source_dir}')


def silver_has_data(silver_dir: Path) -> bool:
    return any(
        (silver_dir / table_name).exists()
        and _parquet_row_count(silver_dir / table_name) > 0
        for table_name in _silver_table_names()
    )


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + '.tmp')
    tmp_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + '\n',
        encoding='utf-8',
    )
    tmp_path.replace(path)


def _now_build_id() -> str:
    return datetime.now(UTC).strftime('%Y%m%dT%H%M%S%fZ')


def _empty_string_frame(columns: list[str]) -> pl.DataFrame:
    return pl.DataFrame({
        column: pl.Series([], dtype=pl.String)
        for column in columns
    })


def _string_values(frame: pl.DataFrame, column: str) -> set[str]:
    if column not in frame.columns:
        return set()
    return {
        value
        for value in (
            frame
            .select(pl.col(column).cast(pl.String).alias(column))
            .get_column(column)
            .drop_nulls()
            .unique()
            .to_list()
        )
        if value
    }


def _read_silver_delta_manifest(silver_dir: Path) -> dict[str, Any] | None:
    manifest_path = silver_dir / 'manifest.json'
    if not manifest_path.exists():
        return None
    try:
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    except json.JSONDecodeError:
        return None
    return manifest if isinstance(manifest, dict) else None


def _manifest_delta_empty(manifest: dict[str, Any] | None) -> bool:
    if not manifest:
        return False
    if manifest.get('delta_strategy') != 'raw_record_lineage':
        return False
    delta_counts = manifest.get('delta_counts')
    if not isinstance(delta_counts, dict):
        return False
    for counts in delta_counts.values():
        if not isinstance(counts, dict):
            return False
        if int(counts.get('added', 0) or 0) != 0:
            return False
        if int(counts.get('removed', 0) or 0) != 0:
            return False
    return True


def _silver_delta_dir_from_manifest(silver_dir: Path, manifest: dict[str, Any] | None) -> Path | None:
    candidates: list[Path] = []
    if manifest:
        delta_dir = manifest.get('delta_dir')
        if delta_dir:
            path = Path(str(delta_dir))
            candidates.append(path if path.is_absolute() else silver_dir / path)
    candidates.append(silver_dir / SILVER_DELTA_DIR)
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def _affected_silver_ids_from_delta(
    *,
    silver_dir: Path,
) -> tuple[set[str], set[str], dict[str, Any]]:
    manifest = _read_silver_delta_manifest(silver_dir)
    if manifest and manifest.get('delta_strategy') == 'no_per_row_delta':
        return set(), set(), {
            'available': False,
            'reason': manifest.get('no_per_row_delta_reason') or 'no_per_row_delta',
            'manifest': manifest,
            'delta_empty': False,
            'delta_strategy': 'no_per_row_delta',
        }
    delta_dir = _silver_delta_dir_from_manifest(silver_dir, manifest)
    raw_record_ids: set[str] = set()
    occurrence_ids: set[str] = set()
    row_counts: dict[str, int] = {}
    unreadable_tables: list[str] = []

    if delta_dir is None:
        return raw_record_ids, occurrence_ids, {
            'available': False,
            'reason': 'missing_silver_delta_dir',
            'manifest': manifest,
        }

    for table_name in _silver_table_names():
        path = delta_dir / table_name
        if not path.exists():
            unreadable_tables.append(table_name)
            continue
        try:
            delta = _read_parquet_table(path)
        except (OSError, pl.exceptions.PolarsError):
            unreadable_tables.append(table_name)
            continue
        row_counts[table_name] = int(delta.height)
        if delta.is_empty():
            continue

        raw_record_ids.update(_string_values(delta, 'record_id'))
        raw_record_ids.update(_string_values(delta, 'raw_record_id'))
        raw_record_ids.update(_string_values(delta, '_raw_record_id'))
        occurrence_ids.update(_string_values(delta, 'occurrence_id'))
        occurrence_ids.update(_string_values(delta, 'parent_occurrence_id'))
        occurrence_ids.update(_string_values(delta, 'member_occurrence_id'))

    if occurrence_ids:
        raw_record_ids.update(_raw_record_ids_for_occurrences(silver_dir, occurrence_ids))

    return raw_record_ids, occurrence_ids, {
        'available': True,
        'manifest': manifest,
        'delta_dir': str(delta_dir),
        'delta_empty': _manifest_delta_empty(manifest),
        'delta_row_counts': row_counts,
        'unreadable_tables': unreadable_tables,
    }


def _raw_record_ids_for_occurrences(silver_dir: Path, occurrence_ids: set[str]) -> set[str]:
    if not occurrence_ids:
        return set()
    occurrence_path = silver_dir / 'entity_occurrence'
    if not occurrence_path.exists():
        return set()
    occurrences = _scan_parquet_table(occurrence_path)
    if 'occurrence_id' not in occurrences.collect_schema().names():
        return set()
    scoped = occurrences.filter(pl.col('occurrence_id').cast(pl.String).is_in(sorted(occurrence_ids))).collect()
    values = _string_values(scoped, 'record_id')
    values.update(_string_values(scoped, '_raw_record_id'))
    return values


def _empty_affected_key_frame(key_column: str) -> pl.DataFrame:
    return _empty_string_frame(['source', key_column, 'change_type', 'reason'])


def _parquet_scan(path: Path | None) -> pl.LazyFrame | None:
    if path is None:
        return None
    resolved_path = _resolve_parquet_table_path(path)
    if resolved_path is not None:
        return _scan_parquet_table(resolved_path)
    return None


def _entity_keys_for_raw_records(path: Path | None, raw_record_ids: set[str]) -> set[str]:
    if not raw_record_ids:
        return set()
    scan = _parquet_scan(path)
    if scan is None:
        return set()
    schema_names = scan.collect_schema().names()
    if 'entity_key' not in schema_names or 'raw_record_id' not in schema_names:
        return set()
    scoped = (
        scan
        .select(['entity_key', 'raw_record_id'])
        .filter(pl.col('raw_record_id').cast(pl.String).is_in(sorted(raw_record_ids)))
        .collect()
    )
    return _string_values(scoped, 'entity_key')


def _relation_keys_for_raw_records(path: Path | None, raw_record_ids: set[str]) -> set[str]:
    if not raw_record_ids:
        return set()
    scan = _parquet_scan(path)
    if scan is None:
        return set()
    schema_names = scan.collect_schema().names()
    if 'relation_key' not in schema_names or 'raw_record_id' not in schema_names:
        return set()
    scoped = scan.filter(pl.col('raw_record_id').cast(pl.String).is_in(sorted(raw_record_ids))).collect()
    return _string_values(scoped, 'relation_key')


def _affected_key_frame(
    *,
    source: str,
    key_column: str,
    keys: set[str],
    reason: str,
) -> pl.DataFrame:
    if not keys:
        return _empty_affected_key_frame(key_column)
    return pl.DataFrame({
        'source': [source] * len(keys),
        key_column: sorted(keys),
        'change_type': ['affected'] * len(keys),
        'reason': [reason] * len(keys),
    })


def _write_gold_scope_artifacts(
    *,
    delta_dir: Path,
    raw_record_ids: set[str],
    occurrence_ids: set[str],
) -> None:
    pl.DataFrame({
        'raw_record_id': pl.Series(sorted(raw_record_ids), dtype=pl.String),
    }).write_parquet(delta_dir / 'affected_raw_record_ids.parquet')
    pl.DataFrame({
        'occurrence_id': pl.Series(sorted(occurrence_ids), dtype=pl.String),
    }).write_parquet(delta_dir / 'affected_occurrence_ids.parquet')


def _current_gold_key_frame(
    *,
    source: str,
    path: Path | None,
    key_column: str,
    reason: str,
) -> pl.DataFrame:
    scan = _parquet_scan(path)
    if scan is None:
        return _empty_affected_key_frame(key_column)
    if key_column not in scan.collect_schema().names():
        return _empty_affected_key_frame(key_column)
    keys = (
        scan
        .select(pl.col(key_column).cast(pl.String).alias(key_column))
        .drop_nulls()
        .unique()
        .collect()
        .get_column(key_column)
        .to_list()
    )
    return _affected_key_frame(
        source=source,
        key_column=key_column,
        keys={key for key in keys if key},
        reason=reason,
    )


def _empty_affected_partition_frame(partition_column: str) -> pl.DataFrame:
    return pl.DataFrame({
        'source': pl.Series([], dtype=pl.String),
        partition_column: pl.Series([], dtype=pl.Int64),
        'affected_key_count': pl.Series([], dtype=pl.Int64),
        'reason': pl.Series([], dtype=pl.String),
    })


def _affected_partition_frames(
    affected_keys: pl.DataFrame,
    *,
    key_column: str,
    bucket_column: str,
    part_column: str,
    add_partition_columns: Any,
) -> tuple[pl.DataFrame, pl.DataFrame]:
    if affected_keys.is_empty() or key_column not in affected_keys.columns:
        return (
            _empty_affected_partition_frame(bucket_column),
            _empty_affected_partition_frame(part_column),
        )

    partitioned = add_partition_columns(affected_keys)
    reason_expr = (
        pl.col('reason').cast(pl.String)
        if 'reason' in partitioned.columns else
        pl.lit('').alias('reason')
    )
    source_expr = (
        pl.col('source').cast(pl.String)
        if 'source' in partitioned.columns else
        pl.lit('').alias('source')
    )
    partitioned = (
        partitioned
        .select([
            source_expr.alias('source'),
            pl.col(key_column).cast(pl.String).alias(key_column),
            pl.col(bucket_column).cast(pl.Int64).alias(bucket_column),
            pl.col(part_column).cast(pl.Int64).alias(part_column),
            reason_expr.alias('reason'),
        ])
        .filter(
            pl.col(key_column).is_not_null()
            & (pl.col(key_column) != '')
            & pl.col(bucket_column).is_not_null()
            & pl.col(part_column).is_not_null()
        )
    )
    if partitioned.is_empty():
        return (
            _empty_affected_partition_frame(bucket_column),
            _empty_affected_partition_frame(part_column),
        )

    buckets = (
        partitioned
        .group_by(['source', bucket_column, 'reason'])
        .agg(pl.col(key_column).n_unique().cast(pl.Int64).alias('affected_key_count'))
        .sort(['source', bucket_column, 'reason'])
    )
    parts = (
        partitioned
        .group_by(['source', part_column, 'reason'])
        .agg(pl.col(key_column).n_unique().cast(pl.Int64).alias('affected_key_count'))
        .sort(['source', part_column, 'reason'])
    )
    return buckets, parts


def _write_affected_partition_artifacts(
    *,
    delta_dir: Path,
    affected_entities: pl.DataFrame,
    affected_relations: pl.DataFrame,
) -> dict[str, Any]:
    entity_buckets, entity_parts = _affected_partition_frames(
        affected_entities,
        key_column='entity_key',
        bucket_column='entity_bucket',
        part_column='entity_part',
        add_partition_columns=add_entity_partition_columns,
    )
    relation_buckets, relation_parts = _affected_partition_frames(
        affected_relations,
        key_column='relation_key',
        bucket_column='relation_bucket',
        part_column='relation_part',
        add_partition_columns=add_relation_partition_columns,
    )

    entity_buckets_path = delta_dir / 'affected_entity_buckets.parquet'
    entity_parts_path = delta_dir / 'affected_entity_parts.parquet'
    relation_buckets_path = delta_dir / 'affected_relation_buckets.parquet'
    relation_parts_path = delta_dir / 'affected_relation_parts.parquet'
    entity_buckets.write_parquet(entity_buckets_path)
    entity_parts.write_parquet(entity_parts_path)
    relation_buckets.write_parquet(relation_buckets_path)
    relation_parts.write_parquet(relation_parts_path)

    def unique_count(frame: pl.DataFrame, column: str) -> int:
        if column not in frame.columns:
            return 0
        return int(frame.get_column(column).drop_nulls().n_unique())

    return {
        'entity_key_algorithm': GOLD_ENTITY_KEY_ALGORITHM,
        'relation_key_algorithm': GOLD_RELATION_KEY_ALGORITHM,
        'bucket_algorithm': GOLD_BUCKET_ALGORITHM,
        'entity_bucket_count': ENTITY_BUCKET_COUNT,
        'entity_part_count': ENTITY_PART_COUNT,
        'relation_bucket_count': RELATION_BUCKET_COUNT,
        'relation_part_count': RELATION_PART_COUNT,
        'affected_entity_bucket_count': unique_count(entity_buckets, 'entity_bucket'),
        'affected_entity_part_count': unique_count(entity_parts, 'entity_part'),
        'affected_relation_bucket_count': unique_count(relation_buckets, 'relation_bucket'),
        'affected_relation_part_count': unique_count(relation_parts, 'relation_part'),
        'delta_artifacts': {
            'affected_entity_keys': 'affected_entity_keys.parquet',
            'affected_entity_buckets': entity_buckets_path.name,
            'affected_entity_parts': entity_parts_path.name,
            'affected_relation_keys': 'affected_relation_keys.parquet',
            'affected_relation_buckets': relation_buckets_path.name,
            'affected_relation_parts': relation_parts_path.name,
        },
    }


def _derive_gold_key_scope_from_silver_delta(
    *,
    source: str,
    silver_dir: Path,
    previous_dir: Path,
    staged_dir: Path,
    previous_output_ready: bool,
) -> tuple[set[str] | None, set[str] | None, pl.DataFrame, pl.DataFrame, dict[str, Any]]:
    raw_record_ids, occurrence_ids, silver_scope = _affected_silver_ids_from_delta(silver_dir=silver_dir)
    reason = 'silver_delta_target'
    scope_metadata = {
        **silver_scope,
        'raw_record_ids': sorted(raw_record_ids),
        'occurrence_ids': sorted(occurrence_ids),
        'raw_record_id_count': len(raw_record_ids),
        'occurrence_id_count': len(occurrence_ids),
        'strategy': 'full_gold_diff',
    }

    if not previous_output_ready:
        scope_metadata['fallback_reason'] = 'missing_or_invalid_previous_gold'
        return None, None, _empty_affected_key_frame('entity_key'), _empty_affected_key_frame('relation_key'), scope_metadata

    if silver_scope.get('delta_empty') is True:
        scope_metadata['strategy'] = 'empty_silver_delta'
        return set(), set(), _empty_affected_key_frame('entity_key'), _empty_affected_key_frame('relation_key'), scope_metadata

    if (
        not silver_scope.get('available')
        or silver_scope.get('unreadable_tables')
        or not raw_record_ids
    ):
        scope_metadata['fallback_reason'] = (
            silver_scope.get('reason')
            if not silver_scope.get('available')
            else 'unreadable_silver_delta_tables'
            if silver_scope.get('unreadable_tables')
            else 'silver_delta_without_raw_record_ids'
        )
        return None, None, _empty_affected_key_frame('entity_key'), _empty_affected_key_frame('relation_key'), scope_metadata

    previous_entity_path = _gold_table_path(previous_dir, 'entities', 'entity_evidence')
    current_entity_path = _gold_table_path(staged_dir, 'entities', 'entity_evidence')
    previous_relation_path = _gold_table_path(previous_dir, 'relations', 'entity_relation_evidence')
    current_relation_path = _gold_table_path(staged_dir, 'relations', 'entity_relation_evidence')

    entity_keys = set()
    entity_keys.update(_entity_keys_for_raw_records(previous_entity_path, raw_record_ids))
    entity_keys.update(_entity_keys_for_raw_records(current_entity_path, raw_record_ids))
    relation_keys = set()
    relation_keys.update(_relation_keys_for_raw_records(previous_relation_path, raw_record_ids))
    relation_keys.update(_relation_keys_for_raw_records(current_relation_path, raw_record_ids))

    scope_metadata['strategy'] = 'silver_delta_target'
    scope_metadata['affected_entity_count'] = len(entity_keys)
    scope_metadata['affected_relation_count'] = len(relation_keys)
    return (
        entity_keys,
        relation_keys,
        _affected_key_frame(
            source=source,
            key_column='entity_key',
            keys=entity_keys,
            reason=reason,
        ),
        _affected_key_frame(
            source=source,
            key_column='relation_key',
            keys=relation_keys,
            reason=reason,
        ),
        scope_metadata,
    )


def _write_gold_delta_artifacts(
    *,
    source: str,
    silver_dir: Path,
    previous_dir: Path,
    staged_dir: Path,
    output_dir: Path,
    previous_output_ready: bool,
) -> dict[str, Any]:
    build_id = _now_build_id()
    delta_dir = output_dir / GOLD_DELTA_DIR / build_id
    entities_delta_dir = delta_dir / 'entities'
    relations_delta_dir = delta_dir / 'relations'
    entities_delta_dir.mkdir(parents=True, exist_ok=True)
    relations_delta_dir.mkdir(parents=True, exist_ok=True)

    if not previous_output_ready:
        reason = 'source_first_build'
        affected_entities = _current_gold_key_frame(
            source=source,
            path=_gold_table_path(staged_dir, 'entities', 'entity'),
            key_column='entity_key',
            reason=reason,
        )
        affected_relations = _current_gold_key_frame(
            source=source,
            path=_gold_table_path(staged_dir, 'relations', 'entity_relation'),
            key_column='relation_key',
            reason=reason,
        )
        _write_gold_scope_artifacts(
            delta_dir=delta_dir,
            raw_record_ids=set(),
            occurrence_ids=set(),
        )
        affected_entities.write_parquet(delta_dir / 'affected_entity_keys.parquet')
        affected_relations.write_parquet(delta_dir / 'affected_relation_keys.parquet')
        partition_metadata = _write_affected_partition_artifacts(
            delta_dir=delta_dir,
            affected_entities=affected_entities,
            affected_relations=affected_relations,
        )

        manifest = {
            'layer': 'gold',
            'source': source,
            'build_id': build_id,
            'created_at': datetime.now(UTC).isoformat(),
            'reason': reason,
            'targeting': {
                'strategy': 'first_build_key_scope',
                'per_row_delta': False,
                'affected_key_scope_available': True,
            },
            'affected_entity_count': int(affected_entities['entity_key'].n_unique())
            if 'entity_key' in affected_entities.columns else 0,
            'affected_relation_count': int(affected_relations['relation_key'].n_unique())
            if 'relation_key' in affected_relations.columns else 0,
            **partition_metadata,
            'delta_counts': {
                'entity_delta.parquet': 0,
                'entity_relation_delta.parquet': 0,
                'entity_relation_evidence_delta.parquet': 0,
            },
        }
        _write_json_atomic(delta_dir / 'manifest.json', manifest)
        _write_json_atomic(output_dir / GOLD_DELTA_DIR / 'latest.json', {
            'source': source,
            'build_id': build_id,
            'path': str(delta_dir),
        })
        return {
            **manifest,
            'delta_dir': str(delta_dir),
        }

    reason = 'source_rebuild'
    (
        affected_entity_key_filter,
        affected_relation_key_filter,
        affected_entities,
        affected_relations,
        silver_scope,
    ) = _derive_gold_key_scope_from_silver_delta(
        source=source,
        silver_dir=silver_dir,
        previous_dir=previous_dir,
        staged_dir=staged_dir,
        previous_output_ready=previous_output_ready,
    )
    _write_gold_scope_artifacts(
        delta_dir=delta_dir,
        raw_record_ids=set(silver_scope.get('raw_record_ids', [])),
        occurrence_ids=set(silver_scope.get('occurrence_ids', [])),
    )

    affected_key_scope_available = (
        affected_entity_key_filter is not None
        and affected_relation_key_filter is not None
    )
    if not affected_key_scope_available:
        affected_entities = _empty_affected_key_frame('entity_key')
        affected_relations = _empty_affected_key_frame('relation_key')

    affected_entities.write_parquet(delta_dir / 'affected_entity_keys.parquet')
    affected_relations.write_parquet(delta_dir / 'affected_relation_keys.parquet')
    partition_metadata = _write_affected_partition_artifacts(
        delta_dir=delta_dir,
        affected_entities=affected_entities,
        affected_relations=affected_relations,
    )

    manifest = {
        'layer': 'gold',
        'source': source,
        'build_id': build_id,
        'created_at': datetime.now(UTC).isoformat(),
        'reason': reason,
        'targeting': {
            key: value
            for key, value in silver_scope.items()
            if key not in {'raw_record_ids', 'occurrence_ids', 'manifest'}
        },
        'affected_entity_count': int(affected_entities['entity_key'].n_unique())
        if 'entity_key' in affected_entities.columns else 0,
        'affected_relation_count': int(affected_relations['relation_key'].n_unique())
        if 'relation_key' in affected_relations.columns else 0,
        **partition_metadata,
        'delta_counts': {
            'entity_delta.parquet': 0,
            'entity_relation_delta.parquet': 0,
            'entity_relation_evidence_delta.parquet': 0,
        },
    }
    manifest['targeting']['affected_key_scope_available'] = affected_key_scope_available
    manifest['targeting']['per_row_delta'] = False
    _write_json_atomic(delta_dir / 'manifest.json', manifest)
    _write_json_atomic(output_dir / GOLD_DELTA_DIR / 'latest.json', {
        'source': source,
        'build_id': build_id,
        'path': str(delta_dir),
    })
    return {
        **manifest,
        'delta_dir': str(delta_dir),
    }


def gold_output_ready(output_dir: Path) -> bool:
    success_path = output_dir / GOLD_SUCCESS_FILE
    return (
        success_path.exists()
        and _gold_table_path(output_dir, 'entities', 'entity') is not None
        and _gold_table_path(output_dir, 'entities', 'entity_map') is not None
        and _parquet_has_columns(
            _gold_table_path(output_dir, 'entities', 'entity_evidence'),
            {
                'source',
                'entity_key',
                'canonical_identifier',
                'canonical_identifier_type',
                'raw_record_id',
                'occurrence_id',
                'fingerprint',
            },
        )
        and _parquet_has_columns(
            _gold_table_path(output_dir, 'relations', 'entity_relation_evidence'),
            {
                'relation_key',
                'subject_entity_key',
                'predicate',
                'object_entity_key',
                'relation_category',
                'source',
                'raw_record_id',
            },
        )
    )


def _parquet_has_columns(path: Path | None, columns: set[str]) -> bool:
    if path is None:
        return False
    try:
        schema = _scan_parquet_table(path).collect_schema()
    except (OSError, pl.exceptions.PolarsError):
        return False
    return columns.issubset(set(schema.names()))


def _filter_silver_for_raw_records(
    *,
    silver_dir: Path,
    output_dir: Path,
    raw_record_ids: set[str],
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    for name in SILVER_TABLE_SCHEMAS:
        path = silver_dir / name
        if not path.exists():
            continue
        frame = _read_parquet_table(path)
        if not raw_record_ids:
            filtered = frame.head(0)
        elif 'record_id' in frame.columns:
            filtered = frame.filter(pl.col('record_id').cast(pl.String).is_in(sorted(raw_record_ids)))
        elif '_raw_record_id' in frame.columns:
            filtered = frame.filter(pl.col('_raw_record_id').cast(pl.String).is_in(sorted(raw_record_ids)))
        else:
            filtered = frame.head(0)
        target = output_dir / name
        target.mkdir(parents=True, exist_ok=True)
        filtered.write_parquet(target / 'part=00000.parquet')


def _build_gold_source_incremental(
    *,
    source: str,
    silver_dir: Path,
    output_dir: Path,
    staged_dir: Path,
    mapping_dir: Path,
    raw_record_ids: set[str],
) -> tuple[dict[str, Any], dict[str, Any]]:
    changed_silver_dir = staged_dir / '_changed_silver'
    changed_entities_dir = staged_dir / '_changed_gold' / 'entities'
    changed_relations_dir = staged_dir / '_changed_gold' / 'relations'
    entities_dir = staged_dir / 'entities'
    relations_dir = staged_dir / 'relations'
    entities_dir.mkdir(parents=True, exist_ok=True)
    relations_dir.mkdir(parents=True, exist_ok=True)

    _filter_silver_for_raw_records(
        silver_dir=silver_dir,
        output_dir=changed_silver_dir,
        raw_record_ids=raw_record_ids,
    )
    entity_summary = build_entities(
        silver_dir=changed_silver_dir,
        mapping_dir=mapping_dir,
        output_dir=changed_entities_dir,
        source_name=source,
    )
    changed_entity_map_path = _require_parquet_table_path(
        _gold_subtable_path(changed_entities_dir, 'entity_map'),
        f'{changed_entities_dir}/entity_map',
    )
    relation_summary = build_relations(
        silver_dir=changed_silver_dir,
        entity_map_path=changed_entity_map_path,
        output_dir=changed_relations_dir,
        source_name=source,
    )

    previous_entity_evidence = _read_parquet_table(_require_parquet_table_path(
        _gold_table_path(output_dir, 'entities', 'entity_evidence'),
        f'{output_dir}/entities/entity_evidence',
    ))
    previous_relation_evidence = _read_parquet_table(_require_parquet_table_path(
        _gold_table_path(output_dir, 'relations', 'entity_relation_evidence'),
        f'{output_dir}/relations/entity_relation_evidence',
    ))
    changed_entity_evidence = _read_parquet_table(_require_parquet_table_path(
        _gold_subtable_path(changed_entities_dir, 'entity_evidence'),
        f'{changed_entities_dir}/entity_evidence',
    ))
    changed_relation_evidence = _read_parquet_table(_require_parquet_table_path(
        _gold_subtable_path(changed_relations_dir, 'entity_relation_evidence'),
        f'{changed_relations_dir}/entity_relation_evidence',
    ))
    previous_entities = _read_parquet_table(_require_parquet_table_path(
        _gold_table_path(output_dir, 'entities', 'entity'),
        f'{output_dir}/entities/entity',
    ))

    original_changed_entity_evidence = changed_entity_evidence
    previous_identity = (
        previous_entity_evidence
        .select([
            'fingerprint',
            pl.col('entity_key').alias('_previous_entity_key'),
            pl.col('canonical_identifier').alias('_previous_canonical_identifier'),
            pl.col('canonical_identifier_type').alias('_previous_canonical_identifier_type'),
        ])
        .unique()
    )
    changed_entity_evidence = (
        changed_entity_evidence
        .join(previous_identity, on='fingerprint', how='left')
        .with_columns([
            pl.coalesce(['_previous_entity_key', 'entity_key']).alias('entity_key'),
            pl.coalesce(['_previous_canonical_identifier', 'canonical_identifier']).alias('canonical_identifier'),
            pl.coalesce(['_previous_canonical_identifier_type', 'canonical_identifier_type']).alias('canonical_identifier_type'),
        ])
        .drop([
            '_previous_entity_key',
            '_previous_canonical_identifier',
            '_previous_canonical_identifier_type',
        ])
    )
    changed_entities = reduce_entities_from_evidence(
        changed_entity_evidence,
        entity_pk_map=previous_entities.select(['entity_key', 'entity_pk']),
    )
    changed_key_remap = (
        original_changed_entity_evidence
        .select([
            pl.col('entity_key').alias('_old_entity_key'),
            'fingerprint',
        ])
        .join(changed_entity_evidence.select(['fingerprint', 'entity_key']).unique(), on='fingerprint', how='inner')
        .select(['_old_entity_key', 'entity_key'])
        .unique()
    )
    if not changed_relation_evidence.is_empty() and not changed_key_remap.is_empty():
        subject_remap = changed_key_remap.rename({
            '_old_entity_key': 'subject_entity_key',
            'entity_key': '_new_subject_entity_key',
        })
        object_remap = changed_key_remap.rename({
            '_old_entity_key': 'object_entity_key',
            'entity_key': '_new_object_entity_key',
        })
        changed_relation_evidence = (
            changed_relation_evidence
            .join(subject_remap, on='subject_entity_key', how='left')
            .join(object_remap, on='object_entity_key', how='left')
            .with_columns([
                pl.coalesce(['_new_subject_entity_key', 'subject_entity_key']).alias('subject_entity_key'),
                pl.coalesce(['_new_object_entity_key', 'object_entity_key']).alias('object_entity_key'),
            ])
            .drop(['_new_subject_entity_key', '_new_object_entity_key'])
            .with_columns(
                pl.struct([
                    'subject_entity_key',
                    'predicate',
                    'object_entity_key',
                    'relation_category',
                ]).map_elements(
                    lambda row: compute_relation_key(
                        row['subject_entity_key'],
                        row['predicate'],
                        row['object_entity_key'],
                        row['relation_category'],
                    ),
                    return_dtype=pl.String,
                ).alias('relation_key')
            )
        )

    kept_entity_evidence = previous_entity_evidence.filter(
        ~pl.col('raw_record_id').cast(pl.String).is_in(sorted(raw_record_ids))
    )
    merged_entity_evidence = pl.concat(
        [kept_entity_evidence, changed_entity_evidence],
        how='diagonal_relaxed',
    )
    merged_entities = reduce_entities_from_evidence(
        merged_entity_evidence,
        entity_pk_map=previous_entities.select(['entity_key', 'entity_pk']),
    )
    merged_entity_evidence.write_parquet(entities_dir / 'entity_evidence.parquet')
    merged_entities.write_parquet(entities_dir / 'entity.parquet')

    changed_entity_key_map = _gold_subtable_path(changed_entities_dir, 'entity_map')
    if changed_entity_key_map is not None:
        changed_map = (
            _read_parquet_table(changed_entity_key_map)
            .join(
                changed_entities.select(['entity_pk', 'entity_key']),
                on='entity_pk',
                how='inner',
            )
            .join(merged_entities.select(['entity_key', 'entity_pk']), on='entity_key', how='inner')
            .select(['_fingerprint', 'entity_pk'])
        )
        previous_map = _read_parquet_table(_require_parquet_table_path(
            _gold_table_path(output_dir, 'entities', 'entity_map'),
            f'{output_dir}/entities/entity_map',
        ))
        merged_map = (
            previous_map
            .join(changed_map.select('_fingerprint'), on='_fingerprint', how='anti')
            .vstack(changed_map)
            .unique()
        )
        merged_map.write_parquet(entities_dir / 'entity_map.parquet')
    else:
        _copy_parquet_table(_require_parquet_table_path(
            _gold_table_path(output_dir, 'entities', 'entity_map'),
            f'{output_dir}/entities/entity_map',
        ), entities_dir, 'entity_map')

    changed_occurrence_map = _gold_subtable_path(changed_entities_dir, 'entity_occurrence_map')
    if changed_occurrence_map is not None:
        changed_occ = (
            _read_parquet_table(changed_occurrence_map)
            .join(
                changed_entities.select(['entity_pk', 'entity_key']),
                on='entity_pk',
                how='inner',
            )
            .join(merged_entities.select(['entity_key', 'entity_pk']), on='entity_key', how='inner')
            .select(['occurrence_id', '_fingerprint', 'entity_pk'])
        )
        previous_occ = _read_parquet_table(_require_parquet_table_path(
            _gold_table_path(output_dir, 'entities', 'entity_occurrence_map'),
            f'{output_dir}/entities/entity_occurrence_map',
        ))
        merged_occ = (
            previous_occ
            .join(changed_occ.select('occurrence_id'), on='occurrence_id', how='anti')
            .vstack(changed_occ)
            .unique()
        )
        merged_occ.write_parquet(entities_dir / 'entity_occurrence_map.parquet')
    else:
        _copy_parquet_table(_require_parquet_table_path(
            _gold_table_path(output_dir, 'entities', 'entity_occurrence_map'),
            f'{output_dir}/entities/entity_occurrence_map',
        ), entities_dir, 'entity_occurrence_map')

    for extra in ('canonicalization_report.md', 'canonicalization_summary.json'):
        source_extra = output_dir / 'entities' / extra
        if source_extra.exists():
            shutil.copy2(source_extra, entities_dir / extra)

    kept_relation_evidence = previous_relation_evidence.filter(
        ~pl.col('raw_record_id').cast(pl.String).is_in(sorted(raw_record_ids))
    )
    merged_relation_evidence = pl.concat(
        [kept_relation_evidence, changed_relation_evidence],
        how='diagonal_relaxed',
    )
    previous_relations = _read_parquet_table(_require_parquet_table_path(
        _gold_table_path(output_dir, 'relations', 'entity_relation'),
        f'{output_dir}/relations/entity_relation',
    ))
    merged_relations = reduce_relations_from_evidence(
        merged_relation_evidence,
        entity_pk_map=merged_entities.select(['entity_key', 'entity_pk']),
        relation_pk_map=previous_relations.select(['relation_key', 'relation_pk']),
    )
    relation_pk_map = merged_relations.select(['relation_key', 'relation_pk'])
    merged_relation_evidence = (
        merged_relation_evidence
        .drop(['relation_evidence_pk', 'relation_pk'])
        .join(relation_pk_map, on='relation_key', how='left')
        .with_row_index('relation_evidence_pk', offset=1)
        .select(list(changed_relation_evidence.columns))
    )
    merged_relation_evidence.write_parquet(relations_dir / 'entity_relation_evidence.parquet')
    merged_relations.write_parquet(relations_dir / 'entity_relation.parquet')

    entity_summary = {
        **entity_summary,
        'incremental': True,
        'changed_raw_record_count': len(raw_record_ids),
        'entity_count': int(merged_entities.height),
    }
    relation_summary = {
        **relation_summary,
        'incremental': True,
        'changed_raw_record_count': len(raw_record_ids),
        'relation_count': int(merged_relations.height),
        'relation_evidence_count': int(merged_relation_evidence.height),
    }
    return entity_summary, relation_summary


def build_gold_source(
    *,
    source: str,
    silver_dir: Path,
    output_dir: Path,
    mapping_dir: Path,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    success_path = output_dir / GOLD_SUCCESS_FILE
    previous_output_ready = gold_output_ready(output_dir)

    raw_record_ids, _, silver_scope = _affected_silver_ids_from_delta(silver_dir=silver_dir)
    can_incremental = (
        previous_output_ready
        and silver_scope.get('available') is True
        and not silver_scope.get('unreadable_tables')
    )
    if can_incremental and silver_scope.get('delta_empty') is True:
        return {
            'output_dir': str(output_dir),
            'entities_dir': str(output_dir / 'entities'),
            'relations_dir': str(output_dir / 'relations'),
            'skipped': 'empty_silver_delta',
            'entity_summary': {
                'incremental': True,
                'skipped': 'empty_silver_delta',
                'changed_raw_record_count': 0,
            },
            'relation_summary': {
                'incremental': True,
                'skipped': 'empty_silver_delta',
                'changed_raw_record_count': 0,
            },
            'silver_scope': silver_scope,
        }

    if success_path.exists():
        success_path.unlink()

    if not silver_has_data(silver_dir):
        return {
            'output_dir': str(output_dir),
            'entities_dir': str(output_dir / 'entities'),
            'relations_dir': str(output_dir / 'relations'),
            'skipped': 'no_data',
            'entity_summary': None,
            'relation_summary': None,
        }

    with tempfile.TemporaryDirectory(prefix='op-pipeline-gold-') as tmp:
        staged_dir = Path(tmp) / source.replace('.', '/')
        entities_dir = staged_dir / 'entities'
        relations_dir = staged_dir / 'relations'
        entities_dir.mkdir(parents=True, exist_ok=True)
        relations_dir.mkdir(parents=True, exist_ok=True)

        if can_incremental and raw_record_ids:
            entity_summary, relation_summary = _build_gold_source_incremental(
                source=source,
                silver_dir=silver_dir,
                output_dir=output_dir,
                staged_dir=staged_dir,
                mapping_dir=mapping_dir,
                raw_record_ids=raw_record_ids,
            )
        else:
            entity_summary = build_entities(
                silver_dir=silver_dir,
                mapping_dir=mapping_dir,
                output_dir=entities_dir,
                source_name=source,
            )

            entity_map_path = _gold_subtable_path(entities_dir, 'entity_map')
            if entity_map_path is None:
                return {
                    'output_dir': str(output_dir),
                    'entities_dir': str(output_dir / 'entities'),
                    'relations_dir': str(output_dir / 'relations'),
                    'skipped': 'missing_entity_map',
                    'entity_summary': entity_summary,
                    'relation_summary': None,
                }

            relation_summary = build_relations(
                silver_dir=silver_dir,
                entity_map_path=entity_map_path,
                output_dir=relations_dir,
                source_name=source,
            )

        delta_summary = _write_gold_delta_artifacts(
            source=source,
            silver_dir=silver_dir,
            previous_dir=output_dir,
            staged_dir=staged_dir,
            output_dir=output_dir,
            previous_output_ready=previous_output_ready,
        )

        for name in ('entities', 'relations'):
            target = output_dir / name
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(staged_dir / name, target)

    archive_path = build_resource_archive(output_dir, source)

    metadata = {
        'output_dir': str(output_dir),
        'entities_dir': str(output_dir / 'entities'),
        'relations_dir': str(output_dir / 'relations'),
        'download_archive_path': str(archive_path),
        'entity_summary': entity_summary,
        'relation_summary': relation_summary,
        'delta_summary': delta_summary,
    }
    _write_json_atomic(success_path, metadata)
    return metadata
