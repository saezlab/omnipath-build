"""Which input columns a piece of mapping code reads.

Declarative sources name their column. Callables are traced on sample rows, then
evaluated on a row restricted to the traced columns: a read outside them is
recorded, never silently answered, and the caller re-runs with the column added.
Iterating a row (``dict(row)``, ``row.items()``) depends on every column.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from typing import Any

ALL = None  # every column


class TracingRow(Mapping):
    """A full row that records the columns read from it."""

    def __init__(self, row: Mapping[str, Any]):
        self._row = row
        self.read: set[str] | None = set()

    def __getitem__(self, key):
        if self.read is not None:
            self.read.add(key)
        return self._row[key]

    def get(self, key, default=None):
        if self.read is not None:
            self.read.add(key)
        return self._row.get(key, default)

    def __contains__(self, key):
        if self.read is not None:
            self.read.add(key)
        return key in self._row

    def __iter__(self) -> Iterator[str]:
        self.read = ALL
        return iter(self._row)

    def __len__(self):
        self.read = ALL
        return len(self._row)


class RestrictedRow(Mapping):
    """The traced columns of one distinct input; reads of other input columns are
    violations. A key that no input row has is absent, as it is in every row."""

    def __init__(self, values: Mapping[str, Any], violations: set[str], known: set[str]):
        self._values = values
        self._violations = violations
        self._known = known

    def _check(self, key):
        if key not in self._values:
            if key in self._known:
                self._violations.add(key)
            return False
        return True

    def __getitem__(self, key):
        if not self._check(key):
            raise KeyError(key)
        return self._values[key]

    def get(self, key, default=None):
        return self._values[key] if self._check(key) else default

    def __contains__(self, key):
        return self._check(key)

    def _whole(self):
        if len(self._values) < len(self._known):
            self._violations.add("*")

    def __iter__(self):
        self._whole()
        return iter(self._values)

    def __len__(self):
        self._whole()
        return len(self._values)


def trace(fn, rows: Iterable[Mapping[str, Any]]) -> set[str] | None:
    """Columns ``fn`` reads on ``rows``; None when it iterates a row."""
    read: set[str] = set()
    for row in rows:
        traced = TracingRow(row)
        try:
            fn(traced)
        except Exception:
            pass  # the mapping's own failure handling runs at evaluation
        if traced.read is ALL:
            return ALL
        read |= traced.read
    return read
