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
    ('SilverEntity', 'entity'),
    ('SilverInteraction', 'interaction'),
    ('SilverCvTerm', 'cv_term'),
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
    """Infer schema type by inspecting the function source code."""
    # Direct attribute on the function takes precedence if available.
    direct_schema = getattr(func, 'schema_type', None)
    if isinstance(direct_schema, str):
        return direct_schema

    try:
        source = inspect.getsource(func)
    except (OSError, TypeError):
        source = ''

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
        return record._asdict()
    if isinstance(record, dict):
        return record
    raise TypeError(f'Cannot normalize record of type {type(record)!r}')


def _ensure_schema(schema_type: str | None) -> tuple[str, pa.Schema, str]:
    """Validate schema type and return (type, pyarrow schema, table name)."""
    if schema_type is None:
        raise ValueError('Unable to determine schema type for records')

    if schema_type not in SCHEMA_LOOKUP:
        raise ValueError(f'Unsupported schema type: {schema_type}')

    schema, table_name = SCHEMA_LOOKUP[schema_type]
    return schema_type, schema, table_name


def process_resource_function(
    resource_fn: ResourceFunction,
    path_manager: PathManager,
    batch_size: int = 10_000,
    dry_run: bool = False,
    override: bool = False,
) -> Optional[Path]:
    """Stream records from a resource function into a parquet file."""
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

    schema_type = resource_fn.schema_type
    schema = None
    table_name = None
    output_file: Optional[Path] = None
    writer: Optional[pq.ParquetWriter] = None
    total_records = 0
    batch: List[dict] = []

    try:
        records = resource_fn.call()
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            f'Failed to execute {resource_fn.source}.{resource_fn.function_name}: {exc}'
        ) from exc

    for record in records:
        if record is None:
            continue

        if schema_type is None:
            schema_type = _schema_from_record(record)
            schema_type, schema, table_name = _ensure_schema(schema_type)
        elif schema is None:
            # Schema type was discovered earlier but schema not yet materialized.
            schema_type, schema, table_name = _ensure_schema(schema_type)

        batch.append(_normalize_record(record))

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
            # Create empty parquet with schema so downstream steps can rely on file presence.
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
