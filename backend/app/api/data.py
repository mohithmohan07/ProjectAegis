"""Bulk Import workbook IO: import a database workbook, export the output workbook."""
from pathlib import Path
from tempfile import NamedTemporaryFile

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, Response
from sqlalchemy.orm import Session

from .. import config, models, schemas
from ..bulk_import import reader, writer
from ..db import get_db

router = APIRouter(prefix="/data", tags=["data"])


@router.post("/import")
async def import_workbook(file: UploadFile = File(...), db: Session = Depends(get_db)):
    """Load a canonical Bulk Import workbook into the normalized DB (append-only)."""
    if not file.filename or not file.filename.lower().endswith((".xlsx", ".xls")):
        raise HTTPException(400, "expected a .xlsx Bulk Import workbook")
    with NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
        tmp.write(await file.read())
        tmp_path = Path(tmp.name)
    try:
        counts = reader.import_workbook(db, tmp_path)
    finally:
        tmp_path.unlink(missing_ok=True)
    return counts


@router.get("/export")
def export_workbook(
    scope: str = Query("all", pattern="^(all|output)$"),
    db: Session = Depends(get_db),
):
    """Export a canonical Bulk Import workbook.

    scope=all    -> a fresh workbook containing every question in the DB
    scope=output -> the append-only output workbook accumulated by generations
    """
    if scope == "output":
        if not config.BULK_IMPORT_OUTPUT.exists():
            raise HTTPException(404, "no output workbook yet — run a generation first")
        return FileResponse(
            config.BULK_IMPORT_OUTPUT,
            filename="bulk_import_output.xlsx",
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    data = writer.write_workbook(db)
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="bulk_import_all.xlsx"'},
    )


@router.get("/questions", response_model=list[schemas.QuestionOut])
def list_questions(
    sheet_kind: str | None = None,
    origin: str | None = None,
    limit: int = Query(200, ge=1, le=2000),
    db: Session = Depends(get_db),
):
    q = db.query(models.Question)
    if sheet_kind:
        q = q.filter(models.Question.sheet_kind == sheet_kind)
    if origin:
        q = q.filter(models.Question.origin == origin)
    return q.order_by(models.Question.id.desc()).limit(limit).all()
