from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import Response
from sqlalchemy.orm import Session

from .. import schemas
from ..db import SessionLocal, get_db
from ..services import build_concepts as svc
from ..services import (
    auth,
    build_concepts_release as release_svc,
    concept_revisions as revisions_svc,
    build_concepts_release_files as release_files,
    build_concepts_release_publication as release_publication,
    release_review as review_svc,
    checkpoints,
    drive_checkpoints,
    progress,
    uploads,
)
from .upload_limits import read_limited_upload

router = APIRouter(prefix="/build-concepts", tags=["build-concepts"])


# --------------------------------------------------------------------------- #
# Release lane (spec T3): which of the job's two staged outputs a download or
# an explicit publication acts on.
#
# A DOWNLOAD defaults to the Post lane, so every existing caller and every
# recorded URL keeps serving Outputs 03/04 exactly as before; Outputs 01/02
# (the Phase 03 Pre-Learning outputs) are reached only by asking for them.
#
# (The numbering here is the owner's, ruling OD4 / register entry D9-Q22:
# 01 Pre Concept, 02 Pre Master, 03 Post Concept, 04 Post Master. Earlier
# text in this file called the Post pair "Outputs 01/02"; it is superseded,
# not wrong. A description string that lies about which lane it serves is
# how the wrong lane gets published by hand.)
#
# A PUBLICATION does NOT default — see ``PUBLISH_LANE_QUERY`` below.
# --------------------------------------------------------------------------- #

LANE_QUERY = Query(
    release_svc.LANE_POST,
    description=(
        "Which staged release to serve: 'post' (Outputs 03/04, the "
        "Post-Learning outputs) or 'pre' (Outputs 01/02, the Phase 03 "
        "Pre-Learning outputs). Downloads default to 'post'."
    ),
)


# --------------------------------------------------------------------------- #
# The publication lane, which is a DIFFERENT question from the download lane
# and must not share its default.
#
# ``release_svc.normalize_lane`` answers "post" for both ``""`` and a missing
# value. For a download that is right and stays: it is backward compatibility
# for every recorded URL, and a download of the wrong lane costs a reviewer
# one click. For a PUBLICATION it is not recoverable — a request that simply
# omits the lane would perform an authenticated write against a lane the
# reviewer never named, and Rule G makes publication a separate, explicit act.
#
# The gate lives HERE, on the server, and not in a client. A server that
# trusts its caller to send the right lane has the same hole open for every
# other caller — curl, a replayed URL, the next client. This is mechanics in
# the CLAUDE.md sense: it refuses an unsafe REQUEST and makes no judgment
# about content.
# --------------------------------------------------------------------------- #

PUBLISH_LANE_QUERY = Query(
    "",
    description=(
        "REQUIRED. Which staged release this act publishes: 'post' "
        "(Outputs 03/04) or 'pre' (Outputs 01/02). Publication has no "
        "default lane; a blank or absent value is refused with 400."
    ),
)

_PUBLISH_LANE_REQUIRED = (
    "publishing requires an explicit release lane: pass lane=post (the "
    "Post-Learning outputs 03/04) or lane=pre (the Pre-Learning outputs "
    "01/02). Publication is a separate, explicit, authenticated act and "
    "has no default lane — a defaulted lane would publish staged rows the "
    "reviewer never named. Downloads are unaffected and still default to "
    "post."
)


def _lane(value: str) -> str:
    try:
        return release_svc.normalize_lane(value)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


def _publish_lane(value: object) -> str:
    """The lane a PUBLICATION acts on. Never defaulted; blank is a 400.

    The message names both valid values, so the refusal tells the caller
    what to send rather than only that it was wrong.
    """

    if not str(value or "").strip():
        raise HTTPException(400, _PUBLISH_LANE_REQUIRED)
    return _lane(str(value))


def _tag(value: str) -> str:
    """Filename infix: empty for Post, so Post filenames are unchanged."""

    return "pre_" if _lane(value) == release_svc.LANE_PRE else ""


# --------------------------------------------------------------------------- #
# Model provider selection (OpenAI / Gemini) — applies to the next run
# --------------------------------------------------------------------------- #

@router.get("/model-provider")
def get_model_provider(
    user: auth.Principal = Depends(auth.require_user),
):
    from ..services import model_provider

    return model_provider.describe()


