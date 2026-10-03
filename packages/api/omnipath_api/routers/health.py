"""Health check endpoints."""

from __future__ import annotations

import time
from typing import Any
from fastapi import APIRouter, Request

from omnipath_api.models import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/", response_model=HealthResponse)
def root(request: Request) -> dict[str, Any]:
    return {"status": "ok", "engine": "duckdb-parquet", "timestamp": time.time()}


@router.get("/health", response_model=HealthResponse)
def health(request: Request) -> dict[str, Any]:
    return {"status": "ok", "engine": "duckdb-parquet", "timestamp": time.time()}
