from __future__ import annotations

import importlib
from dataclasses import fields, is_dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import duckdb
import pyarrow.parquet as pq

from omnipath_build.gold.utils.cv_terms import format_cv_term
from omnipath_build.gold.utils.schema import CV_TERM_ENTITY_TYPE
from omnipath_build.silver.build import discover_resources
from pypath.inputs_v2.base import Resource


ONTOLOGY_ENTITY_TYPE_LABEL = format_cv_term(CV_TERM_ENTITY_TYPE)


def _iso_utc(timestamp: float | None) -> str | None:
    if timestamp is None:
        return None
    return datetime.fromtimestamp(timestamp, tz=UTC).isoformat().replace('+00:00', 'Z')


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _pypath_data_root() -> Path:
    return _project_root() / 'pypath-data'


def _parquet_rows(path: Path) -> int:
    if path.is_dir():
        return sum(
            int(pq.ParquetFile(file_path).metadata.num_rows)
            for file_path in _parquet_files(path)
        )
    return int(pq.ParquetFile(path).metadata.num_rows)


def _parquet_files(path: Path) -> list[Path]:
    if path.is_file():
        return [path]
    if path.is_dir():
        return sorted(child for child in path.rglob('*.parquet') if child.is_file())
    return []


def _resolve_parquet_table_path(*candidates: Path) -> Path | None:
    expanded: list[Path] = []
    seen: set[Path] = set()
    for candidate in candidates:
        variants = (
            [candidate.with_suffix(''), candidate]
            if candidate.suffix == '.parquet' else
            [candidate, candidate.with_suffix('.parquet')]
        )
        for variant in variants:
            if variant not in seen:
                expanded.append(variant)
                seen.add(variant)

    for candidate in expanded:
        if candidate.is_dir() and _parquet_files(candidate):
            return candidate
    for candidate in expanded:
        if candidate.is_file():
            return candidate
    return None


def _gold_table_path(source_dir: Path | None, group_name: str, table_name: str) -> Path | None:
    if source_dir is None:
        return None
    parent_dir = source_dir / group_name
    return _resolve_parquet_table_path(
        parent_dir / table_name,
        parent_dir / f'{table_name}.parquet',
    )


def _collect_subfolders(obj: Any, seen: set[int] | None = None) -> set[str]:
    if obj is None:
        return set()

    if seen is None:
        seen = set()

    obj_id = id(obj)
    if obj_id in seen:
        return set()
    seen.add(obj_id)

    if isinstance(obj, (str, bytes, int, float, bool, Path)):
        return set()

    subfolder = getattr(obj, 'subfolder', None)
    if isinstance(subfolder, str) and subfolder:
        return {subfolder}

    if isinstance(obj, dict):
        result: set[str] = set()
        for value in obj.values():
            result.update(_collect_subfolders(value, seen))
        return result

    if isinstance(obj, (list, tuple, set)):
        result: set[str] = set()
        for value in obj:
            result.update(_collect_subfolders(value, seen))
        return result

    if is_dataclass(obj):
        result: set[str] = set()
        for field in fields(obj):
            result.update(_collect_subfolders(getattr(obj, field.name), seen))
        return result

    if hasattr(obj, '__dict__'):
        result: set[str] = set()
        for value in vars(obj).values():
            result.update(_collect_subfolders(value, seen))
        return result

    return set()


def _latest_file_mtime(paths: list[Path]) -> float | None:
    mtimes = [path.stat().st_mtime for path in paths if path.exists() and path.is_file()]
    return max(mtimes) if mtimes else None


def _resource_download_mtime(resource: Resource) -> str | None:
    data_root = _pypath_data_root()
    subfolders: set[str] = set()
    for dataset in resource.datasets().values():
        subfolders.update(_collect_subfolders(getattr(dataset, 'download', None)))

    files: list[Path] = []
    for subfolder in sorted(subfolders):
        folder = data_root / subfolder
        if folder.exists():
            files.extend(path for path in folder.rglob('*') if path.is_file())

    return _iso_utc(_latest_file_mtime(files))


