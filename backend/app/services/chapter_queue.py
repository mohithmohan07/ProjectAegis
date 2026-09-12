"""The durable queue behind the chapter batch console.

Pure database operations — no threads live here, so every state transition is
testable without a worker. What it provides that the pipeline did not have:

* **a lease that survives a restart.** ``uploads.is_job_running`` is a
  module-level dict of ``threading.Lock``, so after a restart it reads False
  for a chapter that was mid-run and a second run would start on top of the
  first. A lease row with a boot nonce distinguishes a live run from a crashed
  one exactly, not by timing.
* **an attempt budget charged at claim.** A worker killed mid-run has still
  spent an attempt, which is what stops a crash-loop from spending the owner's
  money in a circle overnight.
* **a reconcile before every spend.** A task is never re-dispatched on trust:
  the job is re-read first, so work that actually finished before a crash is
  settled without a second charge, and a run the engine declared unresumable is
  never retried.

Everything here is scheduling mechanics. Nothing reads a source, classifies
content or decides what a chapter means.
"""
from __future__ import annotations

import os
import socket
import uuid
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import and_, or_, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import models
from . import chapter_batches, generation_recovery
from . import build_concepts_release as release_svc

#: This process's identity for a lease. The uuid is a BOOT NONCE: it is what
#: makes a restarted process a different owner even when machine and pid
#: repeat, so "leased by someone who is gone" is an exact test rather than a
#: guess from a timestamp.
WORKER_TOKEN = (
    f"{os.environ.get('FLY_MACHINE_ID') or socket.gethostname()}"
    f":{os.getpid()}:{uuid.uuid4().hex[:8]}"
)

#: The TTL bounds the gap between HEARTBEATS, never the length of a run: the
#: heartbeat is emitted by a timer independent of whether the generation thread
#: is blocked inside a 600-second provider call, so a live two-hour run is
#: never declared dead.
LEASE_TTL_SECONDS = 300
HEARTBEAT_SECONDS = 60

VERDICT_QUEUED = "queued"
VERDICT_ALREADY_QUEUED = "already_queued"
VERDICT_ALREADY_RUNNING = "already_running"
VERDICT_REFUSED = "refused"

_STEP_ACTS = {
    "step01": "pushed Step 01",
    "step02": "pushed Step 02",
    "publish": "pushed publish",
}


def _now() -> datetime:
    """Naive UTC, matching every stored ``DateTime`` column in this schema."""
    return datetime.utcnow()


def new_push_group_id() -> str:
    return uuid.uuid4().hex[:16]


# ---------------------------------------------------------------------------
# Admission
# ---------------------------------------------------------------------------

def _refusal(chapter_id: int, code: str, reason: str) -> dict[str, Any]:
    return {
        "chapter_id": int(chapter_id),
        "verdict": VERDICT_REFUSED,
        "reason_code": code,
        "reason": reason,
        "task_id": None,
        "position": None,
    }


