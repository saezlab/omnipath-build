#!/usr/bin/env python3
"""
Minimal script to run DuckDB SQL scripts that output parquet files.
"""
from glob import glob
import duckdb
from pathlib import Path
import sys
import re

__all__ = [
    'run_sql_scripts',
]


def run_sql_scripts():
    """Run all SQL scripts in order from the specified directory."""

    # Find all .sql files and sort them numerically by the leading number
    def get_file_number(path):
        match = re.match(r'(\d+)', Path(path).name)
        return int(match.group(1)) if match else 0

    sql_files = [Path(f) for f in sorted(glob("*.sql"), key=get_file_number)]

    print(f"Running {len(sql_files)} SQL scripts...")

    # Create a single connection shared across all scripts
    con = duckdb.connect(":memory:")

    for sql_file in sql_files:
        print(f"Executing: {sql_file.name}")
        try:
            sql = sql_file.read_text()
            con.execute(sql)
            print(f"  ✓ Success")
        except Exception as e:
            print(f"  ✗ Error: {e}")
            con.close()
            sys.exit(1)

    con.close()
    print(f"\nAll scripts executed successfully!")


if __name__ == "__main__":
    run_sql_scripts()
