import json
import os
from types import SimpleNamespace
import pyarrow as pa
import pyarrow.parquet as pq

from omnipath_build.reference.build_reference import Build


def test_immutable_hub_stage_reuse(tmp_path):
    old = tmp_path / "old"
    new = tmp_path / "new"
    stage = old / "records-ramp"
    stage.mkdir(parents=True)
    (old / "inputs").mkdir()
    (new / "inputs").mkdir(parents=True)
    pq.write_table(pa.table({"id": ["x"]}), old / "inputs/ramp.parquet")
    os.link(old / "inputs/ramp.parquet", new / "inputs/ramp.parquet")
    pq.write_table(pa.table({"id": ["x"]}), stage / "records.parquet")

    def sql(root):
        return [
            f"COPY (SELECT * FROM read_parquet('{root}/inputs/ramp.parquet')) TO '{root}/records-ramp/records.parquet' (FORMAT PARQUET)"
        ]

    (stage / "job.json").write_text(json.dumps({"sql": sql(old)}))
    (stage / "_SUCCESS.json").write_text(
        json.dumps({"fingerprint": "old", "counts": {"records.parquet": 1}})
    )
    (stage / "counts.json").write_text('{"records.parquet":1}')
    b = Build.__new__(Build)
    b.out = new
    b.fingerprint = "new"
    b.sources = ("ramp",)
    b.completed = []
    b.input_paths = {"ramp": str(new / "inputs/ramp.parquet")}
    b.args = SimpleNamespace(reuse_stages_from=str(old), memory="128MB", threads=1)
    (stage / "parts").mkdir()
    (new / "records-ramp/parts").mkdir(parents=True)
    b.stage("records-ramp", lambda _: sql(new))
    assert (new / "records-ramp/parts").is_symlink()
    assert (new / "records-ramp/records.parquet").is_symlink()
    assert json.loads((new / "records-ramp/_SUCCESS.json").read_text())["fingerprint"] == "new"
    assert json.loads((stage / "_SUCCESS.json").read_text())["fingerprint"] == "old"
    assert b.stage("records-ramp", lambda _: []) == new / "records-ramp"
