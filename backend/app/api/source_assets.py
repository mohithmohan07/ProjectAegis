"""Signed public delivery of immutable GPT PDF-to-ACSD visual crops."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

from ..services import canonical_source_phase221_fallback as fallback

router = APIRouter(prefix="/source-assets", tags=["source-assets"])


@router.get("/{job_id}/{filename}")
def source_asset(job_id: int, filename: str, sig: str = Query(default="")):
    """Serve an unguessable immutable source crop used by canonical rich text."""
    if not fallback.validate_asset_signature(job_id, filename, sig):
        raise HTTPException(404, "source asset not found")
    try:
        path = fallback.source_asset_path(job_id, filename)
    except ValueError as exc:
        raise HTTPException(404, "source asset not found") from exc
    if not path.exists() or not path.is_file():
        raise HTTPException(404, "source asset not found")
    return FileResponse(
        path,
        media_type="image/jpeg",
        headers={
            "Cache-Control": "public, max-age=31536000, immutable",
            "Content-Disposition": f'inline; filename="{filename}"',
            "X-Content-Type-Options": "nosniff",
        },
    )
