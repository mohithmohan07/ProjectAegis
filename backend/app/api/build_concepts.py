from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import Response
from sqlalchemy.orm import Session

from .. import schemas
from ..db import SessionLocal, get_db
from ..services import build_concepts as svc
from ..services import (
    auth,
    checkpoints,
    drive_checkpoints,
    progress,
    uploads,
)
from .upload_limits import read_limited_upload

router = APIRouter(prefix="/build-concepts", tags=["build-concepts"])


# --------------------------------------------------------------------------- #
# Shared upload helpers (stage → replace → convert)
# --------------------------------------------------------------------------- #

@router.get("/uploads/{job_id}", response_model=schemas.UploadJobOut)
def get_upload(
    job_id: int,
    db: Session = Depends(get_db),
    user: auth.Principal = Depends(auth.require_user),
):
    try:
        return uploads.get_job(
            db, job_id, owner_sub=user.sub, module="build_concepts")
    except uploads.UploadJobNotFound as e:
        raise HTTPException(404, str(e))


@router.put("/uploads/{job_id}/file", response_model=schemas.UploadJobOut)
async def replace_upload_file(
    job_id: int, file: UploadFile = File(...), db: Session = Depends(get_db),
    user: auth.Principal = Depends(auth.require_user),
):
    try:
        raw_bytes = await read_limited_upload(file)
        return uploads.replace_file(
            db, job_id, filename=file.filename or "document.txt",
            raw_bytes=raw_bytes, owner_sub=user.sub,
            module="build_concepts")
    except uploads.UploadJobNotFound as e:
        raise HTTPException(404, str(e))
    except uploads.JobAlreadyRunningError as e:
        raise HTTPException(409, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/uploads/{job_id}/inventory.csv")
def download_inventory_csv(
    job_id: int,
    db: Session = Depends(get_db),
    user: auth.Principal = Depends(auth.require_user),
):
    """Question / Task Inventory CSV — one row per extracted question/task,
    with the mined Type(s) each item was classified into, so extraction
    completeness can be audited."""
    try:
        csv_text = svc.inventory_csv(db, job_id, owner_sub=user.sub)
    except uploads.UploadJobNotFound as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(404, str(e))
    return Response(
        content=csv_text,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition":
                f'attachment; filename="question_task_inventory_job_{job_id}.csv"',
        },
    )