@router.put("/model-provider")
def set_model_provider(
    payload: dict,
    user: auth.Principal = Depends(auth.require_user),
):
    from ..services import model_provider

    try:
        return model_provider.set_active_provider(
            str((payload or {}).get("provider") or "")
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))


# --------------------------------------------------------------------------- #
# Shared upload helpers (stage → replace → convert)
# --------------------------------------------------------------------------- #

@router.get("/uploads/{job_id}", response_model=schemas.UploadJobOut)
def get_upload(
    job_id: int,
    db: Session = Depends(get_db),
    user: auth.Principal = Depends(auth.require_user),
):
    try:
        return uploads.get_job(
            db, job_id, owner_sub=user.sub, module="build_concepts")
    except uploads.UploadJobNotFound as e:
        raise HTTPException(404, str(e))


@router.get("/uploads/{job_id}/run-events")
def get_run_events(
    job_id: int,
    after: int = 0,
    db: Session = Depends(get_db),
    user: auth.Principal = Depends(auth.require_user),
):
    """The run's journaled events after a cursor — lossless catch-up.

    A phone that was away reads everything it missed here and rejoins the
    live tail exactly current; the run itself never depended on the
    client being attached. ``next`` is the cursor to poll with; the
    terminal ``result``/``error`` event is in the journal too, so a
    reader can finish a run without re-POSTing it.
    """
    from ..services import run_journal

    try:
        job = uploads.get_job(
            db, job_id, owner_sub=user.sub, module="build_concepts")
    except uploads.UploadJobNotFound as e:
        raise HTTPException(404, str(e))
    payload = run_journal.read_after(job_id, after)
    payload["running"] = bool(job.generation_running)
    return payload


@router.post(
    "/uploads/{job_id}/decisions/{decision_id}",
    response_model=schemas.HumanSemanticDecisionResponse,
)
def record_human_semantic_decision(
    job_id: int,
    decision_id: str,
    req: schemas.HumanSemanticDecisionRequest,
    db: Session = Depends(get_db),
    user: auth.Principal = Depends(auth.require_user),
):
    """Legacy route retained only to give old clients an explicit answer.

    Build Concepts no longer accepts a human semantic choice during generation.
    The complete unresolved decision remains available in the diagnostic export,
    and the newest durable rows are released with their errors attached.
    """
    del job_id, decision_id, req, db, user
    raise HTTPException(
        409,
        "Build Concepts is unattended. Manual semantic selection is disabled; "
        "download or release the staged output with its diagnostic context.",
    )


@router.put("/uploads/{job_id}/file", response_model=schemas.UploadJobOut)
async def replace_upload_file(
    job_id: int, file: UploadFile = File(...), db: Session = Depends(get_db),
    user: auth.Principal = Depends(auth.require_user),
):
    try:
        raw_bytes = await read_limited_upload(file)
        return uploads.replace_file(
            db, job_id, filename=file.filename or "document.txt",
            raw_bytes=raw_bytes, owner_sub=user.sub,
            module="build_concepts")
    except uploads.UploadJobNotFound as e:
        raise HTTPException(404, str(e))
    except uploads.JobAlreadyRunningError as e:
        raise HTTPException(409, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/uploads/{job_id}/inventory.csv")
def download_inventory_csv(
    job_id: int,
    db: Session = Depends(get_db),
    user: auth.Principal = Depends(auth.require_user),
):
    """Question / Task Inventory CSV — one row per extracted question/task,
    with the mined Type(s) each item was classified into, so extraction
    completeness can be audited."""
    try:
        csv_text = svc.inventory_csv(db, job_id, owner_sub=user.sub)
    except uploads.UploadJobNotFound as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(404, str(e))
    return Response(
        content=csv_text,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition":
                f'attachment; filename="question_task_inventory_job_{job_id}.csv"',
        },
    )


