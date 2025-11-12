"""DuckDB-based entity identifier builder for the new gold schema.

This provides an alternative to the Polars-based implementation using DuckDB's
SQL engine for efficient processing of large datasets.

Key features:
1. Native Parquet reading with automatic schema inference
2. SQL-based transformations for clarity and performance
3. Automatic query optimization by DuckDB
4. Streaming aggregations for memory efficiency
5. Direct Parquet output with compression
6. Optional incremental processing
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from dataclasses import dataclass

import duckdb
import polars as pl

from omnipath_build.utils.cv_term_enums import IdentifierNamespaceCv

__all__ = [
    'MERGE_SAFE_IDENTIFIER_TYPES',
    'build_entity_identifiers_duckdb',
]

# Identifier types that are safe to merge across sources.
MERGE_SAFE_IDENTIFIER_TYPES: frozenset[str] = frozenset(
    {
        IdentifierNamespaceCv.UNIPROT.value,
        IdentifierNamespaceCv.STANDARD_INCHI.value,
        IdentifierNamespaceCv.STANDARD_INCHI_KEY.value,
    }
)


def _create_merge_safe_list(merge_safe_types: frozenset[str]) -> str:
    """Create SQL list literal for merge-safe identifier types."""
    quoted = [f"'{t}'" for t in merge_safe_types]
    return f"({', '.join(quoted)})"


def _hash64_identifier(type_id: int, identifier: str) -> int:
    """Generate deterministic 64-bit hash for (type_id, identifier) pair."""
    combined = f'{type_id}:{identifier}'
    digest = hashlib.sha256(combined.encode()).digest()
    return int.from_bytes(digest[:8], byteorder='big', signed=True)


def build_entity_identifiers_duckdb(
    data_root: Path,
    output_dir: Path,
    cv_terms: pl.DataFrame | None = None,
    sources: pl.DataFrame | None = None,
    merge_safe_types: frozenset[str] = MERGE_SAFE_IDENTIFIER_TYPES,
    persist: bool = True,
    include_provenance: bool = True,
    compression: str = 'zstd',
    memory_limit: str = '4GB',
) -> tuple[int, int]:
    """Build entity identifier tables using DuckDB.

    Args:
        data_root: Directory containing silver parquet files.
        output_dir: Directory where output parquet files will be written.
        cv_terms: Optional pre-loaded cv_term table. Loaded from output_dir if None.
        sources: Optional pre-loaded source table. Loaded from output_dir if None.
        merge_safe_types: Identifier namespaces considered merge-safe.
        persist: When True, write preliminary and final tables to output_dir.
        include_provenance: When True, include origins/source_names; else keep only source_ids.
        compression: Parquet compression codec ("zstd", "snappy", or "uncompressed").
        memory_limit: DuckDB memory limit (e.g., "4GB", "8GB").

    Returns:
        Tuple of (preliminary_row_count, entity_identifier_row_count).
    """
    data_root = Path(data_root)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Initialize DuckDB connection with memory limit
    con = duckdb.connect()
    con.execute(f"SET memory_limit='{memory_limit}'")

    # Set compression for all output
    compression_map = {
        'zstd': 'ZSTD',
        'snappy': 'SNAPPY',
        'uncompressed': 'UNCOMPRESSED',
    }
    duckdb_compression = compression_map.get(compression, 'ZSTD')

    # Load lookup tables
    print('\nStep 0: Loading lookup tables...')
    if cv_terms is None:
        cv_term_path = output_dir / 'cv_term.parquet'
        if not cv_term_path.exists():
            raise FileNotFoundError(f'cv_term table not found at {cv_term_path}')
        con.execute(f"CREATE TABLE cv_term AS SELECT * FROM '{cv_term_path}'")
    else:
        con.execute("CREATE TABLE cv_term AS SELECT * FROM cv_terms")

    if sources is None:
        source_path = output_dir / 'source.parquet'
        if not source_path.exists():
            raise FileNotFoundError(f'source table not found at {source_path}')
        con.execute(f"CREATE TABLE source AS SELECT * FROM '{source_path}'")
    else:
        con.execute("CREATE TABLE source AS SELECT * FROM sources")

    cv_count = con.execute("SELECT COUNT(*) FROM cv_term").fetchone()[0]
    source_count = con.execute("SELECT COUNT(*) FROM source").fetchone()[0]
    print(f'  Loaded {cv_count:,} cv_terms, {source_count:,} sources')

    # Stage 1: Extract identifiers from entity files
    print('\nStep 1: Extracting identifiers from silver entity files...')

    # Find all parquet files
    entity_pattern = str(data_root / '*' / '*.parquet')

    con.execute(f"""
        CREATE OR REPLACE TABLE preliminary_raw AS
        WITH unnested AS (
            SELECT
                filename,
                t.source,
                UNNEST(t.identifiers) AS id_struct
            FROM read_parquet('{entity_pattern}', filename=true, union_by_name=true) AS t
            WHERE t.source IS NOT NULL
        )
        SELECT
            filename AS source_file,
            source AS source_name,
            id_struct.type AS type_accession,
            TRIM(id_struct.value) AS identifier,
            CONCAT(
                regexp_extract(filename, '([^/]+)/[^/]+\\.parquet$', 1),
                '/',
                regexp_extract(filename, '([^/]+)\\.parquet$', 1),
                ':entity'
            ) AS origin
        FROM unnested
        WHERE id_struct.value IS NOT NULL
          AND id_struct.type IS NOT NULL
          AND TRIM(id_struct.value) != ''
    """)

    # Also extract from entity_a and entity_b in interaction files if they exist
    print('  Extracting identifiers from interaction files...')

    con.execute(f"""
        CREATE OR REPLACE TABLE preliminary_interactions AS
        WITH unnested_a AS (
            SELECT
                filename,
                COALESCE(t.entity_a.source, t.source) AS source_name,
                UNNEST(COALESCE(t.entity_a.identifiers, [])) AS id_struct
            FROM read_parquet('{entity_pattern}', filename=true, union_by_name=true) AS t
            WHERE COALESCE(t.entity_a.source, t.source) IS NOT NULL
        ),
        unnested_b AS (
            SELECT
                filename,
                COALESCE(t.entity_b.source, t.source) AS source_name,
                UNNEST(COALESCE(t.entity_b.identifiers, [])) AS id_struct
            FROM read_parquet('{entity_pattern}', filename=true, union_by_name=true) AS t
            WHERE COALESCE(t.entity_b.source, t.source) IS NOT NULL
        )
        SELECT
            filename AS source_file,
            source_name,
            id_struct.type AS type_accession,
            TRIM(id_struct.value) AS identifier,
            CONCAT(
                regexp_extract(filename, '([^/]+)/[^/]+\\.parquet$', 1),
                '/',
                regexp_extract(filename, '([^/]+)\\.parquet$', 1),
                ':entity_a'
            ) AS origin
        FROM unnested_a
        WHERE id_struct.value IS NOT NULL
          AND id_struct.type IS NOT NULL
          AND TRIM(id_struct.value) != ''

        UNION ALL

        SELECT
            filename AS source_file,
            source_name,
            id_struct.type AS type_accession,
            TRIM(id_struct.value) AS identifier,
            CONCAT(
                regexp_extract(filename, '([^/]+)/[^/]+\\.parquet$', 1),
                '/',
                regexp_extract(filename, '([^/]+)\\.parquet$', 1),
                ':entity_b'
            ) AS origin
        FROM unnested_b
        WHERE id_struct.value IS NOT NULL
          AND id_struct.type IS NOT NULL
          AND TRIM(id_struct.value) != ''
    """)

    # Combine all preliminary data
    con.execute("""
        CREATE OR REPLACE TABLE preliminary_combined AS
        SELECT * FROM preliminary_raw
        UNION ALL
        SELECT * FROM preliminary_interactions
    """)

    # Join with lookup tables early to get IDs
    print('  Resolving foreign keys...')

    if include_provenance:
        prelim_select_cols = [
            'p.source_file',
            'p.source_name',
            'p.type_accession',
            'p.identifier',
            'p.origin',
            'cv.id AS type_id',
            's.id AS source_id',
        ]
    else:
        prelim_select_cols = [
            'p.type_accession',
            'p.identifier',
            'cv.id AS type_id',
            's.id AS source_id',
        ]

    con.execute(f"""
        CREATE OR REPLACE TABLE preliminary AS
        SELECT
            {', '.join(prelim_select_cols)}
        FROM preliminary_combined p
        LEFT JOIN cv_term cv ON p.type_accession = cv.accession
        LEFT JOIN source s ON p.source_name = s.name
        WHERE cv.id IS NOT NULL AND s.id IS NOT NULL
    """)

    preliminary_count = con.execute("SELECT COUNT(*) FROM preliminary").fetchone()[0]
    print(f'  Collected {preliminary_count:,} identifier instances')

    # Save preliminary table if requested
    if persist:
        prelim_path = output_dir / 'entity_identifier_preliminary.parquet'
        print(f'  Writing preliminary → {prelim_path}')
        con.execute(f"""
            COPY preliminary TO '{prelim_path}'
            (FORMAT PARQUET, COMPRESSION {duckdb_compression})
        """)

    # Stage 2: Deduplicate and aggregate
    print('\nStep 2: Deduplicating identifiers...')

    merge_safe_sql = _create_merge_safe_list(merge_safe_types)

    agg_columns = ['type_id', 'identifier', 'type_accession', 'is_merge_safe',
                   'occurrences', 'source_ids']
    if include_provenance:
        agg_columns.append('source_names')

    con.execute(f"""
        CREATE OR REPLACE TABLE entity_identifier_preagg AS
        SELECT
            type_id,
            identifier,
            type_accession,
            COUNT(*) AS occurrences,
            LIST(DISTINCT source_id ORDER BY source_id) AS source_ids,
            {'LIST(DISTINCT source_name ORDER BY source_name) AS source_names,' if include_provenance else ''}
            type_accession IN {merge_safe_sql} AS is_merge_safe
        FROM preliminary
        GROUP BY type_id, type_accession, identifier
    """)

    # Register Python UDF for deterministic ID generation
    con.create_function('hash64_identifier', _hash64_identifier, [duckdb.typing.BIGINT, duckdb.typing.VARCHAR], duckdb.typing.BIGINT)

    # Generate deterministic IDs and create final table
    final_select = [
        'hash64_identifier(type_id, identifier) AS id',
        'CAST(NULL AS BIGINT) AS entity_id',
        'identifier',
        'type_id',
        'type_accession',
        'is_merge_safe',
        'occurrences',
        'source_ids',
    ]
    if include_provenance:
        final_select.append('source_names')

    con.execute(f"""
        CREATE OR REPLACE TABLE entity_identifier AS
        SELECT
            {', '.join(final_select)}
        FROM entity_identifier_preagg
        ORDER BY type_accession, identifier
    """)

    entity_identifier_count = con.execute("SELECT COUNT(*) FROM entity_identifier").fetchone()[0]
    print(f'  Deduplicated to {entity_identifier_count:,} unique identifiers')

    # Save final table if requested
    if persist:
        final_path = output_dir / 'entity_identifier.parquet'
        print(f'  Writing final → {final_path}')
        con.execute(f"""
            COPY entity_identifier TO '{final_path}'
            (FORMAT PARQUET, COMPRESSION {duckdb_compression})
        """)

    # Display summary statistics
    print('\nStep 3: Summary statistics...')

    stats = con.execute("""
        SELECT
            type_accession,
            COUNT(*) AS unique_identifiers,
            SUM(occurrences) AS total_occurrences,
            COUNT(CASE WHEN is_merge_safe THEN 1 END) AS merge_safe_count
        FROM entity_identifier
        GROUP BY type_accession
        ORDER BY unique_identifiers DESC
        LIMIT 20
    """).fetchall()

    print('\n  Top identifier types:')
    print('  ' + '-' * 80)
    print(f'  {"Type":<30} {"Unique":<15} {"Total Occurrences":<20} {"Merge Safe":<15}')
    print('  ' + '-' * 80)
    for type_acc, unique, total, merge_safe in stats:
        print(f'  {type_acc:<30} {unique:<15,} {total:<20,} {merge_safe:<15,}')

    print(f'\n✓ Successfully built entity_identifier table with {entity_identifier_count:,} unique identifiers')

    con.close()

    return preliminary_count, entity_identifier_count


def validate_entity_identifiers(
    output_dir: Path,
    check_foreign_keys: bool = True,
) -> dict[str, bool]:
    """Validate entity_identifier table integrity using DuckDB.

    Args:
        output_dir: Directory containing the parquet files.
        check_foreign_keys: When True, verify foreign key relationships.

    Returns:
        Dictionary of validation checks and their results.
    """
    output_dir = Path(output_dir)
    con = duckdb.connect()

    results = {}

    # Load tables
    entity_id_path = output_dir / 'entity_identifier.parquet'
    if not entity_id_path.exists():
        raise FileNotFoundError(f'entity_identifier table not found at {entity_id_path}')

    con.execute(f"CREATE TABLE entity_identifier AS SELECT * FROM '{entity_id_path}'")

    # Check 1: No null identifiers
    null_count = con.execute("""
        SELECT COUNT(*) FROM entity_identifier
        WHERE identifier IS NULL OR TRIM(identifier) = ''
    """).fetchone()[0]
    results['no_null_identifiers'] = (null_count == 0)
    print(f'  No null identifiers: {"✓" if results["no_null_identifiers"] else "✗"} ({null_count} nulls)')

    # Check 2: No null type_ids
    null_type_count = con.execute("""
        SELECT COUNT(*) FROM entity_identifier WHERE type_id IS NULL
    """).fetchone()[0]
    results['no_null_type_ids'] = (null_type_count == 0)
    print(f'  No null type_ids: {"✓" if results["no_null_type_ids"] else "✗"} ({null_type_count} nulls)')

    # Check 3: Unique (type_id, identifier) pairs
    duplicate_count = con.execute("""
        SELECT COUNT(*) FROM (
            SELECT type_id, identifier, COUNT(*) as cnt
            FROM entity_identifier
            GROUP BY type_id, identifier
            HAVING cnt > 1
        )
    """).fetchone()[0]
    results['unique_type_identifier_pairs'] = (duplicate_count == 0)
    print(f'  Unique (type_id, identifier) pairs: {"✓" if results["unique_type_identifier_pairs"] else "✗"} ({duplicate_count} duplicates)')

    # Check 4: ID determinism - regenerate IDs and compare
    con.create_function('hash64_identifier', _hash64_identifier, [duckdb.typing.BIGINT, duckdb.typing.VARCHAR], duckdb.typing.BIGINT)
    mismatch_count = con.execute("""
        SELECT COUNT(*) FROM entity_identifier
        WHERE id != hash64_identifier(type_id, identifier)
    """).fetchone()[0]
    results['deterministic_ids'] = (mismatch_count == 0)
    print(f'  Deterministic IDs: {"✓" if results["deterministic_ids"] else "✗"} ({mismatch_count} mismatches)')

    if check_foreign_keys:
        # Check 5: All type_ids exist in cv_term
        cv_term_path = output_dir / 'cv_term.parquet'
        if cv_term_path.exists():
            con.execute(f"CREATE TABLE cv_term AS SELECT * FROM '{cv_term_path}'")
            missing_types = con.execute("""
                SELECT COUNT(DISTINCT type_id) FROM entity_identifier
                WHERE type_id NOT IN (SELECT id FROM cv_term)
            """).fetchone()[0]
            results['valid_type_foreign_keys'] = (missing_types == 0)
            print(f'  Valid type_id foreign keys: {"✓" if results["valid_type_foreign_keys"] else "✗"} ({missing_types} missing)')

        # Check 6: All source_ids exist in source table
        source_path = output_dir / 'source.parquet'
        if source_path.exists():
            con.execute(f"CREATE TABLE source AS SELECT * FROM '{source_path}'")
            missing_sources = con.execute("""
                SELECT COUNT(*) FROM (
                    SELECT UNNEST(source_ids) AS source_id
                    FROM entity_identifier
                    WHERE source_ids IS NOT NULL
                ) sub
                WHERE source_id NOT IN (SELECT id FROM source)
            """).fetchone()[0]
            results['valid_source_foreign_keys'] = (missing_sources == 0)
            print(f'  Valid source_id foreign keys: {"✓" if results["valid_source_foreign_keys"] else "✗"} ({missing_sources} missing)')

    con.close()

    all_passed = all(results.values())
    print(f'\n{"✓ All validation checks passed" if all_passed else "✗ Some validation checks failed"}')

    return results


if __name__ == '__main__':  # pragma: no cover - convenience entry point
    import argparse

    parser = argparse.ArgumentParser(description='Build entity identifier tables using DuckDB.')
    parser.add_argument('--data-root', type=Path, required=True, help='Directory with silver parquet files.')
    parser.add_argument('--output-dir', type=Path, required=True, help='Output directory for parquet tables.')
    parser.add_argument(
        '--lightweight-provenance',
        action='store_true',
        help='Skip origins/source_names; keep only compact source_ids.',
    )
    parser.add_argument(
        '--compression',
        choices=['zstd', 'snappy', 'uncompressed'],
        default='zstd',
        help='Parquet compression codec (default: zstd).',
    )
    parser.add_argument(
        '--memory-limit',
        default='4GB',
        help='DuckDB memory limit (default: 4GB).',
    )
    parser.add_argument(
        '--validate',
        action='store_true',
        help='Run validation checks after building.',
    )

    args = parser.parse_args()

    prelim_count, final_count = build_entity_identifiers_duckdb(
        args.data_root,
        args.output_dir,
        include_provenance=not args.lightweight_provenance,
        compression=args.compression,
        memory_limit=args.memory_limit,
    )

    if args.validate:
        print('\n' + '=' * 80)
        print('Running validation checks...')
        print('=' * 80)
        validate_entity_identifiers(args.output_dir)
