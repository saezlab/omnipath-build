#!/usr/bin/env python3
"""Silver loader that discovers resource generators dynamically."""

from __future__ import annotations

import importlib.util
import inspect
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Callable, Dict, Iterable, Iterator, List, Optional

import pyarrow as pa
import pyarrow.parquet as pq

from omnipath_build.utils.path_manager import PathManager
from omnipath_build.utils.silver_schema import (
    SilverCvTerm,
    SilverEntity,
    SilverInteraction,
    SILVER_CV_TERM_SCHEMA,
    SILVER_ENTITY_SCHEMA,
    SILVER_INTERACTION_SCHEMA,
)

# Ensure legacy imports (from silver_schema import SilverEntity) keep working
from omnipath_build.utils import silver_schema as canonical_silver_schema  # noqa: E402

sys.modules.setdefault('silver_schema', canonical_silver_schema)

__all__ = [
    'DiscoveryError',
    'ResourceFunction',
    'SCHEMA_LOOKUP',
    'SCHEMA_SOURCE_HINTS',
    'discover_resources',
    'load_module_from_path',
    'process_resource_function',
    'run_silver_loader',
]

# Mapping of schema type identifiers to pyarrow schema and table name.
SCHEMA_LOOKUP = {
    'entity': (SILVER_ENTITY_SCHEMA, 'silver_entities'),
    'interaction': (SILVER_INTERACTION_SCHEMA, 'silver_interactions'),
    'cv_term': (SILVER_CV_TERM_SCHEMA, 'silver_cv_terms'),
}

# Strings used when inspecting function source code to infer schema type.
SCHEMA_SOURCE_HINTS = [
    ('SilverInteraction', 'interaction'),
    ('SilverCvTerm', 'cv_term'),
    ('SilverEntity', 'entity'),
]


@dataclass(slots=True)
class ResourceFunction:
    """Container describing a discovered resource transformation function."""

    source: str
    function_name: str
    call: Callable[[], Iterable]
    schema_type: Optional[str] = None
    module_path: Optional[Path] = None


class DiscoveryError(RuntimeError):
    """Raised when resource discovery fails."""


def load_module_from_path(module_path: Path) -> ModuleType:
    """Import a module directly from its file path."""
    spec = importlib.util.spec_from_file_location(
        f'omnipath.resources.{module_path.stem}',
        module_path,
    )
    if spec is None or spec.loader is None:
        raise DiscoveryError(f'Unable to load module from {module_path}')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[call-arg]
    return module


def _infer_schema_from_function_source(func: Callable) -> Optional[str]:
    """Infer schema type by inspecting the function source code for yield statements."""
    # Direct attribute on the function takes precedence if available.
    direct_schema = getattr(func, 'schema_type', None)
    if isinstance(direct_schema, str):
        return direct_schema

    try:
        source = inspect.getsource(func)
    except (OSError, TypeError):
        source = ''

    # Look for what's actually being yielded by checking yield statements
    for hint, schema_type in SCHEMA_SOURCE_HINTS:
        # Check if the hint appears in a yield statement (e.g., "yield SilverInteraction(")
        if f'yield {hint}(' in source:
            return schema_type

    # Fallback: if no yield statement found, check if hint appears anywhere in source
    for hint, schema_type in SCHEMA_SOURCE_HINTS:
        if hint in source:
            return schema_type
    return None


def discover_resources(
    database_name: str,
    base_path: Optional[Path] = None,
) -> tuple[Dict[str, List[ResourceFunction]], PathManager]:
    """Discover resource modules and the silver functions they expose."""
    path_manager = PathManager(database_name, base_path)
    resources_dir = path_manager.resources_path()

    if not resources_dir.exists():
        raise DiscoveryError(f'Resource directory not found: {resources_dir}')

    discovered: Dict[str, List[ResourceFunction]] = {}

    for module_path in sorted(resources_dir.glob('*.py')):
        if module_path.name.startswith('_'):
            continue

        module = load_module_from_path(module_path)
        export_names = getattr(module, '__all__', None)

        if not export_names:
            export_names = [
                name for name, obj in inspect.getmembers(module, inspect.isfunction)
                if obj.__module__ == module.__name__
            ]

        if not export_names:
            continue

        schema_mapping = getattr(module, 'SCHEMA_TYPES', {})
        module_functions: List[ResourceFunction] = []

        for function_name in export_names:
            func = getattr(module, function_name, None)
            if not callable(func):
                continue

            schema_type = None
            if isinstance(schema_mapping, dict):
                mapped = schema_mapping.get(function_name)
                if isinstance(mapped, str):
                    schema_type = mapped

            if schema_type is None:
                schema_type = _infer_schema_from_function_source(func)

            module_functions.append(
                ResourceFunction(
                    source=module_path.stem,
                    function_name=function_name,
                    call=func,
                    schema_type=schema_type,
                    module_path=module_path,
                )
            )

        if module_functions:
            discovered[module_path.stem] = module_functions

    if not discovered:
        raise DiscoveryError(f'No resource functions found in {resources_dir}')

    return discovered, path_manager