@router.get("/uploads/{job_id}/checkpoint")
def download_checkpoint(
    job_id: int,
    db: Session = Depends(get_db),
    user: auth.Principal = Depends(auth.require_user),
):
    """Download a portable converted-source + generation checkpoint bundle."""
    try:
        filename, content = checkpoints.export_bundle(
            db, job_id, owner_sub=user.sub)
    except uploads.UploadJobNotFound as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(404, str(e))
    return Response(
        content=content,
        media_type="application/json; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.post(
    "/checkpoints/import",
    response_model=schemas.UploadJobOut,
)
async def import_checkpoint(
    file: UploadFile = File(...),
    learning_kind: str = "",
    db: Session = Depends(get_db),
    user: auth.Principal = Depends(auth.require_user),
):
    """Restore a portable checkpoint as a new converted Build Concepts job."""
    try:
        raw_bytes = await file.read(checkpoints.MAX_IMPORT_BYTES + 1)
        if len(raw_bytes) > checkpoints.MAX_IMPORT_BYTES:
            raise HTTPException(
                413, "checkpoint file exceeds the 25 MB import limit")
        return checkpoints.import_bundle(
            db,
            raw_bytes,
            expected_learning_kind=learning_kind,
            owner_sub=user.sub,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.delete(
    "/uploads/{job_id}/checkpoint",
    response_model=schemas.UploadJobOut,
)
def clear_checkpoint(
    job_id: int,
    db: Session = Depends(get_db),
    user: auth.Principal = Depends(auth.require_user),
):
    try:
        return checkpoints.clear_checkpoint(
            db, job_id, owner_sub=user.sub)
    except uploads.UploadJobNotFound as e:
        raise HTTPException(404, str(e))
    except uploads.JobAlreadyRunningError as e:
        raise HTTPException(409, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get(
    "/checkpoints/resumable",
    response_model=schemas.ResumableCheckpointJobs,
)
def list_resumable_checkpoints(
    learning_kind: str,
    db: Session = Depends(get_db),
    user: auth.Principal = Depends(auth.require_user),
):
    try:
        items, total = checkpoints.resumable_jobs(
            db,
            owner_sub=user.sub,
            learning_kind=learning_kind,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"items": items, "total": total}


@router.post("/uploads/{job_id}/convert")
def convert_upload(
    job_id: int,
    db: Session = Depends(get_db),
    user: auth.Principal = Depends(auth.require_user),
):
    """Convert the staged document to MMD (streamed progress)."""
    try:
        job = uploads.get_job(
            db, job_id, owner_sub=user.sub, module="build_concepts")
    except uploads.UploadJobNotFound as e:
        raise HTTPException(404, str(e))
    if job.status == "generated":
        raise HTTPException(
            409,
            "this upload has already been generated; start a new upload",
        )
    if uploads.is_job_running(job_id):
        raise HTTPException(
            409,
            "generation is already running for this upload; wait for the "
            "active run to finish before changing it",
        )

    def work():
        worker_db = SessionLocal()
        try:
            return uploads.convert_job(
                worker_db,
                job_id,
                owner_sub=user.sub,
                module="build_concepts",
            )
        finally:
            worker_db.close()
    return progress.stream(work, title="Converting document to MMD")


# --------------------------------------------------------------------------- #
# Post Learning
# --------------------------------------------------------------------------- #

@router.post("/post-learning/uploads", response_model=schemas.UploadJobOut)
async def post_learning_upload(
    source_book: str = "",
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: auth.Principal = Depends(auth.require_user),
):
    """Stage the file only — conversion to MMD is a separate /convert step."""
    try:
        raw_bytes = await read_limited_upload(file)
        return svc.create_post_learning_job(
            db, filename=file.filename or "document.txt", raw_bytes=raw_bytes,
            source_book=source_book,
            owner_sub=user.sub,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/post-learning/uploads/{job_id}/generate")
def post_learning_generate(
    job_id: int,
    req: schemas.PostLearningGenerateRequest,
    db: Session = Depends(get_db),
    user: auth.Principal = Depends(auth.require_user),
):
    try:
        job = uploads.get_job(
            db,
            job_id,
            owner_sub=user.sub,
            module="build_concepts",
            learning_kind="post",
        )
    except uploads.UploadJobNotFound as e:
        raise HTTPException(404, str(e))
    if job.status == "generated":
        raise HTTPException(
            409,
            "this upload has already been generated; start a new upload",
        )
    if uploads.is_job_running(job_id):
        raise HTTPException(
            409,
            "generation is already running for this upload; wait for the "
            "active run to finish before resuming",
        )

    def work():
        worker_db = SessionLocal()
        try:
            return uploads.run_with_openai_usage(
                worker_db,
                job_id,
                lambda: svc.generate_post_learning(
                    worker_db,
                    job_id,
                    req.target_chapter_id,
                    owner_sub=user.sub,
                ),
                owner_sub=user.sub,
            )
        finally:
            # Queue the post-accounting state as well as each stage checkpoint.
            # Success overwrites the remote resumable snapshot with the final
            # cleared checkpoint; failure includes final usage and diagnostics.
            drive_checkpoints.schedule_checkpoint_backup(job_id)
            worker_db.close()
    return progress.stream(work, title="Build Concepts — post-learning generation")


# --------------------------------------------------------------------------- #
# Pre Learning — Option A: upload
# --------------------------------------------------------------------------- #

@router.post("/pre-learning/uploads", response_model=schemas.UploadJobOut)
async def pre_learning_upload(
    source_book: str = "",
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: auth.Principal = Depends(auth.require_user),
):
    try:
        raw_bytes = await read_limited_upload(file)
        return svc.create_pre_learning_upload_job(
            db, filename=file.filename or "document.txt", raw_bytes=raw_bytes,
            source_book=source_book,
            owner_sub=user.sub,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/pre-learning/uploads/{job_id}/generate")
def pre_learning_generate_from_upload(
    job_id: int,
    req: schemas.PostLearningGenerateRequest,
    db: Session = Depends(get_db),
    user: auth.Principal = Depends(auth.require_user),
):
    try:
        job = uploads.get_job(
            db,
            job_id,
            owner_sub=user.sub,
            module="build_concepts",
            learning_kind="pre",
        )
    except uploads.UploadJobNotFound as e:
        raise HTTPException(404, str(e))
    if job.status == "generated":
        raise HTTPException(
            409,
            "this upload has already been generated; start a new upload",
        )
    if uploads.is_job_running(job_id):
        raise HTTPException(
            409,
            "generation is already running for this upload; wait for the "
            "active run to finish before resuming",
        )

    def work():
        worker_db = SessionLocal()
        try:
            return uploads.run_with_openai_usage(
                worker_db,
                job_id,
                lambda: svc.generate_pre_learning_from_upload(
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
    return progress.stream(work, title="Build Concepts — pre-learning generation")


# --------------------------------------------------------------------------- #
# Pre Learning — Option B: use existing Post Learning
# --------------------------------------------------------------------------- #

@router.post("/pre-learning/from-existing")
def pre_learning_from_existing(req: schemas.PreLearningExistingRequest):
    def work():
        db = SessionLocal()
        try:
            return svc.generate_pre_learning_from_existing(
                db, req.chapter_ids, req.source_book)
        finally:
            db.close()
    return progress.stream(work, title="Build Concepts — pre-learning from existing")
