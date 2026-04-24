from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Any

from id_resolver.build.mapping_tables import (
    CHEMICAL_SOURCES,
    run_sources as materialize_resolver_tables,
)
from omnipath_build.gold.canonicalize import normalize_target_schema_dir
from omnipath_build._archive.convert import SourceConverter
from omnipath_build._archive.dedup import deduplicate_target_schema_dir
from omnipath_build.silver.build import run_silver_loader

REFERENCE_MAPPING_SOURCES = ['uniprot', *CHEMICAL_SOURCES]
TEST_MODE_REFERENCE_MAPPING_SOURCES = [
    'uniprot',
    'chebi',
]

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

    copied_artifacts: list[str] = []
    for artifact in sorted(path for path in silver_dir.iterdir() if path.is_file() and path.suffix != '.parquet'):
        target = output_dir / artifact.name
        shutil.copy2(artifact, target)
        copied_artifacts.append(artifact.name)

    canonicalize_summary = normalize_target_schema_dir(
        source_dir=output_dir,
        mapping_dir=mapping_dir,
        source_name=source,
    )
    dedup_summary = deduplicate_target_schema_dir(output_dir)
    return {
        'files': sorted(p.name for p in output_dir.iterdir() if p.is_file()),
        'copied_artifacts': copied_artifacts,
        'dedup_summary': dedup_summary,
        'canonicalize_summary': canonicalize_summary,
    }
