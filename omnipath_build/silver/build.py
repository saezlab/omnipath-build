#!/usr/bin/env python3
"""Silver loader that discovers resource generators dynamically."""

from __future__ import annotations

import os
from enum import Enum
import json
import time
from typing import Dict, List, Callable, Iterable, Iterator, Optional
import inspect
from pathlib import Path
import pkgutil
import importlib
import traceback
from dataclasses import dataclass

import pyarrow as pa
import pyarrow.parquet as pq

from omnipath_build.silver.paths import PathManager
from omnipath_build.silver.tables import (
    SilverTableWriter,
    silver_table_dir,
    has_silver_tables,
    has_raw_keyed_silver_tables,
)
from pypath.inputs_v2.raw_records import (
    ProvenancedRecord,
    RawRecordProvenance,
    changed_keys,
)
from omnipath_build.silver.validate import validate_entity_identifier_shapes
from pypath.internals.silver_schema import (
    ENTITY_SCHEMA,
    Entity as SilverEntity,
)
from omnipath_build.pipeline.progress import set_phase, update_phase
from pypath.internals.ontology_schema import OntologyTerm

__all__ = [
    'DiscoveryError',
    'ResourceFunction',
    'TEST_MODE_RECORD_LIMITS_BY_SOURCE',
    'discover_resources',
    'process_resource_function',
    'run_silver_loader',
]


# In test mode, only these high-volume sources are capped.
# All other sources run uncapped.
TEST_MODE_RECORD_LIMITS_BY_SOURCE: dict[str, int] = {
    'foodb': 100,
    'ptfi': 100,
    'guidetopharma': 100,
    'bindingdb': 100,
    'swisslipids': 100,
    'lipidmaps': 100,
    'hmdb': 100,
    'intact': 1000,
    'stitch': 1000,
    'chembl': 1000,
    'rampdb': 1000,
}

_PROGRESS_PREFIX = '__OMNIPATH_PROGRESS__'

def _emit_progress(
    *,
    source: str,
    function: str,
    event: str,
    records: int,
    output: str | None = None,
) -> None:
    """Emit machine-readable progress events to stdout when enabled."""
    if os.environ.get('OMNIPATH_PROGRESS_STDOUT') != '1':
        return

    payload: dict[str, object] = {
        'source': source,
        'function': function,
        'event': event,
        'records': int(records),
    }
    if output is not None:
        payload['output'] = output

    print(f"{_PROGRESS_PREFIX}{json.dumps(payload, separators=(',', ':'))}", flush=True)


def _test_mode_record_limit(source: str, test_mode: bool) -> int | None:
    """Resolve per-source record limit for test mode."""
    if not test_mode:
        return None
    return TEST_MODE_RECORD_LIMITS_BY_SOURCE.get(source)


@dataclass(slots=True)
class ResourceFunction:
    """Container describing a discovered resource transformation function."""

    source: str
    function_name: str
    qualified_module: str
    call: Callable[[], Iterable] | Callable[[], object]
    resource_id: str
    output_kind: str = 'entity'
    file_extension: str | None = None
    file_stem: str | None = None
    document: object | None = None
    ontology_id: str | None = None


class DiscoveryError(RuntimeError):
    """Raised when resource discovery fails."""


def _configure_pypath_download_dir() -> Path:
    """Configure pypath download directory for this project runtime."""

    configured = os.environ.get('PYPATH_DOWNLOAD_DATADIR')
    if configured:
        data_dir = Path(configured)
    else:
        project_root = Path(__file__).resolve().parents[2]
        data_dir = project_root / 'pypath-data'
        os.environ['PYPATH_DOWNLOAD_DATADIR'] = str(data_dir)

    data_dir.mkdir(parents=True, exist_ok=True)

    return data_dir


