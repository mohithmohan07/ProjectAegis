from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import Response
from sqlalchemy.orm import Session

from .. import schemas
from ..db import SessionLocal, get_db
from ..services import build_concepts as svc
from ..services import checkpoints, progress, uploads

router = APIRouter(prefix="/build-concepts", tags=["build-concepts"])


# --------------------------------------------------------------------------- #
# Shared upload helpers (stage → replace → convert)
# --------------------------------------------------------------------------- #

@router.get("/uploads/{job_id}", response_model=schemas.UploadJobOut)
def get_upload(job_id: int, db: Session = Depends(get_db)):
    try:
        return uploads.get_job(db, job_id)
    except ValueError as e:
        raise HTTPException(404, str(e))


@router.put("/uploads/{job_id}/file", response_model=schemas.UploadJobOut)
async def replace_upload_file(
    job_id: int, file: UploadFile = File(...), db: Session = Depends(get_db),
):
    try:
        return uploads.replace_file(
            db, job_id, filename=file.filename or "document.txt",
            raw_bytes=await file.read())
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/uploads/{job_id}/inventory.csv")
def download_inventory_csv(job_id: int, db: Session = Depends(get_db)):
    """Question / Task Inventory CSV — one row per extracted question/task,
    with the mined Type(s) each item was classified into, so extraction
    completeness can be audited."""
    try:
        csv_text = svc.inventory_csv(db, job_id)
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
def download_checkpoint(job_id: int, db: Session = Depends(get_db)):
    """Download a portable converted-source + generation checkpoint bundle."""
    try:
        filename, content = checkpoints.export_bundle(db, job_id)
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
        )
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.delete(
    "/uploads/{job_id}/checkpoint",
    response_model=schemas.UploadJobOut,
)
def clear_checkpoint(job_id: int, db: Session = Depends(get_db)):
    try:
        return checkpoints.clear_checkpoint(db, job_id)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/uploads/{job_id}/convert")
def convert_upload(job_id: int):
    """Convert the staged document to MMD (streamed progress)."""
    def work():
        db = SessionLocal()
        try:
            return uploads.convert_job(db, job_id)
        finally:
            db.close()
    return progress.stream(work, title="Converting document to MMD")


# --------------------------------------------------------------------------- #
# Post Learning
# --------------------------------------------------------------------------- #

@router.post("/post-learning/uploads", response_model=schemas.UploadJobOut)
async def post_learning_upload(
    source_book: str = "",
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """Stage the file only — conversion to MMD is a separate /convert step."""
    try:
        return svc.create_post_learning_job(
            db, filename=file.filename or "document.txt", raw_bytes=await file.read(),
            source_book=source_book,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/post-learning/uploads/{job_id}/generate")
def post_learning_generate(job_id: int, req: schemas.PostLearningGenerateRequest):
    def work():
        db = SessionLocal()
        try:
            return uploads.run_with_openai_usage(
                db,
                job_id,
                lambda: svc.generate_post_learning(
                    db, job_id, req.target_chapter_id
                ),
            )
        finally:
            db.close()
    return progress.stream(work, title="Build Concepts — post-learning generation")


# --------------------------------------------------------------------------- #
# Pre Learning — Option A: upload
# --------------------------------------------------------------------------- #

@router.post("/pre-learning/uploads", response_model=schemas.UploadJobOut)
async def pre_learning_upload(
    source_book: str = "",
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    try:
        return svc.create_pre_learning_upload_job(
            db, filename=file.filename or "document.txt", raw_bytes=await file.read(),
            source_book=source_book,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/pre-learning/uploads/{job_id}/generate")
def pre_learning_generate_from_upload(job_id: int, req: schemas.PostLearningGenerateRequest):
    def work():
        db = SessionLocal()
        try:
            return uploads.run_with_openai_usage(
                db,
                job_id,
                lambda: svc.generate_pre_learning_from_upload(
                    db, job_id, req.target_chapter_id
                ),
            )
        finally:
            db.close()
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