def _resource_categories(
    *,
    interaction_count: int,
    association_count: int,
    ontology_term_count: int,
) -> list[str]:
    categories: list[str] = []

    if interaction_count > 0:
        categories.append('interaction')
    if association_count > 0 or ontology_term_count > 0:
        categories.append('association')

    return categories


def _source_gold_dir(gold_root: Path, source: str) -> Path | None:
    path = gold_root / source
    return path if path.exists() and path.is_dir() else None


def _count_file(source_dir: Path | None, relative_path: str) -> int:
    if source_dir is None:
        return 0
    path = source_dir / relative_path
    if not path.exists():
        return 0
    return _parquet_rows(path)


def _count_table(source_dir: Path | None, group_name: str, table_name: str) -> int:
    path = _gold_table_path(source_dir, group_name, table_name)
    if path is None:
        return 0
    return _parquet_rows(path)


def _identifier_count(source_dir: Path | None) -> int:
    path = _gold_table_path(source_dir, 'entities', 'entity')
    if path is None:
        return 0
    con = duckdb.connect()
    try:
        relation_sql = _read_dataset_sql(path)
        if 'identifiers' not in _relation_columns(con, relation_sql):
            return _table_count_sql(con, relation_sql)
        return int(
            con.execute(
                f'''
                select count(*) + coalesce(sum(coalesce(list_count(identifiers), 0)), 0)
                from {relation_sql}
                '''
            ).fetchone()[0]
            or 0
        )
    finally:
        con.close()


def _ontology_entity_count(source_dir: Path | None) -> int:
    path = _gold_table_path(source_dir, 'entities', 'entity')
    if path is None:
        return 0
    con = duckdb.connect()
    try:
        relation_sql = _read_dataset_sql(path)
        if 'entity_type' not in _relation_columns(con, relation_sql):
            return 0
        return int(
            con.execute(
                f'''
                select count(*)
                from {relation_sql}
                where entity_type = ?
                ''',
                [ONTOLOGY_ENTITY_TYPE_LABEL],
            ).fetchone()[0]
            or 0
        )
    finally:
        con.close()


def _relation_category_counts(source_dir: Path | None) -> dict[str, int]:
    path = _gold_table_path(source_dir, 'relations', 'entity_relation')
    if path is None:
        return {}
    con = duckdb.connect()
    try:
        relation_sql = _read_dataset_sql(path)
        if 'relation_category' not in _relation_columns(con, relation_sql):
            return {}
        rows = con.execute(
            f'''
            select relation_category, count(*)::bigint
            from {relation_sql}
            group by relation_category
            '''
        ).fetchall()
        return {str(category): int(count) for category, count in rows}
    finally:
        con.close()


def _gold_files(source_dir: Path | None) -> list[Path]:
    if source_dir is None:
        return []
    return sorted(path for path in source_dir.rglob('*') if path.is_file())


def _ontology_labels(resource: Resource) -> list[str]:
    values = []
    for ontology in getattr(resource.config, 'annotation_ontologies', ()):
        label = getattr(ontology, 'definition', None) or str(ontology)
        values.append(str(label))
    return values


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _sql_path(path: Path) -> str:
    return str(path).replace("'", "''")


def _sql_literal(value: object) -> str:
    if value is None:
        return 'null'
    return "'" + str(value).replace("'", "''") + "'"


def _sql_string_list(values: list[str]) -> str:
    if not values:
        return '[]::varchar[]'
    return '[' + ', '.join(f'{_sql_literal(value)}::varchar' for value in values) + ']::varchar[]'


def _read_dataset_sql(path: Path) -> str:
    if path.is_dir():
        return f"read_parquet('{_sql_path(path / '**' / '*.parquet')}', union_by_name=true, hive_partitioning=false)"
    return f"read_parquet('{_sql_path(path)}', union_by_name=true)"


