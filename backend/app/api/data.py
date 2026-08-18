"""Bulk Import workbook IO: import a database workbook, export the output workbook."""
from pathlib import Path
from tempfile import NamedTemporaryFile

from fastapi import (
    APIRouter,
    Depends,
    File,
    Header,
    HTTPException,
    Query,
    UploadFile,
)
from fastapi.responses import FileResponse, Response
from sqlalchemy.orm import Session

from .. import config, models, schemas
from ..bulk_import import layouts, reader, workbook_sync, writer
from ..db import get_db
from ..services import data_reset as reset_svc
from . import admin as admin_api
from .upload_limits import read_limited_upload

router = APIRouter(prefix="/data", tags=["data"])


def _lossless_xlsx_or_422(factory):
    """Expose Excel's cell limit as an actionable client error."""
    try:
        return factory()
    except writer.ExcelCellLimitError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/import")
async def import_workbook(file: UploadFile = File(...), db: Session = Depends(get_db)):
    """Load a canonical Bulk Import workbook into the normalized DB (append-only)."""
    if not file.filename or not file.filename.lower().endswith((".xlsx", ".xls")):
        raise HTTPException(400, "expected a .xlsx Bulk Import workbook")
    raw_bytes = await read_limited_upload(
        file, description="Bulk Import workbook")
    with NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
        tmp.write(raw_bytes)
        tmp_path = Path(tmp.name)
    try:
        counts = reader.import_workbook(db, tmp_path)
    except layouts.WorkbookLayoutError as exc:
        # The workbook's column geometry could not be established, so nothing
        # was read and nothing was written. Refusing the upload loses nothing
        # and is instantly actionable; reading it wrongly would silently
        # corrupt every identity on every row (spec-step8 T6).
        raise HTTPException(status_code=422, detail=str(exc)) from exc
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
        with workbook_sync.output_workbook_lock():
            # A crash between DB commit and workbook publication leaves the
            # staged export queued; serve the converged file, never the stale one.
            workbook_sync.recover_pending_publication(config.BULK_IMPORT_OUTPUT)
        if not config.BULK_IMPORT_OUTPUT.exists():
            raise HTTPException(404, "no output workbook yet — run a generation first")
        return FileResponse(
            config.BULK_IMPORT_OUTPUT,
            filename="bulk_import_output.xlsx",
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    data = _lossless_xlsx_or_422(lambda: writer.write_workbook(db))
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
    data = _lossless_xlsx_or_422(
        lambda: writer.write_workbook(db, question_ids=question_ids)
    )
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
    data = _lossless_xlsx_or_422(
        lambda: writer.write_concepts_workbook(db, concept_ids)
    )
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
    data = _lossless_xlsx_or_422(
        lambda: writer.write_subject_workbook(
            db,
            subject=subject.strip(),
            board=board.strip(),
            grade=grade.strip(),
            include_content=(mode == "content"),
        )
    )
    parts = [p.replace(" ", "") for p in (subject, board, grade) if p.strip()]
    fname = "bulk_import_" + "_".join(parts) + ".xlsx"
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@router.post("/reset")
def reset_data(
    db: Session = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    """Wipe the DB, output workbook, uploads, and generated PDFs for a fresh start."""
    admin_api.require_admin(x_admin_token)
    return reset_svc.reset_all(db=db)


@router.post("/syllabus/import")
def import_syllabus(db: Session = Depends(get_db)):
    """Mirror the bundled ``data/syllabus/`` workbooks into the database.

    Adds what is new and retires superseded EMPTY chapters, so a re-issued
    syllabus does not leave the same chapter listed twice under its old and
    new subject. Chapters carrying authored work are never deleted; they come
    back in ``retained_with_content`` for a human to resolve.
    """
    from ..services import syllabus_import as syllabus_svc

    return syllabus_svc.refresh_syllabus(db)


@router.post("/syllabus/upload")
async def upload_syllabus(
    files: list[UploadFile] = File(...),
    db: Session = Depends(get_db),
):
    """Upload one or more syllabus Excel files and import unit/chapter shells."""
    from ..services import syllabus_import as syllabus_svc

    if not files:
        raise HTTPException(400, "upload at least one .xlsx syllabus file")

    pending: list[tuple[str, bytes]] = []
    total_bytes = 0
    for file in files:
        if not file.filename or not file.filename.lower().endswith((".xlsx", ".xls")):
            raise HTTPException(400, f"expected .xlsx files, got {file.filename!r}")
        raw_bytes = await read_limited_upload(
            file,
            max_bytes=int(config.MAX_UPLOAD_BYTES) - total_bytes,
            description="combined syllabus upload",
        )
        total_bytes += len(raw_bytes)
        pending.append((Path(file.filename).name, raw_bytes))

    saved: list[str] = []
    paths: list[Path] = []
    for filename, raw_bytes in pending:
        dest = config.SYLLABUS_DIR / filename
        dest.write_bytes(raw_bytes)
        saved.append(filename)
        paths.append(dest)

    result = syllabus_svc.import_syllabus_paths(db, paths)
    result["uploaded_files"] = saved
    return result


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
