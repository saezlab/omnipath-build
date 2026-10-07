"""Evaluate Python mapping code once per distinct input, in parallel processes.

Mapping code (builders, lambdas, molecular-form helpers) stays the single
definition of behaviour. It runs on distinct tuples of the columns it reads,
never on every row, and SQL joins the results back. Workers are forked so they
inherit the mapper objects, lambdas included, without pickling them.
"""

from __future__ import annotations

import multiprocessing as mp
import os
from collections.abc import Callable, Sequence
from typing import Any

import pyarrow as pa

_TASK: Callable[[tuple], Any] | None = None


def _run(chunk: list[tuple]) -> list[Any]:
    return [_TASK(values) for values in chunk]


def evaluate(
    db,
    query: str,
    key: str,
    columns: Sequence[str],
    fn: Callable[[tuple], str | None],
    name: str,
    *,
    workers: int | None = None,
    chunk_size: int = 2000,
) -> int:
    """Create table ``name`` (``key``, ``result``): ``fn`` of each row of ``query``.

    ``query`` selects one row per distinct input, with its ``key`` and the
    ``columns`` passed to ``fn``; ``fn`` returns a string (JSON) or None.
    Returns the number of inputs evaluated.
    """
    global _TASK
    table = db.execute(query).fetch_arrow_table()
    rows = list(zip(*(table.column(c).to_pylist() for c in columns))) if columns else [()]
    workers = workers or int(os.environ.get("OMNIPATH_COLUMNAR_WORKERS", os.cpu_count() or 1))
    chunks = [rows[i : i + chunk_size] for i in range(0, len(rows), chunk_size)]
    _TASK = fn
    try:
        if workers > 1 and len(chunks) > 1:
            with mp.get_context("fork").Pool(min(workers, len(chunks))) as pool:
                results = [r for part in pool.imap(_run, chunks) for r in part]
        else:
            results = [r for chunk in chunks for r in _run(chunk)]
    finally:
        _TASK = None
    out = {key: table.column(key), "result": pa.array(results, type=pa.string())}
    db.register(f"{name}_arrow", pa.table(out))
    db.execute(f"CREATE OR REPLACE TEMP TABLE {name} AS SELECT * FROM {name}_arrow")
    db.unregister(f"{name}_arrow")
    return len(rows)