def discover_resources(
    database_name: str,
    base_path: Optional[Path] = None,
    inputs_package: str = 'pypath.inputs_v2',
) -> tuple[Dict[str, List[ResourceFunction]], PathManager]:
    """Discover generator functions from the inputs_v2 package.

    For each module, discovers:
    1. Resource objects - emits metadata() to resource.parquet (processed first)
    2. Dataset objects - emits data entities to <dataset_name>.parquet
    """
    path_manager = PathManager(database_name, base_path)
    _configure_pypath_download_dir()
    from pypath.inputs_v2.base import (
        Dataset,
        Resource,
        ArtifactDataset,
        OntologyDataset,
    )  # Local import to avoid import-time side effects.

    try:
        root_module = importlib.import_module(inputs_package)
    except ImportError as exc:  # noqa: BLE001
        raise DiscoveryError(
            f'Unable to import inputs package "{inputs_package}": {exc}'
        ) from exc

    package_paths = getattr(root_module, '__path__', None)
    if package_paths is None:
        raise DiscoveryError(
            f'Inputs package "{inputs_package}" is not a namespace package'
        )

    prefix = f'{inputs_package}.'
    discovered: Dict[str, List[ResourceFunction]] = {}

    for module_info in pkgutil.walk_packages(package_paths, prefix):
        module_name = module_info.name
        relative_name = module_name[len(prefix) :]
        if not relative_name:
            continue

        leaf = relative_name.split('.')[-1]
        if leaf.startswith('_'):
            continue

        try:
            module = importlib.import_module(module_name)
        except Exception as exc:  # noqa: BLE001
            print(
                f'[discover_resources] skipping {module_name}: '
                f'{exc.__class__.__name__}: {exc}'
            )
            if os.environ.get('OMNIPATH_DISCOVERY_TRACEBACK') == '1':
                traceback.print_exc()
            continue

        # First, discover Resource objects to emit their metadata
        resource_members = [
            (name, obj)
            for name, obj in inspect.getmembers(module)
            if isinstance(obj, Resource)
        ]

        dataset_types = (Dataset, OntologyDataset, ArtifactDataset)

        # Discover datasets for entities and ontology/artifact outputs.
        # Resolver-specific translation datasets are declared on the dataset
        # itself via kind='id_translation' and excluded from the regular silver
        # parquet build.
        dataset_members = [
            (name, obj)
            for name, obj in inspect.getmembers(module)
            if isinstance(obj, dataset_types) and getattr(obj, 'kind', None) != 'id_translation'
        ]

        # Also discover datasets nested inside Resource objects
        datasets_from_resources: List[tuple[str, object]] = []
        try:
            for _, resource_obj in resource_members:
                for ds_name, ds_obj in resource_obj.datasets().items():
                    if getattr(ds_obj, 'kind', None) == 'id_translation':
                        continue
                    # Only add if not already in dataset_members (avoid duplicates)
                    if ds_name not in [n for n, _ in dataset_members]:
                        datasets_from_resources.append((ds_name, ds_obj))
        except Exception as exc:  # noqa: BLE001
            print(
                f'[discover_resources] skipping {module_name}: '
                f'{exc.__class__.__name__}: {exc}'
            )
            if os.environ.get('OMNIPATH_DISCOVERY_TRACEBACK') == '1':
                traceback.print_exc()
            continue
        dataset_members.extend(datasets_from_resources)

        if not dataset_members and not resource_members:
            continue

        resource_id = relative_name

        module_functions: List[ResourceFunction] = []

        # Add Resource metadata as first function (writes to resource.parquet)
        # This ensures the source entity gets local_entity_id = 1
        for resource_name, resource_obj in resource_members:
            # Use the Resource's __call__ method which yields metadata entities
            module_functions.append(
                ResourceFunction(
                    source=relative_name,
                    function_name='resource',  # Always 'resource' so it sorts first
                    qualified_module=module_name,
                    call=resource_obj,  # Resource.__call__() yields metadata()
                    resource_id=resource_id,
                ),
            )
            # Only use the first Resource found per module
            break

        # Add Dataset functions for data entities
        for dataset_name, dataset_obj in dataset_members:
            output_kind = 'entity'
            file_extension = None
            file_stem = None
            document = None
            ontology_id = None
            if isinstance(dataset_obj, OntologyDataset):
                output_kind = 'ontology'
                file_extension = dataset_obj.extension
                file_stem = dataset_obj.file_stem
                document = dataset_obj.document
                ontology_id = dataset_obj.ontology_id
            elif isinstance(dataset_obj, ArtifactDataset):
                output_kind = 'artifact'
                file_extension = dataset_obj.extension
                file_stem = dataset_obj.file_stem

            if output_kind in {'entity', 'ontology'}:
                def dataset_call(
                    dataset_obj=dataset_obj,
                    source_name=relative_name,
                    dataset_name=dataset_name,
                ):
                    raw_snapshot = getattr(dataset_obj, '_raw_snapshot_override', None)
                    changed_only = bool(getattr(dataset_obj, '_changed_only_override', False))
                    kwargs = {
                        'source': source_name,
                        'dataset': dataset_name,
                        'use_preparse': True,
                        'changed_only': changed_only,
                    }
                    if raw_snapshot is not None:
                        kwargs['raw_snapshot'] = raw_snapshot
                    return dataset_obj(**kwargs)
                dataset_call._raw_dataset = dataset_obj
            else:
                dataset_call = dataset_obj

            module_functions.append(
                ResourceFunction(
                    source=relative_name,
                    function_name=dataset_name,
                    qualified_module=module_name,
                    call=dataset_call,
                    resource_id=resource_id,
                    output_kind=output_kind,
                    file_extension=file_extension,
                    file_stem=file_stem,
                    document=document,
                    ontology_id=ontology_id,
                ),
            )

        if module_functions:
            discovered[relative_name] = module_functions

    if not discovered:
        raise DiscoveryError(
            f'No resource functions found under package "{inputs_package}"'
        )

    return discovered, path_manager


def _unwrap_record(record: object) -> object:
    if isinstance(record, ProvenancedRecord):
        return record.record
    return record


def _record_provenance(record: object) -> RawRecordProvenance | None:
    if isinstance(record, ProvenancedRecord):
        return record.provenance
    return None


def _changed_keys_by_type(delta_path: Path) -> tuple[set[str], set[str]]:
    if not delta_path.exists():
        return set(), set()
    table = pq.read_table(delta_path, columns=['_raw_record_key', '_change_type'])
    added: set[str] = set()
    removed: set[str] = set()
    for raw_record_key, change_type in zip(
        table.column('_raw_record_key').to_pylist(),
        table.column('_change_type').to_pylist(),
        strict=False,
    ):
        if raw_record_key is None:
            continue
        if change_type == 'added':
            added.add(str(raw_record_key))
        elif change_type == 'removed':
            removed.add(str(raw_record_key))
    return added, removed


