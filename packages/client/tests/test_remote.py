"""Real HTTP range reads; skips only if DuckDB's httpfs is not installed."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import random
import multiprocessing
import queue

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from omnipath_client import Client


def _serve(parquet, ready, transfers, stop):
    relations_buffer = pa.BufferOutputStream()
    pq.write_table(pa.table({"relation_key": pa.array([], type=pa.string())}), relations_buffer)
    relations = relations_buffer.getvalue().to_pybytes()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_HEAD(self):
            self.respond(False)

        def do_GET(self):
            self.respond(True)

        def respond(self, body):
            if self.path.startswith("/api/resources?"):
                payload = json.dumps(
                    {
                        "resources": [
                            {
                                "resource_id": "alpha",
                                "version": "1",
                                "files": [
                                    {
                                        "name": "entity.parquet",
                                        "size_bytes": len(parquet),
                                        "url": f"http://127.0.0.1:{self.server.server_port}/entity.parquet",
                                    },
                                    {
                                        "name": "relation.parquet",
                                        "size_bytes": len(relations),
                                        "url": f"http://127.0.0.1:{self.server.server_port}/relation.parquet",
                                    },
                                ],
                            }
                        ]
                    }
                ).encode()
                self.send_response(200)
            elif self.path == "/relation.parquet":
                payload = relations
                self.send_response(200)
            elif self.path == "/entity.parquet":
                start, end = 0, len(parquet) - 1
                value = self.headers.get("Range")
                if value:
                    bounds = value.removeprefix("bytes=").split("-")
                    if bounds[0]:
                        start = int(bounds[0])
                        end = int(bounds[1]) if bounds[1] else end
                    else:
                        start = len(parquet) - int(bounds[1])
                    end = min(end, len(parquet) - 1)
                    self.send_response(206)
                    self.send_header("Content-Range", f"bytes {start}-{end}/{len(parquet)}")
                else:
                    self.send_response(200)
                self.send_header("Accept-Ranges", "bytes")
                payload = parquet[start : end + 1]
                if body:
                    transfers.put((value, len(payload)))
            else:
                self.send_error(404)
                return
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            if body:
                self.wfile.write(payload)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.timeout = 0.2
    ready.send(server.server_port)
    ready.close()
    try:
        while not stop.is_set():
            server.handle_request()
    finally:
        server.server_close()


def test_remote_projection_uses_ranges_without_full_download():
    with duckdb.connect() as con:
        try:
            con.execute("LOAD httpfs")
        except duckdb.Error:
            pytest.skip("Install DuckDB httpfs to run the local HTTP integration check")
    buffer = pa.BufferOutputStream()
    rng = random.Random(12)
    pq.write_table(
        pa.table(
            {
                "entity_key": range(100_000),
                "taxon": ["9606"] * 100_000,
                "bulk": [rng.randbytes(64) for _ in range(100_000)],
            }
        ),
        buffer,
        row_group_size=10_000,
    )
    parquet = buffer.getvalue().to_pybytes()
    # A separate process serves metadata while DuckDB binds under the Python GIL.
    ctx = multiprocessing.get_context("spawn")
    receiver, sender = ctx.Pipe(duplex=False)
    transfers, stop = ctx.Queue(), ctx.Event()
    server = ctx.Process(target=_serve, args=(parquet, sender, transfers, stop), daemon=True)
    server.start()
    try:
        assert receiver.poll(15), "HTTP fixture did not start"
        port = receiver.recv()
        with Client(f"http://127.0.0.1:{port}/api") as client:
            relation = client.entities("alpha", filters={"taxon": "9606"}, columns=["entity_key"])
            assert relation.limit(3).fetchall() == [(0,), (1,), (2,)]
        stop.set()
        server.join(5)
        requests = []
        while True:
            try:
                requests.append(transfers.get_nowait())
            except queue.Empty:
                break
        assert requests and all(value for value, _ in requests)
        assert sum(size for _, size in requests) < len(parquet) // 2
    finally:
        stop.set()
        server.join(5)
        if server.is_alive():
            server.terminate()
            server.join(5)
        receiver.close()
        sender.close()
        transfers.close()
