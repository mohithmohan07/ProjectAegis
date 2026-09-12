"""The chapter batch console API.

One page of the catalogue with its live run state, per-row source staging, and
a push that admits many chapters at once. Every act here drives only the
three-step routes; the legacy force-release, revisions and release-review acts
stay closed for these jobs (Q52).

Two shapes are deliberate:

* **a push never fails as a batch.** It always answers 200 with a per-row
  verdict, because one ineligible chapter must not silently drop the other
  nineteen a person selected.
* **nothing here invents a status.** Row state, the label for it, and which
  action a row offers are all derived server-side from the engine's own
  markers and shipped with the page, so the button a person sees and the answer
  they get cannot disagree.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..db import get_db
from ..services import auth, uploads
from ..services import build_concepts as build_concepts_svc
from ..services import chapter_batches, chapter_queue, chapter_queue_worker
from ..services import run_journal
from .upload_limits import read_limited_upload

router = APIRouter(prefix="/chapter-batches", tags=["chapter-batches"])


class PushRow(BaseModel):
    chapter_id: int
    # Named explicitly for a publish; never defaulted. Publishing the lane the
    # caller did not name is an authenticated write nobody authorised.
    lanes: list[str] | None = None


class PushRequest(BaseModel):
    step: str = Field(..., description="step01 | step02 | publish")
    rows: list[PushRow] = Field(default_factory=list)


class ChapterIdsRequest(BaseModel):
    chapter_ids: list[int] = Field(default_factory=list)


def _running_probe(job_id: int) -> bool:
    return uploads.is_job_running(int(job_id))


def _row_or_404(db: Session, chapter_id: int) -> dict[str, Any]:
    row = chapter_batches.project_one(
        db, chapter_id, running_probe=_running_probe,
    )
    if row is None:
        raise HTTPException(404, "chapter not found")
    return row


def _bound_job(db: Session, chapter_id: int):
    """The job on this chapter's console row, or a readable refusal."""
    batch_row = (
        db.query(chapter_batches.models.ChapterBatchRow)
        .filter(chapter_batches.models.ChapterBatchRow.chapter_id == int(chapter_id))
        .one_or_none()
    )
    if batch_row is None or not batch_row.job_id:
        raise HTTPException(
            404, "no source file has been staged for this chapter",
        )
    return batch_row


@router.get("")
def list_rows(
    board: str = "",
    grade: str = "",
    subject: str = "",
    q: str = "",
    state: str = "",
    page: int = 1,
    page_size: int = 25,
    db: Session = Depends(get_db),
    user: auth.Principal = Depends(auth.require_user),
):
    """One page of chapters with everything the table renders."""
    return chapter_batches.list_page(
        db,
        board=board, grade=grade, subject=subject, q=q, state=state,
        page=page, page_size=page_size,
        capacity=chapter_queue_worker.capacity(),
        worker_alive=chapter_queue_worker.worker_alive(),
        running_probe=_running_probe,
    )


@router.get("/{chapter_id}")
def get_row(
    chapter_id: int,
    db: Session = Depends(get_db),
    user: auth.Principal = Depends(auth.require_user),
):
    """One row plus the full job — the only place the big payload is loaded."""
    row = _row_or_404(db, chapter_id)
    job = None
    if row.get("job_id"):
        try:
            job = uploads.get_job_for_reader(
                db, int(row["job_id"]), owner_sub=user.sub,
                module="build_concepts",
            )
        except uploads.UploadJobNotFound:
            job = None
    return {"row": row, "job": job}


@router.get("/{chapter_id}/events")
def get_events(
    chapter_id: int,
    after: int = 0,
    db: Session = Depends(get_db),
    user: auth.Principal = Depends(auth.require_user),
):
    """The run journal tail for this chapter's bound job."""
    batch_row = _bound_job(db, chapter_id)
    job = uploads.get_job_for_reader(
        db, int(batch_row.job_id), owner_sub=user.sub, module="build_concepts",
    )
    payload = run_journal.read_after(int(job.id), after)
    # "Running" for the console is the queue's lease, never the process-local
    # job lock, which reads False after a restart for a chapter still mid-run.
    row = _row_or_404(db, chapter_id)
    payload["running"] = row["state"] in {
        "step01_running", "step02_running", "publish_running",
    }
    return payload