def _ensure_entity_record(record: object) -> None:
    """Validate that a record is a SilverEntity instance."""
    unwrapped = _unwrap_record(record)
    if not isinstance(unwrapped, SilverEntity):
        raise ValueError(
            f'Unsupported record type {type(unwrapped)!r}; expected pypath.internals.silver_schema.Entity',
        )


def _normalize_record(record: object) -> dict:
    """Convert a namedtuple-like record into a plain dictionary."""
    if hasattr(record, '_asdict'):
        record_dict = record._asdict()
        # Recursively normalize nested structures (e.g., membership entities)
        for key, value in list(record_dict.items()):
            if hasattr(value, '_asdict'):
                record_dict[key] = _normalize_record(value)
            elif isinstance(value, list):
                record_dict[key] = [
                    _normalize_record(item) if hasattr(item, '_asdict') else item
                    for item in value
                ]
        return record_dict
    if isinstance(record, dict):
        record_dict = {}
        for key, value in record.items():
            if hasattr(value, '_asdict'):
                record_dict[key] = _normalize_record(value)
            elif isinstance(value, list):
                record_dict[key] = [
                    _normalize_record(item) if hasattr(item, '_asdict') else item
                    for item in value
                ]
            else:
                record_dict[key] = value
        return record_dict
    raise TypeError(f'Cannot normalize record of type {type(record)!r}')


def _coerce_list_fields(record: dict, schema: pa.Schema) -> None:
    """Ensure list-typed schema fields receive list values and proper types."""
    for field in schema:
        if not pa.types.is_list(field.type):
            continue
        value = record.get(field.name)
        if value is None:
            continue

        # Convert tuples to lists
        if isinstance(value, tuple):
            value = list(value)
        elif not isinstance(value, list):
            value = [value]

        # For list of structs, ensure nested field types match schema
        if pa.types.is_struct(field.type.value_type):
            struct_type = field.type.value_type
            coerced_list = []
            for item in value:
                if item is None:
                    continue
                if not isinstance(item, dict):
                    coerced_list.append(item)
                    continue

                coerced_item = {}
                for struct_field in struct_type:
                    field_name = struct_field.name
                    field_value = item.get(field_name)

                    if field_value is None:
                        coerced_item[field_name] = None
                    elif pa.types.is_string(struct_field.type):
                        # Normalize Enum values before string conversion
                        if isinstance(field_value, Enum):
                            field_value = field_value.value
                        coerced_item[field_name] = str(field_value)
                    else:
                        coerced_item[field_name] = field_value

                coerced_list.append(coerced_item)
            value = coerced_list

        record[field.name] = value


def _is_multi_output_record(record: object) -> bool:
    """Check if record is a multi-output dict (not a namedtuple with _asdict)."""
    record = _unwrap_record(record)
    return isinstance(record, dict) and not hasattr(record, '_asdict')


def _normalize_source_filter(source: str, inputs_package: str) -> str:
    """Normalize CLI-provided source names to discovered keys."""
    cleaned = source.strip()
    if cleaned.startswith(f'{inputs_package}.'):
        return cleaned[len(inputs_package) + 1 :]
    return cleaned


def _ensure_writer(
    resource_fn: ResourceFunction,
    path_manager: PathManager,
    output_file: Optional[Path],
    writer: Optional[pq.ParquetWriter],
    schema: pa.Schema,
) -> tuple[Path, pq.ParquetWriter]:
    """Ensure we have an initialized Parquet writer for a resource."""
    if output_file is None:
        output_file = path_manager.silver_file(
            resource_fn.source,
            resource_fn.function_name,
            resource_fn.function_name,
        )
        output_file.parent.mkdir(parents=True, exist_ok=True)

    if writer is None:
        writer = pq.ParquetWriter(output_file, schema)

    return output_file, writer