def _source_state_alias(source: str) -> str:
    cleaned = ''.join(ch if ch.isalnum() else '_' for ch in source.lower())
    return f'resource_source_state_{cleaned}'


def _attached_database_exists(con: duckdb.DuckDBPyConnection, alias: str) -> bool:
    rows = con.execute('select database_name from duckdb_databases()').fetchall()
    return alias in {str(row[0]) for row in rows}


def _attached_database_for_path(con: duckdb.DuckDBPyConnection, path: Path) -> str | None:
    target = str(path.resolve())
    rows = con.execute('select database_name, path from duckdb_databases() where path is not null').fetchall()
    for database_name, database_path in rows:
        if str(Path(str(database_path)).resolve()) == target:
            return str(database_name)
    return None


def _attach_source_state(
    con: duckdb.DuckDBPyConnection,
    *,
    source: str,
    state_path: Path,
) -> str:
    existing_alias = _attached_database_for_path(con, state_path)
    if existing_alias is not None:
        return _quote_identifier(existing_alias)
    alias = _source_state_alias(source)
    if not _attached_database_exists(con, alias):
        con.execute(
            f"attach '{_sql_path(state_path)}' as {_quote_identifier(alias)} (read_only)"
        )
    return _quote_identifier(alias)


def _relation_columns(con: duckdb.DuckDBPyConnection, relation_sql: str) -> set[str]:
    try:
        rows = con.execute(f'describe select * from {relation_sql} limit 0').fetchall()
        return {str(row[0]) for row in rows}
    except duckdb.Error:
        return set()


def _table_count_sql(con: duckdb.DuckDBPyConnection, relation_sql: str) -> int:
    if not _relation_columns(con, relation_sql):
        return 0
    return int(con.execute(f'select count(*) from {relation_sql}').fetchone()[0] or 0)


def _source_state_counts(
    con: duckdb.DuckDBPyConnection,
    *,
    source: str,
    state_path: Path,
) -> dict[str, int]:
    schema = _attach_source_state(con, source=source, state_path=state_path)
    entity_sql = f'{schema}.gold_entity'
    relation_sql = f'{schema}.gold_entity_relation'

    entity_columns = _relation_columns(con, entity_sql)
    relation_columns = _relation_columns(con, relation_sql)
    entity_count = _table_count_sql(con, entity_sql)
    identifier_count = entity_count
    ontology_term_count = 0
    interaction_count = 0
    association_count = 0

    if entity_columns:
        if 'identifiers' in entity_columns:
            identifier_count = int(
                con.execute(
                    f"""
                    select count(*) + coalesce(sum(coalesce(list_count(identifiers), 0)), 0)
                    from {entity_sql}
                    """
                ).fetchone()[0]
                or 0
            )
        if 'entity_type' in entity_columns:
            ontology_term_count = int(
                con.execute(
                    f"""
                    select count(*)
                    from {entity_sql}
                    where entity_type = ?
                    """,
                    [ONTOLOGY_ENTITY_TYPE_LABEL],
                ).fetchone()[0]
                or 0
            )

    if relation_columns and 'relation_category' in relation_columns:
        rows = con.execute(
            f"""
            select relation_category, count(*)::bigint
            from {relation_sql}
            group by relation_category
            """
        ).fetchall()
        relation_counts = {str(category): int(count) for category, count in rows}
        interaction_count = relation_counts.get('interaction', 0)
        association_count = relation_counts.get('association', 0)

    return {
        'entity_count': entity_count,
        'interaction_count': interaction_count,
        'association_count': association_count,
        'identifier_count': identifier_count,
        'ontology_term_count': ontology_term_count,
        'total_size_bytes': state_path.stat().st_size if state_path.exists() else 0,
        'last_built_at': _iso_utc(state_path.stat().st_mtime) if state_path.exists() else None,
    }


