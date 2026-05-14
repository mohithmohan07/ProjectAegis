from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from .. import models, schemas
from ..db import get_db
from ..services import build_assessments as svc

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
        )
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/sessions/{session_id}/generate")
def generate(session_id: int, db: Session = Depends(get_db)):
    try:
        return svc.generate(db, session_id)
    except ValueError as e:
        raise HTTPException(400, str(e))


# --------------------------------------------------------------------------- #
# Path B — From Upload
# --------------------------------------------------------------------------- #

@router.post("/uploads", response_model=schemas.UploadJobOut)
async def create_upload(
    upload_type: str,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    try:
        return svc.create_upload_job(
            db, upload_type=upload_type,
            filename=file.filename or "upload.txt",
            raw_bytes=await file.read(),
        )
    except ValueError as e:
        raise HTTPException(400, str(e))


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
def generate_from_upload(
    job_id: int, req: schemas.GenerateFromUploadRequest, db: Session = Depends(get_db)
):
    try:
        return svc.generate_from_upload(db, job_id, req.question_type)
    except ValueError as e:
        raise HTTPException(400, str(e))
