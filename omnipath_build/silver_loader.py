#!/usr/bin/env python3
"""Silver loader that discovers resource generators dynamically."""

from __future__ import annotations

import importlib
import inspect
import pkgutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Iterable, Iterator, List, Optional
from enum import Enum

import pyarrow as pa
import pyarrow.parquet as pq

from omnipath_build.utils.path_manager import PathManager
from pypath.internals.silver_schema import (
    Entity as SilverEntity,
    Resource,
    ENTITY_SCHEMA,
)

__all__ = [
    'DiscoveryError',
    'ResourceFunction',
    'discover_resources',
    'process_resource_function',
    'run_silver_loader',
]

@dataclass(slots=True)
class ResourceFunction:
    """Container describing a discovered resource transformation function."""

    source: str
    function_name: str
    qualified_module: str
    call: Callable[[], Iterable]
    resource_id: str
    resource: Optional[Resource] = None


class DiscoveryError(RuntimeError):
    """Raised when resource discovery fails."""


def discover_resources(
    database_name: str,
    base_path: Optional[Path] = None,
    inputs_package: str = 'pypath.inputs_v2',
) -> tuple[Dict[str, List[ResourceFunction]], PathManager]:
    """Discover generator functions from the inputs_v2 package."""
    path_manager = PathManager(database_name, base_path)

    try:
        root_module = importlib.import_module(inputs_package)
    except ImportError as exc:  # noqa: BLE001
        raise DiscoveryError(f'Unable to import inputs package "{inputs_package}": {exc}') from exc

    package_paths = getattr(root_module, '__path__', None)
    if package_paths is None:
        raise DiscoveryError(f'Inputs package "{inputs_package}" is not a namespace package')

    prefix = f'{inputs_package}.'
    discovered: Dict[str, List[ResourceFunction]] = {}

    for module_info in pkgutil.walk_packages(package_paths, prefix):
        module_name = module_info.name
        relative_name = module_name[len(prefix):]
        if not relative_name:
            continue

        leaf = relative_name.split('.')[-1]
        if leaf.startswith('_'):
            continue

        module = importlib.import_module(module_name)

        export_names = [
            name for name, obj in inspect.getmembers(module, inspect.isfunction)
            if obj.__module__ == module.__name__ and inspect.isgeneratorfunction(obj)
        ]

        if not export_names:
            continue

        resource_details: Optional[Resource] = None
        resource_id = relative_name
        get_resource = getattr(module, 'get_resource', None)
        if callable(get_resource):
            try:
                resource_details = get_resource()
                if hasattr(resource_details, 'id') and resource_details.id:
                    resource_id = resource_details.id
            except Exception as exc:  # noqa: BLE001
                raise DiscoveryError(
                    f'Failed to load resource metadata from {module_name}: {exc}',
                ) from exc

        module_functions: List[ResourceFunction] = []
        for function_name in export_names:
            func = getattr(module, function_name, None)
            if func is None:
                continue

            module_functions.append(
                ResourceFunction(
                    source=relative_name,
                    function_name=function_name,
                    qualified_module=module_name,
                    call=func,
                    resource_id=resource_id,
                    resource=resource_details,
                ),
            )

        if module_functions:
            discovered[relative_name] = module_functions

    if not discovered:
        raise DiscoveryError(f'No resource functions found under package "{inputs_package}"')

    return discovered, path_manager


def _ensure_entity_record(record: object) -> None:
    """Validate that a record is a SilverEntity instance."""
    if not isinstance(record, SilverEntity):
        raise ValueError(
            f'Unsupported record type {type(record)!r}; expected pypath.internals.silver_schema.Entity',
        )


def _normalize_record(record: object) -> dict:
    """Convert a namedtuple-like record into a plain dictionary."""
    if hasattr(record, '_asdict'):
        normalized = record._asdict()
        # Recursively normalize nested structures (e.g., membership entities)
        for key, value in list(normalized.items()):
            if hasattr(value, '_asdict'):
                normalized[key] = _normalize_record(value)
            elif isinstance(value, list):
                normalized[key] = [
                    _normalize_record(item) if hasattr(item, '_asdict') else item
                    for item in value
                ]
        return normalized
    if isinstance(record, dict):
        normalized = {}
        for key, value in record.items():
            if hasattr(value, '_asdict'):
                normalized[key] = _normalize_record(value)
            elif isinstance(value, list):
                normalized[key] = [
                    _normalize_record(item) if hasattr(item, '_asdict') else item
                    for item in value
                ]
            else:
                normalized[key] = value
        return normalized
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


