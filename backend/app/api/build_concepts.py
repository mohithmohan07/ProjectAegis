from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from .. import schemas
from ..db import get_db
from ..services import build_concepts as svc

router = APIRouter(prefix="/build-concepts", tags=["build-concepts"])


# --------------------------------------------------------------------------- #
# Post Learning
# --------------------------------------------------------------------------- #

@router.post("/post-learning/uploads", response_model=schemas.UploadJobOut)
async def post_learning_upload(file: UploadFile = File(...), db: Session = Depends(get_db)):
    return svc.create_post_learning_job(
        db, filename=file.filename or "document.txt", raw_bytes=await file.read(),
    )


@router.post("/post-learning/uploads/{job_id}/generate")
def post_learning_generate(
    job_id: int, req: schemas.PostLearningGenerateRequest, db: Session = Depends(get_db)
):
    try:
        return svc.generate_post_learning(db, job_id, req.target_chapter_id)
    except ValueError as e:
        raise HTTPException(400, str(e))


# --------------------------------------------------------------------------- #
# Pre Learning — Option A: upload
# --------------------------------------------------------------------------- #

@router.post("/pre-learning/uploads", response_model=schemas.UploadJobOut)
async def pre_learning_upload(file: UploadFile = File(...), db: Session = Depends(get_db)):
    return svc.create_pre_learning_upload_job(
        db, filename=file.filename or "document.txt", raw_bytes=await file.read(),
    )


@router.post("/pre-learning/uploads/{job_id}/generate")
def pre_learning_generate_from_upload(
    job_id: int, req: schemas.PostLearningGenerateRequest, db: Session = Depends(get_db)
):
    try:
        return svc.generate_pre_learning_from_upload(db, job_id, req.target_chapter_id)
    except ValueError as e:
        raise HTTPException(400, str(e))


# --------------------------------------------------------------------------- #
# Pre Learning — Option B: use existing Post Learning
# --------------------------------------------------------------------------- #

@router.post("/pre-learning/from-existing")
def pre_learning_from_existing(
    req: schemas.PreLearningExistingRequest, db: Session = Depends(get_db)
):
    try:
        return svc.generate_pre_learning_from_existing(db, req.chapter_ids)
    except ValueError as e:
        raise HTTPException(400, str(e))
