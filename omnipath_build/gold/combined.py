from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import polars as pl

ENTITY_SCHEMA = {
    'entity_id': pl.String,
    'entity_id_type': pl.String,
    'entity_type': pl.String,
    'taxonomy_id': pl.String,
    'entity_attributes': pl.List(
        pl.Struct({
            'term': pl.String,
            'value': pl.String,
            'unit': pl.String,
        })
    ),
    'sources': pl.List(pl.String),
}

ENTITY_IDENTIFIERS_SCHEMA = {
    'entity_id': pl.String,
    'entity_id_type': pl.String,
    'identifier': pl.String,
    'identifier_type': pl.String,
    'is_canonical': pl.Boolean,
    'sources': pl.List(pl.String),
}

INTERACTION_EVIDENCE_SCHEMA = {
    'source': pl.String,
    'interaction_id': pl.Int64,
    'entity_a_id': pl.String,
    'entity_a_id_type': pl.String,
    'entity_b_id': pl.String,
    'entity_b_id_type': pl.String,
    'direction': pl.Int64,
    'sign': pl.Int64,
    'record_attributes': pl.List(
        pl.Struct({
            'term': pl.String,
            'value': pl.String,
            'unit': pl.String,
        })
    ),
    'entity_a_attributes': pl.List(
        pl.Struct({
            'term': pl.String,
            'value': pl.String,
            'unit': pl.String,
        })
    ),
    'entity_b_attributes': pl.List(
        pl.Struct({
            'term': pl.String,
            'value': pl.String,
            'unit': pl.String,
        })
    ),
    'evidence': pl.List(
        pl.Struct({
            'term': pl.String,
            'value': pl.String,
            'unit': pl.String,
        })
    ),
}

INTERACTION_SCHEMA = {
    'interaction_id': pl.String,
    'entity_a_id': pl.String,
    'entity_a_id_type': pl.String,
    'entity_b_id': pl.String,
    'entity_b_id_type': pl.String,
    'direction': pl.Int64,
    'sign': pl.Int64,
    'evidence_count': pl.Int64,
    'sources': pl.List(pl.String),
}

ASSOCIATION_EVIDENCE_SCHEMA = {
    'source': pl.String,
    'association_id': pl.Int64,
    'parent_entity_id': pl.String,
    'parent_entity_id_type': pl.String,
    'member_entity_id': pl.String,
    'member_entity_id_type': pl.String,
    'role_term_id': pl.String,
    'stoichiometry': pl.String,
    'record_attributes': pl.List(
        pl.Struct({
            'term': pl.String,
            'value': pl.String,
            'unit': pl.String,
        })
    ),
    'parent_attributes': pl.List(
        pl.Struct({
            'term': pl.String,
            'value': pl.String,
            'unit': pl.String,
        })
    ),
    'member_attributes': pl.List(
        pl.Struct({
            'term': pl.String,
            'value': pl.String,
            'unit': pl.String,
        })
    ),
    'evidence': pl.List(
        pl.Struct({
            'term': pl.String,
            'value': pl.String,
            'unit': pl.String,
        })
    ),
}

ASSOCIATION_SCHEMA = {
    'association_id': pl.String,
    'parent_entity_id': pl.String,
    'parent_entity_id_type': pl.String,
    'member_entity_id': pl.String,
    'member_entity_id_type': pl.String,
    'role_term_id': pl.String,
    'stoichiometry': pl.String,
    'sources': pl.List(pl.String),
}

ENTITY_ANNOTATION_SCHEMA = {
    'entity_id': pl.String,
    'entity_id_type': pl.String,
    'cv_term': pl.String,
    'sources': pl.List(pl.String),
}

INTERACTION_ANNOTATION_SCHEMA = {
    'interaction_id': pl.String,
    'cv_term': pl.String,
    'sources': pl.List(pl.String),
}

ARTIFACT_OUTPUTS = {
    'entity.parquet': ENTITY_SCHEMA,
    'entity_identifiers.parquet': ENTITY_IDENTIFIERS_SCHEMA,
    'interaction_evidence.parquet': INTERACTION_EVIDENCE_SCHEMA,
    'association_evidence.parquet': ASSOCIATION_EVIDENCE_SCHEMA,
    'interaction.parquet': INTERACTION_SCHEMA,
    'association.parquet': ASSOCIATION_SCHEMA,
    'entity_annotation.parquet': ENTITY_ANNOTATION_SCHEMA,
    'interaction_annotation.parquet': INTERACTION_ANNOTATION_SCHEMA,
}


