from __future__ import annotations
from fastapi import Request
from fastapi.responses import Response

from omnipath_api.models import (
    ExportRequest,
)
from fastapi import APIRouter
from omnipath_api.routers.common import _call, _dump

router = APIRouter()


@router.post("/export", tags=["export"], response_model=None)
def export_slice(request: Request, payload: ExportRequest) -> Response:
    """Stream a filtered relation slice as Parquet, Arrow, CSV, TSV, or JSON."""
    body = _dump(payload)
    fmt = str(body.get("format") or "parquet").lower()
    data, content_type = _call(
        request,
        "export_slice",
        filters=body.get("filters") or {},
        resources=body.get("resources"),
        format=fmt,
    )
    filename = f"omnipath_slice.{fmt if fmt != 'arrow' else 'arrow'}"
    return Response(
        content=data,
        media_type=content_type,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Access-Control-Allow-Origin": "*",
        },
    )