def _resource_metadata_row(source: str, resource: Resource) -> dict[str, object]:
    config = resource.config
    return {
        'resource_id': source,
        'resource_name': config.name,
        'description': config.description,
        'homepage_url': config.url,
        'license': format_cv_term(str(config.license)) or str(config.license),
        'pubmed_id': config.pubmed,
        'resource_kind': getattr(config, 'resource_kind', 'data_resource'),
        'annotation_ontologies': _ontology_labels(resource),
        'last_downloaded_at': _resource_download_mtime(resource),
    }


def _resources_values_sql(rows: list[dict[str, object]]) -> str:
    if not rows:
        return """
            select
                null::varchar as resource_id,
                null::varchar as resource_name,
                null::varchar as description,
                null::varchar as homepage_url,
                null::varchar as license,
                null::varchar as pubmed_id,
                null::varchar as resource_kind,
                []::varchar[] as categories,
                []::varchar[] as annotation_ontologies,
                null::bigint as entity_count,
                null::bigint as interaction_count,
                null::bigint as association_count,
                null::bigint as identifier_count,
                null::bigint as ontology_term_count,
                null::bigint as total_size_bytes,
                null::varchar as last_downloaded_at,
                null::varchar as last_built_at,
                null::varchar as build_status
            where false
        """

    selects = []
    for row in rows:
        categories = cast(list[str], row['categories'])
        annotation_ontologies = cast(list[str], row['annotation_ontologies'])
        selects.append(
            'select '
            f'{_sql_literal(row["resource_id"])}::varchar as resource_id, '
            f'{_sql_literal(row["resource_name"])}::varchar as resource_name, '
            f'{_sql_literal(row["description"])}::varchar as description, '
            f'{_sql_literal(row["homepage_url"])}::varchar as homepage_url, '
            f'{_sql_literal(row["license"])}::varchar as license, '
            f'{_sql_literal(row["pubmed_id"])}::varchar as pubmed_id, '
            f'{_sql_literal(row["resource_kind"])}::varchar as resource_kind, '
            f'{_sql_string_list(categories)} as categories, '
            f'{_sql_string_list(annotation_ontologies)} as annotation_ontologies, '
            f'{int(row["entity_count"])}::bigint as entity_count, '
            f'{int(row["interaction_count"])}::bigint as interaction_count, '
            f'{int(row["association_count"])}::bigint as association_count, '
            f'{int(row["identifier_count"])}::bigint as identifier_count, '
            f'{int(row["ontology_term_count"])}::bigint as ontology_term_count, '
            f'{int(row["total_size_bytes"])}::bigint as total_size_bytes, '
            f'{_sql_literal(row["last_downloaded_at"])}::varchar as last_downloaded_at, '
            f'{_sql_literal(row["last_built_at"])}::varchar as last_built_at, '
            f'{_sql_literal(row["build_status"])}::varchar as build_status'
        )
    return '\nunion all\n'.join(selects)