@dataclass(frozen=True)
class GoldSourceDir:
    source: str
    version: str
    path: Path


def discover_gold_source_dirs(gold_root: str | Path) -> list[GoldSourceDir]:
    root = Path(gold_root)
    if not root.exists():
        raise FileNotFoundError(f'Gold root does not exist: {root}')

    latest: dict[str, GoldSourceDir] = {}
    for artifact_path in root.rglob('entity.parquet'):
        version_dir = artifact_path.parent
        if version_dir == root or not version_dir.name.isdigit():
            continue
        source_rel = version_dir.parent.relative_to(root)
        source = source_rel.as_posix().replace('/', '.')
        candidate = GoldSourceDir(source=source, version=version_dir.name, path=version_dir)
        current = latest.get(source)
        if current is None or int(candidate.version) > int(current.version):
            latest[source] = candidate

    return sorted(latest.values(), key=lambda item: item.source)


def build_combined_parquets(
    *,
    gold_root: str | Path = 'data_v2/gold',
    output_dir: str | Path = 'data_v2/combined',
) -> dict[str, Any]:
    gold_root = Path(gold_root)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    source_dirs = discover_gold_source_dirs(gold_root)

    outputs = {
        'entity.parquet': _build_entity(source_dirs),
        'entity_identifiers.parquet': _build_entity_identifiers(source_dirs),
        'interaction_evidence.parquet': _build_interaction_evidence(source_dirs),
        'association_evidence.parquet': _build_association_evidence(source_dirs),
        'interaction.parquet': _build_interaction(source_dirs),
        'association.parquet': _build_association(source_dirs),
        'entity_annotation.parquet': _build_entity_annotation(source_dirs),
        'interaction_annotation.parquet': _build_interaction_annotation(source_dirs),
    }

    for stale_name in (
        'entity_identifier.parquet',
        'entity_annotation_evidence.parquet',
        'interaction_annotation_evidence.parquet',
    ):
        stale_path = output_dir / stale_name
        if stale_path.exists():
            stale_path.unlink()

    for file_name, frame in outputs.items():
        _write_if_nonempty(frame, output_dir / file_name)

    summary = {
        'gold_root': str(gold_root),
        'output_dir': str(output_dir),
        'sources': [
            {
                'source': item.source,
                'version': item.version,
                'path': str(item.path),
            }
            for item in source_dirs
        ],
        'row_counts': {
            file_name: int(frame.height)
            for file_name, frame in outputs.items()
        },
    }
    (output_dir / 'combined_build_summary.json').write_text(
        json.dumps(summary, indent=2) + '\n',
        encoding='utf-8',
    )
    return summary


def _build_entity(source_dirs: list[GoldSourceDir]) -> pl.DataFrame:
    frames = _read_frames(source_dirs, 'entity.parquet', {
        'entity_id': pl.col('entity_id').cast(pl.String),
        'entity_id_type': pl.col('entity_id_type').cast(pl.String),
        'entity_type': pl.col('entity_type').cast(pl.String),
        'taxonomy_id': pl.col('taxonomy_id').cast(pl.String),
        'entity_attributes': pl.col('entity_attributes'),
        'sources': pl.col('sources'),
    })
    if not frames:
        return _empty_frame(ENTITY_SCHEMA)
    combined = pl.concat(frames, how='vertical_relaxed')
    return (
        combined
        .group_by(['entity_id', 'entity_id_type'])
        .agg([
            pl.col('entity_type').drop_nulls().first().alias('entity_type'),
            pl.col('taxonomy_id').drop_nulls().first().alias('taxonomy_id'),
            pl.col('entity_attributes').drop_nulls().first().alias('entity_attributes'),
            pl.col('sources').explode().drop_nulls().unique().sort().alias('sources'),
        ])
        .select(list(ENTITY_SCHEMA.keys()))
        .sort(['entity_id_type', 'entity_id'])
    )


def _build_entity_identifiers(source_dirs: list[GoldSourceDir]) -> pl.DataFrame:
    frames = _read_frames(source_dirs, 'entity_identifiers.parquet', {
        'entity_id': pl.col('entity_id').cast(pl.String),
        'entity_id_type': pl.col('entity_id_type').cast(pl.String),
        'identifier': pl.col('identifier').cast(pl.String),
        'identifier_type': pl.col('identifier_type').cast(pl.String),
        'is_canonical': pl.col('is_canonical').cast(pl.Boolean),
        'sources': pl.col('sources'),
    })
    if not frames:
        return _empty_frame(ENTITY_IDENTIFIERS_SCHEMA)
    combined = pl.concat(frames, how='vertical_relaxed')
    return (
        combined
        .group_by(['entity_id', 'entity_id_type', 'identifier', 'identifier_type'])
        .agg([
            pl.col('is_canonical').any().alias('is_canonical'),
            pl.col('sources').explode().drop_nulls().unique().sort().alias('sources'),
        ])
        .select(list(ENTITY_IDENTIFIERS_SCHEMA.keys()))
        .sort(['entity_id_type', 'entity_id', 'identifier_type', 'identifier'])
    )


