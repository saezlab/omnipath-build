"""Load a dataset's parsed rows into a DuckDB table.

``raw`` has ``rid`` (the row's position in the parser output, which the row
path numbers the same way), ``payload_json`` (the published evidence payload)
and one VARCHAR column per input field. Non-text values are kept as JSON and
decoded again before mapping code sees them.
"""

from __future__ import annotations

import json
import time
from itertools import islice

import pyarrow as pa

from omnipath_core.keys import canonical_json

BATCH = 50_000


def load_raw(db, rows, *, max_records: int | None = None) -> dict:
    """Create table ``raw`` from an iterator of row mappings."""
    started = time.perf_counter()
    columns: list[str] = []
    json_columns: set[str] = set()
    rid = 0
    iterator = iter(rows if max_records is None else islice(rows, max_records))
    db.execute("DROP TABLE IF EXISTS raw")
    while batch := list(islice(iterator, BATCH)):
        for row in batch:
            for key in row:
                if key not in columns:
                    columns.append(key)
        data = {"rid": list(range(rid, rid + len(batch))), "payload_json": []}
        for name in columns:
            data[name] = []
        for row in batch:
            data["payload_json"].append(canonical_json(row))
            for name in columns:
                value = row.get(name)
                if value is not None and not isinstance(value, str):
                    json_columns.add(name)
                    value = json.dumps(value, default=str)
                data[name].append(value)
        table = pa.table(
            {k: pa.array(v, type=pa.int64() if k == "rid" else pa.string()) for k, v in data.items()}
        )
        db.register("raw_batch", table)
        if rid == 0:
            db.execute("CREATE TABLE raw AS SELECT * FROM raw_batch")
        else:
            for name in columns:
                db.execute(f'ALTER TABLE raw ADD COLUMN IF NOT EXISTS "{name}" VARCHAR')
            db.execute("INSERT INTO raw BY NAME SELECT * FROM raw_batch")
        db.unregister("raw_batch")
        rid += len(batch)
    return dict(
        rows=rid, columns=columns, json_columns=sorted(json_columns),
        seconds=time.perf_counter() - started,
    )  # fmt: skip


def decode(value, is_json: bool):
    return json.loads(value) if is_json and value is not None else value


def add_payloads(db, name: str = "raw") -> None:
    """Add ``payload_json``: each row's fields (non-NULL columns) as ``canonical_json``.

    DuckDB writes the JSON; it matches Python's ``json.dumps`` for ASCII text, so
    rows with other characters (Python escapes them as \\uXXXX) are written in Python.
    """
    columns = [r[0] for r in db.execute(f"DESCRIBE {name}").fetchall() if r[0] not in ("rid", "payload_json")]
    ordered = sorted(columns)
    parts = ", ".join(
        f"""CASE WHEN "{c.replace('"', '""')}" IS NOT NULL THEN {_sql_text(json.dumps(c) + ':')} || to_json("{c.replace('"', '""')}") END"""
        for c in ordered
    )
    db.execute(f"ALTER TABLE {name} ADD COLUMN IF NOT EXISTS payload_json VARCHAR")
    db.execute(f"UPDATE {name} SET payload_json = '{{' || concat_ws(',', {parts}) || '}}'")
    quoted = ", ".join('"' + c.replace('"', '""') + '"' for c in columns)
    rows = db.execute(
        f"SELECT rid, {quoted} FROM {name} WHERE regexp_matches(payload_json, '[^\\x00-\\x7E]')"
    ).fetchall()
    if rows:
        fixed = pa.table({
            "rid": [r[0] for r in rows],
            "payload": [canonical_json({c: v for c, v in zip(columns, r[1:]) if v is not None}) for r in rows],
        })
        db.register("payload_fix", fixed)
        db.execute(f"UPDATE {name} SET payload_json = payload_fix.payload FROM payload_fix WHERE {name}.rid = payload_fix.rid")
        db.unregister("payload_fix")


def _sql_text(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"
