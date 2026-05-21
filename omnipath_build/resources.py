"""Discover runnable pypath ``inputs_v2`` resources for the build pipeline.

The build pipeline does not keep a hand-written list of datasets.
Instead, it imports the configured inputs package, walks resource modules, and
collects ``Resource``, ``Dataset``, ``OntologyDataset``, and
``ArtifactDataset`` objects exposed by pypath. Only entity and ontology datasets
with raw dataset access are selected for evidence ingest; id translation and
artifact-only datasets are skipped.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
import importlib
import inspect
import os
from pathlib import Path
import pkgutil
import time


@dataclass(slots=True)
class ResourceFunction:
    """Discovered pypath inputs_v2 resource or dataset callable."""

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
    """Raised when inputs_v2 resource discovery fails."""


def configure_pypath_download_dir() -> Path:
    """Ensure pypath downloads use a project-local cache directory."""

    configured = os.environ.get('PYPATH_DOWNLOAD_DATADIR')
    if configured:
        data_dir = Path(configured)
    else:
        project_root = Path(__file__).resolve().parents[1]
        data_dir = project_root / 'pypath-data'
        os.environ['PYPATH_DOWNLOAD_DATADIR'] = str(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


def discover_resources(
    database_name: str,
    inputs_package: str = 'pypath.inputs_v2',
    progress: bool = False,
) -> tuple[dict[str, list[ResourceFunction]], None]:
    """Return input dataset callables grouped by source module name."""

    del database_name
    started = time.perf_counter()
    scanned_modules = 0
    configure_pypath_download_dir()
    if progress:
        print(
            f'[discover_resources] importing package={inputs_package}',
            flush=True,
        )

    from pypath.inputs_v2.base import (  # noqa: PLC0415
        ArtifactDataset,
        Dataset,
        OntologyDataset,
        Resource,
    )

    try:
        root_module = importlib.import_module(inputs_package)
    except ImportError as exc:
        raise DiscoveryError(
            f'Unable to import inputs package "{inputs_package}": {exc}'
        ) from exc

    package_paths = getattr(root_module, '__path__', None)
    if package_paths is None:
        raise DiscoveryError(
            f'Inputs package "{inputs_package}" is not a namespace package'
        )

    prefix = f'{inputs_package}.'
    discovered: dict[str, list[ResourceFunction]] = {}
    dataset_types = (Dataset, OntologyDataset, ArtifactDataset)

    for module_info in pkgutil.walk_packages(package_paths, prefix):
        scanned_modules += 1
        module_name = module_info.name
        relative_name = module_name[len(prefix) :]
        if not relative_name or relative_name.split('.')[-1].startswith('_'):
            continue

        try:
            module = importlib.import_module(module_name)
        except Exception as exc:  # noqa: BLE001
            if progress:
                print(
                    f'[discover_resources] skipping {module_name}: '
                    f'{exc.__class__.__name__}: {exc}',
                    flush=True,
                )
            continue

        resource_members = [
            (name, obj)
            for name, obj in inspect.getmembers(module)
            if isinstance(obj, Resource)
        ]
        dataset_members = [
            (name, obj)
            for name, obj in inspect.getmembers(module)
            if isinstance(obj, dataset_types)
            and getattr(obj, 'kind', None) != 'id_translation'
        ]

        seen_dataset_names = {name for name, _ in dataset_members}
        for _, resource_obj in resource_members:
            for dataset_name, dataset_obj in resource_obj.datasets().items():
                if (
                    getattr(dataset_obj, 'kind', None) != 'id_translation'
                    and dataset_name not in seen_dataset_names
                ):
                    dataset_members.append((dataset_name, dataset_obj))
                    seen_dataset_names.add(dataset_name)

        if not resource_members and not dataset_members:
            continue

        module_functions: list[ResourceFunction] = []
        for _, resource_obj in resource_members:
            module_functions.append(
                ResourceFunction(
                    source=relative_name,
                    function_name='resource',
                    qualified_module=module_name,
                    call=resource_obj,
                    resource_id=relative_name,
                ),
            )
            break

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
                    return dataset_obj(
                        source=source_name,
                        dataset=dataset_name,
                    )

                dataset_call._raw_dataset = dataset_obj
            else:
                dataset_call = dataset_obj

            module_functions.append(
                ResourceFunction(
                    source=relative_name,
                    function_name=dataset_name,
                    qualified_module=module_name,
                    call=dataset_call,
                    resource_id=relative_name,
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
    if progress:
        function_count = sum(len(functions) for functions in discovered.values())
        print(
            '[discover_resources] done '
            f'modules={scanned_modules} sources={len(discovered)} '
            f'functions={function_count} '
            f'elapsed={time.perf_counter() - started:.1f}s',
            flush=True,
        )
    return discovered, None