@router.get("/uploads/{job_id}/checkpoint")
def download_checkpoint(
    job_id: int,
    db: Session = Depends(get_db),
    user: auth.Principal = Depends(auth.require_user),
):
    """Download a portable converted-source + generation checkpoint bundle."""
    try:
        filename, content = checkpoints.export_bundle(
            db, job_id, owner_sub=user.sub)
    except uploads.UploadJobNotFound as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(404, str(e))
    return Response(
        content=content,
        media_type="application/json; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.post(
    "/checkpoints/import",
    response_model=schemas.UploadJobOut,
)
async def import_checkpoint(
    file: UploadFile = File(...),
    learning_kind: str = "",
    db: Session = Depends(get_db),
    user: auth.Principal = Depends(auth.require_user),
):
    """Restore a portable checkpoint as a new converted Build Concepts job."""
    try:
        raw_bytes = await file.read(checkpoints.MAX_IMPORT_BYTES + 1)
        if len(raw_bytes) > checkpoints.MAX_IMPORT_BYTES:
            raise HTTPException(
                413, "checkpoint file exceeds the 25 MB import limit")
        return checkpoints.import_bundle(
            db,
            raw_bytes,
            expected_learning_kind=learning_kind,
            owner_sub=user.sub,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.delete(
    "/uploads/{job_id}/checkpoint",
    response_model=schemas.UploadJobOut,
)
def clear_checkpoint(
    job_id: int,
    db: Session = Depends(get_db),
    user: auth.Principal = Depends(auth.require_user),
):
    try:
        return checkpoints.clear_checkpoint(
            db, job_id, owner_sub=user.sub)
    except uploads.UploadJobNotFound as e:
        raise HTTPException(404, str(e))
    except uploads.JobAlreadyRunningError as e:
        raise HTTPException(409, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get(
    "/checkpoints/resumable",
    response_model=schemas.ResumableCheckpointJobs,
)
def list_resumable_checkpoints(
    learning_kind: str,
    db: Session = Depends(get_db),
    user: auth.Principal = Depends(auth.require_user),
):
    try:
        items, total = checkpoints.resumable_jobs(
            db,
            owner_sub=user.sub,
            learning_kind=learning_kind,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"items": items, "total": total}


# --------------------------------------------------------------------------- #
# Released output and diagnostic context
# --------------------------------------------------------------------------- #

@router.post(
    "/uploads/{job_id}/release",
    response_model=schemas.UploadJobOut,
)
def release_latest_output(
    job_id: int,
    db: Session = Depends(get_db),
    user: auth.Principal = Depends(auth.require_user),
):
    """Release the newest durable output without publishing it to the DB."""
    try:
        job = uploads.get_job(
            db, job_id, owner_sub=user.sub, module="build_concepts")
        if release_svc.release_available(job):
            return job
        return release_svc.force_release(
            db, job_id, owner_sub=user.sub
        )
    except uploads.UploadJobNotFound as e:
        raise HTTPException(404, str(e))
    except uploads.JobAlreadyRunningError as e:
        raise HTTPException(409, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/uploads/{job_id}/release-bulk-import.xlsx")
def download_release_bulk_import(
    job_id: int,
    lane: str = LANE_QUERY,
    db: Session = Depends(get_db),
    user: auth.Principal = Depends(auth.require_user),
):
    """The released rows in the canonical Bulk Import workbook format."""
    try:
        job = uploads.get_job(
            db, job_id, owner_sub=user.sub, module="build_concepts")
        content = release_files.build_release_bulk_import_workbook(
            db, job, lane=_lane(lane)
        )
    except uploads.UploadJobNotFound as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(404, str(e))
    return Response(
        content=content,
        media_type=(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ),
        headers={
            "Content-Disposition":
                f'attachment; filename="bulk_import_{_tag(lane)}job_'
                f'{job_id}.xlsx"',
        },
    )


@router.get("/uploads/{job_id}/release.xlsx")
def download_released_workbook(
    job_id: int,
    lane: str = LANE_QUERY,
    db: Session = Depends(get_db),
    user: auth.Principal = Depends(auth.require_user),
):
    try:
        job = uploads.get_job(
            db, job_id, owner_sub=user.sub, module="build_concepts")
        content = release_files.build_release_workbook(job, lane=_lane(lane))
    except uploads.UploadJobNotFound as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(404, str(e))
    return Response(
        content=content,
        media_type=(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ),
        headers={
            "Content-Disposition":
                f'attachment; filename="concept_release_{_tag(lane)}job_'
                f'{job_id}.xlsx"',
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/uploads/{job_id}/diagnostics.zip")
def download_release_diagnostics(
    job_id: int,
    lane: str = LANE_QUERY,
    db: Session = Depends(get_db),
    user: auth.Principal = Depends(auth.require_user),
):
    try:
        job = uploads.get_job(
            db, job_id, owner_sub=user.sub, module="build_concepts")
        content = release_files.build_diagnostics_zip(job, lane=_lane(lane))
    except uploads.UploadJobNotFound as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(404, str(e))
    return Response(
        content=content,
        media_type="application/zip",
        headers={
            "Content-Disposition":
                f'attachment; filename="concept_diagnostics_{_tag(lane)}'
                f'job_{job_id}.zip"',
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/uploads/{job_id}/release.json")
def download_release_payload(
    job_id: int,
    lane: str = LANE_QUERY,
    db: Session = Depends(get_db),
    user: auth.Principal = Depends(auth.require_user),
):
    try:
        job = uploads.get_job(
            db, job_id, owner_sub=user.sub, module="build_concepts")
        content = release_files.release_payload_bytes(job, lane=_lane(lane))
    except uploads.UploadJobNotFound as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(404, str(e))
    return Response(
        content=content,
        media_type="application/json; charset=utf-8",
        headers={
            "Content-Disposition":
                f'attachment; filename="concept_release_{_tag(lane)}job_'
                f'{job_id}.json"',
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.post("/uploads/{job_id}/upload-release")
def upload_released_output_to_database(
    job_id: int,
    lane: str = PUBLISH_LANE_QUERY,
    db: Session = Depends(get_db),
    user: auth.Principal = Depends(auth.require_user),
):
    """Explicitly publish the staged rows; generation never calls this route.

    Rule G is unchanged by the Pre lane: publication stays one separate,
    explicit, authenticated act — ``lane`` only says WHICH staged output
    this act publishes, and each lane needs its own act.

    ``lane`` is REQUIRED here and only here. The download routes above
    keep their Post default; this one refuses a blank or absent lane with
    a 400 naming both valid values, because publishing the lane the
    caller did not name is an authenticated write nobody authorised.
    """
    resolved = _publish_lane(lane)
    try:
        return release_publication.upload_release_to_database(
            db,
            job_id,
            owner_sub=user.sub,
            lane=resolved,
        )
    except uploads.UploadJobNotFound as e:
        raise HTTPException(404, str(e))
    except release_svc.ReleaseUnavailableError as e:
        raise HTTPException(404, str(e))
    except uploads.JobAlreadyRunningError as e:
        raise HTTPException(409, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/uploads/{job_id}/upload-edited-workbook")
async def upload_edited_workbook_to_cms(
    job_id: int,
    lane: str = PUBLISH_LANE_QUERY,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: auth.Principal = Depends(auth.require_user),
):
    """The reviewer's local Excel edits, applied and published in one act.

    Owner steer, 2026-08-20: the downloaded Concept workbook, edited in
    Excel on the reviewer's machine, is uploaded here — it becomes one
    recorded review round (same trail and version rows as the review
    page) and is then published to the CMS through the one publication
    writer. This replaces the separate upload-to-database button for the
    concept outputs. ``lane`` is REQUIRED, exactly as it is for every
    other publication act.
    """

    import tempfile
    from pathlib import Path as _Path

    from ..services import release_workbook_edits
    from ..services import release_review as review_svc_errors

    resolved = _publish_lane(lane)
    try:
        job = uploads.get_job(
            db, job_id, owner_sub=user.sub, module="build_concepts"
        )
        raw_bytes = await read_limited_upload(
            file, description="edited Concept workbook"
        )
        with tempfile.NamedTemporaryFile(
            suffix=".xlsx", delete=False
        ) as handle:
            handle.write(raw_bytes)
            temp_path = _Path(handle.name)
        try:
            return release_workbook_edits.apply_workbook_and_publish(
                db, job,
                lane=resolved,
                workbook_path=temp_path,
                owner_sub=user.sub,
            )
        finally:
            temp_path.unlink(missing_ok=True)
    except release_workbook_edits.WorkbookEditError as e:
        raise HTTPException(422, str(e))
    except review_svc_errors.ReviewConflict as e:
        raise HTTPException(409, str(e))
    except review_svc_errors.ReviewEditError as e:
        raise HTTPException(422, str(e))
    except uploads.UploadJobNotFound as e:
        raise HTTPException(404, str(e))
    except release_svc.ReleaseUnavailableError as e:
        raise HTTPException(404, str(e))
    except uploads.JobAlreadyRunningError as e:
        raise HTTPException(409, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))


# --------------------------------------------------------------------------- #
# Post-run reviewer revisions
#
# The reviewer downloads the released workbook (``release.xlsx`` above), reads
# it, and describes what needs correcting. Aegis applies the instruction to the
# job's concepts; the workbook is rebuilt from those concepts, so the delivered
# file only ever carries corrected content and never the review history itself.
# --------------------------------------------------------------------------- #

@router.post(
    "/uploads/{job_id}/revisions",
    response_model=schemas.ConceptRevisionOut,
)
def submit_concept_revision(
    job_id: int,
    payload: schemas.ConceptRevisionIn,
    db: Session = Depends(get_db),
    user: auth.Principal = Depends(auth.require_user),
):
    """Record one reviewer instruction and apply it. Rounds are unlimited."""

    try:
        job = uploads.get_job(
            db, job_id, owner_sub=user.sub, module="build_concepts")
    except uploads.UploadJobNotFound as e:
        raise HTTPException(404, str(e))

    try:
        revision = revisions_svc.record_instruction(
            db, job, payload.instruction, owner_sub=user.sub
        )
    except revisions_svc.RevisionError as e:
        raise HTTPException(400, str(e))

    # A provider failure is recorded on the round, not raised: the reviewer's
    # words are already committed and must not be lost with the attempt.
    applied = revisions_svc.apply_instruction(db, job, revision)
    return revisions_svc.revision_summary(applied)


@router.get(
    "/uploads/{job_id}/revisions",
    response_model=schemas.ConceptRevisionListOut,
)
def list_concept_revisions(
    job_id: int,
    db: Session = Depends(get_db),
    user: auth.Principal = Depends(auth.require_user),
):
    """Return every round for this job, oldest first."""

    try:
        job = uploads.get_job(
            db, job_id, owner_sub=user.sub, module="build_concepts")
    except uploads.UploadJobNotFound as e:
        raise HTTPException(404, str(e))
    return {
        "job_id": job.id,
        "revisions": [
            revisions_svc.revision_summary(row)
            for row in revisions_svc.list_revisions(
                db, job.id, owner_sub=user.sub
            )
        ],
    }


# --------------------------------------------------------------------------- #
# Step 9 — the staged-release review & edit surface (doc §7)
#
# The reviewer opens the STAGED release as a rendered page, edits fields in
# place (applied verbatim, recorded, nothing re-runs), or types a
# plain-language instruction (ONE bounded model pass). Every applied round
# mints a new staged_release_uid + staged_version and an immutable
# ConceptReleaseVersion row; a stale uid is refused with 409 so two open
# tabs can never silently overwrite each other. Publication stays the
# separate explicit act above (Rule G) — this surface never uploads.
# --------------------------------------------------------------------------- #

def _review_job(db: Session, job_id: int, owner_sub: str):
    try:
        return uploads.get_job(
            db, job_id, owner_sub=owner_sub, module="build_concepts")
    except uploads.UploadJobNotFound as e:
        raise HTTPException(404, str(e))


@router.get(
    "/uploads/{job_id}/release-review",
    response_model=schemas.ReleaseReviewViewOut,
)
def get_release_review(
    job_id: int,
    lane: str = LANE_QUERY,
    db: Session = Depends(get_db),
    user: auth.Principal = Depends(auth.require_user),
):
    """The staged release rendered for review (read-only projection)."""

    job = _review_job(db, job_id, user.sub)
    try:
        return review_svc.review_view(db, job, _lane(lane))
    except review_svc.ReviewUnavailable as e:
        raise HTTPException(404, str(e))


@router.post(
    "/uploads/{job_id}/release-review/manual-edit",
    response_model=schemas.ReleaseReviewViewOut,
)
def apply_release_manual_edit(
    job_id: int,
    payload: schemas.ReleaseReviewManualEditIn,
    db: Session = Depends(get_db),
    user: auth.Principal = Depends(auth.require_user),
):
    """Apply the reviewer's verbatim field edits as one recorded round."""

    job = _review_job(db, job_id, user.sub)
    try:
        return review_svc.apply_manual_edits(
            db, job,
            lane=_lane(payload.lane),
            staged_release_uid=payload.staged_release_uid,
            edits=[edit.model_dump() for edit in payload.edits],
            owner_sub=user.sub,
        )
    except review_svc.ReviewUnavailable as e:
        raise HTTPException(404, str(e))
    except review_svc.ReviewConflict as e:
        raise HTTPException(409, str(e))
    except review_svc.ReviewEditError as e:
        raise HTTPException(422, str(e))


@router.post(
    "/uploads/{job_id}/release-review/apply-instruction",
    response_model=schemas.ReleaseReviewViewOut,
)
def apply_release_instruction(
    job_id: int,
    payload: schemas.ReleaseReviewInstructionIn,
    db: Session = Depends(get_db),
    user: auth.Principal = Depends(auth.require_user),
):
    """One bounded model pass applies the reviewer's change list (§7).

    A failed pass is 502 — the round is already recorded as an
    ``instruction_failed`` version row (the reviewer's words are never
    lost with the outage), and the staged release is unchanged.
    """

    job = _review_job(db, job_id, user.sub)
    try:
        return review_svc.apply_instruction_round(
            db, job,
            lane=_lane(payload.lane),
            staged_release_uid=payload.staged_release_uid,
            instruction=payload.instruction,
            owner_sub=user.sub,
        )
    except review_svc.ReviewUnavailable as e:
        raise HTTPException(404, str(e))
    except review_svc.ReviewConflict as e:
        raise HTTPException(409, str(e))
    except review_svc.ReviewEditError as e:
        raise HTTPException(422, str(e))
    except review_svc.InstructionRoundFailed as e:
        raise HTTPException(502, str(e))


@router.post("/uploads/{job_id}/convert")
def convert_upload(
    job_id: int,
    db: Session = Depends(get_db),
    user: auth.Principal = Depends(auth.require_user),
):
    """Convert the staged document to MMD (streamed progress)."""
    try:
        job = uploads.get_job(
            db, job_id, owner_sub=user.sub, module="build_concepts")
    except uploads.UploadJobNotFound as e:
        raise HTTPException(404, str(e))
    if job.status in {"generated", release_svc.RELEASE_STATUS}:
        raise HTTPException(
            409,
            "this upload already has a released or published output; start a "
            "new upload to change the source",
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
                module="build_concepts",
            )
        finally:
            worker_db.close()
    return progress.stream(
        work,
        title="Preparing the document source",
        journal_job_id=job_id,
    )


# --------------------------------------------------------------------------- #
# Post Learning
# --------------------------------------------------------------------------- #

@router.post("/post-learning/uploads", response_model=schemas.UploadJobOut)
async def post_learning_upload(
    source_book: str = "",
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: auth.Principal = Depends(auth.require_user),
):
    """Stage the file only — conversion to MMD is a separate /convert step."""
    try:
        raw_bytes = await read_limited_upload(file)
        return svc.create_post_learning_job(
            db, filename=file.filename or "document.txt", raw_bytes=raw_bytes,
            source_book=source_book,
            owner_sub=user.sub,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/post-learning/uploads/{job_id}/generate")
def post_learning_generate(
    job_id: int,
    req: schemas.PostLearningGenerateRequest,
    db: Session = Depends(get_db),
    user: auth.Principal = Depends(auth.require_user),
):
    try:
        job = uploads.get_job(
            db,
            job_id,
            owner_sub=user.sub,
            module="build_concepts",
            learning_kind="post",
        )
    except uploads.UploadJobNotFound as e:
        raise HTTPException(404, str(e))
    if job.status in {"generated", release_svc.RELEASE_STATUS}:
        raise HTTPException(
            409,
            "this upload already has a released or published output; start a "
            "new upload",
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
                lambda: svc.generate_post_learning(
                    worker_db,
                    job_id,
                    req.target_chapter_id,
                    owner_sub=user.sub,
                ),
                owner_sub=user.sub,
            )
        finally:
            # Queue the post-accounting state as well as each stage checkpoint.
            drive_checkpoints.schedule_checkpoint_backup(job_id)
            worker_db.close()
    return progress.stream(
        work,
        title="Build Concepts — post-learning generation",
        journal_job_id=job_id,
    )
