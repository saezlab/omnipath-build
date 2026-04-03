from __future__ import annotations

import hashlib
import importlib
import json
import shutil
import tempfile
from pathlib import Path
from typing import Any

from id_resolver.build.mapping_tables import (
    CHEMICAL_SOURCES,
    run_sources as materialize_resolver_tables,
)
from id_resolver.resolve.target_schema import normalize_target_schema_dir
from omnipath_build.loaders.silver import run_silver_loader
from scripts.silver_to_target_schema import SourceConverter
from scripts.target_schema_entity_dedup import deduplicate_target_schema_dir

REFERENCE_MAPPING_SOURCES = ['uniprot', *CHEMICAL_SOURCES]


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def tree_sha256(path: Path) -> str:
    files = [p for p in sorted(path.rglob('*')) if p.is_file()]
    payload = [
        {
            'path': str(file.relative_to(path)),
            'sha256': file_sha256(file),
        }
        for file in files
    ]
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(',', ':')).encode('utf-8')
    ).hexdigest()


def module_file_hash(module_name: str) -> str:
    module = importlib.import_module(module_name)
    module_file = getattr(module, '__file__', None)
    if not module_file:
        raise FileNotFoundError(f'No __file__ for module {module_name}')
    return file_sha256(Path(module_file))


def resolver_mappings_ready(mapping_dir: Path) -> bool:
    required = [
        mapping_dir / 'proteins' / 'protein_reference_to_uniprot.parquet',
        mapping_dir / 'proteins' / 'uniprot_secondary_to_primary.parquet',
        mapping_dir / 'chemicals' / 'chebi.parquet',
        mapping_dir / 'chemicals' / 'hmdb.parquet',
        mapping_dir / 'chemicals' / 'lipidmaps.parquet',
        mapping_dir / 'chemicals' / 'swisslipids.parquet',
    ]
    return all(path.exists() for path in required)


def build_resolver_mappings(output_dir: Path) -> dict[str, int]:
    output_dir.mkdir(parents=True, exist_ok=True)
    return materialize_resolver_tables(
        sources=REFERENCE_MAPPING_SOURCES,
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
    with tempfile.TemporaryDirectory(prefix='op-gold-pipeline-silver-') as tmp:
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

    return {
        'files': sorted(p.name for p in output_dir.iterdir() if p.is_file()),
        'functions': [f.function_name for f in (selected_functions or [])],
        'outputs': [str(output) for output in (outputs or []) if output is not None],
    }


def build_gold_source(
    *,
    source: str,
    silver_dir: Path,
    output_dir: Path,
    mapping_dir: Path,
    batch_size: int,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    converter = SourceConverter(
        source=source,
        silver_dir=silver_dir,
        output_dir=output_dir,
        batch_size=batch_size,
    )
    try:
        converter.convert()
    finally:
        converter.close()

    dedup_summary = deduplicate_target_schema_dir(output_dir)
    canonicalize_summary = normalize_target_schema_dir(
        source_dir=output_dir,
        mapping_dir=mapping_dir,
        source_name=source,
    )
    return {
        'files': sorted(p.name for p in output_dir.iterdir() if p.is_file()),
        'dedup_summary': dedup_summary,
        'canonicalize_summary': canonicalize_summary,
    }