def _schema_from_record(record: object) -> str:
    """Derive schema type from an emitted record instance."""
    if isinstance(record, SilverEntity):
        return 'entity'
    if isinstance(record, SilverInteraction):
        return 'interaction'
    if isinstance(record, SilverCvTerm):
        return 'cv_term'
    raise ValueError(
        f'Unsupported record type {type(record)!r}; expected SilverEntity, '
        'SilverInteraction, or SilverCvTerm',
    )


def _normalize_record(record: object) -> dict:
    """Convert a namedtuple-like record into a plain dictionary."""
    if hasattr(record, '_asdict'):
        normalized = record._asdict()
        # Recursively normalize nested structures (e.g., SilverEntity inside SilverInteraction)
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
                        # Convert to string if schema expects string
                        coerced_item[field_name] = str(field_value)
                    else:
                        coerced_item[field_name] = field_value

                coerced_list.append(coerced_item)
            value = coerced_list

        record[field.name] = value


def _ensure_schema(schema_type: str | None) -> tuple[str, pa.Schema, str]:
    """Validate schema type and return (type, pyarrow schema, table name)."""
    if schema_type is None:
        raise ValueError('Unable to determine schema type for records')

    if schema_type not in SCHEMA_LOOKUP:
        raise ValueError(f'Unsupported schema type: {schema_type}')

    schema, table_name = SCHEMA_LOOKUP[schema_type]
    return schema_type, schema, table_name