def _build_interaction_evidence(source_dirs: list[GoldSourceDir]) -> pl.DataFrame:
    frames = _read_frames(source_dirs, 'interaction_evidence.parquet', {
        'source': pl.col('source').cast(pl.String),
        'interaction_id': pl.col('interaction_id').cast(pl.Int64),
        'entity_a_id': pl.col('entity_a_id').cast(pl.String),
        'entity_a_id_type': pl.col('entity_a_id_type').cast(pl.String),
        'entity_b_id': pl.col('entity_b_id').cast(pl.String),
        'entity_b_id_type': pl.col('entity_b_id_type').cast(pl.String),
        'direction': pl.col('direction').cast(pl.Int64, strict=False),
        'sign': pl.col('sign').cast(pl.Int64, strict=False),
        'record_attributes': pl.col('record_attributes'),
        'entity_a_attributes': pl.col('entity_a_attributes'),
        'entity_b_attributes': pl.col('entity_b_attributes'),
        'evidence': pl.col('evidence'),
    })
    if not frames:
        return _empty_frame(INTERACTION_EVIDENCE_SCHEMA)
    return (
        pl.concat(frames, how='vertical_relaxed')
        .unique()
        .select(list(INTERACTION_EVIDENCE_SCHEMA.keys()))
        .sort(['source', 'interaction_id'])
    )


def _build_association_evidence(source_dirs: list[GoldSourceDir]) -> pl.DataFrame:
    frames = _read_frames(source_dirs, 'association_evidence.parquet', {
        'source': pl.col('source').cast(pl.String),
        'association_id': pl.col('association_id').cast(pl.Int64),
        'parent_entity_id': pl.col('parent_entity_id').cast(pl.String),
        'parent_entity_id_type': pl.col('parent_entity_id_type').cast(pl.String),
        'member_entity_id': pl.col('member_entity_id').cast(pl.String),
        'member_entity_id_type': pl.col('member_entity_id_type').cast(pl.String),
        'role_term_id': pl.col('role_term_id').cast(pl.String),
        'stoichiometry': pl.col('stoichiometry').cast(pl.String),
        'record_attributes': pl.col('record_attributes'),
        'parent_attributes': pl.col('parent_attributes'),
        'member_attributes': pl.col('member_attributes'),
        'evidence': pl.col('evidence'),
    })
    if not frames:
        return _empty_frame(ASSOCIATION_EVIDENCE_SCHEMA)
    return (
        pl.concat(frames, how='vertical_relaxed')
        .unique()
        .select(list(ASSOCIATION_EVIDENCE_SCHEMA.keys()))
        .sort(['source', 'association_id'])
    )


def _build_interaction(source_dirs: list[GoldSourceDir]) -> pl.DataFrame:
    frames = _read_frames(source_dirs, 'interaction.parquet', {
        'interaction_id': pl.col('interaction_id').cast(pl.String),
        'entity_a_id': pl.col('entity_a_id').cast(pl.String),
        'entity_a_id_type': pl.col('entity_a_id_type').cast(pl.String),
        'entity_b_id': pl.col('entity_b_id').cast(pl.String),
        'entity_b_id_type': pl.col('entity_b_id_type').cast(pl.String),
        'direction': pl.col('direction').cast(pl.Int64, strict=False),
        'sign': pl.col('sign').cast(pl.Int64, strict=False),
        'evidence_count': pl.col('evidence_count').cast(pl.Int64),
        'sources': pl.col('sources'),
    })
    if not frames:
        return _empty_frame(INTERACTION_SCHEMA)
    combined = pl.concat(frames, how='vertical_relaxed')
    return (
        combined
        .group_by([
            'interaction_id',
            'entity_a_id',
            'entity_a_id_type',
            'entity_b_id',
            'entity_b_id_type',
            'direction',
            'sign',
        ])
        .agg([
            pl.col('evidence_count').sum().cast(pl.Int64).alias('evidence_count'),
            pl.col('sources').explode().drop_nulls().unique().sort().alias('sources'),
        ])
        .select(list(INTERACTION_SCHEMA.keys()))
        .sort('interaction_id')
    )