def process_resource_function(
    resource_fn: ResourceFunction,
    path_manager: PathManager,
    batch_size: int = 10_000,
    dry_run: bool = False,
    override: bool = False,
    test_mode: bool = False,
    silver_writer: SilverTableWriter | None = None,
) -> Optional[Path] | Dict[str, Path]:
    """Stream records from a resource function into parquet file(s).

    Args:
        resource_fn: The resource function to process
        path_manager: Path manager for output files
        batch_size: Number of records per batch
        dry_run: If True, don't write to disk
        override: If True, overwrite existing files
        test_mode: If True, apply selective per-source limits (see TEST_MODE_RECORD_LIMITS_BY_SOURCE)
        silver_writer: Source-level silver tables writer for entity datasets.

    Returns:
        - Optional[Path] for single-output functions
        - Dict[str, Path] for multi-output functions (yields dicts with named outputs)
    """
    # Check if output file already exists and skip if not overriding
    if not override:
        if resource_fn.output_kind in {'ontology', 'artifact'}:
            potential_output = path_manager.artifact_file(
                resource_fn.source,
                resource_fn.function_name,
                resource_fn.file_extension or ('obo' if resource_fn.output_kind == 'ontology' else 'txt'),
                file_stem=resource_fn.file_stem,
            )
        else:
            potential_output = path_manager.silver_file(
                resource_fn.source,
                resource_fn.function_name,
                resource_fn.function_name,
            )
        if potential_output.exists() and (
            resource_fn.function_name == 'resource'
            or resource_fn.output_kind == 'artifact'
        ):
            print(
                f'[{resource_fn.source}.{resource_fn.function_name}] skipping (file exists: {potential_output})'
            )
            return potential_output

    try:
        if resource_fn.output_kind == 'artifact':
            return _process_artifact_output(resource_fn, path_manager, dry_run=dry_run)
        records = resource_fn.call()
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            f'Failed to execute {resource_fn.source}.{resource_fn.function_name}: {exc}'
        ) from exc

    if resource_fn.output_kind == 'ontology':
        return _process_ontology_output(
            resource_fn,
            path_manager,
            records,
            dry_run=dry_run,
            silver_writer=silver_writer,
        )

    # Peek at first record to detect multi-output
    first_record = None
    records_iter = iter(records)
    for rec in records_iter:
        if rec is not None:
            first_record = rec
            break

    if first_record is None:
        # No records at all
        print(
            f'[{resource_fn.source}.{resource_fn.function_name}] no records generated'
        )
        return None

    is_multi_output = _is_multi_output_record(first_record)

    # For multi-output functions without override, check if all output files exist
    if is_multi_output and not override:
        output_names = first_record.keys()
        existing_files = {}
        all_exist = True

        for output_name in output_names:
            output_record = first_record[output_name]
            if output_record is None:
                continue

            potential_file = path_manager.silver_file(
                resource_fn.source,
                output_name,
                output_name,
            )
            if potential_file.exists():
                existing_files[output_name] = potential_file
            else:
                all_exist = False
                break

        if all_exist and existing_files and resource_fn.function_name == 'resource':
            output_list = ', '.join(existing_files.keys())
            print(
                f'[{resource_fn.source}.{resource_fn.function_name}] skipping (multi-output files exist: {output_list})'
            )
            return existing_files

    max_records = _test_mode_record_limit(resource_fn.source, test_mode)

    if is_multi_output:
        # Process as multi-output function
        return _process_multi_output(
            resource_fn,
            path_manager,
            first_record,
            records_iter,
            batch_size,
            dry_run,
            max_records,
            silver_writer,
        )
    else:
        # Process as single-output function (existing logic)
        return _process_single_output(
            resource_fn,
            path_manager,
            first_record,
            records_iter,
            batch_size,
            dry_run,
            max_records,
            silver_writer,
        )


