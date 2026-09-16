"""Adopt interrupted interactive Concept runs into the durable queue.

Only recorded lifecycle markers and exact saved chapter IDs authorize recovery.
No source is read, no title is matched, and no reviewed/completed run is reopened.
Existing queued/leased/blocked tasks remain the queue owner's responsibility.
"""
from __future__ import annotations

import hashlib
from collections.abc import Mapping
from datetime import datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import models
from . import build_concepts_release as release
from . import generation_recovery, uploads

RECOVERY_REQUEST_KEY = "_aegis_run_recovery_request"


def _mapping(value) -> dict:
    return dict(value) if isinstance(value, Mapping) else {}


def _chapter_id(value) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    if isinstance(value, str) and value.isdecimal() and int(value) > 0:
        return int(value)
    return None


def _active_step(job: models.UploadJob) -> str | None:
    marker = release.concept_review_state(job)
    clock = _mapping(job.run_state)
    if generation_recovery.blocked_recovery(job) or job.pending_decision:
        return None
    if str(job.status or "") in {"generated", "failed", "cancelled"}:
        return None
    if clock.get("status") in {"completed", "failed"}:
        return None
    if marker:
        return "step02" if marker.get("status") == release.CONCEPT_REVIEW_MASTER_BUILDING else None
    if str(job.status or "") == release.RELEASE_STATUS:
        return None
    previous = _mapping(_mapping(job.question_inventory).get(RECOVERY_REQUEST_KEY))
    if (clock.get("status") == "waiting" and previous.get("status") == "blocked"
            and previous.get("step") == "step02"):
        return "step02"
    if clock.get("status") in {"processing", "waiting"}:
        return "step01"
    # A Master clock without its reviewed-input marker is not permission to
    # generate from the source again. The caller records the missing boundary.
    return "step02" if clock.get("status") == "master" else None


def _record(db: Session, job, *, status: str, code: str, reason: str,
            step: str, chapter_id: int | None = None, task_id: int | None = None):
    record = {
        "version": 1, "status": status, "reason_code": code, "reason": reason,
        "step": step, "chapter_id": chapter_id, "task_id": task_id,
    }
    inventory = _mapping(job.question_inventory)
    previous = _mapping(inventory.get(RECOVERY_REQUEST_KEY))
    if {key: previous.get(key) for key in record} != record:
        record["recorded_at"] = datetime.utcnow().isoformat() + "Z"
        inventory[RECOVERY_REQUEST_KEY] = record
        job.question_inventory = inventory
    # Preserve the last durable active-time counter. Counting from the old
    # process's anchor to this boot would bill downtime as processing time.
    clock = _mapping(job.run_state)
    if clock:
        clock.update(status="waiting", active_started_at_epoch=None,
                     review_started_at_epoch=None)
        job.run_state = clock
    job.detail = reason
    db.commit()


