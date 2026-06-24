"""Create Workbooks endpoints: generate revision-workbook PDFs, browse library."""
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from .. import config
from ..services import progress, workbooks as svc

router = APIRouter(prefix="/workbooks", tags=["workbooks"])


@router.get("/subjects")
def subjects():
    return {"subjects": svc.SUBJECTS, "live": svc.use_live()}


@router.post("/generate")
async def generate(
    file: UploadFile = File(...),
    subject: str = Form(""),
):
    """Generate a workbook PDF, streaming build progress (NDJSON)."""
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "expected a chapter source PDF")
    dest = config.UPLOAD_DIR / Path(file.filename).name
    dest.write_bytes(await file.read())

    def work():
        result = svc.generate(Path(dest), subject)
        log_text = ""
        log_path = Path(result.get("build_log", ""))
        if log_path.exists():
            log_text = log_path.read_text(errors="ignore")
        return {**result, "log": log_text}

    return progress.stream(work, title=f"Create Workbooks — {file.filename}")


@router.get("/library")
def library():
    return svc.library()


@router.get("/file")
def get_file(rel: str):
    try:
        path = svc.resolve_library_file(rel)
    except (ValueError, FileNotFoundError):
        raise HTTPException(404, "file not found")
    media = "application/pdf" if path.suffix == ".pdf" else "text/plain"
    return FileResponse(path, filename=path.name, media_type=media)