def _build_association(source_dirs: list[GoldSourceDir]) -> pl.DataFrame:
    frames = _read_frames(source_dirs, 'association.parquet', {
        'association_id': pl.col('association_id').cast(pl.String),
        'parent_entity_id': pl.col('parent_entity_id').cast(pl.String),
        'parent_entity_id_type': pl.col('parent_entity_id_type').cast(pl.String),
        'member_entity_id': pl.col('member_entity_id').cast(pl.String),
        'member_entity_id_type': pl.col('member_entity_id_type').cast(pl.String),
        'role_term_id': pl.col('role_term_id').cast(pl.String),
        'stoichiometry': pl.col('stoichiometry').cast(pl.String),
        'sources': pl.col('sources'),
    })
    if not frames:
        return _empty_frame(ASSOCIATION_SCHEMA)
    combined = pl.concat(frames, how='vertical_relaxed')
    return (
        combined
        .group_by([
            'association_id',
            'parent_entity_id',
            'parent_entity_id_type',
            'member_entity_id',
            'member_entity_id_type',
            'role_term_id',
            'stoichiometry',
        ])
        .agg([
            pl.col('sources').explode().drop_nulls().unique().sort().alias('sources'),
        ])
        .select(list(ASSOCIATION_SCHEMA.keys()))
        .sort('association_id')
    )


def _build_entity_annotation(source_dirs: list[GoldSourceDir]) -> pl.DataFrame:
    frames = _read_frames(source_dirs, 'entity_annotation.parquet', {
        'entity_id': pl.col('entity_id').cast(pl.String),
        'entity_id_type': pl.col('entity_id_type').cast(pl.String),
        'cv_term': pl.col('cv_term').cast(pl.String),
        'sources': pl.col('sources'),
    })
    if not frames:
        return _empty_frame(ENTITY_ANNOTATION_SCHEMA)
    combined = pl.concat(frames, how='vertical_relaxed')
    return (
        combined
        .group_by(['entity_id', 'entity_id_type', 'cv_term'])
        .agg([
            pl.col('sources').explode().drop_nulls().unique().sort().alias('sources'),
        ])
        .select(list(ENTITY_ANNOTATION_SCHEMA.keys()))
        .sort(['entity_id_type', 'entity_id', 'cv_term'])
    )


def _build_interaction_annotation(source_dirs: list[GoldSourceDir]) -> pl.DataFrame:
    frames = _read_frames(source_dirs, 'interaction_annotation.parquet', {
        'interaction_id': pl.col('interaction_id').cast(pl.String),
        'cv_term': pl.col('cv_term').cast(pl.String),
        'sources': pl.col('sources'),
    })
    if not frames:
        return _empty_frame(INTERACTION_ANNOTATION_SCHEMA)
    combined = pl.concat(frames, how='vertical_relaxed')
    return (
        combined
        .group_by(['interaction_id', 'cv_term'])
        .agg([
            pl.col('sources').explode().drop_nulls().unique().sort().alias('sources'),
        ])
        .select(list(INTERACTION_ANNOTATION_SCHEMA.keys()))
        .sort(['interaction_id', 'cv_term'])
    )


def _read_frames(
    source_dirs: list[GoldSourceDir],
    artifact_name: str,
    expressions: dict[str, pl.Expr],
) -> list[pl.DataFrame]:
    frames: list[pl.DataFrame] = []
    for source_dir in source_dirs:
        path = source_dir.path / artifact_name
        if not path.exists():
            continue
        frame = pl.read_parquet(path).select(list(expressions.values()))
        frame.columns = list(expressions.keys())
        frames.append(frame)
    return frames


def _empty_frame(schema: dict[str, pl.DataType]) -> pl.DataFrame:
    return pl.DataFrame({
        name: pl.Series([], dtype=dtype)
        for name, dtype in schema.items()
    })


def _write_if_nonempty(frame: pl.DataFrame, path: Path) -> None:
    if frame.is_empty():
        if path.exists():
            path.unlink()
        return
    frame.write_parquet(path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description='Build combined warehouse parquet artifacts from per-source gold outputs.',
    )
    parser.add_argument(
        '--gold-root',
        type=Path,
        default=Path('data_v2/gold'),
        help='Root directory containing per-source gold outputs (default: data_v2/gold)',
    )
    parser.add_argument(
        '--output-dir',
        type=Path,
        default=Path('data_v2/combined'),
        help='Directory to write combined parquet artifacts (default: data_v2/combined)',
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    build_combined_parquets(gold_root=args.gold_root, output_dir=args.output_dir)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