def _is_multi_output_record(record: object) -> bool:
    """Check if record is a multi-output dict (not a namedtuple with _asdict)."""
    return isinstance(record, dict) and not hasattr(record, '_asdict')


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
        schema_type = resource_fn.schema_type
        if schema_type:
            try:
                _, _, table_name = _ensure_schema(schema_type)
                potential_output = path_manager.silver_file(
                    resource_fn.source,
                    resource_fn.function_name,
                    table_name,
                )
                if potential_output.exists():
                    print(f'[{resource_fn.source}.{resource_fn.function_name}] skipping (file exists: {potential_output})')
                    return potential_output
            except ValueError:
                pass  # Schema type not yet determined, continue with processing

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
            # Infer schema type from first record's output
            output_record = first_record[output_name]
            if output_record is None:
                continue

            try:
                schema_type = _schema_from_record(output_record)
                _, _, table_name = _ensure_schema(schema_type)
                potential_file = path_manager.silver_file(
                    resource_fn.source,
                    output_name,
                    table_name,
                )
                if potential_file.exists():
                    existing_files[output_name] = potential_file
                else:
                    all_exist = False
                    break
            except (ValueError, TypeError):
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
    """Process single-output function (original logic)."""
    schema_type = resource_fn.schema_type
    schema = None
    table_name = None
    output_file: Optional[Path] = None
    writer: Optional[pq.ParquetWriter] = None
    total_records = 0
    batch: List[dict] = []

    # Process first record
    normalized = _normalize_record(first_record)
    if schema_type is None:
        schema_type = _schema_from_record(first_record)
        schema_type, schema, table_name = _ensure_schema(schema_type)
    elif schema is None:
        schema_type, schema, table_name = _ensure_schema(schema_type)

    if schema is not None:
        _coerce_list_fields(normalized, schema)
    batch.append(normalized)

    # Process remaining records
    for record in records_iter:
        if record is None:
            continue

        normalized = _normalize_record(record)

        if schema_type is None:
            schema_type = _schema_from_record(record)
            schema_type, schema, table_name = _ensure_schema(schema_type)
        elif schema is None:
            schema_type, schema, table_name = _ensure_schema(schema_type)

        if schema is not None:
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

            if output_file is None:
                output_file = path_manager.silver_file(
                    resource_fn.source,
                    resource_fn.function_name,
                    table_name,
                )
                output_file.parent.mkdir(parents=True, exist_ok=True)

            if writer is None:
                writer = pq.ParquetWriter(output_file, schema)

            table = pa.Table.from_pylist(batch, schema=schema)
            writer.write_table(table)
            total_records += len(batch)
            print(f'[{resource_fn.source}.{resource_fn.function_name}] processed {total_records:,} records...')
            batch.clear()

    if not batch:
        if total_records == 0 and dry_run:
            print(f'[{resource_fn.source}.{resource_fn.function_name}] dry-run complete (no write)')
            return None
        if total_records == 0 and schema is not None and not dry_run:
            output_file = path_manager.silver_file(
                resource_fn.source,
                resource_fn.function_name,
                table_name,
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

    if output_file is None:
        output_file = path_manager.silver_file(
            resource_fn.source,
            resource_fn.function_name,
            table_name,
        )
        output_file.parent.mkdir(parents=True, exist_ok=True)

    if writer is None:
        writer = pq.ParquetWriter(output_file, schema)

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
    # Multi-output state tracking
    batches: Dict[str, List[dict]] = {}
    schemas: Dict[str, pa.Schema] = {}
    table_names: Dict[str, str] = {}
    writers: Dict[str, pq.ParquetWriter] = {}
    output_files: Dict[str, Path] = {}
    record_counts: Dict[str, int] = {}
    schema_types: Dict[str, str] = {}

    def process_output_record(output_name: str, output_record: object) -> None:
        """Process a single output record."""
        # Initialize structures for this output if first time
        if output_name not in batches:
            batches[output_name] = []
            record_counts[output_name] = 0

        # Normalize record
        normalized = _normalize_record(output_record)

        # Determine schema if needed
        if output_name not in schemas:
            schema_type = _schema_from_record(output_record)
            schema_type_key, schema, table_name = _ensure_schema(schema_type)
            schemas[output_name] = schema
            table_names[output_name] = table_name
            schema_types[output_name] = schema_type_key

        # Coerce fields
        _coerce_list_fields(normalized, schemas[output_name])
        batches[output_name].append(normalized)

        # Print progress
        if len(batches[output_name]) % 10000 == 0:
            total = record_counts[output_name] + len(batches[output_name])
            print(f'[{resource_fn.source}.{resource_fn.function_name}:{output_name}] collected {total:,} records...')

        # Flush if batch is full
        if len(batches[output_name]) >= batch_size:
            if not dry_run:
                # Initialize output file and writer if needed
                if output_name not in output_files:
                    output_files[output_name] = path_manager.silver_file(
                        resource_fn.source,
                        output_name,
                        table_names[output_name],
                    )
                    output_files[output_name].parent.mkdir(parents=True, exist_ok=True)

                if output_name not in writers:
                    writers[output_name] = pq.ParquetWriter(
                        output_files[output_name],
                        schemas[output_name],
                    )

                # Write batch
                table = pa.Table.from_pylist(batches[output_name], schema=schemas[output_name])
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
            # Initialize if needed
            if output_name not in output_files:
                output_files[output_name] = path_manager.silver_file(
                    resource_fn.source,
                    output_name,
                    table_names[output_name],
                )
                output_files[output_name].parent.mkdir(parents=True, exist_ok=True)

            if output_name not in writers:
                writers[output_name] = pq.ParquetWriter(
                    output_files[output_name],
                    schemas[output_name],
                )

            # Write final batch
            table = pa.Table.from_pylist(batch, schema=schemas[output_name])
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
        )
    except DiscoveryError as exc:
        raise DiscoveryError(str(exc)) from exc

    if list_only:
        return discovered, path_manager, None, None

    # Select the subset to process based on CLI arguments.
    selected_functions: List[ResourceFunction] = []

    if source and source not in discovered:
        raise ValueError(f'Unknown source "{source}". Use list_only=True to inspect available sources.')

    for source_name, functions in discovered.items():
        if source and source_name != source:
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
