"""Release-first Build Concepts routes.

These routes are intentionally included before the legacy generation router.
They own the same uploaded-source generation URLs, but return a review release
instead of depositing concepts. Database publication is a separate explicit
POST after the release passes its deterministic gates.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, Response
from sqlalchemy.orm import Session

from .. import schemas
from ..db import SessionLocal, get_db
from ..services import auth
from ..services import concept_release as release
from ..services import drive_checkpoints, progress, uploads


router = APIRouter(prefix="/build-concepts", tags=["concept-releases"])


def _download(path: Path, filename: str, media_type: str) -> FileResponse:
    return FileResponse(
        path,
        filename=filename,
        media_type=media_type,
        headers={
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.post("/post-learning/uploads/{job_id}/generate")
def post_learning_release_generate(
    job_id: int,
    req: schemas.PostLearningGenerateRequest,
    db: Session = Depends(get_db),
    user: auth.Principal = Depends(auth.require_user),
):
    """Generate a review release; never deposit from the generation request."""

    try:
        job = uploads.get_job(
            db,
            job_id,
            owner_sub=user.sub,
            module="build_concepts",
            learning_kind="post",
        )
    except uploads.UploadJobNotFound as exc:
        raise HTTPException(404, str(exc))
    if job.status == "generated":
        raise HTTPException(
            409,
            "this release has already been uploaded to the database; start a "
            "new source upload for another generation",
        )
    if uploads.is_job_running(job_id):
        raise HTTPException(
            409,
            "generation is already running for this upload; Aegis did not "
            "start a duplicate",
        )

    def work():
        worker_db = SessionLocal()
        try:
            return uploads.run_with_openai_usage(
                worker_db,
                job_id,
                lambda: release.generate_post_learning_release(
                    worker_db,
                    job_id,
                    req.target_chapter_id,
                    owner_sub=user.sub,
                ),
                owner_sub=user.sub,
            )
        finally:
            drive_checkpoints.schedule_checkpoint_backup(job_id)
            worker_db.close()

    return progress.stream(
        work,
        title="Build Concepts — post-learning review release",
    )


@router.post("/pre-learning/uploads/{job_id}/generate")
def pre_learning_release_generate(
    job_id: int,
    req: schemas.PreLearningUploadGenerateRequest,
    db: Session = Depends(get_db),
    user: auth.Principal = Depends(auth.require_user),
):
    """Generate a Pre Learning review release with no implicit deposit."""

    try:
        job = uploads.get_job(
            db,
            job_id,
            owner_sub=user.sub,
            module="build_concepts",
            learning_kind="pre",
        )
    except uploads.UploadJobNotFound as exc:
        raise HTTPException(404, str(exc))
    if job.status == "generated":
        raise HTTPException(
            409,
            "this release has already been uploaded to the database; start a "
            "new source upload for another generation",
        )
    if uploads.is_job_running(job_id):
        raise HTTPException(
            409,
            "generation is already running for this upload; Aegis did not "
            "start a duplicate",
        )

    def work():
        worker_db = SessionLocal()
        try:
            return uploads.run_with_openai_usage(
                worker_db,
                job_id,
                lambda: release.generate_pre_learning_release(
                    worker_db,
                    job_id,
                    req.target_chapter_id,
                    owner_sub=user.sub,
                ),
                owner_sub=user.sub,
            )
        finally:
            drive_checkpoints.schedule_checkpoint_backup(job_id)
            worker_db.close()

    return progress.stream(
        work,
        title="Build Concepts — pre-learning review release",
    )


@router.get("/releases/{job_id}")
def get_release_summary(
    job_id: int,
    db: Session = Depends(get_db),
    user: auth.Principal = Depends(auth.require_user),
):
    try:
        return release.release_summary(db, job_id, owner_sub=user.sub)
    except uploads.UploadJobNotFound as exc:
        raise HTTPException(404, str(exc))
    except ValueError as exc:
        raise HTTPException(404, str(exc))


@router.get("/releases/{job_id}/workbook")
def download_release_workbook(
    job_id: int,
    db: Session = Depends(get_db),
    user: auth.Principal = Depends(auth.require_user),
):
    try:
        path, release_id = release.release_file(
            db,
            job_id,
            "workbook_path",
            owner_sub=user.sub,
        )
    except uploads.UploadJobNotFound as exc:
        raise HTTPException(404, str(exc))
    except ValueError as exc:
        raise HTTPException(404, str(exc))
    return _download(
        path,
        f"{release_id}__concepts_review.xlsx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@router.get("/releases/{job_id}/context")
def download_release_context(
    job_id: int,
    db: Session = Depends(get_db),
    user: auth.Principal = Depends(auth.require_user),
):
    """Download a release ZIP, or snapshot the currently saved issue context."""

    try:
        path, release_id = release.release_file(
            db,
            job_id,
            "context_bundle_path",
            owner_sub=user.sub,
            create_diagnostic=True,
        )
    except uploads.UploadJobNotFound as exc:
        raise HTTPException(404, str(exc))
    except ValueError as exc:
        raise HTTPException(404, str(exc))
    return _download(
        path,
        f"{release_id}__complete_context.zip",
        "application/zip",
    )


@router.get("/releases/{job_id}/manifest")
def download_release_manifest(
    job_id: int,
    db: Session = Depends(get_db),
    user: auth.Principal = Depends(auth.require_user),
):
    try:
        path, release_id = release.release_file(
            db,
            job_id,
            "manifest_path",
            owner_sub=user.sub,
        )
    except uploads.UploadJobNotFound as exc:
        raise HTTPException(404, str(exc))
    except ValueError as exc:
        raise HTTPException(404, str(exc))
    return _download(
        path,
        f"{release_id}__manifest.json",
        "application/json",
    )


@router.post("/releases/{job_id}/upload-to-database")
def upload_release_to_database(
    job_id: int,
    db: Session = Depends(get_db),
    user: auth.Principal = Depends(auth.require_user),
):
    """Explicitly publish a reviewed release; generation never calls this."""

    if uploads.is_job_running(job_id):
        raise HTTPException(
            409,
            "another operation is already running for this release",
        )
    try:
        return release.publish_release(db, job_id, owner_sub=user.sub)
    except uploads.UploadJobNotFound as exc:
        raise HTTPException(404, str(exc))
    except uploads.JobAlreadyRunningError as exc:
        raise HTTPException(409, str(exc))
    except release.build_concepts.DepositValidationError as exc:
        raise HTTPException(409, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.get("/releases/{job_id}/context-available")
def context_available(
    job_id: int,
    db: Session = Depends(get_db),
    user: auth.Principal = Depends(auth.require_user),
):
    """Cheap owner-scoped probe used by the UI while a run is incomplete."""

    try:
        job = uploads.get_job(
            db, job_id, owner_sub=user.sub, module="build_concepts"
        )
    except uploads.UploadJobNotFound as exc:
        raise HTTPException(404, str(exc))
    return {
        "job_id": job.id,
        "available": bool(
            job.mmd_text
            or job.generation_log
            or job.generation_checkpoint
            or job.question_inventory
        ),
        "context_bundle_url": (
            f"/build-concepts/releases/{job.id}/context"
        ),
    }