def _process_single_output(
    resource_fn: ResourceFunction,
    path_manager: PathManager,
    first_record: object,
    records_iter: Iterator,
    batch_size: int,
    dry_run: bool,
    max_records: int | None = None,
    silver_writer: SilverTableWriter | None = None,
) -> Optional[Path]:
    """Process single-output function producing Entity records."""
    schema = ENTITY_SCHEMA
    output_file: Optional[Path] = None
    writer: Optional[pq.ParquetWriter] = None
    total_records = 0
    batch: List[dict] = []
    write_nested = resource_fn.function_name == 'resource'
    if dry_run:
        silver_writer = None

    _emit_progress(
        source=resource_fn.source,
        function=resource_fn.function_name,
        event='start',
        records=0,
    )

    # Process first record
    _ensure_entity_record(first_record)
    first_provenance = _record_provenance(first_record)
    first_entity = _unwrap_record(first_record)
    validate_entity_identifier_shapes(
        first_entity,
        context=f'{resource_fn.source}.{resource_fn.function_name}[0]',
    )
    if silver_writer is not None:
        silver_writer.write_entity(
            first_entity,
            dataset=resource_fn.function_name,
            raw_record_id=first_provenance.raw_record_id if first_provenance else None,
            raw_record_key=first_provenance.raw_record_key if first_provenance else None,
            snapshot_id=first_provenance.snapshot_id if first_provenance else None,
        )
    if write_nested:
        record_dict = _normalize_record(first_entity)
        _coerce_list_fields(record_dict, schema)
        batch.append(record_dict)
    else:
        total_records += 1

    # Process remaining records
    for record in records_iter:
        # Check test mode limit
        if max_records and (total_records + len(batch)) >= max_records:
            print(
                f'[{resource_fn.source}.{resource_fn.function_name}] test mode: stopping at {max_records:,} records'
            )
            break
        if record is None:
            continue

        _ensure_entity_record(record)
        provenance = _record_provenance(record)
        entity = _unwrap_record(record)
        validate_entity_identifier_shapes(
            entity,
            context=f'{resource_fn.source}.{resource_fn.function_name}[{total_records + len(batch)}]',
        )
        if silver_writer is not None:
            silver_writer.write_entity(
                entity,
                dataset=resource_fn.function_name,
                raw_record_id=provenance.raw_record_id if provenance else None,
                raw_record_key=provenance.raw_record_key if provenance else None,
                snapshot_id=provenance.snapshot_id if provenance else None,
            )
        if write_nested:
            record_dict = _normalize_record(entity)
            _coerce_list_fields(record_dict, schema)
            batch.append(record_dict)
        else:
            total_records += 1

        total_pending = total_records + len(batch)
        if total_pending and total_pending % 10000 == 0:
            print(
                f'[{resource_fn.source}.{resource_fn.function_name}] collected {total_pending:,} records...'
            )

        if len(batch) >= batch_size:
            if dry_run:
                total_records += len(batch)
                batch.clear()
                continue

            output_file, writer = _ensure_writer(
                resource_fn,
                path_manager,
                output_file,
                writer,
                schema,
            )
            table = pa.Table.from_pylist(batch, schema=schema)
            writer.write_table(table)
            total_records += len(batch)
            _emit_progress(
                source=resource_fn.source,
                function=resource_fn.function_name,
                event='update',
                records=total_records,
            )
            print(
                f'[{resource_fn.source}.{resource_fn.function_name}] processed {total_records:,} records...'
            )
            batch.clear()

    if not batch:
        if total_records == 0 and dry_run:
            print(
                f'[{resource_fn.source}.{resource_fn.function_name}] dry-run complete (no write)'
            )
            _emit_progress(
                source=resource_fn.source,
                function=resource_fn.function_name,
                event='done',
                records=0,
            )
            return None
        if total_records == 0 and not dry_run:
            output_file = path_manager.silver_file(
                resource_fn.source,
                resource_fn.function_name,
                resource_fn.function_name,
            )
            output_file.parent.mkdir(parents=True, exist_ok=True)
            pq.write_table(pa.Table.from_pylist([], schema=schema), output_file)
            print(
                f'[{resource_fn.source}.{resource_fn.function_name}] wrote empty table to {output_file}'
            )
            _emit_progress(
                source=resource_fn.source,
                function=resource_fn.function_name,
                event='done',
                records=0,
            )
            return output_file
        if writer:
            writer.close()
            print(
                f'[{resource_fn.source}.{resource_fn.function_name}] wrote {total_records:,} records to {output_file}'
            )
            _emit_progress(
                source=resource_fn.source,
                function=resource_fn.function_name,
                event='done',
                records=total_records,
            )
            return output_file
        _emit_progress(
            source=resource_fn.source,
            function=resource_fn.function_name,
            event='done',
            records=total_records,
        )
        if silver_writer is not None:
            print(
                f'[{resource_fn.source}.{resource_fn.function_name}] wrote {total_records:,} records to silver tables'
            )
            return silver_writer.output_dir
        return output_file

    if dry_run:
        total_records += len(batch)
        print(
            f'[{resource_fn.source}.{resource_fn.function_name}] dry-run result: {total_records:,} records pending write'
        )
        _emit_progress(
            source=resource_fn.source,
            function=resource_fn.function_name,
            event='done',
            records=total_records,
        )
        return None

    output_file, writer = _ensure_writer(
        resource_fn,
        path_manager,
        output_file,
        writer,
        schema,
    )

    table = pa.Table.from_pylist(batch, schema=schema)
    writer.write_table(table)
    total_records += len(batch)
    writer.close()

    _emit_progress(
        source=resource_fn.source,
        function=resource_fn.function_name,
        event='update',
        records=total_records,
    )
    print(
        f'[{resource_fn.source}.{resource_fn.function_name}] wrote {total_records:,} records to {output_file}'
    )
    _emit_progress(
        source=resource_fn.source,
        function=resource_fn.function_name,
        event='done',
        records=total_records,
    )
    return output_file


