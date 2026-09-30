"""Large compressed payloads must not become a complete in-memory SQL result."""

import json
from pathlib import Path
import subprocess
import sys
import textwrap

import pyarrow as pa
import pyarrow.parquet as pq

from omnipath_core.schema import PAYLOAD_SCHEMA
from omnipath_postgres import projection


def test_compressed_payload_scan_does_not_retain_whole_result(tmp_path):
    # 128 MiB of distinct, highly compressible raw records. Write small groups so
    # fixture creation itself never constructs the entire input in memory.
    count = 2048
    text = "x" * (64 * 1024)
    path = tmp_path / "evidence_payloads.parquet"
    with pq.ParquetWriter(
        path, PAYLOAD_SCHEMA, compression="zstd", use_dictionary=False, write_statistics=False
    ) as writer:
        for start in range(0, count, 64):
            writer.write_table(
                pa.Table.from_pylist(
                    [
                        {
                            "entity_key": "published-entity",
                            "relation_key": None,
                            "source": "synthetic",
                            "row_id": str(index),
                            "payload_json": json.dumps({"index": index, "text": text}),
                        }
                        for index in range(start, start + 64)
                    ],
                    schema=PAYLOAD_SCHEMA,
                )
            )
    assert path.stat().st_size < 2 * 1024 * 1024
    script = textwrap.dedent(
        """
        import json
        from pathlib import Path
        import resource
        import sys
        from omnipath_postgres import projection
        from omnipath_postgres.projection import iter_rows, iter_validated_payloads

        # Isolate Arrow's streaming allocation from the separately bounded
        # exact-text validation cache tested in test_payload_validation_cache.
        projection._MAX_PAYLOAD_CACHE_BYTES = 0

        def peak_bytes():
            value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            return value if sys.platform == 'darwin' else value * 1024

        baseline = peak_bytes()
        path = Path(sys.argv[1])
        rows = iter_rows(path, batch_size=1024)
        first = next(rows)
        assert json.loads(first['payload_json']) == {'index': 0, 'text': 'x' * 65536}
        rows.close()
        first_growth = peak_bytes() - baseline
        total = 0
        for reference in iter_validated_payloads(path.parent, batch_size=1024):
            assert reference.ordinal == total
            assert reference.row_id == str(total)
            assert reference.owner_key == 'published-entity'
            assert reference.source_record_type == 'object'
            assert len(reference.source_record_sha256) == 64
            total += 1
        print(json.dumps({'count': total, 'first_growth': first_growth,
                          'full_growth': peak_bytes() - baseline}))
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", script, str(path)],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    measured = json.loads(result.stdout)
    assert measured["count"] == count
    # The former execute/fetchmany reader allocated the 128 MiB result before
    # returning its first row. Allow native decoder overhead while rejecting it.
    assert measured["first_growth"] < 64 * 1024 * 1024, measured
    assert measured["full_growth"] < 64 * 1024 * 1024, measured


def test_direct_file_reader_preserves_nested_values_without_hive_inference(tmp_path):
    directory = tmp_path / "resource=not-a-column"
    directory.mkdir()
    path = directory / "file'with'quotes.parquet"
    rows = [
        {"key": "0001:α", "items": [{"id": "same", "values": [None, 1.25]}] * 2},
        {"key": "0002", "items": None},
        {"key": "0003", "items": []},
    ]
    pq.write_table(pa.Table.from_pylist(rows), path, row_group_size=1)
    assert list(projection.iter_rows(path, batch_size=2)) == rows


def test_file_closes_if_decoder_construction_fails(monkeypatch):
    closed = []

    class Parquet:
        def __init__(self, *_args, **_kwargs):
            pass

        def iter_batches(self, **_kwargs):
            raise RuntimeError("decoder construction failed")

        def close(self):
            closed.append(True)

    monkeypatch.setattr(projection.pq, "ParquetFile", Parquet)
    import pytest

    with pytest.raises(RuntimeError, match="decoder construction failed"):
        next(projection.iter_rows(Path("unused.parquet")))
    assert closed == [True]