def enqueue_one(
    db: Session,
    chapter_id: int,
    *,
    step: str,
    push_group_id: str,
    actor_sub: str = "",
    actor_email: str = "",
    lanes: Sequence[str] | None = None,
    running_probe=None,
) -> dict[str, Any]:
    """Admit one chapter for one step, or say exactly why not.

    Eligibility is read from the same ``can`` map the console renders, so the
    button a person sees and the answer they get cannot disagree.
    """
    if step not in models.CHAPTER_BATCH_STEPS:
        return _refusal(chapter_id, "wrong_state", f"unknown step {step!r}")

    chapter = db.get(models.Chapter, int(chapter_id))
    if chapter is None:
        return _refusal(chapter_id, "unknown_chapter", "this chapter no longer exists")

    row = (
        db.query(models.ChapterBatchRow)
        .filter(models.ChapterBatchRow.chapter_id == int(chapter_id))
        .one_or_none()
    )
    if row is None or not row.job_id:
        return _refusal(
            chapter_id, "no_source",
            "upload a source PDF for this chapter before pushing it",
        )

    live = (
        db.query(models.ChapterBatchTask)
        .filter(
            models.ChapterBatchTask.batch_row_id == row.id,
            models.ChapterBatchTask.state.in_(
                models.CHAPTER_BATCH_LIVE_TASK_STATES),
        )
        .order_by(models.ChapterBatchTask.id.desc())
        .first()
    )
    if live is not None:
        if live.state == "queued":
            return {
                "chapter_id": int(chapter_id),
                "verdict": VERDICT_ALREADY_QUEUED,
                "reason_code": "already_live",
                "reason": f"already queued for {live.kind}",
                "task_id": int(live.id),
                "position": chapter_batches.queue_positions(db).get(int(live.id)),
            }
        if live.state == "leased":
            return {
                "chapter_id": int(chapter_id),
                "verdict": VERDICT_ALREADY_RUNNING,
                "reason_code": "already_live",
                "reason": f"{live.kind} is running for this chapter",
                "task_id": int(live.id),
                "position": None,
            }
        return _refusal(
            chapter_id, "blocked",
            live.last_error
            or f"this chapter is blocked ({live.blocked_kind or 'needs a person'}) "
               "and must be resolved before it can run again",
        )

    job = db.get(models.UploadJob, int(row.job_id))
    if job is None:
        return _refusal(
            chapter_id, "no_source",
            "the staged upload for this chapter is gone; upload the source again",
        )

    signals = chapter_batches.signals_from_job(job)
    task_for_state = chapter_batches.latest_tasks(db, [row.id]).get(int(row.id))
    verdict = chapter_batches.derive_state(
        signals, task_for_state,
        process_running=bool(running_probe(job.id)) if running_probe else False,
    )
    lane_views = chapter_batches._lane_views(signals)
    can = chapter_batches._can(verdict["state"], signals, task_for_state, lane_views)

    if verdict["state"] == "dead":
        return _refusal(
            chapter_id, "dead",
            verdict["blocked_reason"] or "this run cannot be resumed",
        )
    if not can.get(step):
        return _refusal(
            chapter_id, "not_ready",
            f"this chapter is {verdict['state'].replace('_', ' ')} and cannot "
            f"take {step} right now",
        )

    resolved_lanes: list[str] = []
    if step == "publish":
        offered = [str(lane) for lane in (lanes or [])]
        run_lanes = chapter_batches.available_lanes(signals)
        resolved_lanes = [lane for lane in offered if lane in run_lanes]
        if not resolved_lanes:
            # Never default a publication target. The caller names the lanes and
            # they must be lanes this run actually has.
            return _refusal(
                chapter_id, "no_lanes",
                "name the lanes to publish; this run has "
                + (", ".join(run_lanes) if run_lanes else "no publishable lane"),
            )

    task = models.ChapterBatchTask(
        batch_row_id=int(row.id),
        kind=step,
        lanes=resolved_lanes,
        state="queued",
        attempt=0,
        push_group_id=str(push_group_id or ""),
        enqueued_by_sub=str(actor_sub or ""),
        enqueued_by_email=str(actor_email or ""),
        enqueued_at=_now(),
    )
    db.add(task)
    try:
        db.flush()
    except IntegrityError:
        # The partial UNIQUE index refused a second live task for this chapter.
        # A lost race is reported, never swallowed.
        db.rollback()
        return {
            "chapter_id": int(chapter_id),
            "verdict": VERDICT_ALREADY_QUEUED,
            "reason_code": "already_live",
            "reason": "another push already queued this chapter",
            "task_id": None,
            "position": None,
        }

    chapter_batches.record_act(
        row, act=_STEP_ACTS.get(step, step),
        actor_sub=actor_sub, actor_email=actor_email,
    )
    return {
        "chapter_id": int(chapter_id),
        "verdict": VERDICT_QUEUED,
        "reason_code": "",
        "reason": "",
        "task_id": int(task.id),
        "position": None,
    }


def cancel_one(
    db: Session, chapter_id: int, *, actor_sub: str = "", actor_email: str = "",
) -> dict[str, Any]:
    """Take a chapter out of the queue. Only ever a row that has not started."""
    row = (
        db.query(models.ChapterBatchRow)
        .filter(models.ChapterBatchRow.chapter_id == int(chapter_id))
        .one_or_none()
    )
    if row is None:
        return _refusal(chapter_id, "unknown_chapter", "this chapter has no run")
    task = (
        db.query(models.ChapterBatchTask)
        .filter(
            models.ChapterBatchTask.batch_row_id == row.id,
            models.ChapterBatchTask.state == "queued",
        )
        .order_by(models.ChapterBatchTask.id.desc())
        .first()
    )
    if task is None:
        return _refusal(
            chapter_id, "wrong_state",
            "nothing is queued for this chapter; a running step is stopped from "
            "the run itself, never from the table",
        )
    changed = db.execute(
        update(models.ChapterBatchTask)
        .where(
            models.ChapterBatchTask.id == task.id,
            models.ChapterBatchTask.state == "queued",
        )
        .values(state="cancelled", finished_at=_now())
        .execution_options(synchronize_session=False)
    )
    if changed.rowcount != 1:
        return _refusal(
            chapter_id, "already_live", "this chapter started before it could be "
                                        "cancelled",
        )
    chapter_batches.record_act(
        row, act="cancelled", actor_sub=actor_sub, actor_email=actor_email,
    )
    return {
        "chapter_id": int(chapter_id), "verdict": VERDICT_QUEUED,
        "reason_code": "", "reason": "", "task_id": int(task.id), "position": None,
    }


