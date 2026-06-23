"""Bulk Import workbook IO: import a database workbook, export the output workbook."""
from pathlib import Path
from tempfile import NamedTemporaryFile

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, Response
from sqlalchemy.orm import Session

from .. import config, models, schemas
from ..bulk_import import reader, writer
from ..db import get_db
from ..services import data_reset as reset_svc

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


_XLSX_MEDIA = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _parse_ids(ids: str) -> list[int]:
    out: list[int] = []
    for part in (ids or "").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            out.append(int(part))
        except ValueError:
            raise HTTPException(400, f"invalid id {part!r}")
    return out


@router.get("/export/questions")
def export_questions(
    ids: str = Query(..., description="comma-separated question ids"),
    db: Session = Depends(get_db),
):
    """Download a canonical Bulk Import workbook for a specific set of questions.

    Powers the per-functionality export on each Build Assessments result, so
    the user can download exactly what was just generated (in Bulk Import
    format) without going to the Database tab.
    """
    question_ids = _parse_ids(ids)
    if not question_ids:
        raise HTTPException(400, "no question ids provided")
    data = writer.write_workbook(db, question_ids=question_ids)
    return Response(
        content=data, media_type=_XLSX_MEDIA,
        headers={"Content-Disposition": 'attachment; filename="bulk_import_questions.xlsx"'},
    )


@router.get("/export/concepts")
def export_concepts(
    ids: str = Query(..., description="comma-separated concept ids"),
    db: Session = Depends(get_db),
):
    """Download a canonical Bulk Import workbook for a specific set of concepts.

    Powers the per-functionality export on each Build Concepts result.
    """
    concept_ids = _parse_ids(ids)
    if not concept_ids:
        raise HTTPException(400, "no concept ids provided")
    data = writer.write_concepts_workbook(db, concept_ids)
    return Response(
        content=data, media_type=_XLSX_MEDIA,
        headers={"Content-Disposition": 'attachment; filename="bulk_import_concepts.xlsx"'},
    )


@router.get("/workbook/new")
def create_subject_workbook(
    subject: str,
    board: str = "",
    grade: str = "",
    mode: str = Query("content", pattern="^(blank|content)$"),
    db: Session = Depends(get_db),
):
    """Create a canonical Bulk Import workbook scoped to one subject.

    mode=blank   -> empty authoring template (exact canonical headers)
    mode=content -> pre-filled with the subject's existing chapters' content
    """
    if not subject.strip():
        raise HTTPException(400, "subject is required")
    data = writer.write_subject_workbook(
        db, subject=subject.strip(), board=board.strip(), grade=grade.strip(),
        include_content=(mode == "content"),
    )
    parts = [p.replace(" ", "") for p in (subject, board, grade) if p.strip()]
    fname = "bulk_import_" + "_".join(parts) + ".xlsx"
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@router.post("/reset")
def reset_data(db: Session = Depends(get_db)):
    """Wipe the DB, output workbook, uploads, and generated PDFs for a fresh start."""
    return reset_svc.reset_all(db=db)


@router.post("/syllabus/import")
def import_syllabus(db: Session = Depends(get_db)):
    """Load unit/chapter syllabus workbooks from ``data/syllabus/``."""
    from ..services import syllabus_import as syllabus_svc

    return syllabus_svc.load_all_syllabus_files(db)


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
