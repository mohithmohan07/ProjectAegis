from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from .. import schemas
from ..db import SessionLocal, get_db
from ..services import build_assessments as svc
from ..services import auth, progress, uploads
from .upload_limits import read_limited_upload

router = APIRouter(prefix="/build-assessments", tags=["build-assessments"])


# --------------------------------------------------------------------------- #
# Path A — From Concept Mapping
# --------------------------------------------------------------------------- #

@router.post("/sessions", response_model=schemas.SessionOut)
def create_session(
    req: schemas.CreateSessionRequest,
    db: Session = Depends(get_db),
    user: auth.Principal = Depends(auth.require_user),
):
    try:
        return svc.create_session(
            db,
            req.scope_type,
            req.scope_ids,
            owner_sub=user.sub,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/sessions/{session_id}", response_model=schemas.SessionOut)
def get_session(
    session_id: int,
    db: Session = Depends(get_db),
    user: auth.Principal = Depends(auth.require_user),
):
    try:
        return svc.get_session(db, session_id, owner_sub=user.sub)
    except svc.AssessmentSessionNotFound as e:
        raise HTTPException(404, str(e))


@router.post("/sessions/{session_id}/batches", response_model=schemas.BlueprintBatchOut)
def add_batch(
    session_id: int,
    req: schemas.BlueprintBatchRequest,
    db: Session = Depends(get_db),
    user: auth.Principal = Depends(auth.require_user),
):
    try:
        return svc.add_batch(
            db, session_id,
            cognitive_skills=req.cognitive_skills,
            difficulty_levels=req.difficulty_levels,
            categories=req.categories,
            question_type=req.question_type,
            num_questions=req.num_questions,
            appears_in=req.appears_in,
            owner_sub=user.sub,
        )
    except svc.AssessmentSessionNotFound as e:
        raise HTTPException(404, str(e))
    except svc.AssessmentSessionAlreadyRunning as e:
        raise HTTPException(409, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/sessions/{session_id}/generate")
def generate(
    session_id: int,
    db: Session = Depends(get_db),
    user: auth.Principal = Depends(auth.require_user),
):
    """Stream question generation progress (NDJSON)."""
    try:
        session = svc.get_session(db, session_id, owner_sub=user.sub)
    except svc.AssessmentSessionNotFound as e:
        raise HTTPException(404, str(e))
    if session.status == "generated":
        raise HTTPException(
            409,
            "this assessment session has already been generated; "
            "create a new session",
        )
    if svc.is_session_running(session.id):
        raise HTTPException(
            409,
            "generation is already running for this assessment session",
        )
    if not session.batches:
        raise HTTPException(400, "add at least one blueprint batch before generating")

    owner_sub = user.sub

    def work():
        worker_db = SessionLocal()
        try:
            return svc.generate(
                worker_db,
                session_id,
                owner_sub=owner_sub,
            )
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
    user: auth.Principal = Depends(auth.require_user),
):
    """Stage the file only — does NOT convert to MMD (call /convert next)."""
    try:
        raw_bytes = await read_limited_upload(file)
        return svc.create_upload_job(
            db, upload_type=upload_type,
            filename=file.filename or "upload.txt",
            raw_bytes=raw_bytes,
            source_book=source_book,
            owner_sub=user.sub,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/uploads/{job_id}", response_model=schemas.UploadJobOut)
def get_upload(
    job_id: int,
    db: Session = Depends(get_db),
    user: auth.Principal = Depends(auth.require_user),
):
    try:
        return uploads.get_job(
            db, job_id, owner_sub=user.sub, module="build_assessments")
    except uploads.UploadJobNotFound as e:
        raise HTTPException(404, str(e))


@router.put("/uploads/{job_id}/file", response_model=schemas.UploadJobOut)
async def replace_upload_file(
    job_id: int, file: UploadFile = File(...), db: Session = Depends(get_db),
    user: auth.Principal = Depends(auth.require_user),
):
    """Swap the staged file (e.g. wrong PDF) before conversion."""
    try:
        raw_bytes = await read_limited_upload(file)
        return uploads.replace_file(
            db, job_id, filename=file.filename or "upload.txt",
            raw_bytes=raw_bytes, owner_sub=user.sub,
            module="build_assessments")
    except uploads.UploadJobNotFound as e:
        raise HTTPException(404, str(e))
    except uploads.JobAlreadyRunningError as e:
        raise HTTPException(409, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/uploads/{job_id}/convert")
def convert_upload(
    job_id: int,
    db: Session = Depends(get_db),
    user: auth.Principal = Depends(auth.require_user),
):
    """Convert the staged document to MMD (streamed progress)."""
    try:
        job = uploads.get_job(
            db, job_id, owner_sub=user.sub, module="build_assessments")
    except uploads.UploadJobNotFound as e:
        raise HTTPException(404, str(e))
    if job.status == "generated":
        raise HTTPException(
            409,
            "cannot reconvert a completed upload; start a new upload",
        )
    if uploads.is_job_running(job_id):
        raise HTTPException(
            409,
            "generation is already running for this upload; wait for the "
            "active run to finish before changing it",
        )

    def work():
        worker_db = SessionLocal()
        try:
            return uploads.convert_job(
                worker_db,
                job_id,
                owner_sub=user.sub,
                module="build_assessments",
            )
        finally:
            worker_db.close()
    return progress.stream(work, title="Converting document to MMD")


@router.post("/uploads/{job_id}/textbook-mode", response_model=schemas.UploadJobOut)
def textbook_mode(
    job_id: int,
    req: schemas.TextbookModeRequest,
    db: Session = Depends(get_db),
    user: auth.Principal = Depends(auth.require_user),
):
    try:
        return svc.set_textbook_mode(
            db, job_id, req.mode, owner_sub=user.sub)
    except uploads.UploadJobNotFound as e:
        raise HTTPException(404, str(e))
    except uploads.JobAlreadyRunningError as e:
        raise HTTPException(409, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/uploads/{job_id}/deposit", response_model=schemas.UploadJobOut)
def set_deposit(
    job_id: int,
    req: schemas.DepositRequest,
    db: Session = Depends(get_db),
    user: auth.Principal = Depends(auth.require_user),
):
    try:
        return svc.set_deposit(
            db,
            job_id,
            req.scope_type,
            req.scope_ids,
            owner_sub=user.sub,
        )
    except uploads.UploadJobNotFound as e:
        raise HTTPException(404, str(e))
    except uploads.JobAlreadyRunningError as e:
        raise HTTPException(409, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/uploads/{job_id}/generate")
def generate_from_upload(
    job_id: int,
    req: schemas.GenerateFromUploadRequest,
    db: Session = Depends(get_db),
    user: auth.Principal = Depends(auth.require_user),
):
    """Identify & deposit questions from the uploaded MMD (streamed progress)."""
    if req.question_type not in {"auto", "objective", "subjective", "descriptive"}:
        raise HTTPException(
            400, "question_type must be auto | objective | subjective | descriptive")
    try:
        job = uploads.get_job(
            db, job_id, owner_sub=user.sub, module="build_assessments")
    except uploads.UploadJobNotFound as e:
        raise HTTPException(404, str(e))
    if job.status == "generated":
        raise HTTPException(
            409,
            "this upload has already been generated; start a new upload",
        )
    if uploads.is_job_running(job_id):
        raise HTTPException(
            409,
            "generation is already running for this upload; wait for the "
            "active run to finish before resuming",
        )

    def work():
        worker_db = SessionLocal()
        try:
            return uploads.run_with_openai_usage(
                worker_db,
                job_id,
                lambda: svc.generate_from_upload(
                    worker_db,
                    job_id,
                    req.question_type,
                    owner_sub=user.sub,
                ),
                owner_sub=user.sub,
            )
        finally:
            worker_db.close()
    return progress.stream(work, title="Build Assessments — generating from upload")


# --------------------------------------------------------------------------- #
# MES releases: dual downloads, issues, explicit database upload (spec §16)
# --------------------------------------------------------------------------- #

_XLSX_MEDIA = (
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


def _release_summary(release) -> dict:
    diagnostics = release.diagnostics or {}
    return {
        "id": release.id,
        "release_uid": release.release_uid,
        "version": release.version,
        "state": release.state,
        "readiness": diagnostics.get("readiness", ""),
        "concept_snapshot_sha256": release.concept_snapshot_sha256,
        "workbook_sha256s": release.workbook_hashes or {},
        "published": bool((release.publication or {}).get("manifest")),
        "uploaded": bool((release.publication or {}).get("uploaded_key")),
        "created_at": release.created_at.isoformat(),
    }


@router.get("/releases/{release_id}")
def get_assessment_release(
    release_id: int,
    db: Session = Depends(get_db),
    user: auth.Principal = Depends(auth.require_user),
):
    from ..services import assessment_release_service as release_svc

    try:
        release = release_svc.get_release(db, release_id, owner_sub=user.sub)
    except release_svc.ReleaseNotFound as e:
        raise HTTPException(404, str(e))
    return _release_summary(release)


@router.get("/releases/{release_id}/issues")
def get_assessment_release_issues(
    release_id: int,
    db: Session = Depends(get_db),
    user: auth.Principal = Depends(auth.require_user),
):
    from ..services import assessment_release_service as release_svc

    try:
        release = release_svc.get_release(db, release_id, owner_sub=user.sub)
    except release_svc.ReleaseNotFound as e:
        raise HTTPException(404, str(e))
    diagnostics = release.diagnostics or {}
    return {
        "readiness": diagnostics.get("readiness", ""),
        "payload_errors": diagnostics.get("payload_errors", []),
        "read_back": diagnostics.get("read_back", {}),
        "issues": diagnostics.get("issues", {}),
    }


def _release_artifact(release_id, db, user, filename: str, label: str):
    from fastapi import Response

    from ..services import assessment_release_service as release_svc

    try:
        release = release_svc.get_release(db, release_id, owner_sub=user.sub)
        data = release_svc.published_artifact(release, filename)
    except release_svc.ReleaseNotFound as e:
        raise HTTPException(404, str(e))
    name = f"aegis_{label}_{release.release_uid}_v{release.version}.xlsx"
    return Response(
        content=data,
        media_type=_XLSX_MEDIA,
        headers={
            "Content-Disposition": f'attachment; filename="{name}"'},
    )


@router.get("/releases/{release_id}/concepts.xlsx")
def download_release_concepts(
    release_id: int,
    db: Session = Depends(get_db),
    user: auth.Principal = Depends(auth.require_user),
):
    from ..services import assessment_release_service as release_svc

    return _release_artifact(
        release_id, db, user, release_svc.CONCEPTS_FILENAME, "concepts")


@router.get("/releases/{release_id}/master.xlsx")
def download_release_master(
    release_id: int,
    db: Session = Depends(get_db),
    user: auth.Principal = Depends(auth.require_user),
):
    from ..services import assessment_release_service as release_svc

    return _release_artifact(
        release_id, db, user, release_svc.MASTER_FILENAME, "master")


@router.post("/releases/{release_id}/upload-to-database")
def upload_release_master_to_database(
    release_id: int,
    db: Session = Depends(get_db),
    user: auth.Principal = Depends(auth.require_user),
):
    """Upload Master to Database — the explicit, idempotent action (§13.4).

    Generation never calls this; only a person does.
    """
    from ..services import assessment_release_service as release_svc

    try:
        release = release_svc.get_release(db, release_id, owner_sub=user.sub)
        return release_svc.upload_master_to_database(
            db, release, owner_sub=user.sub)
    except release_svc.ReleaseNotFound as e:
        raise HTTPException(404, str(e))
    except release_svc.UploadRefused as e:
        raise HTTPException(409, str(e))
