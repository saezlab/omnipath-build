from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import json
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Any

import duckdb

from omnipath_build.gold.build_entities import GoldPartitionConfig
from omnipath_build.pipeline.resource_archives import build_resource_archive
from omnipath_build.rewrite.bronze import source_state_path
from omnipath_build.rewrite.gold_direct import build_gold_source_duckdb


GOLD_TABLES = (
    'entity',
    'entity_evidence',
    'entity_map',
    'entity_occurrence_map',
    'entity_relation',
    'entity_relation_evidence',
)
@dataclass(frozen=True)
class GoldRewriteResult:
    source: str
    source_state_path: Path
    archive_path: Path
    archive_version: str
    archive_written: bool
    gold_changed: bool
    rows_by_table: dict[str, int]


def materialize_gold_duckdb(
    *,
    source: str,
    data_root: str | Path = 'data_rewrite',
    mapping_dir: str | Path = 'id_resolver/data',
    partition_config: GoldPartitionConfig | None = None,
) -> GoldRewriteResult:
    """Build source-gold from rewrite silver and import it into source DuckDB state."""
    data_root = Path(data_root)
    state_path = source_state_path(data_root, source)
    if not state_path.exists():
        raise FileNotFoundError(f'rewrite source state does not exist: {state_path}')

    con = duckdb.connect(str(state_path))
    try:
        con.execute('begin transaction')
        try:
            build_result = build_gold_source_duckdb(
                con,
                source=source,
                mapping_dir=mapping_dir,
                cfg=partition_config or GoldPartitionConfig(),
            )
            con.execute('commit')
        except Exception:
            con.execute('rollback')
            raise
    finally:
        con.close()

    if build_result.changed:
        archive_path, archive_version, archive_written = _export_gold_archive(
            state_path=state_path,
            data_root=data_root,
            source=source,
        )
    else:
        archive_path, archive_version, archive_written = _latest_archive_pointer(
            data_root=data_root,
            source=source,
        )

    return GoldRewriteResult(
        source=source,
        source_state_path=state_path,
        archive_path=archive_path,
        archive_version=archive_version,
        archive_written=archive_written,
        gold_changed=build_result.changed,
        rows_by_table=build_result.rows_by_table,
    )

def _export_gold_archive(
    *,
    state_path: Path,
    data_root: Path,
    source: str,
) -> tuple[Path, str, bool]:
    version = datetime.now(UTC).strftime('%Y%m%dT%H%M%S%fZ')

    with tempfile.TemporaryDirectory(prefix=f'op-rewrite-gold-archive-{source}-') as tmp_name:
        temp_root = Path(tmp_name)
        temp_source_dir = temp_root / source
        _export_archive_input_tables(
            state_path=state_path,
            source_dir=temp_source_dir,
        )
        temp_archive_path = build_resource_archive(temp_source_dir, source)
        content_hash = _sha256_zip_contents(temp_archive_path)
        latest_path = data_root / 'artifacts' / 'gold' / source / 'latest.json'
        latest = _read_json(latest_path)
        if (
            latest is not None
            and latest.get('content_hash') == content_hash
            and latest.get('archive_path')
            and Path(str(latest['archive_path'])).exists()
        ):
            return Path(str(latest['archive_path'])), str(latest['version']), False

        artifact_dir = data_root / 'artifacts' / 'gold' / source / version
        artifact_dir.mkdir(parents=True, exist_ok=True)
        archive_path = artifact_dir / f'{source}.zip'
        shutil.copy2(temp_archive_path, archive_path)

    manifest = {
        'layer': 'gold',
        'kind': 'source_archive',
        'source': source,
        'version': version,
        'created_at': datetime.now(UTC).isoformat(),
        'archive_path': str(archive_path),
        'content_hash': content_hash,
        'source_state_path': str(state_path),
        'tables': [
            'gold_entity',
            'gold_entity_relation',
            'gold_entity_relation_evidence',
        ],
    }
    _write_json(artifact_dir / 'manifest.json', manifest)
    latest_data = {
        'source': source,
        'version': version,
        'archive_path': str(archive_path),
        'manifest_path': str(artifact_dir / 'manifest.json'),
        'content_hash': content_hash,
        'updated_at': datetime.now(UTC).isoformat(),
    }
    _write_json(latest_path, latest_data)
    return archive_path, version, True


def _latest_archive_pointer(*, data_root: Path, source: str) -> tuple[Path, str, bool]:
    latest_path = data_root / 'artifacts' / 'gold' / source / 'latest.json'
    latest = _read_json(latest_path)
    if (
        latest is None
        or not latest.get('archive_path')
        or not latest.get('version')
        or not Path(str(latest['archive_path'])).exists()
    ):
        return _export_gold_archive(
            state_path=source_state_path(data_root, source),
            data_root=data_root,
            source=source,
        )
    return Path(str(latest['archive_path'])), str(latest['version']), False


def _export_archive_input_tables(
    *,
    state_path: Path,
    source_dir: Path,
) -> None:
    tables = {
        'gold_entity': source_dir / 'entities' / 'entity',
        'gold_entity_relation': source_dir / 'relations' / 'entity_relation',
        'gold_entity_relation_evidence': source_dir / 'relations' / 'entity_relation_evidence',
    }
    con = duckdb.connect(str(state_path), read_only=True)
    try:
        for table, output_dir in tables.items():
            output_dir.mkdir(parents=True, exist_ok=True)
            con.execute(
                f"""
                copy (select * from {_quote_identifier(table)})
                to '{_sql_path(output_dir / 'part=00000.parquet')}'
                (format parquet, compression zstd)
                """
            )
    finally:
        con.close()

def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _sql_path(path: Path) -> str:
    return str(path).replace("'", "''")


def _sha256_zip_contents(path: Path) -> str:
    digest = hashlib.sha256()
    with zipfile.ZipFile(path) as archive:
        for name in sorted(archive.namelist()):
            digest.update(name.encode('utf-8'))
            digest.update(b'\0')
            with archive.open(name) as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b''):
                    digest.update(chunk)
            digest.update(b'\0')
    return digest.hexdigest()


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + '.tmp')
    tmp_path.write_text(json.dumps(data, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    tmp_path.replace(path)


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None
