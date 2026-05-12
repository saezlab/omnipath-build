from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

import polars as pl

ENTITY_BUCKET_COUNT = 4096
ENTITY_PART_COUNT = 128
RELATION_BUCKET_COUNT = 4096
RELATION_PART_COUNT = 128


def stable_bucket(value: str | None, bucket_count: int) -> int | None:
    if value is None:
        return None
    digest = hashlib.sha256(str(value).encode('utf-8')).digest()
    return int.from_bytes(digest[:8], 'big', signed=False) % bucket_count


def stable_part(
    value: str | None,
    bucket_count: int,
    part_count: int,
) -> int | None:
    bucket = stable_bucket(value, bucket_count)
    if bucket is None:
        return None
    return int(bucket * part_count // bucket_count)


def add_entity_partition_columns(
    frame: pl.DataFrame,
    *,
    bucket_count: int = ENTITY_BUCKET_COUNT,
    part_count: int = ENTITY_PART_COUNT,
) -> pl.DataFrame:
    return frame.with_columns([
        pl.col('entity_key')
        .map_elements(
            lambda value: stable_bucket(value, bucket_count),
            return_dtype=pl.Int64,
        )
        .alias('entity_bucket'),
        pl.col('entity_key')
        .map_elements(
            lambda value: stable_part(value, bucket_count, part_count),
            return_dtype=pl.Int64,
        )
        .alias('entity_part'),
    ])


def add_relation_partition_columns(
    frame: pl.DataFrame,
    *,
    bucket_count: int = RELATION_BUCKET_COUNT,
    part_count: int = RELATION_PART_COUNT,
) -> pl.DataFrame:
    return frame.with_columns([
        pl.col('relation_key')
        .map_elements(
            lambda value: stable_bucket(value, bucket_count),
            return_dtype=pl.Int64,
        )
        .alias('relation_bucket'),
        pl.col('relation_key')
        .map_elements(
            lambda value: stable_part(value, bucket_count, part_count),
            return_dtype=pl.Int64,
        )
        .alias('relation_part'),
    ])


def write_part_dataset(
    frame: pl.DataFrame,
    root: str | Path,
    *,
    part_col: str,
    bucket_col: str,
    key_col: str,
    part_count: int,
) -> None:
    root = Path(root)
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True, exist_ok=True)

    sort_columns = [
        column for column in (bucket_col, key_col) if column in frame.columns
    ]
    for part in range(part_count):
        part_dir = root / f'part={part:05d}'
        part_dir.mkdir(parents=True, exist_ok=True)
        part_frame = frame.filter(pl.col(part_col) == part)
        if sort_columns and not part_frame.is_empty():
            part_frame = part_frame.sort(sort_columns)
        part_frame.write_parquet(part_dir / 'data.parquet')
