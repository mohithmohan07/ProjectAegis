"""Private download endpoints for Phase-1 canonical-source shadow artifacts."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from ..db import get_db
from ..services import auth, uploads

router = APIRouter(prefix="/source-artifacts", tags=["source-artifacts"])


@router.get("/uploads/{job_id}")
def source_artifact_manifest(
    job_id: int,
    db: Session = Depends(get_db),
    user: auth.Principal = Depends(auth.require_user),
):
    """Return the shadow status and links without exposing another user's upload."""
    try:
        job = uploads.get_job(db, job_id, owner_sub=user.sub)
    except uploads.UploadJobNotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    return uploads.source_artifact_manifest(job)


@router.get("/uploads/{job_id}/{artifact_kind}")
def download_source_artifact(
    job_id: int,
    artifact_kind: str,
    db: Session = Depends(get_db),
    user: auth.Principal = Depends(auth.require_user),
):
    """Download one immutable/raw, canonical, derived, or report artifact."""
    try:
        job = uploads.get_job(db, job_id, owner_sub=user.sub)
        path, spec = uploads.source_artifact_download(job, artifact_kind)
    except uploads.UploadJobNotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    return FileResponse(
        path,
        filename=spec["filename"],
        media_type=spec["media_type"],
        headers={
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
            "X-Aegis-Shadow-Only": "true",
        },
    )
