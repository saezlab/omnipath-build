from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb

from omnipath_build.rewrite.bronze import source_state_path
from omnipath_build.rewrite.gold_config import GoldPartitionConfig
from omnipath_build.rewrite.gold_direct import build_gold_source_duckdb


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
        temp_archive_path = temp_root / f'{source}.zip'
        _write_gold_archive_from_state(
            state_path=state_path,
            archive_path=temp_archive_path,
        )
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


def _write_gold_archive_from_state(
    *,
    state_path: Path,
    archive_path: Path,
) -> None:
    tables = {
        'gold_entity': ('entities/entity.parquet', '''
            select
                entity_pk as entity_id,
                canonical_identifier,
                canonical_identifier_type,
                identifiers,
                entity_type,
                taxonomy_id,
                entity_attributes,
                sources
            from gold_entity
            order by entity_key
        '''),
        'gold_entity_relation': ('relations/entity_relation.parquet', '''
            select
                relation_pk as relation_id,
                subject_entity_pk as subject_entity_id,
                predicate,
                object_entity_pk as object_entity_id,
                relation_category,
                evidence_count,
                sources
            from gold_entity_relation
            order by relation_key
        '''),
        'gold_entity_relation_evidence': ('relations/entity_relation_evidence.parquet', '''
            select
                relation_evidence_pk as relation_evidence_id,
                relation_pk as relation_id,
                source,
                record_attributes,
                subject_attributes,
                object_attributes,
                evidence
            from gold_entity_relation_evidence
            order by relation_pk, source, raw_record_id
        '''),
    }
    con = duckdb.connect(str(state_path), read_only=True)
    try:
        with tempfile.TemporaryDirectory(prefix='op-rewrite-gold-archive-tables-') as table_tmp_name:
            table_tmp = Path(table_tmp_name)
            archive_entries: list[tuple[Path, str]] = []
            for table, (archive_name, query) in tables.items():
                if not _table_exists(con, table):
                    continue
                output_path = table_tmp / archive_name
                output_path.parent.mkdir(parents=True, exist_ok=True)
                con.execute(
                    f"""
                    copy ({query})
                    to '{_sql_path(output_path)}'
                    (format parquet, compression zstd)
                    """
                )
                archive_entries.append((output_path, archive_name))
            if not archive_entries:
                raise ValueError(f'No gold tables available to archive in {state_path}')
            archive_path.parent.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(archive_path, mode='w', compression=zipfile.ZIP_DEFLATED) as zf:
                for source_path, archive_name in archive_entries:
                    zf.write(source_path, arcname=archive_name)
    finally:
        con.close()


def _table_exists(con: duckdb.DuckDBPyConnection, table: str) -> bool:
    return bool(
        con.execute(
            """
            select count(*)
            from information_schema.tables
            where table_schema = 'main'
              and table_name = ?
            """,
            [table],
        ).fetchone()[0]
    )


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