def _process_multi_output(
    resource_fn: ResourceFunction,
    path_manager: PathManager,
    first_record: object,
    records_iter: Iterator,
    batch_size: int,
    dry_run: bool,
    max_records: int | None = None,
    silver_writer: SilverTableWriter | None = None,
) -> Dict[str, Path]:
    """Process multi-output function that yields dicts with named outputs."""
    batches: Dict[str, List[dict]] = {}
    writers: Dict[str, pq.ParquetWriter] = {}
    output_files: Dict[str, Path] = {}
    record_counts: Dict[str, int] = {}
    if dry_run:
        silver_writer = None

    def ensure_output_paths(output_name: str) -> None:
        if output_name not in output_files:
            output_files[output_name] = path_manager.silver_file(
                resource_fn.source,
                output_name,
                output_name,
            )
            output_files[output_name].parent.mkdir(parents=True, exist_ok=True)

        if output_name not in writers:
            writers[output_name] = pq.ParquetWriter(
                output_files[output_name],
                ENTITY_SCHEMA,
            )

    def process_output_record(
        output_name: str,
        output_record: object,
        provenance: RawRecordProvenance | None = None,
    ) -> None:
        """Process a single output record."""
        if output_record is None:
            return

        _ensure_entity_record(output_record)
        output_entity = _unwrap_record(output_record)
        output_provenance = _record_provenance(output_record) or provenance
        validate_entity_identifier_shapes(
            output_entity,
            context=(
                f'{resource_fn.source}.{resource_fn.function_name}:{output_name}'
                f'[{record_counts.get(output_name, 0) + len(batches.get(output_name, []))}]'
            ),
        )

        if output_name not in batches:
            batches[output_name] = []
            record_counts[output_name] = 0
            _emit_progress(
                source=resource_fn.source,
                function=resource_fn.function_name,
                output=output_name,
                event='start',
                records=0,
            )

        if silver_writer is not None:
            silver_writer.write_entity(
                output_entity,
                dataset=output_name,
                raw_record_id=output_provenance.raw_record_id if output_provenance else None,
                raw_record_key=output_provenance.raw_record_key if output_provenance else None,
                snapshot_id=output_provenance.snapshot_id if output_provenance else None,
            )
        if silver_writer is None:
            record_dict = _normalize_record(output_entity)
            _coerce_list_fields(record_dict, ENTITY_SCHEMA)
            batches[output_name].append(record_dict)
        else:
            record_counts[output_name] += 1

        total = record_counts[output_name] + len(batches[output_name])
        if total and total % 10000 == 0:
            print(
                f'[{resource_fn.source}.{resource_fn.function_name}:{output_name}] collected {total:,} records...'
            )

        if len(batches[output_name]) >= batch_size:
            if not dry_run:
                ensure_output_paths(output_name)
                table = pa.Table.from_pylist(
                    batches[output_name], schema=ENTITY_SCHEMA
                )
                writers[output_name].write_table(table)

            record_counts[output_name] += len(batches[output_name])
            _emit_progress(
                source=resource_fn.source,
                function=resource_fn.function_name,
                output=output_name,
                event='update',
                records=record_counts[output_name],
            )
            print(
                f'[{resource_fn.source}.{resource_fn.function_name}:{output_name}] processed {record_counts[output_name]:,} records...'
            )
            batches[output_name].clear()

    # Process first record
    first_provenance = _record_provenance(first_record)
    first_outputs = _unwrap_record(first_record)
    if not isinstance(first_outputs, dict):
        raise ValueError(f'Expected multi-output dict, got {type(first_outputs)!r}')
    for output_name, output_record in first_outputs.items():
        if output_record is not None:
            process_output_record(output_name, output_record, first_provenance)

    # Process remaining records
    for record in records_iter:
        if record is None:
            continue

        # Check test mode limit for any output
        if max_records:
            max_output_count = max(
                (
                    record_counts.get(name, 0) + len(batches.get(name, []))
                    for name in batches.keys()
                ),
                default=0,
            )
            if max_output_count >= max_records:
                print(
                    f'[{resource_fn.source}.{resource_fn.function_name}] test mode: stopping at {max_records:,} records per output'
                )
                break

        if not _is_multi_output_record(record):
            raise ValueError(
                f'Mixed single/multi output in {resource_fn.source}.{resource_fn.function_name}: '
                'all records must be dicts or all must be single records'
            )

        provenance = _record_provenance(record)
        outputs = _unwrap_record(record)
        if not isinstance(outputs, dict):
            raise ValueError(f'Expected multi-output dict, got {type(outputs)!r}')
        for output_name, output_record in outputs.items():
            if output_record is not None:
                process_output_record(output_name, output_record, provenance)

    # Flush remaining batches for legacy/debug dry-run paths only.
    for output_name, batch in batches.items():
        if not batch:
            continue

        if not dry_run:
            ensure_output_paths(output_name)
            table = pa.Table.from_pylist(batch, schema=ENTITY_SCHEMA)
            writers[output_name].write_table(table)

        record_counts[output_name] += len(batch)
        _emit_progress(
            source=resource_fn.source,
            function=resource_fn.function_name,
            output=output_name,
            event='update',
            records=record_counts[output_name],
        )

    # Close all writers
    for output_name, writer in writers.items():
        writer.close()
        print(
            f'[{resource_fn.source}.{resource_fn.function_name}:{output_name}] wrote {record_counts[output_name]:,} records to {output_files[output_name]}'
        )
        _emit_progress(
            source=resource_fn.source,
            function=resource_fn.function_name,
            output=output_name,
            event='done',
            records=record_counts[output_name],
        )

    if dry_run:
        print(f'[{resource_fn.source}.{resource_fn.function_name}] dry-run complete:')
        for output_name in batches.keys():
            print(f'  {output_name}: {record_counts[output_name]:,} records')
            _emit_progress(
                source=resource_fn.source,
                function=resource_fn.function_name,
                output=output_name,
                event='done',
                records=record_counts[output_name],
            )
        return {}

    if silver_writer is not None:
        for output_name, count in record_counts.items():
            print(
                f'[{resource_fn.source}.{resource_fn.function_name}:{output_name}] wrote {count:,} records to silver tables'
            )
        return {name: silver_writer.output_dir for name in record_counts}

    return output_files


def _process_ontology_output(
    resource_fn: ResourceFunction,
    path_manager: PathManager,
    records: Iterable[OntologyTerm],
    *,
    dry_run: bool,
    silver_writer: SilverTableWriter | None = None,
) -> Optional[Path]:
    from pypath.inputs_v2.base import ontology_term_to_entity
    from pypath.inputs_v2.ontology_serializers import format_obo

    terms = [term for term in records if term is not None]
    output_file = path_manager.artifact_file(
        resource_fn.source,
        resource_fn.function_name,
        resource_fn.file_extension or 'obo',
        file_stem=resource_fn.file_stem,
    )

    _emit_progress(source=resource_fn.source, function=resource_fn.function_name, event='start', records=0)
    if dry_run:
        print(f'[{resource_fn.source}.{resource_fn.function_name}] dry-run ontology result: {len(terms):,} terms')
        _emit_progress(source=resource_fn.source, function=resource_fn.function_name, event='done', records=len(terms))
        return None

    output_file.parent.mkdir(parents=True, exist_ok=True)
    content = format_obo(resource_fn.document, terms)
    output_file.write_text(content, encoding='utf-8')

    if silver_writer is not None:
        for row_number, term in enumerate(terms):
            if not resource_fn.ontology_id:
                raise ValueError(
                    f'Ontology resource {resource_fn.source}.{resource_fn.function_name} is missing ontology_id'
                )
            entity = ontology_term_to_entity(term, ontology_id=resource_fn.ontology_id)
            validate_entity_identifier_shapes(
                entity,
                context=f'{resource_fn.source}.{resource_fn.function_name}[{row_number}]',
            )
            silver_writer.write_entity(
                entity,
                dataset=resource_fn.function_name,
            )
        print(
            f'[{resource_fn.source}.{resource_fn.function_name}] wrote {len(terms):,} ontology entities to silver tables'
        )

    print(f'[{resource_fn.source}.{resource_fn.function_name}] wrote {len(terms):,} ontology terms to {output_file}')
    _emit_progress(source=resource_fn.source, function=resource_fn.function_name, event='done', records=len(terms))
    return output_file