def retry_one(
    db: Session, chapter_id: int, *, actor_sub: str = "", actor_email: str = "",
) -> dict[str, Any]:
    """Return a blocked or failed row to the queue — an explicit human act.

    The queue never clears a block by itself. A person resolves what stopped
    the run and then says so here, which is also what refreshes the attempt
    budget: a block costs no attempts, so one is granted back.
    """
    row = (
        db.query(models.ChapterBatchRow)
        .filter(models.ChapterBatchRow.chapter_id == int(chapter_id))
        .one_or_none()
    )
    if row is None:
        return _refusal(chapter_id, "unknown_chapter", "this chapter has no run")
    task = (
        db.query(models.ChapterBatchTask)
        .filter(
            models.ChapterBatchTask.batch_row_id == row.id,
            models.ChapterBatchTask.state.in_(("blocked", "failed")),
        )
        .order_by(models.ChapterBatchTask.id.desc())
        .first()
    )
    if task is None:
        return _refusal(
            chapter_id, "wrong_state", "nothing is blocked or failed for this chapter",
        )
    if str(task.failure_code or "") == "non_resumable":
        return _refusal(
            chapter_id, "dead",
            "this run recorded an explicit do-not-resume verdict; follow its "
            "recovery action instead of retrying",
        )
    changed = db.execute(
        update(models.ChapterBatchTask)
        .where(
            models.ChapterBatchTask.id == task.id,
            models.ChapterBatchTask.state.in_(("blocked", "failed")),
        )
        .values(
            state="queued",
            blocked_kind="",
            failure_code="",
            last_error="",
            last_error_at=None,
            finished_at=None,
            enqueued_at=_now(),
            enqueued_by_sub=str(actor_sub or ""),
            enqueued_by_email=str(actor_email or ""),
            max_attempts=models.ChapterBatchTask.max_attempts + 1,
        )
        .execution_options(synchronize_session=False)
    )
    if changed.rowcount != 1:
        return _refusal(chapter_id, "already_live", "this chapter changed state")
    chapter_batches.record_act(
        row, act="returned to the queue", actor_sub=actor_sub,
        actor_email=actor_email,
    )
    return {
        "chapter_id": int(chapter_id), "verdict": VERDICT_QUEUED,
        "reason_code": "", "reason": "", "task_id": int(task.id), "position": None,
    }


# ---------------------------------------------------------------------------
# The lease
# ---------------------------------------------------------------------------

def claimable(db: Session, *, kinds: Sequence[str]) -> list[models.ChapterBatchTask]:
    """Queued tasks in admission order: oldest push first, ties by id."""
    now = _now()
    return (
        db.query(models.ChapterBatchTask)
        .filter(
            models.ChapterBatchTask.kind.in_(tuple(kinds)),
            or_(
                models.ChapterBatchTask.state == "queued",
                and_(
                    models.ChapterBatchTask.state == "leased",
                    models.ChapterBatchTask.lease_expires_at.isnot(None),
                    models.ChapterBatchTask.lease_expires_at < now,
                ),
            ),
        )
        .order_by(
            models.ChapterBatchTask.enqueued_at.asc(),
            models.ChapterBatchTask.id.asc(),
        )
        .all()
    )


