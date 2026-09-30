"""Explicit resource versions and publication of complete build artifacts."""

from __future__ import annotations

import re


def validate_version(version: str | None) -> str:
    if not isinstance(version, str) or not re.fullmatch(r"[0-9]+(?:\.[0-9]+)*", version):
        raise ValueError("An explicit numeric resource version is required (for example 1.0.0)")
    return version


def validate_source(source: str) -> str:
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", source):
        raise ValueError(f"Invalid resource name: {source!r}")
    return source