def build_resources_parquet_from_duckdb(
    *,
    con: duckdb.DuckDBPyConnection,
    source_state_paths: dict[str, str | Path],
    output_path: str | Path = 'data/combined/resources.parquet',
    inputs_package: str = 'pypath.inputs_v2',
) -> tuple[Path, int]:
    """Write resources.parquet using DuckDB source-gold state for row stats."""
    output_path = Path(output_path)
    discovered, _ = discover_resources(
        database_name='.',
        base_path=None,
        inputs_package=inputs_package,
    )

    state_paths = {source: Path(path) for source, path in source_state_paths.items()}
    rows: list[dict[str, object]] = []
    for source in sorted(discovered):
        try:
            module = importlib.import_module(f'{inputs_package}.{source}')
        except Exception as exc:  # noqa: BLE001
            print(
                f'[build_resources_parquet_from_duckdb] skipping {inputs_package}.{source}: '
                f'{exc.__class__.__name__}: {exc}'
            )
            continue

        resource = getattr(module, 'resource', None)
        if not isinstance(resource, Resource):
            continue

        counts = (
            _source_state_counts(con, source=source, state_path=state_paths[source])
            if source in state_paths and state_paths[source].exists()
            else {
                'entity_count': 0,
                'interaction_count': 0,
                'association_count': 0,
                'identifier_count': 0,
                'ontology_term_count': 0,
                'total_size_bytes': 0,
                'last_built_at': None,
            }
        )
        rows.append({
            **_resource_metadata_row(source, resource),
            **counts,
            'categories': _resource_categories(
                interaction_count=counts['interaction_count'],
                association_count=counts['association_count'],
                ontology_term_count=counts['ontology_term_count'],
            ),
            'build_status': 'success' if source in state_paths and state_paths[source].exists() else 'not_built',
        })

    output_path.parent.mkdir(parents=True, exist_ok=True)
    con.execute(
        f"""
        copy (
            select *
            from ({_resources_values_sql(rows)})
            order by resource_id
        )
        to '{_sql_path(output_path)}'
        (format parquet, compression zstd)
        """
    )
    return output_path, len(rows)


def _resource_row(*, source: str, resource: Resource, gold_root: Path) -> dict[str, Any]:
    config = resource.config
    source_dir = _source_gold_dir(gold_root, source)
    gold_files = _gold_files(source_dir)
    relation_category_counts = _relation_category_counts(source_dir)

    interaction_count = int(relation_category_counts.get('interaction', 0))
    association_count = int(relation_category_counts.get('association', 0))
    ontology_term_count = _ontology_entity_count(source_dir)

    return {
        'resource_id': source,
        'resource_name': config.name,
        'description': config.description,
        'homepage_url': config.url,
        'license': format_cv_term(str(config.license)) or str(config.license),
        'pubmed_id': config.pubmed,
        'resource_kind': getattr(config, 'resource_kind', 'data_resource'),
        'categories': _resource_categories(
            interaction_count=interaction_count,
            association_count=association_count,
            ontology_term_count=ontology_term_count,
        ),
        'annotation_ontologies': _ontology_labels(resource),
        'entity_count': _count_table(source_dir, 'entities', 'entity'),
        'interaction_count': interaction_count,
        'association_count': association_count,
        'identifier_count': _identifier_count(source_dir),
        'ontology_term_count': ontology_term_count,
        'total_size_bytes': sum(path.stat().st_size for path in gold_files),
        'last_downloaded_at': _resource_download_mtime(resource),
        'last_built_at': _iso_utc(_latest_file_mtime(gold_files)),
        'build_status': 'success' if gold_files else 'not_built',
    }


def build_resources_parquet(
    *,
    gold_root: str | Path = 'data/gold',
    output_path: str | Path = 'data/combined/resources.parquet',
    inputs_package: str = 'pypath.inputs_v2',
) -> Path:
    gold_root = Path(gold_root)
    output_path = Path(output_path)

    discovered, _ = discover_resources(
        database_name='.',
        base_path=None,
        inputs_package=inputs_package,
    )

    rows: list[dict[str, object]] = []
    for source in sorted(discovered):
        try:
            module = importlib.import_module(f'{inputs_package}.{source}')
        except Exception as exc:  # noqa: BLE001
            print(
                f'[build_resources_parquet] skipping {inputs_package}.{source}: '
                f'{exc.__class__.__name__}: {exc}'
            )
            continue

        resource = getattr(module, 'resource', None)
        if not isinstance(resource, Resource):
            continue

        rows.append(_resource_row(source=source, resource=resource, gold_root=gold_root))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    try:
        con.execute(
            f"""
            copy (
                select *
                from ({_resources_values_sql(rows)})
                order by resource_id
            )
            to '{_sql_path(output_path)}'
            (format parquet, compression zstd)
            """
        )
    finally:
        con.close()
    return output_path