def _ensure_record_source(record: dict, source_value: str) -> None:
    """Guarantee the required source column is present."""
    if not source_value:
        raise ValueError('resource id must be a non-empty string')
    current = record.get('source')
    if current:
        return
    record['source'] = source_value


def _is_multi_output_record(record: object) -> bool:
    """Check if record is a multi-output dict (not a namedtuple with _asdict)."""
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
) -> Optional[Path] | Dict[str, Path]:
    """Stream records from a resource function into parquet file(s).

    Returns:
        - Optional[Path] for single-output functions
        - Dict[str, Path] for multi-output functions (yields dicts with named outputs)
    """
    # Check if output file already exists and skip if not overriding
    if not override:
        potential_output = path_manager.silver_file(
            resource_fn.source,
            resource_fn.function_name,
            resource_fn.function_name,
        )
        if potential_output.exists():
            print(f'[{resource_fn.source}.{resource_fn.function_name}] skipping (file exists: {potential_output})')
            return potential_output

    try:
        records = resource_fn.call()
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            f'Failed to execute {resource_fn.source}.{resource_fn.function_name}: {exc}'
        ) from exc

    # Peek at first record to detect multi-output
    first_record = None
    records_iter = iter(records)
    for rec in records_iter:
        if rec is not None:
            first_record = rec
            break

    if first_record is None:
        # No records at all
        print(f'[{resource_fn.source}.{resource_fn.function_name}] no records generated')
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

        if all_exist and existing_files:
            output_list = ', '.join(existing_files.keys())
            print(f'[{resource_fn.source}.{resource_fn.function_name}] skipping (multi-output files exist: {output_list})')
            return existing_files

    if is_multi_output:
        # Process as multi-output function
        return _process_multi_output(
            resource_fn, path_manager, first_record, records_iter, batch_size, dry_run
        )
    else:
        # Process as single-output function (existing logic)
        return _process_single_output(
            resource_fn, path_manager, first_record, records_iter, batch_size, dry_run
        )


def _process_single_output(
    resource_fn: ResourceFunction,
    path_manager: PathManager,
    first_record: object,
    records_iter: Iterator,
    batch_size: int,
    dry_run: bool,
) -> Optional[Path]:
    """Process single-output function producing Entity records."""
    schema = ENTITY_SCHEMA
    output_file: Optional[Path] = None
    writer: Optional[pq.ParquetWriter] = None
    total_records = 0
    batch: List[dict] = []

    # Process first record
    _ensure_entity_record(first_record)
    normalized = _normalize_record(first_record)
    _ensure_record_source(normalized, resource_fn.resource_id)

    _coerce_list_fields(normalized, schema)
    batch.append(normalized)

    # Process remaining records
    for record in records_iter:
        if record is None:
            continue

        _ensure_entity_record(record)
        normalized = _normalize_record(record)
        _ensure_record_source(normalized, resource_fn.resource_id)
        _coerce_list_fields(normalized, schema)

        batch.append(normalized)

        # Print progress every 10 records
        if len(batch) % 10000 == 0:
            print(f'[{resource_fn.source}.{resource_fn.function_name}] collected {total_records + len(batch):,} records...')

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
            print(f'[{resource_fn.source}.{resource_fn.function_name}] processed {total_records:,} records...')
            batch.clear()

    if not batch:
        if total_records == 0 and dry_run:
            print(f'[{resource_fn.source}.{resource_fn.function_name}] dry-run complete (no write)')
            return None
        if total_records == 0 and not dry_run:
            output_file = path_manager.silver_file(
                resource_fn.source,
                resource_fn.function_name,
                resource_fn.function_name,
            )
            output_file.parent.mkdir(parents=True, exist_ok=True)
            pq.write_table(pa.Table.from_pylist([], schema=schema), output_file)
            print(f'[{resource_fn.source}.{resource_fn.function_name}] wrote empty table to {output_file}')
            return output_file
        if writer:
            writer.close()
            print(f'[{resource_fn.source}.{resource_fn.function_name}] wrote {total_records:,} records to {output_file}')
            return output_file
        return output_file

    if dry_run:
        total_records += len(batch)
        print(f'[{resource_fn.source}.{resource_fn.function_name}] dry-run result: {total_records:,} records pending write')
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

    print(f'[{resource_fn.source}.{resource_fn.function_name}] wrote {total_records:,} records to {output_file}')
    return output_file