@router.post("/{chapter_id}/source")
async def stage_source(
    chapter_id: int,
    source_book: str = "",
    chapter_duration_minutes: int = 0,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: auth.Principal = Depends(auth.require_user),
):
    """Stage a PDF against a chapter. Nothing is spent until it is pushed.

    ``source_book`` is the publication this upload came from and becomes the
    Concept Source and extracted Post-learning Question Source (Q42/Q45). It is
    an explicit field, never synthesised from the chapter code.
    """
    chapter = db.get(chapter_batches.models.Chapter, int(chapter_id))
    if chapter is None:
        raise HTTPException(404, "chapter not found")

    row = chapter_batches.get_or_create_row(
        db, int(chapter_id), actor_sub=user.sub, actor_email=user.email,
    )
    current = chapter_batches.project_one(
        db, int(chapter_id), running_probe=_running_probe,
    )
    if current and not current["can"]["upload_source"]:
        raise HTTPException(
            409,
            "this chapter is busy or cannot be resumed; it cannot take a new "
            "source file right now",
        )

    try:
        raw_bytes = await read_limited_upload(file)
        job = build_concepts_svc.create_post_learning_job(
            db,
            filename=file.filename or "document.pdf",
            raw_bytes=raw_bytes,
            source_book=source_book,
            chapter_duration_minutes=chapter_duration_minutes,
            owner_sub=user.sub,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    if row.job_id and int(row.job_id) != int(job.id):
        # A replaced source never erases the run that came before it.
        previous = list(row.previous_job_ids or [])
        previous.append(int(row.job_id))
        row.previous_job_ids = previous
    row.job_id = int(job.id)
    row.source_filename = Path(file.filename or "document.pdf").name
    row.source_book = str(source_book or "").strip()
    row.chapter_duration_minutes = int(chapter_duration_minutes or 0)
    row.source_staged_at = chapter_batches._now()
    if not row.created_by_sub:
        row.created_by_sub = user.sub
        row.created_by_email = user.email
    chapter_batches.record_act(
        row, act="staged the source", actor_sub=user.sub, actor_email=user.email,
    )
    db.commit()
    return _row_or_404(db, chapter_id)


@router.post("/push")
def push(
    payload: PushRequest,
    db: Session = Depends(get_db),
    user: auth.Principal = Depends(auth.require_user),
):
    """Admit many chapters for one step. Always 200, one verdict per row."""
    step = str(payload.step or "").strip()
    if step not in chapter_batches.models.CHAPTER_BATCH_STEPS:
        raise HTTPException(
            400,
            "step must be one of "
            + ", ".join(chapter_batches.models.CHAPTER_BATCH_STEPS),
        )
    group = chapter_queue.new_push_group_id()
    results: list[dict[str, Any]] = []
    for item in payload.rows:
        outcome = chapter_queue.enqueue_one(
            db, int(item.chapter_id), step=step, push_group_id=group,
            actor_sub=user.sub, actor_email=user.email,
            lanes=item.lanes, running_probe=_running_probe,
        )
        db.commit()
        outcome["row"] = chapter_batches.project_one(
            db, int(item.chapter_id), running_probe=_running_probe,
        )
        results.append(outcome)
    positions = chapter_batches.queue_positions(db)
    for outcome in results:
        if outcome.get("task_id"):
            outcome["position"] = positions.get(int(outcome["task_id"]))
    # A push should start now, not at the next poll tick.
    chapter_queue_worker.nudge()
    return {"step": step, "push_group_id": group, "results": results}


def _bulk(db: Session, user: auth.Principal, chapter_ids: list[int], act) -> dict:
    results: list[dict[str, Any]] = []
    for chapter_id in chapter_ids:
        outcome = act(
            db, int(chapter_id), actor_sub=user.sub, actor_email=user.email,
        )
        db.commit()
        outcome["row"] = chapter_batches.project_one(
            db, int(chapter_id), running_probe=_running_probe,
        )
        results.append(outcome)
    return {"step": "", "push_group_id": "", "results": results}


@router.post("/cancel")
def cancel(
    payload: ChapterIdsRequest,
    db: Session = Depends(get_db),
    user: auth.Principal = Depends(auth.require_user),
):
    """Take queued chapters out of the line. A running step is never cancelled."""
    return _bulk(db, user, payload.chapter_ids, chapter_queue.cancel_one)


@router.post("/retry")
def retry(
    payload: ChapterIdsRequest,
    db: Session = Depends(get_db),
    user: auth.Principal = Depends(auth.require_user),
):
    """Return blocked or failed chapters to the queue — an explicit human act."""
    result = _bulk(db, user, payload.chapter_ids, chapter_queue.retry_one)
    chapter_queue_worker.nudge()
    return result


@router.post("/{chapter_id}/concept-review")
async def upload_concept_review(
    chapter_id: int,
    lane: str = Query(..., description="Concept lane: post or pre"),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: auth.Principal = Depends(auth.require_user),
):
    """Step 02 input: the team's reviewed Concept file for one lane.

    Delegates to the existing reviewed-Concept route so there is exactly one
    implementation of a review round — the console addresses it by chapter, the
    Build Concepts page by job.
    """
    from ..services.build_concepts_release_api_contract import (
        _concept_review_upload_endpoint,
    )

    batch_row = _bound_job(db, chapter_id)
    await _concept_review_upload_endpoint(
        job_id=int(batch_row.job_id), lane=lane, file=file, db=db, user=user,
    )
    chapter_batches.record_act(
        batch_row, act=f"uploaded the reviewed {lane} Concept file",
        actor_sub=user.sub, actor_email=user.email,
    )
    db.commit()
    return _row_or_404(db, chapter_id)


@router.post("/{chapter_id}/master-review")
async def upload_master_review(
    chapter_id: int,
    lane: str = Query(..., description="Master lane: post or pre"),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: auth.Principal = Depends(auth.require_user),
):
    """Step 03 input: the team's reviewed Master file for one lane."""
    from .build_concepts import submit_reviewed_master_file

    batch_row = _bound_job(db, chapter_id)
    await submit_reviewed_master_file(
        job_id=int(batch_row.job_id), lane=lane, file=file, db=db, user=user,
    )
    chapter_batches.record_act(
        batch_row, act=f"uploaded the reviewed {lane} Master file",
        actor_sub=user.sub, actor_email=user.email,
    )
    db.commit()
    return _row_or_404(db, chapter_id)
