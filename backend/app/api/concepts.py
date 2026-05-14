from pathlib import Path
from tempfile import NamedTemporaryFile

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy.orm import Session

from .. import schemas
from ..db import get_db
from ..services import concepts as concept_svc

router = APIRouter(prefix="/concepts", tags=["concepts"])


@router.get("", response_model=list[schemas.ConceptOut])
def list_concepts(
    pre_learning: bool | None = None,
    chapter_code: str | None = None,
    subject: str | None = None,
    limit: int = Query(200, ge=1, le=1000),
    db: Session = Depends(get_db),
):
    return concept_svc.list_concepts(
        db, pre_learning=pre_learning, chapter_code=chapter_code, subject=subject, limit=limit,
    )


@router.get("/chapters")
def list_chapters(db: Session = Depends(get_db)):
    return concept_svc.chapters(db)


@router.post("/import")
async def import_concepts(
    file: UploadFile = File(...),
    sheet: str = "Concepts",
    pre_learning: bool = False,
    db: Session = Depends(get_db),
):
    if not file.filename or not file.filename.lower().endswith((".xlsx", ".xls")):
        raise HTTPException(400, "Expected .xlsx file")
    with NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
        tmp.write(await file.read())
        tmp_path = Path(tmp.name)
    try:
        count = concept_svc.import_excel(db, tmp_path, is_pre_learning=pre_learning, sheet=sheet)
    finally:
        tmp_path.unlink(missing_ok=True)
    return {"inserted": count}
