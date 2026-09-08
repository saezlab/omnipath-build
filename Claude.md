## Python Environment

This project uses `uv` for Python dependency management. When running Python commands, always use:

```bash
uv run python <script or -c "code">
```

Instead of calling `python` or `python3` directly.

## Build run logging

Every time you run a real `make load` / `make reload` / `make all` against a
database (not a unit test) — capped or uncapped, one source or all — add an
entry to [`docs/build-log.md`](docs/build-log.md): why the run happened, the
exact parameters used, and wall-clock durations per phase (per-source delete,
preparse, stage/canonicalize, copy, derive), calling out any single-dataset
outlier by name. Do this whether or not the run finishes cleanly — a failed or
still-running build is as worth recording as a successful one. See that file's
own header for the expected format.