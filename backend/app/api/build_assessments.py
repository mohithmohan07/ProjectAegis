from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from .. import models, schemas
from ..db import SessionLocal, get_db
from ..services import build_assessments as svc
from ..services import progress, uploads

router = APIRouter(prefix="/build-assessments", tags=["build-assessments"])


# --------------------------------------------------------------------------- #
# Path A — From Concept Mapping
# --------------------------------------------------------------------------- #

@router.post("/sessions", response_model=schemas.SessionOut)
def create_session(req: schemas.CreateSessionRequest, db: Session = Depends(get_db)):
    try:
        return svc.create_session(db, req.scope_type, req.scope_ids)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/sessions/{session_id}", response_model=schemas.SessionOut)
def get_session(session_id: int, db: Session = Depends(get_db)):
    session = db.get(models.AssessmentSession, session_id)
    if not session:
        raise HTTPException(404, "session not found")
    return session


@router.post("/sessions/{session_id}/batches", response_model=schemas.BlueprintBatchOut)
def add_batch(session_id: int, req: schemas.BlueprintBatchRequest, db: Session = Depends(get_db)):
    try:
        return svc.add_batch(
            db, session_id,
            cognitive_skills=req.cognitive_skills,
            difficulty_levels=req.difficulty_levels,
            categories=req.categories,
            question_type=req.question_type,
            num_questions=req.num_questions,
            appears_in=req.appears_in,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/sessions/{session_id}/generate")
def generate(session_id: int, db: Session = Depends(get_db)):
    """Stream question generation progress (NDJSON)."""
    session = db.get(models.AssessmentSession, session_id)
    if not session:
        raise HTTPException(404, "session not found")
    if not session.batches:
        raise HTTPException(400, "add at least one blueprint batch before generating")

    def work():
        worker_db = SessionLocal()
        try:
            return svc.generate(worker_db, session_id)
        finally:
            worker_db.close()
    return progress.stream(work, title="Build Assessments — generating questions")


# --------------------------------------------------------------------------- #
# Path B — From Upload (stage → convert → deposit → generate)
# --------------------------------------------------------------------------- #

@router.post("/uploads", response_model=schemas.UploadJobOut)
async def create_upload(
    upload_type: str,
    source_book: str = "",
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """Stage the file only — does NOT convert to MMD (call /convert next)."""
    try:
        return svc.create_upload_job(
            db, upload_type=upload_type,
            filename=file.filename or "upload.txt",
            raw_bytes=await file.read(),
            source_book=source_book,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))


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
    """Swap the staged file (e.g. wrong PDF) before conversion."""
    try:
        return uploads.replace_file(
            db, job_id, filename=file.filename or "upload.txt",
            raw_bytes=await file.read())
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


@router.post("/uploads/{job_id}/textbook-mode", response_model=schemas.UploadJobOut)
def textbook_mode(job_id: int, req: schemas.TextbookModeRequest, db: Session = Depends(get_db)):
    try:
        return svc.set_textbook_mode(db, job_id, req.mode)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/uploads/{job_id}/deposit", response_model=schemas.UploadJobOut)
def set_deposit(job_id: int, req: schemas.DepositRequest, db: Session = Depends(get_db)):
    try:
        return svc.set_deposit(db, job_id, req.scope_type, req.scope_ids)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/uploads/{job_id}/generate")
def generate_from_upload(job_id: int, req: schemas.GenerateFromUploadRequest):
    """Identify & deposit questions from the uploaded MMD (streamed progress)."""
    if req.question_type not in {"auto", "objective", "subjective", "descriptive"}:
        raise HTTPException(
            400, "question_type must be auto | objective | subjective | descriptive")

    def work():
        db = SessionLocal()
        try:
            return uploads.run_with_openai_usage(
                db,
                job_id,
                lambda: svc.generate_from_upload(db, job_id, req.question_type),
            )
        finally:
            db.close()
    return progress.stream(work, title="Build Assessments — generating from upload")