def recover_interrupted_runs(db: Session) -> list[int]:
    """Queue the same saved work once, preserving job/source/run identities."""
    recovered: list[int] = []
    jobs = db.query(models.UploadJob).filter_by(module="build_concepts").all()
    for job in jobs:
        step = _active_step(job)
        if not step or uploads.is_job_running(int(job.id)):
            continue
        # An existing durable task, including a foreign worker's valid lease,
        # is never re-enqueued by this legacy-interactive migration.
        if db.query(models.ChapterBatchTask.id).filter(
            models.ChapterBatchTask.job_id == job.id,
            models.ChapterBatchTask.state.in_(models.CHAPTER_BATCH_LIVE_TASK_STATES),
        ).first():
            continue
        bound = db.query(models.ChapterBatchRow).filter_by(job_id=job.id).all()
        if any(db.query(models.ChapterBatchTask.id).filter(
            models.ChapterBatchTask.batch_row_id == row.id,
            models.ChapterBatchTask.state.in_(models.CHAPTER_BATCH_LIVE_TASK_STATES),
        ).first() for row in bound):
            continue

        checkpoint = _mapping(job.generation_checkpoint)
        marker = release.concept_review_state(job)
        values = [job.requested_chapter_id, checkpoint.get("target_chapter_id"),
                  _mapping(checkpoint.get("context")).get("target_chapter_id"),
                  marker.get("target_chapter_id"),
                  *(row.chapter_id for row in bound)]
        if str(job.deposit_scope_type or "") == "chapter":
            values.extend(job.deposit_scope_ids or [])
        supplied = [value for value in values if value not in (None, "")]
        targets = {_chapter_id(value) for value in supplied}
        if len(targets) != 1 or None in targets:
            _record(db, job, status="blocked", code="saved_target_unavailable",
                    reason="Automatic recovery needs one consistent saved chapter ID; the source and checkpoint are preserved.",
                    step=step)
            continue
        target = next(iter(targets))
        if db.get(models.Chapter, target) is None:
            _record(db, job, status="blocked", code="saved_chapter_missing",
                    reason="Automatic recovery cannot find the saved chapter; the source and checkpoint are preserved.",
                    step=step, chapter_id=target)
            continue
        if step == "step02" and not marker:
            _record(db, job, status="blocked", code="review_boundary_missing",
                    reason="Automatic Master recovery needs the saved reviewed-input marker; it will not rebuild from the original source.",
                    step=step, chapter_id=target)
            continue

        row = db.query(models.ChapterBatchRow).filter_by(chapter_id=target).one_or_none()
        if row is not None:
            if row.job_id != job.id:
                _record(db, job, status="blocked", code="chapter_source_replaced",
                        reason="This saved chapter now has another source binding. Its current source was preserved; the interrupted run remains in history.",
                        step=step, chapter_id=target)
                continue
            if db.query(models.ChapterBatchTask.id).filter(
                models.ChapterBatchTask.batch_row_id == row.id,
                models.ChapterBatchTask.state.in_(models.CHAPTER_BATCH_LIVE_TASK_STATES),
            ).first():
                continue
            # Startup does not undo an explicit terminal queue action.
            previous = db.query(models.ChapterBatchTask).filter(
                models.ChapterBatchTask.batch_row_id == row.id,
                models.ChapterBatchTask.kind == step,
                models.ChapterBatchTask.job_id == job.id,
            ).order_by(models.ChapterBatchTask.id.desc()).first()
            if previous and previous.state in {"failed", "cancelled"}:
                continue

        try:
            if row is None:
                row = models.ChapterBatchRow(
                    chapter_id=target, job_id=job.id,
                    source_filename=str(job.filename or ""),
                    source_book=str(job.source_book or ""),
                    chapter_duration_minutes=int(job.chapter_duration_minutes or 0),
                    created_by_sub=str(job.started_by_sub or ""),
                    created_by_email=str(job.started_by_email or ""),
                )
                db.add(row)
                db.flush()
            token = "recovery" + hashlib.sha256(
                f"{job.id}:{job.run_id or ''}:{step}".encode()
            ).hexdigest()[:24]
            # Recovery continues the recorded transport. Historical/direct
            # runs can carry frozen non-OpenAI routing; forcing those into a
            # strict OpenAI Batch cohort would refuse their saved work. Only
            # an explicitly recorded Batch run is authorized to use Batch.
            cohort = token if str(job.execution_mode or "") == "batch" else ""
            task = models.ChapterBatchTask(
                batch_row_id=row.id, job_id=job.id, kind=step, state="queued",
                attempt=0, push_group_id=token, cohort_id=cohort,
                enqueued_by_sub=str(job.started_by_sub or ""),
                enqueued_by_email=str(job.started_by_email or ""),
            )
            db.add(task)
            db.flush()
            _record(db, job, status="queued", code="restart_recovery",
                    reason="Resuming the saved run automatically after the server restart. Paid decisions, source files and reviewed inputs are preserved.",
                    step=step, chapter_id=target, task_id=task.id)
            recovered.append(int(job.id))
        except IntegrityError:
            # A second boot/request won the same chapter's unique row/live-task
            # constraint. It owns the queue; never disturb its binding or lease.
            db.rollback()
    return recovered