def _process_multi_output(
    resource_fn: ResourceFunction,
    path_manager: PathManager,
    first_record: dict,
    records_iter: Iterator,
    batch_size: int,
    dry_run: bool,
) -> Dict[str, Path]:
    """Process multi-output function that yields dicts with named outputs."""
    batches: Dict[str, List[dict]] = {}
    writers: Dict[str, pq.ParquetWriter] = {}
    output_files: Dict[str, Path] = {}
    record_counts: Dict[str, int] = {}

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

    def process_output_record(output_name: str, output_record: object) -> None:
        """Process a single output record."""
        if output_record is None:
            return

        _ensure_entity_record(output_record)

        if output_name not in batches:
            batches[output_name] = []
            record_counts[output_name] = 0

        normalized = _normalize_record(output_record)
        _ensure_record_source(normalized, resource_fn.resource_id)
        _coerce_list_fields(normalized, ENTITY_SCHEMA)
        batches[output_name].append(normalized)

        if len(batches[output_name]) % 10000 == 0:
            total = record_counts[output_name] + len(batches[output_name])
            print(f'[{resource_fn.source}.{resource_fn.function_name}:{output_name}] collected {total:,} records...')

        if len(batches[output_name]) >= batch_size:
            if not dry_run:
                ensure_output_paths(output_name)
                table = pa.Table.from_pylist(batches[output_name], schema=ENTITY_SCHEMA)
                writers[output_name].write_table(table)

            record_counts[output_name] += len(batches[output_name])
            print(f'[{resource_fn.source}.{resource_fn.function_name}:{output_name}] processed {record_counts[output_name]:,} records...')
            batches[output_name].clear()

    # Process first record
    for output_name, output_record in first_record.items():
        if output_record is not None:
            process_output_record(output_name, output_record)

    # Process remaining records
    for record in records_iter:
        if record is None:
            continue

        if not _is_multi_output_record(record):
            raise ValueError(
                f'Mixed single/multi output in {resource_fn.source}.{resource_fn.function_name}: '
                'all records must be dicts or all must be single records'
            )

        for output_name, output_record in record.items():
            if output_record is not None:
                process_output_record(output_name, output_record)

    # Flush remaining batches
    for output_name, batch in batches.items():
        if not batch:
            continue

        if not dry_run:
            ensure_output_paths(output_name)
            table = pa.Table.from_pylist(batch, schema=ENTITY_SCHEMA)
            writers[output_name].write_table(table)

        record_counts[output_name] += len(batch)

    # Close all writers
    for output_name, writer in writers.items():
        writer.close()
        print(f'[{resource_fn.source}.{resource_fn.function_name}:{output_name}] wrote {record_counts[output_name]:,} records to {output_files[output_name]}')

    if dry_run:
        print(f'[{resource_fn.source}.{resource_fn.function_name}] dry-run complete:')
        for output_name in batches.keys():
            print(f'  {output_name}: {record_counts[output_name]:,} records')
        return {}

    return output_files


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
    inputs_package: str = 'pypath.inputs_v2',
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

    normalized_source = None
    if source:
        normalized_source = _normalize_source_filter(source, inputs_package)
        if normalized_source not in discovered:
            raise ValueError(
                f'Unknown source "{source}". Use --list to inspect available modules under {inputs_package}.'
            )

    for source_name, functions in discovered.items():
        if normalized_source and source_name != normalized_source:
            continue
        for fn in functions:
            if function and fn.function_name != function:
                continue
            selected_functions.append(fn)

    if not selected_functions:
        raise ValueError('No resource functions selected. Adjust filters or set list_only=True to inspect options.')

    outputs: List[Optional[Path]] = []
    for fn in selected_functions:
        try:
            result = process_resource_function(
                fn,
                path_manager=path_manager,
                batch_size=batch_size,
                dry_run=dry_run,
                override=override,
            )
            outputs.append(result)
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f'Failed to process {fn.source}.{fn.function_name}: {exc}') from exc

    return discovered, path_manager, selected_functions, outputs
