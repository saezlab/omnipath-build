from __future__ import annotations

import json
import shutil
from typing import Any
import hashlib
from pathlib import Path
import tempfile
import importlib.util

from omnipath_build.silver.build import run_silver_loader
from id_resolver.build.mapping_tables import (
    CHEMICAL_SOURCES,
    run_sources as materialize_resolver_tables,
)
from omnipath_build.gold.build_entities import build_entities
from omnipath_build.gold.build_relations import build_relations
from omnipath_build.pipeline.resource_archives import build_resource_archive

REFERENCE_MAPPING_SOURCES = ['uniprot', *CHEMICAL_SOURCES]
TEST_MODE_REFERENCE_MAPPING_SOURCES = [
    'uniprot',
    'chebi',
]

INPUTS_MODULE_HASH_FILE = 'inputs_module_hash.json'
GOLD_SUCCESS_FILE = '_SUCCESS.json'


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
    output_dir.mkdir(parents=True, exist_ok=True)
    inputs_hash = hash_inputs_module(inputs_package, source)
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
        )
        staged_source_dir = stage_root / 'silver' / source.replace('.', '/')
        if not staged_source_dir.exists():
            raise FileNotFoundError(f'Silver output missing for {source}: {staged_source_dir}')

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
    }


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
        path.name != 'resource.parquet'
        for path in silver_dir.glob('*.parquet')
    )


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + '.tmp')
    tmp_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + '\n',
        encoding='utf-8',
    )
    tmp_path.replace(path)


def gold_output_ready(output_dir: Path) -> bool:
    entities_dir = output_dir / 'entities'
    success_path = output_dir / GOLD_SUCCESS_FILE
    return (
        success_path.exists()
        and (entities_dir / 'entity.parquet').exists()
        and (entities_dir / 'entity_map.parquet').exists()
    )


def build_gold_source(
    *,
    source: str,
    silver_dir: Path,
    output_dir: Path,
    mapping_dir: Path,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    success_path = output_dir / GOLD_SUCCESS_FILE
    if success_path.exists():
        success_path.unlink()
    entities_dir = output_dir / 'entities'
    relations_dir = output_dir / 'relations'

    if not silver_has_data(silver_dir):
        return {
            'output_dir': str(output_dir),
            'entities_dir': str(entities_dir),
            'relations_dir': str(relations_dir),
            'skipped': 'no_data',
            'entity_summary': None,
            'relation_summary': None,
        }

    if entities_dir.exists():
        shutil.rmtree(entities_dir)
    if relations_dir.exists():
        shutil.rmtree(relations_dir)
    entities_dir.mkdir(parents=True, exist_ok=True)
    relations_dir.mkdir(parents=True, exist_ok=True)

    entity_summary = build_entities(
        silver_dir=silver_dir,
        mapping_dir=mapping_dir,
        output_dir=entities_dir,
        source_name=source,
    )

    entity_map_path = entities_dir / 'entity_map.parquet'
    if not entity_map_path.exists():
        return {
            'output_dir': str(output_dir),
            'entities_dir': str(entities_dir),
            'relations_dir': str(relations_dir),
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
    archive_path = build_resource_archive(output_dir, source)

    metadata = {
        'output_dir': str(output_dir),
        'entities_dir': str(entities_dir),
        'relations_dir': str(relations_dir),
        'download_archive_path': str(archive_path),
        'entity_summary': entity_summary,
        'relation_summary': relation_summary,
    }
    _write_json_atomic(success_path, metadata)
    return metadata