def _process_artifact_output(
    resource_fn: ResourceFunction,
    path_manager: PathManager,
    *,
    dry_run: bool,
) -> Optional[Path]:
    output_file = path_manager.artifact_file(
        resource_fn.source,
        resource_fn.function_name,
        resource_fn.file_extension or 'txt',
        file_stem=resource_fn.file_stem,
    )
    _emit_progress(source=resource_fn.source, function=resource_fn.function_name, event='start', records=0)
    if dry_run:
        print(f'[{resource_fn.source}.{resource_fn.function_name}] dry-run artifact render')
        _emit_progress(source=resource_fn.source, function=resource_fn.function_name, event='done', records=1)
        return None

    output_file.parent.mkdir(parents=True, exist_ok=True)
    content = resource_fn.call.render()
    output_file.write_text(content, encoding='utf-8')
    print(f'[{resource_fn.source}.{resource_fn.function_name}] wrote artifact to {output_file}')
    _emit_progress(source=resource_fn.source, function=resource_fn.function_name, event='done', records=1)
    return output_file



def run_silver_loader(
    database: str = 'omnipath',
    base_path: Optional[Path] = None,
    source: Optional[str] = None,
    function: Optional[str] = None,
    *,
    list_only: bool = False,
    batch_size: int = 10_000,
    dry_run: bool = False,
    override: bool = False,
    test_mode: bool = False,
    inputs_package: str = 'pypath.inputs_v2',
    changed_only: bool = False,
    silver_state_dir: Path | None = None,
) -> tuple[
    Dict[str, List[ResourceFunction]],
    PathManager,
    Optional[List[ResourceFunction]],
    Optional[List[Optional[Path]]],
]:
    """Discover and optionally process silver resource functions."""
    try:
        discovered, path_manager = discover_resources(
            database_name=database,
            base_path=base_path,
            inputs_package=inputs_package,
        )
    except DiscoveryError as exc:
        raise DiscoveryError(str(exc)) from exc

    if list_only:
        return discovered, path_manager, None, None

    # Select the subset to process based on CLI arguments.
    selected_functions: List[ResourceFunction] = []

    selected_source = None
    if source:
        selected_source = _normalize_source_filter(source, inputs_package)
        if selected_source not in discovered:
            raise ValueError(
                f'Unknown source "{source}". Use --list to inspect available modules under {inputs_package}.'
            )

    for source_name, functions in discovered.items():
        if selected_source and source_name != selected_source:
            continue
        for fn in functions:
            if function and fn.function_name != function:
                continue
            selected_functions.append(fn)

    if not selected_functions:
        raise ValueError(
            'No resource functions selected. Adjust filters or set list_only=True to inspect options.'
        )

    outputs: List[Optional[Path]] = []
    silver_writers: dict[str, SilverTableWriter] = {}
    incremental_state_roots: dict[str, Path] = {}
    try:
        for fn in selected_functions:
            function_started = time.perf_counter()
            phase_label = f'silver:{fn.source}.{fn.function_name}'
            set_phase(phase_label, f'kind={fn.output_kind}')
            print(
                f'[silver:{fn.source}.{fn.function_name}] start '
                f'kind={fn.output_kind}',
                flush=True,
            )
            writer = None
            raw_dataset = getattr(fn.call, '_raw_dataset', None)
            incremental_state_dir: Path | None = None
            incremental_added_keys: set[str] = set()
            incremental_removed_keys: set[str] = set()
            use_incremental_raw_rows = False
            if (
                raw_dataset is not None
                and fn.output_kind in {'entity', 'ontology'}
                and fn.function_name != 'resource'
                and not dry_run
            ):
                bronze_started = time.perf_counter()
                max_records = _test_mode_record_limit(fn.source, test_mode)
                preparse_kwargs: dict[str, int] = {}
                if max_records is not None:
                    preparse_kwargs['max_lines'] = max_records
                set_phase(
                    f'bronze:{fn.source}.{fn.function_name}',
                    (
                        'preparse'
                        + (
                            f' max_lines={max_records:,}'
                            if max_records is not None else ''
                        )
                    ),
                )
                print(
                    f'[bronze:{fn.source}.{fn.function_name}] preparse requested'
                    + (
                        f' max_lines={max_records:,}'
                        if max_records is not None else ''
                    ),
                    flush=True,
                )
                snapshot = raw_dataset.preparse(
                    source=fn.source,
                    dataset=fn.function_name,
                    **preparse_kwargs,
                )
                delta_keys = changed_keys(snapshot.delta_path)
                incremental_added_keys, incremental_removed_keys = _changed_keys_by_type(
                    snapshot.delta_path
                )
                update_phase(f'preparse ready delta_keys={len(delta_keys):,}')
                print(
                    f'[bronze:{fn.source}.{fn.function_name}] preparse ready '
                    f'snapshot={snapshot.snapshot_id} '
                    f'delta_keys={len(delta_keys):,} '
                    f'elapsed={time.perf_counter() - bronze_started:.1f}s',
                    flush=True,
                )
                raw_dataset._raw_snapshot_override = snapshot
                source_dir = path_manager.source_path(fn.source)
                candidate_state_dir = (
                    silver_state_dir
                    if silver_state_dir is not None
                    else incremental_state_roots.get(fn.source)
                )
                if (
                    candidate_state_dir is not None
                    and has_raw_keyed_silver_tables(candidate_state_dir)
                ):
                    incremental_state_dir = candidate_state_dir
                    incremental_state_roots[fn.source] = candidate_state_dir
                    use_incremental_raw_rows = True
                raw_dataset._changed_only_override = (
                    fn.output_kind == 'entity'
                    and (changed_only or use_incremental_raw_rows)
                )
                if (
                    not delta_keys
                    and (
                        incremental_state_dir is not None
                        or (not override and has_silver_tables(source_dir))
                    )
                ):
                    raw_dataset.accept_last_preparse()
                    if hasattr(raw_dataset, '_raw_snapshot_override'):
                        delattr(raw_dataset, '_raw_snapshot_override')
                    if hasattr(raw_dataset, '_changed_only_override'):
                        delattr(raw_dataset, '_changed_only_override')
                    result = (
                        silver_table_dir(source_dir)
                        if incremental_state_dir is None
                        else silver_table_dir(incremental_state_dir)
                    )
                    print(
                        f'[{fn.source}.{fn.function_name}] raw delta empty; '
                        f'skipping silver mapping'
                        f' elapsed={time.perf_counter() - function_started:.1f}s',
                        flush=True,
                    )
                    outputs.append(result)
                    continue

            if fn.output_kind in {'entity', 'ontology'} and fn.function_name != 'resource' and not dry_run:
                set_phase(phase_label, 'preparing silver writer')
                writer = silver_writers.get(fn.source)
                if writer is None:
                    source_dir = path_manager.source_path(fn.source)
                    if override and incremental_state_dir is None:
                        for legacy_file in source_dir.glob('*.parquet'):
                            if legacy_file.name != 'resource.parquet':
                                legacy_file.unlink()
                        stale_dir = source_dir / 'normalized'
                        if stale_dir.exists():
                            for stale_file in stale_dir.glob('*.parquet'):
                                stale_file.unlink()
                            stale_dir.rmdir()
                    writer = SilverTableWriter(
                        silver_table_dir(source_dir),
                        fn.source,
                        batch_size=batch_size,
                        seed_from_dir=incremental_state_dir,
                    )
                    silver_writers[fn.source] = writer
                if incremental_state_dir is not None:
                    if fn.output_kind == 'ontology':
                        writer.exclude_dataset(fn.function_name)
                    else:
                        writer.exclude_raw_record_keys(incremental_removed_keys)
                    if delta_keys:
                        print(
                            f'[{fn.source}.{fn.function_name}] incremental silver update: '
                            f'added_raw_keys={len(incremental_added_keys):,} '
                            f'removed_raw_keys={len(incremental_removed_keys):,}',
                            flush=True,
                        )
                    else:
                        print(
                            f'[{fn.source}.{fn.function_name}] raw delta empty; '
                            'copying silver state forward',
                            flush=True,
                        )
                elif writer.seed_from_dir is not None:
                    writer.exclude_dataset(fn.function_name)
            try:
                set_phase(phase_label, 'mapping records')
                result = process_resource_function(
                    fn,
                    path_manager=path_manager,
                    batch_size=batch_size,
                    dry_run=dry_run,
                    override=override,
                    test_mode=test_mode,
                    silver_writer=writer,
                )
                raw_dataset = getattr(fn.call, '_raw_dataset', None)
                if raw_dataset is not None and hasattr(raw_dataset, 'accept_last_preparse'):
                    raw_dataset.accept_last_preparse()
                    if hasattr(raw_dataset, '_raw_snapshot_override'):
                        delattr(raw_dataset, '_raw_snapshot_override')
                    if hasattr(raw_dataset, '_changed_only_override'):
                        delattr(raw_dataset, '_changed_only_override')
                outputs.append(result)
                print(
                    f'[silver:{fn.source}.{fn.function_name}] done '
                    f'elapsed={time.perf_counter() - function_started:.1f}s',
                    flush=True,
                )
            except Exception as exc:  # noqa: BLE001
                raise RuntimeError(
                    f'Failed to process {fn.source}.{fn.function_name}: {exc}'
                ) from exc
    finally:
        for writer in silver_writers.values():
            writer.close()
            print(f'[{writer.source}] wrote silver tables to {writer.output_dir}')

    return discovered, path_manager, selected_functions, outputs
