from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from omnipath_build.rewrite.combine_duckdb import (
    CombinedRewriteConfig,
    build_combined_duckdb,
)
from omnipath_build.rewrite.bronze import source_state_path


@dataclass(frozen=True)
class CombinedRewriteResult:
    data_root: Path
    combined_state_path: Path
    latest_dir: Path
    reports_dir: Path
    mode: str
    row_counts: dict[str, int]
    summary: dict[str, Any]


def materialize_combined_duckdb(
    *,
    sources: list[str],
    data_root: str | Path = 'data_rewrite',
    inputs_package: str = 'pypath.inputs_v2',
    config: CombinedRewriteConfig | None = None,
) -> CombinedRewriteResult:
    """Build rewrite combined state from source-local rewrite gold DuckDB state."""
    data_root = Path(data_root)
    state_paths = {
        source: source_state_path(data_root, source)
        for source in sources
    }
    missing = [str(path) for path in state_paths.values() if not path.exists()]
    if missing:
        raise FileNotFoundError(
            'rewrite source state does not exist: ' + ', '.join(missing)
        )

    combined_state_path = data_root / 'state' / 'combined.duckdb'
    output_dir = data_root / 'artifacts' / 'combined'
    summary = build_combined_duckdb(
        gold_root=data_root / 'artifacts' / 'gold',
        output_dir=output_dir,
        reports_dir=data_root / 'reports',
        state_path=combined_state_path,
        source_state_paths=state_paths,
        use_source_scopes=True,
        inputs_package=inputs_package,
        config=config or CombinedRewriteConfig(),
    )
    return CombinedRewriteResult(
        data_root=data_root,
        combined_state_path=combined_state_path,
        latest_dir=output_dir / 'latest',
        reports_dir=data_root / 'reports' / 'combined',
        mode=str(summary.get('mode', 'unknown')),
        row_counts={
            str(name): int(count)
            for name, count in dict(summary.get('row_counts') or {}).items()
        },
        summary=summary,
    )
