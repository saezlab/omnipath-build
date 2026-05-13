from __future__ import annotations

from dataclasses import dataclass, replace


DEFAULT_MIN_PART_SIZE_BYTES = 200 * 1024 * 1024


@dataclass(frozen=True)
class GoldPartitionConfig:
    """Physical layout and bounded-memory knobs for rewrite gold tables."""

    bucket_count: int = 4096
    part_count: int = 128
    duckdb_memory_limit: str | None = None
    duckdb_threads: int | None = None
    duckdb_max_temp_directory_size: str | None = None
    duckdb_partitioned_write_max_open_files: int = 64
    row_group_size: int = 100_000
    min_part_size_bytes: int = DEFAULT_MIN_PART_SIZE_BYTES

    def __post_init__(self) -> None:
        if self.bucket_count <= 0:
            raise ValueError('bucket_count must be positive')
        if self.part_count <= 0:
            raise ValueError('part_count must be positive')
        if self.bucket_count < self.part_count:
            raise ValueError('bucket_count must be >= part_count')
        if self.duckdb_partitioned_write_max_open_files <= 0:
            raise ValueError('duckdb_partitioned_write_max_open_files must be positive')
        if self.min_part_size_bytes < 0:
            raise ValueError('min_part_size_bytes must be non-negative')

    def effective_for_input_bytes(self, input_bytes: int) -> GoldPartitionConfig:
        if self.min_part_size_bytes <= 0 or input_bytes <= 0:
            return self
        max_parts_by_size = max(1, input_bytes // self.min_part_size_bytes)
        effective_part_count = max(1, min(self.part_count, int(max_parts_by_size)))
        if effective_part_count == self.part_count:
            return self
        return replace(self, part_count=effective_part_count)
