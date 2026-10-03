"""Memory and thread budgets for resource-build DuckDB connections."""

import os
import re


def build_memory_limit(explicit: str | None = None) -> str:
    value = (
        explicit if explicit is not None else os.environ.get("OMNIPATH_BUILD_DUCKDB_MEMORY", "1GB")
    )
    match = re.fullmatch(r"\s*(\d+(?:\.\d*)?|\.\d+)\s*(B|[KMGTPE]I?B)\s*", value, re.IGNORECASE)
    if not match or float(match[1]) <= 0:
        raise ValueError("Build DuckDB memory must be a positive size, for example 1GB or 512MiB")
    return match[1] + match[2].upper()


def configure_memory(connection, limit: str | None = None) -> None:
    connection.execute("SET memory_limit = ?", [build_memory_limit(limit)])


def build_threads() -> int:
    value = int(os.environ.get("OMNIPATH_BUILD_DUCKDB_THREADS", "4"))
    if value < 1:
        raise ValueError("Build DuckDB threads must be positive")
    return value