def claim(db: Session, task_id: int) -> models.ChapterBatchTask | None:
    """Take the lease, or return None because someone else has it.

    One conditional UPDATE, the compare-and-swap idiom this repo already uses.
    Under SQLite WAL with ``busy_timeout`` it is serialized by the database
    write lock, so it is atomic across threads — which the module-level
    ``threading.Lock`` dict never was across a restart.
    """
    now = _now()
    expires = now + timedelta(seconds=LEASE_TTL_SECONDS)
    changed = db.execute(
        update(models.ChapterBatchTask)
        .where(
            models.ChapterBatchTask.id == int(task_id),
            or_(
                models.ChapterBatchTask.state == "queued",
                and_(
                    models.ChapterBatchTask.state == "leased",
                    models.ChapterBatchTask.lease_expires_at.isnot(None),
                    models.ChapterBatchTask.lease_expires_at < now,
                ),
            ),
        )
        .values(
            state="leased",
            lease_owner=WORKER_TOKEN,
            leased_at=now,
            heartbeat_at=now,
            lease_expires_at=expires,
            started_at=now,
            # Charged HERE, not at success: a worker that dies mid-run has
            # still spent an attempt, so a crash-loop cannot spin forever.
            attempt=models.ChapterBatchTask.attempt + 1,
        )
        .execution_options(synchronize_session=False)
    )
    if changed.rowcount != 1:
        db.rollback()
        return None
    db.commit()
    task = db.get(models.ChapterBatchTask, int(task_id))
    if task is not None:
        db.refresh(task)
    return task


def heartbeat(db: Session, task_id: int) -> bool:
    """Extend this worker's lease. False means the lease was lost — stop."""
    now = _now()
    changed = db.execute(
        update(models.ChapterBatchTask)
        .where(
            models.ChapterBatchTask.id == int(task_id),
            models.ChapterBatchTask.state == "leased",
            models.ChapterBatchTask.lease_owner == WORKER_TOKEN,
        )
        .values(
            heartbeat_at=now,
            lease_expires_at=now + timedelta(seconds=LEASE_TTL_SECONDS),
        )
        .execution_options(synchronize_session=False)
    )
    db.commit()
    return changed.rowcount == 1


def finish(
    db: Session,
    task_id: int,
    *,
    state: str,
    blocked_kind: str = "",
    failure_code: str = "",
    error: str = "",
    refund_attempt: bool = False,
) -> None:
    """Settle a task and drop its lease."""
    now = _now()
    values: dict[str, Any] = {
        "state": state,
        "lease_owner": "",
        "leased_at": None,
        "heartbeat_at": None,
        "lease_expires_at": None,
        "blocked_kind": str(blocked_kind or ""),
        "failure_code": str(failure_code or ""),
        "last_error": str(error or "")[:4000],
        "last_error_at": now if error else None,
        "finished_at": now if state in {"done", "failed", "cancelled"} else None,
    }
    if refund_attempt:
        # Another route held the per-job lock, so this was never a real try.
        values["attempt"] = models.ChapterBatchTask.attempt - 1
    db.execute(
        update(models.ChapterBatchTask)
        .where(models.ChapterBatchTask.id == int(task_id))
        .values(**values)
        .execution_options(synchronize_session=False)
    )
    db.commit()


def requeue(
    db: Session, task_id: int, *, error: str = "", refund_attempt: bool = False,
) -> None:
    """Put a retryable task back in line, keeping its recorded diagnostic."""
    now = _now()
    values: dict[str, Any] = {
        "state": "queued",
        "lease_owner": "",
        "leased_at": None,
        "heartbeat_at": None,
        "lease_expires_at": None,
        "last_error": str(error or "")[:4000],
        "last_error_at": now if error else None,
    }
    if refund_attempt:
        values["attempt"] = models.ChapterBatchTask.attempt - 1
    db.execute(
        update(models.ChapterBatchTask)
        .where(models.ChapterBatchTask.id == int(task_id))
        .values(**values)
        .execution_options(synchronize_session=False)
    )
    db.commit()


