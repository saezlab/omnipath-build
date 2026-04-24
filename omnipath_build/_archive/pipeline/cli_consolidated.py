from __future__ import annotations

from collections.abc import Sequence

from omnipath_build.pipeline.pipeline import main as pipeline_main


def main(argv: Sequence[str] | None = None) -> int:
    return pipeline_main(argv)


if __name__ == '__main__':
    raise SystemExit(main())
