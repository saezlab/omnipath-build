"""Explicit opt-in integration tests own a disposable local PostgreSQL cluster."""

import os
from pathlib import Path
import shutil
import subprocess
import tempfile

import pytest


@pytest.fixture(scope="session")
def postgres_dsn():
    if os.environ.get("OMNIPATH_TEST_POSTGRES") != "1":
        pytest.skip("Set OMNIPATH_TEST_POSTGRES=1 to start a disposable PostgreSQL cluster")
    candidates = [
        os.environ.get("OMNIPATH_POSTGRES_BIN", ""),
        "/opt/homebrew/opt/postgresql@16/bin",
        str(Path(shutil.which("initdb") or "/missing/initdb").parent),
    ]
    binary = next(
        (
            Path(candidate)
            for candidate in candidates
            if candidate
            and all((Path(candidate) / name).is_file() for name in ("initdb", "pg_ctl"))
        ),
        None,
    )
    if binary is None:
        pytest.fail("Set OMNIPATH_POSTGRES_BIN to a directory containing initdb and pg_ctl")
    root = Path(tempfile.mkdtemp(prefix="op-pg-", dir="/tmp"))
    root.chmod(0o700)
    cluster = root / "cluster"
    logfile = root / "postgres.log"
    environment = {**os.environ, "LC_ALL": "C"}

    def run(*args):
        result = subprocess.run(args, text=True, capture_output=True, env=environment, timeout=30)
        if result.returncode:
            log = logfile.read_text() if logfile.exists() else ""
            raise RuntimeError(f"Temporary PostgreSQL failed: {result.stderr}\n{log}")
        return result

    try:
        run(
            str(binary / "initdb"),
            "-D",
            str(cluster),
            "-U",
            "omnipath_migration",
            "--auth=trust",
            "--no-locale",
            "-E",
            "UTF8",
        )
        run(
            str(binary / "pg_ctl"),
            "-D",
            str(cluster),
            "-l",
            str(logfile),
            "-w",
            "start",
            "-o",
            f"-F -c listen_addresses='' -c unix_socket_directories='{root}' "
            "-p 55479 -c max_connections=20 -c shared_buffers=32MB",
        )
        yield f"host={root} port=55479 user=omnipath_migration dbname=postgres"
    finally:
        if (cluster / "postmaster.pid").exists():
            run(str(binary / "pg_ctl"), "-D", str(cluster), "-m", "fast", "-w", "stop")
        shutil.rmtree(root)