def reclaim_orphans(db: Session, *, in_flight: Sequence[int] = ()) -> dict[str, int]:
    """Recover leases whose worker is gone.

    Two signals, and BOTH are required. ``lease_owner`` says whose lease it is —
    the boot nonce makes a restarted process a different owner even when machine
    and pid repeat. Expiry says the holder stopped: a live worker heartbeats
    every minute, so a lease past its TTL is one nobody is renewing.

    A foreign token alone is not enough. Two processes can share one volume and
    therefore one queue (a second uvicorn worker, say), and a startup sweep that
    reclaimed foreign leases on sight would hand a colleague's live two-hour run
    to a second thread and charge it twice. Requiring expiry as well costs at
    most one TTL of latency after a restart and removes that entirely.

    An expired lease owned by THIS boot is additionally protected while the task
    is in the in-flight registry: a heartbeat merely late behind 48 provider
    threads on two shared vCPUs must not cost a second run either.
    """
    now = _now()
    protected = {int(value) for value in in_flight}
    candidates = (
        db.query(models.ChapterBatchTask)
        .filter(models.ChapterBatchTask.state == "leased")
        .all()
    )
    requeued = 0
    failed = 0
    for task in candidates:
        foreign = str(task.lease_owner or "") != WORKER_TOKEN
        stale = (
            task.lease_expires_at is None or task.lease_expires_at < now
        )
        if not stale:
            continue
        if not foreign and int(task.id) in protected:
            continue
        if int(task.attempt or 0) < int(task.max_attempts or 0):
            task.state = "queued"
            requeued += 1
        else:
            task.state = "failed"
            task.failure_code = "worker_restart"
            task.finished_at = now
            failed += 1
        task.lease_owner = ""
        task.leased_at = None
        task.heartbeat_at = None
        task.lease_expires_at = None
        task.last_error = (
            "the worker stopped while this step was running"
        )
        task.last_error_at = now
    if requeued or failed:
        db.commit()
    return {"requeued": requeued, "failed": failed}


# ---------------------------------------------------------------------------
# Reconcile before spend
# ---------------------------------------------------------------------------

_STEP_COMPLETE_STATUSES = {
    "step01": {
        release_svc.CONCEPT_REVIEW_PENDING,
        release_svc.CONCEPT_REVIEW_REVIEWED,
        release_svc.CONCEPT_REVIEW_MASTER_BUILDING,
        release_svc.CONCEPT_REVIEW_MASTER_READY,
        release_svc.CONCEPT_REVIEW_PUBLISHED,
    },
    "step02": {
        release_svc.CONCEPT_REVIEW_MASTER_READY,
        release_svc.CONCEPT_REVIEW_PUBLISHED,
    },
}


def reconcile_before_dispatch(
    db: Session, task: models.ChapterBatchTask,
) -> dict[str, Any] | None:
    """Re-read the job and refuse to spend when spending would be wrong.

    Two verdicts, and they are the two halves of "never record false success":

    * ``non_resumable`` — the engine recorded an explicit do-not-resume
      verdict. Terminal for the job, never retried.
    * ``already_complete`` — the step's own output is already recorded, which
      happens when a crash landed between the service call returning and the
      task being stamped. Settling it ``done`` is the truth; re-running it
      would be a second charge for work that exists.
    """
    row = db.get(models.ChapterBatchRow, int(task.batch_row_id))
    if row is None or not row.job_id:
        return {
            "state": "failed", "failure_code": "job_missing",
            "error": "the staged upload for this chapter is gone",
        }
    job = db.get(models.UploadJob, int(row.job_id))
    if job is None:
        return {
            "state": "failed", "failure_code": "job_missing",
            "error": "the staged upload for this chapter is gone",
        }

    blocked = generation_recovery.blocked_recovery(job)
    if blocked:
        return {
            "state": "failed",
            "failure_code": "non_resumable",
            "error": str(
                blocked.get("recovery_action")
                or blocked.get("message")
                or "this run recorded an explicit do-not-resume verdict"
            ),
        }

    status = str(release_svc.concept_review_state(job).get("status") or "")
    if status and status in _STEP_COMPLETE_STATUSES.get(task.kind, set()):
        # The step's own output is already recorded. Admission never queues a
        # step whose output exists, so reaching here means the marker advanced
        # between the push and the claim — a crash landing between the service
        # call returning and the task being stamped, or another surface doing
        # the same work. Either way the truth is ``done``; re-running would be
        # a second charge for work that exists. A deliberate rebuild is reached
        # by uploading the reviewed Concept file again, which returns the
        # marker to ``reviewed`` and makes Step 02 pushable once more.
        return {
            "state": "done", "failure_code": "", "error": "",
            "already_complete": True,
        }
    return None


def in_flight_task_ids(db: Session) -> list[int]:
    return [
        int(row[0])
        for row in db.query(models.ChapterBatchTask.id)
        .filter(
            models.ChapterBatchTask.state == "leased",
            models.ChapterBatchTask.lease_owner == WORKER_TOKEN,
        )
        .all()
    ]
