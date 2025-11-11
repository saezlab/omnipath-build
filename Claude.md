# Claude Code Instructions

## Python Environment

This project uses `uv` for Python dependency management. When running Python commands, always use:

```bash
uv run python <script or -c "code">
```

Instead of calling `python` or `python3` directly.

## Examples

Good:
```bash
uv run python -c "from pypath.inputs.guidetopharma import interactions; print(next(interactions()))"
uv run python scripts/process_data.py
```

Bad:
```bash
python -c "..."
python3 scripts/process_data.py
```