from pathlib import Path
from tempfile import NamedTemporaryFile

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy.orm import Session

from .. import models, schemas
from ..db import get_db
from ..services import questions as question_svc

router = APIRouter(prefix="/questions", tags=["questions"])


@router.get("", response_model=list[schemas.QuestionOut])
def list_questions(
    sheet_kind: str | None = None,
    difficulty: str | None = None,
    cognitive: str | None = None,
    limit: int = Query(200, ge=1, le=1000),
    db: Session = Depends(get_db),
):
    return question_svc.list_questions(
        db, sheet_kind=sheet_kind, difficulty=difficulty, cognitive=cognitive, limit=limit,
    )


@router.post("", response_model=schemas.QuestionOut)
def create_question(payload: schemas.QuestionIn, db: Session = Depends(get_db)):
    data = payload.model_dump()
    data["answers"] = [a if isinstance(a, dict) else a.model_dump() for a in data.get("answers", [])]
    q = models.Question(**data)
    db.add(q)
    db.commit()
    db.refresh(q)
    return q


@router.patch("/{qid}", response_model=schemas.QuestionOut)
def update_question(qid: int, payload: schemas.QuestionIn, db: Session = Depends(get_db)):
    q = db.get(models.Question, qid)
    if not q:
        raise HTTPException(404)
    data = payload.model_dump(exclude_unset=True)
    if "answers" in data:
        data["answers"] = [a if isinstance(a, dict) else a.model_dump() for a in data["answers"]]
    for k, v in data.items():
        setattr(q, k, v)
    db.commit()
    db.refresh(q)
    return q


@router.delete("/{qid}", status_code=204)
def delete_question(qid: int, db: Session = Depends(get_db)):
    q = db.get(models.Question, qid)
    if not q:
        raise HTTPException(404)
    db.delete(q)
    db.commit()


@router.post("/import")
async def import_questions(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    if not file.filename or not file.filename.lower().endswith((".xlsx", ".xls")):
        raise HTTPException(400, "Expected .xlsx file")
    with NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
        tmp.write(await file.read())
        tmp_path = Path(tmp.name)
    try:
        counts = question_svc.import_excel(db, tmp_path)
    finally:
        tmp_path.unlink(missing_ok=True)
    return {"inserted": counts}
