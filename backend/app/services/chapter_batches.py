"""Project a catalogue chapter into one honest console row.

The batch console shows every chapter as a row and has to answer, cheaply and
for a whole page at once: where is this chapter, is anything running, and what
can a person do next.

Two rules shape this module.

**Nothing about workflow state is stored.** ``chapter_batch_rows`` holds the
chapter -> job binding and who acted; it holds no status column. Every status
is read here, at query time, from the markers the engine itself wrote. A cached
copy would be a second answer to the same question and would eventually say
"published" about a chapter that is not.

**The markers are read without loading the payloads they sit beside.**
``question_inventory`` also carries both staged release payloads and runs to
megabytes, so a page of rows cannot afford ``job.question_inventory``. SQLite's
``json_extract`` pulls out the small recorded objects by key. That is selection,
not interpretation: no text is parsed and nothing is inferred.
"""
from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from .. import models
from . import build_concepts_release as release_svc
from . import directory

# The closed set of row states. The server owns this vocabulary and ships it
# with every page so the client never invents a member or hardcodes a label.
STATES: tuple[tuple[str, str, str], ...] = (
    ("no_source", "No source file", "neutral"),
    ("source_staged", "Source staged", "accent"),
    ("step01_queued", "Queued for Step 01", "accent"),
    ("step01_running", "Running Step 01", "accent"),
    ("recovering", "Interrupted — recovering", "yellow"),
    ("concept_review", "Concept files ready for review", "yellow"),
    ("reviewed", "Reviewed file received", "accent"),
    ("step02_queued", "Queued for Step 02", "accent"),
    ("step02_running", "Running Step 02", "accent"),
    ("master_failed", "Step 02 failed", "red"),
    ("master_review", "Master files ready for review", "yellow"),
    ("publish_queued", "Queued to publish", "accent"),
    ("publish_running", "Publishing", "accent"),
    ("partly_published", "Partly published", "yellow"),
    ("published", "Published", "green"),
    ("blocked", "Blocked — needs a person", "yellow"),
    ("failed", "Failed", "red"),
    ("dead", "Cannot resume", "red"),
    ("cancelled", "Cancelled", "neutral"),
    ("legacy", "Legacy run", "neutral"),
)
STATE_VALUES = tuple(value for value, _label, _tone in STATES)
_STATE_LABELS = {value: label for value, label, _tone in STATES}

LANES = ("post", "pre")

_MARKER_KEY = release_svc.CONCEPT_REVIEW_KEY
_RECOVERY_KEY = models.GENERATION_RECOVERY_INVENTORY_KEY


def _now() -> datetime:
    """Naive UTC, matching every stored ``DateTime`` column in this schema."""
    return datetime.utcnow()


def _iso(value: datetime | None) -> str | None:
    if not isinstance(value, datetime):
        return None
    stamped = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return stamped.isoformat()


def _as_mapping(value: Any) -> dict[str, Any]:
    """A stored JSON object, whichever way the driver handed it back."""
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, (str, bytes)):
        try:
            parsed = json.loads(value)
        except (ValueError, TypeError):
            return {}
        return dict(parsed) if isinstance(parsed, Mapping) else {}
    return {}


def _truthy(value: Any) -> bool:
    """SQLite hands a JSON boolean back as 0/1; a Python driver as a bool."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1"}
    return False


# ---------------------------------------------------------------------------
# Job signals: the small recorded objects a row is derived from
# ---------------------------------------------------------------------------

def _extract(column, path: str):
    """``json_extract(column, path)`` — the recorded value at one key."""
    return func.json_extract(column, path)


def _signal_columns() -> list[Any]:
    job = models.UploadJob
    post_key = release_svc.release_key_for_lane("post")
    pre_key = release_svc.release_key_for_lane("pre")
    return [
        job.id,
        job.status,
        job.filename,
        job.owner_sub,
        job.run_id,
        job.run_state,
        job.created_at,
        _extract(job.question_inventory, f"$.{_MARKER_KEY}").label("marker"),
        _extract(job.question_inventory, f"$.{_RECOVERY_KEY}").label("recovery"),
        _extract(
            job.generation_checkpoint, "$.human_decisions.pending",
        ).label("pending_decision"),
        _extract(
            job.question_inventory, f"$.{post_key}.summary.database_uploaded",
        ).label("post_concept_uploaded"),
        _extract(
            job.question_inventory, f"$.{pre_key}.summary.database_uploaded",
        ).label("pre_concept_uploaded"),
    ]


def job_signals(db: Session, job_ids: Sequence[int]) -> dict[int, dict[str, Any]]:
    """Everything a row needs from its job, without loading the big payloads.

    One statement for a whole page. The alternative — ``db.get(UploadJob, id)``
    per row — would pull both staged release payloads for every chapter on the
    screen.
    """
    wanted = [int(value) for value in job_ids if value]
    if not wanted:
        return {}
    rows = db.execute(
        select(*_signal_columns()).where(models.UploadJob.id.in_(wanted))
    ).all()
    signals: dict[int, dict[str, Any]] = {}
    for row in rows:
        mapping = row._mapping
        signals[int(mapping["id"])] = {
            "id": int(mapping["id"]),
            "status": str(mapping["status"] or ""),
            "filename": str(mapping["filename"] or ""),
            "owner_sub": str(mapping["owner_sub"] or ""),
            "run_id": str(mapping["run_id"] or ""),
            "run_state": _as_mapping(mapping["run_state"]),
            "created_at": mapping["created_at"],
            "marker": _as_mapping(mapping["marker"]),
            "recovery": _as_mapping(mapping["recovery"]),
            "pending_decision": _as_mapping(mapping["pending_decision"]),
            "concept_uploaded": {
                "post": _truthy(mapping["post_concept_uploaded"]),
                "pre": _truthy(mapping["pre_concept_uploaded"]),
            },
        }
    return signals


def signals_from_job(job: models.UploadJob) -> dict[str, Any]:
    """The same signal shape read straight off a loaded job.

    Used where the job is already in memory (a single row, a just-finished
    task) so one code path derives the row in both places.
    """
    inventory = job.question_inventory if isinstance(job.question_inventory, Mapping) else {}
    uploaded: dict[str, bool] = {}
    for lane in LANES:
        payload = release_svc.release_payload(job, lane=lane) or {}
        uploaded[lane] = bool(
            (payload.get("summary") or {}).get("database_uploaded")
        )
    return {
        "id": int(job.id),
        "status": str(job.status or ""),
        "filename": str(job.filename or ""),
        "owner_sub": str(job.owner_sub or ""),
        "run_id": str(job.run_id or ""),
        "run_state": _as_mapping(job.run_state),
        "created_at": job.created_at,
        "marker": _as_mapping(inventory.get(_MARKER_KEY)),
        "recovery": dict(job.generation_recovery or {}),
        "pending_decision": _as_mapping(job.pending_decision),
        "concept_uploaded": uploaded,
    }


# ---------------------------------------------------------------------------
# Lanes
# ---------------------------------------------------------------------------

def _lane_view(
    lane: str, marker: Mapping[str, Any], concept_uploaded: Mapping[str, bool],
) -> dict[str, Any]:
    """One lane's publication truth, read from the marker the engine wrote."""
    available = lane in list(marker.get("available_lanes") or [])
    reviewed_lanes = list(marker.get("reviewed_lanes") or [])
    corrected = _as_mapping((marker.get("corrected_inputs") or {}).get(lane))
    master_review = _as_mapping((marker.get("master_review") or {}).get(lane))
    published = _as_mapping(master_review.get("published"))
    cms_status = str(
        (_as_mapping(published.get("cms_workbook")).get("status") or "")
    )

    concept_state = "unavailable"
    concept_reason = "the Concept file for this lane has not been staged"
    if available:
        if concept_uploaded.get(lane):
            concept_state = "published"
            concept_reason = ""
        else:
            concept_state = "available"
            concept_reason = ""

    master_state = "none"
    master_reason = ""
    if published:
        if cms_status == "published":
            master_state = "published"
        else:
            # A queued CMS append is not a publication. ``master_review``
            # records it honestly and so does this row.
            master_state = "queued"
            master_reason = str(
                _as_mapping(published.get("cms_workbook")).get("queued_reason")
                or "the CMS workbook append is queued and has not been written"
            )
    elif str(marker.get("status") or "") in {
        release_svc.CONCEPT_REVIEW_MASTER_READY,
        release_svc.CONCEPT_REVIEW_PUBLISHED,
    } and available:
        master_state = "ready"

    return {
        "lane": lane,
        "available": available,
        "concept_reviewed": lane in reviewed_lanes,
        "concept_reviewed_filename": str(corrected.get("filename") or ""),
        "concept": concept_state,
        "concept_reason": concept_reason,
        "master": master_state,
        "master_reason": master_reason,
        "master_version": int(master_review.get("version") or 0),
    }


def _lane_views(signals: Mapping[str, Any]) -> list[dict[str, Any]]:
    marker = signals.get("marker") or {}
    uploaded = signals.get("concept_uploaded") or {}
    return [_lane_view(lane, marker, uploaded) for lane in LANES]


def available_lanes(signals: Mapping[str, Any]) -> list[str]:
    """The run's OWN lanes. A Post-only run must be able to reach published."""
    marker = signals.get("marker") or {}
    lanes = [
        str(lane) for lane in (
            marker.get("available_lanes") or marker.get("required_lanes") or []
        )
    ]
    return [lane for lane in LANES if lane in lanes]


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

_RUNNING_STATE = {
    "step01": "step01_running",
    "step02": "step02_running",
    "publish": "publish_running",
}
_QUEUED_STATE = {
    "step01": "step01_queued",
    "step02": "step02_queued",
    "publish": "publish_queued",
}


def _task_is_live(task: models.ChapterBatchTask | None) -> bool:
    return task is not None and task.state in models.CHAPTER_BATCH_LIVE_TASK_STATES


def _lease_expired(task: models.ChapterBatchTask | None, now: datetime) -> bool:
    return bool(
        task is not None
        and task.state == "leased"
        and task.lease_expires_at is not None
        and task.lease_expires_at < now
    )


def derive_state(
    signals: Mapping[str, Any] | None,
    task: models.ChapterBatchTask | None,
    *,
    now: datetime | None = None,
    process_running: bool = False,
) -> dict[str, Any]:
    """The row's state, its reason, and why a person is needed.

    Ordered so that nothing optimistic can win: a job that cannot resume is
    ``dead`` before any marker is read, a lease that ran out is ``recovering``
    rather than ``running``, and ``published`` is only ever the engine's own
    recorded verdict over the run's available lanes.
    """
    moment = now or _now()
    if not signals:
        return {"state": "no_source", "blocked_kind": "", "blocked_reason": "",
                "error_message": ""}

    recovery = signals.get("recovery") or {}
    if recovery.get("resume_allowed") is False:
        return {
            "state": "dead",
            "blocked_kind": "non_resumable",
            "blocked_reason": str(
                recovery.get("recovery_action")
                or recovery.get("message")
                or "this run cannot be resumed"
            ),
            "error_message": str(recovery.get("message") or ""),
        }

    marker = signals.get("marker") or {}
    status = str(marker.get("status") or "")

    if _task_is_live(task) and task is not None:
        if task.state == "blocked":
            return {
                "state": "blocked",
                "blocked_kind": str(task.blocked_kind or ""),
                "blocked_reason": str(task.last_error or ""),
                "error_message": "",
            }
        if task.state == "leased":
            if _lease_expired(task, moment):
                return {
                    "state": "recovering", "blocked_kind": "",
                    "blocked_reason": "",
                    "error_message": "the worker stopped mid-step; this row is "
                                     "being recovered",
                }
            return {"state": _RUNNING_STATE.get(task.kind, "step01_running"),
                    "blocked_kind": "", "blocked_reason": "", "error_message": ""}
        return {"state": _QUEUED_STATE.get(task.kind, "step01_queued"),
                "blocked_kind": "", "blocked_reason": "", "error_message": ""}

    pending = signals.get("pending_decision") or {}
    if pending:
        return {
            "state": "blocked",
            "blocked_kind": "human_decision",
            "blocked_reason": str(
                pending.get("question")
                or pending.get("prompt")
                or "the run needs a recorded decision before it can continue"
            ),
            "error_message": "",
        }

    if status == release_svc.CONCEPT_REVIEW_PUBLISHED:
        return {"state": "published", "blocked_kind": "", "blocked_reason": "",
                "error_message": ""}

    if status == release_svc.CONCEPT_REVIEW_MASTER_READY:
        lanes = _lane_views(signals)
        live = [lane for lane in lanes if lane["available"]]
        if any(lane["master"] in {"published", "queued"} for lane in live):
            queued = next(
                (lane for lane in live if lane["master"] == "queued"), None,
            )
            return {
                "state": "partly_published",
                "blocked_kind": "cms_workbook_queued" if queued else "",
                "blocked_reason": (queued or {}).get("master_reason", ""),
                "error_message": "",
            }
        return {"state": "master_review", "blocked_kind": "",
                "blocked_reason": "", "error_message": ""}

    if status == release_svc.CONCEPT_REVIEW_MASTER_FAILED:
        return {"state": "master_failed", "blocked_kind": "",
                "blocked_reason": "", "error_message": ""}

    if status == release_svc.CONCEPT_REVIEW_MASTER_BUILDING:
        if process_running:
            # A Step 02 started from another surface in THIS process. The
            # in-process lock is accurate for that case and is the only signal
            # available; it is never consulted for the queue's own runs, where
            # the lease is the authority.
            return {"state": "step02_running", "blocked_kind": "",
                    "blocked_reason": "", "error_message": ""}
        return {
            "state": "blocked",
            "blocked_kind": "interrupted_master",
            "blocked_reason": "Step 02 started and did not finish; push Step 02 "
                              "again to rebuild the Master files",
            "error_message": "",
        }

    if status == release_svc.CONCEPT_REVIEW_REVIEWED:
        return {"state": "reviewed", "blocked_kind": "", "blocked_reason": "",
                "error_message": ""}

    if status == release_svc.CONCEPT_REVIEW_PENDING:
        return {"state": "concept_review", "blocked_kind": "",
                "blocked_reason": "", "error_message": ""}

    # No review marker at all.
    job_status = str(signals.get("status") or "")
    if task is not None and task.state == "done" and task.kind == "step01":
        return {
            "state": "blocked",
            "blocked_kind": "no_review_marker",
            "blocked_reason": "Step 01 finished but recorded no Concept-review "
                              "marker; the Concept files cannot be reviewed from "
                              "here",
            "error_message": "",
        }
    if job_status in {"generated", "released"}:
        return {"state": "legacy", "blocked_kind": "", "blocked_reason": "",
                "error_message": ""}
    if task is not None and task.state == "failed":
        return {
            "state": "failed",
            "blocked_kind": str(task.failure_code or ""),
            "blocked_reason": "",
            "error_message": str(task.last_error or ""),
        }
    if task is not None and task.state == "cancelled":
        return {"state": "cancelled", "blocked_kind": "", "blocked_reason": "",
                "error_message": ""}
    return {"state": "source_staged", "blocked_kind": "", "blocked_reason": "",
            "error_message": ""}


def _can(
    state: str,
    signals: Mapping[str, Any] | None,
    task: models.ChapterBatchTask | None,
    lanes: Sequence[Mapping[str, Any]],
) -> dict[str, bool]:
    """What a person may do with this row right now. The server is authority."""
    live = _task_is_live(task)
    has_job = bool(signals)
    marker_status = str((signals or {}).get("marker", {}).get("status") or "")
    dead = state == "dead"
    publishable = [
        lane for lane in lanes
        if lane["available"] and lane["master"] in {"ready", "queued"}
    ]
    return {
        # Replacing the staged source is how a person recovers a bad upload;
        # it is refused only while a step actually holds the chapter.
        "upload_source": not live and not dead,
        "step01": (
            has_job and not live and not dead and not marker_status
            and str((signals or {}).get("status") or "") in {"uploaded", "converted"}
        ),
        "step02": (
            has_job and not live and not dead
            and marker_status in {
                release_svc.CONCEPT_REVIEW_PENDING,
                release_svc.CONCEPT_REVIEW_REVIEWED,
            }
        ),
        "publish": (
            has_job and not live and not dead and bool(publishable)
            and marker_status in {
                release_svc.CONCEPT_REVIEW_MASTER_READY,
                release_svc.CONCEPT_REVIEW_PUBLISHED,
            }
        ),
        "cancel": bool(task is not None and task.state == "queued"),
        # A blocked row returns to the queue only by an explicit human act,
        # after the person has resolved what stopped it.
        "retry": bool(
            task is not None
            and not dead
            and (
                task.state == "blocked"
                or (task.state == "failed"
                    and str(task.failure_code or "") != "non_resumable")
            )
        ),
        # Also open once the Masters exist: a team that spots a Concept error
        # only after seeing the Master files would otherwise be stranded. The
        # engine supports it — a reviewed upload returns the marker to
        # ``reviewed``, which makes Step 02 pushable again.
        "upload_concept": (
            has_job and not live and not dead
            and marker_status in {
                release_svc.CONCEPT_REVIEW_PENDING,
                release_svc.CONCEPT_REVIEW_REVIEWED,
                release_svc.CONCEPT_REVIEW_MASTER_READY,
            }
        ),
        "upload_master": (
            has_job and not live and not dead
            and marker_status in {
                release_svc.CONCEPT_REVIEW_MASTER_READY,
                release_svc.CONCEPT_REVIEW_PUBLISHED,
            }
        ),
    }


def _queue_view(
    task: models.ChapterBatchTask | None,
    position: int | None,
    now: datetime,
) -> dict[str, Any]:
    if task is None:
        return {
            "task_id": None, "kind": None, "state": None, "position": None,
            "attempt": 0, "max_attempts": 0, "blocked_kind": "",
            "failure_code": "", "last_error": "", "enqueued_by_email": "",
            "enqueued_at": None, "started_at": None, "lease_expired": False,
        }
    return {
        "task_id": int(task.id),
        "kind": str(task.kind or ""),
        "state": str(task.state or ""),
        "position": position,
        "attempt": int(task.attempt or 0),
        "max_attempts": int(task.max_attempts or 0),
        "blocked_kind": str(task.blocked_kind or ""),
        "failure_code": str(task.failure_code or ""),
        "last_error": str(task.last_error or ""),
        "enqueued_by_email": str(task.enqueued_by_email or ""),
        "enqueued_at": _iso(task.enqueued_at),
        "started_at": _iso(task.started_at),
        "lease_expired": _lease_expired(task, now),
    }


def _pending_decision_view(signals: Mapping[str, Any] | None) -> dict | None:
    pending = (signals or {}).get("pending_decision") or {}
    if not pending:
        return None
    companions = pending.get("companion_pending_decisions")
    return {
        "decision_id": str(pending.get("decision_id") or pending.get("id") or ""),
        "kind": str(pending.get("kind") or ""),
        "question": str(
            pending.get("question") or pending.get("prompt") or ""
        ),
        "companions": len(companions) if isinstance(companions, list) else 0,
    }


def project_row(
    chapter: models.Chapter,
    batch_row: models.ChapterBatchRow | None,
    signals: Mapping[str, Any] | None,
    task: models.ChapterBatchTask | None,
    *,
    position: int | None = None,
    now: datetime | None = None,
    process_running: bool = False,
) -> dict[str, Any]:
    """One console row. Every field is derived; none is a stored status."""
    moment = now or _now()
    verdict = derive_state(
        signals, task, now=moment, process_running=process_running,
    )
    lanes = _lane_views(signals) if signals else [
        _lane_view(lane, {}, {}) for lane in LANES
    ]
    run_state = (signals or {}).get("run_state") or {}
    state = verdict["state"]
    running = state in {"step01_running", "step02_running", "publish_running"}
    return {
        "chapter_id": int(chapter.id),
        "chapter_code": str(chapter.chapter_code or ""),
        "chapter_title": str(chapter.chapter_title or ""),
        "chapter_display_name": str(chapter.chapter_display_name or ""),
        "board": str(chapter.board or ""),
        "grade": str(chapter.grade or ""),
        # The subject the DIRECTORY shows, so this table and the Build
        # Concepts dropdowns name the same chapter the same way. CBSE and
        # Karnataka teach History, Geography, Civics and Economics as one
        # Social Science subject, and a person filtering for it must find
        # every one of them.
        "subject": directory.effective_subject_for_tags(
            chapter.board, chapter.subject) or "",
        "unit": str(chapter.unit or ""),
        "job_id": int(signals["id"]) if signals else None,
        "source_filename": str(
            (batch_row.source_filename if batch_row else "")
            or (signals or {}).get("filename", "")
        ),
        "source_book": str(batch_row.source_book if batch_row else ""),
        "staged_by_email": str(batch_row.created_by_email if batch_row else ""),
        "source_staged_at": _iso(batch_row.source_staged_at if batch_row else None),
        "state": state,
        "state_label": _STATE_LABELS.get(state, state),
        # Only a row that is actually executing reports a stage and a bar. A
        # queued row borrowing the last run's stage would read as progress.
        "stage": str(run_state.get("stage") or "") if running else "",
        "progress": float(run_state.get("progress") or 0.0) if running else 0.0,
        "workflow_status": str((signals or {}).get("marker", {}).get("status") or ""),
        "lanes": lanes,
        "blocked_kind": verdict["blocked_kind"],
        "blocked_reason": verdict["blocked_reason"],
        "error_message": verdict["error_message"],
        "pending_decision": _pending_decision_view(signals),
        "can": _can(state, signals, task, lanes),
        "queue": _queue_view(task, position, moment),
        "last_actor_email": str(batch_row.last_actor_email if batch_row else ""),
        "last_actor_act": str(batch_row.last_actor_act if batch_row else ""),
        "last_actor_at": _iso(batch_row.last_actor_at if batch_row else None),
        "updated_at": _iso(batch_row.updated_at if batch_row else None),
    }


# ---------------------------------------------------------------------------
# Rows, tasks and the page
# ---------------------------------------------------------------------------

def get_or_create_row(
    db: Session, chapter_id: int, *, actor_sub: str = "", actor_email: str = "",
) -> models.ChapterBatchRow:
    """The console's row for a chapter, minted on first use."""
    row = (
        db.query(models.ChapterBatchRow)
        .filter(models.ChapterBatchRow.chapter_id == int(chapter_id))
        .one_or_none()
    )
    if row is not None:
        return row
    row = models.ChapterBatchRow(
        chapter_id=int(chapter_id),
        previous_job_ids=[],
        created_by_sub=str(actor_sub or ""),
        created_by_email=str(actor_email or ""),
        created_at=_now(),
        updated_at=_now(),
    )
    db.add(row)
    db.flush()
    return row


def record_act(
    row: models.ChapterBatchRow, *, act: str, actor_sub: str = "",
    actor_email: str = "",
) -> None:
    """Who did what, last. Authorship — never authorization."""
    row.last_actor_act = str(act or "")[:64]
    row.last_actor_sub = str(actor_sub or "")[:255]
    row.last_actor_email = str(actor_email or "")[:320]
    row.last_actor_at = _now()
    row.updated_at = _now()


def latest_tasks(
    db: Session, batch_row_ids: Sequence[int],
) -> dict[int, models.ChapterBatchTask]:
    """The task that speaks for each row: the live one, else the newest."""
    wanted = [int(value) for value in batch_row_ids if value]
    if not wanted:
        return {}
    tasks = (
        db.query(models.ChapterBatchTask)
        .filter(models.ChapterBatchTask.batch_row_id.in_(wanted))
        .order_by(models.ChapterBatchTask.id.asc())
        .all()
    )
    chosen: dict[int, models.ChapterBatchTask] = {}
    for task in tasks:
        key = int(task.batch_row_id)
        current = chosen.get(key)
        if current is None:
            chosen[key] = task
            continue
        current_live = current.state in models.CHAPTER_BATCH_LIVE_TASK_STATES
        task_live = task.state in models.CHAPTER_BATCH_LIVE_TASK_STATES
        if task_live or not current_live:
            chosen[key] = task
    return chosen


def queue_positions(db: Session) -> dict[int, int]:
    """1-based place in line for every queued task, oldest first."""
    rows = (
        db.query(models.ChapterBatchTask.id)
        .filter(models.ChapterBatchTask.state == "queued")
        .order_by(
            models.ChapterBatchTask.enqueued_at.asc(),
            models.ChapterBatchTask.id.asc(),
        )
        .all()
    )
    return {int(row[0]): index for index, row in enumerate(rows, start=1)}


def facets(db: Session) -> dict[str, Any]:
    """Filter values from the catalogue itself.

    Not from ``bulk_import.GRADES``, which lists 01,02,03,06,07,08,09,10 and
    would hide every grade 04 and 05 chapter that actually exists.
    """
    rows = db.execute(
        select(
            models.Chapter.board, models.Chapter.grade, models.Chapter.subject,
        ).group_by(
            models.Chapter.board, models.Chapter.grade, models.Chapter.subject,
        )
    ).all()
    # Fold each row's stored subject to the one the directory presents, then
    # de-duplicate: History and Civics in the same class are one Social
    # Science facet, not two.
    folded = {
        (
            str(board or ""),
            str(grade or ""),
            directory.effective_subject_for_tags(board, subject) or "",
        )
        for board, grade, subject in rows
    }
    triples = [
        {"board": board, "grade": grade, "subject": subject}
        for board, grade, subject in folded
    ]
    triples.sort(key=lambda item: (item["board"], item["grade"], item["subject"]))
    return {
        "boards": sorted({item["board"] for item in triples if item["board"]}),
        "grades": sorted({item["grade"] for item in triples if item["grade"]}),
        "subjects": sorted({item["subject"] for item in triples if item["subject"]}),
        "triples": triples,
    }


def queue_summary(db: Session, *, capacity: int, worker_alive: bool) -> dict[str, Any]:
    counts = dict(
        db.execute(
            select(
                models.ChapterBatchTask.state,
                func.count(models.ChapterBatchTask.id),
            ).group_by(models.ChapterBatchTask.state)
        ).all()
    )
    return {
        "running": int(counts.get("leased") or 0),
        "queued": int(counts.get("queued") or 0),
        "blocked": int(counts.get("blocked") or 0),
        "capacity": int(capacity),
        "worker_alive": bool(worker_alive),
    }


def list_page(
    db: Session,
    *,
    board: str = "",
    grade: str = "",
    subject: str = "",
    q: str = "",
    state: str = "",
    page: int = 1,
    page_size: int = 25,
    capacity: int = 0,
    worker_alive: bool = False,
    running_probe=None,
) -> dict[str, Any]:
    """One page of chapters with their live console state.

    A ``state`` filter is applied AFTER projection because state is derived,
    not stored. The unfiltered page is the common case and stays a plain
    LIMIT/OFFSET; a filtered one walks the catalogue in bounded batches rather
    than projecting every chapter at once.
    """
    now = _now()
    page = max(1, int(page or 1))
    page_size = max(1, min(200, int(page_size or 25)))

    query = db.query(models.Chapter)
    if board:
        query = query.filter(models.Chapter.board == board)
    if grade:
        query = query.filter(models.Chapter.grade == grade)
    if subject:
        # Match on the folded subject, so picking Social Science returns the
        # History, Geography, Civics and Economics chapters stored under it.
        # The fold depends on the BOARD, so the pairs are resolved together;
        # the result is a plain indexed IN rather than a per-row computation.
        pairs = db.query(
            models.Chapter.board, models.Chapter.subject,
        ).distinct().all()
        raw_subjects = sorted({
            str(row_subject or "")
            for row_board, row_subject in pairs
            if directory.effective_subject_for_tags(row_board, row_subject)
            == subject
        })
        query = query.filter(
            models.Chapter.subject.in_(raw_subjects or [subject]))
    if q:
        needle = f"%{q.strip()}%"
        query = query.filter(
            or_(
                models.Chapter.chapter_title.ilike(needle),
                models.Chapter.chapter_display_name.ilike(needle),
                models.Chapter.chapter_code.ilike(needle),
                models.Chapter.unit.ilike(needle),
            )
        )
    query = query.order_by(
        models.Chapter.board.asc(), models.Chapter.grade.asc(),
        models.Chapter.subject.asc(), models.Chapter.unit.asc(),
        models.Chapter.chapter_code.asc(), models.Chapter.id.asc(),
    )

    wanted_state = str(state or "").strip()
    if wanted_state and wanted_state not in STATE_VALUES:
        wanted_state = ""

    if not wanted_state:
        total = query.count()
        chapters = query.offset((page - 1) * page_size).limit(page_size).all()
        items = _project_many(db, chapters, now, running_probe)
    else:
        # State is derived, so a state filter has to project before it can
        # count. The whole filtered set is walked in bounded batches rather
        # than stopping at the first full page: a ``total`` that stopped early
        # would be a number the console displays and that is not true.
        matched: list[dict[str, Any]] = []
        offset = 0
        batch = max(page_size * 4, 100)
        while True:
            chapters = query.offset(offset).limit(batch).all()
            if not chapters:
                break
            for row in _project_many(db, chapters, now, running_probe):
                if row["state"] == wanted_state:
                    matched.append(row)
            offset += batch
        total = len(matched)
        items = matched[(page - 1) * page_size: page * page_size]

    total_pages = max(1, (total + page_size - 1) // page_size) if total else 1
    return {
        "items": items,
        "page": page,
        "page_size": page_size,
        "total": int(total),
        "total_pages": int(total_pages),
        "facets": facets(db),
        "states": [
            {"value": value, "label": label, "tone": tone}
            for value, label, tone in STATES
        ],
        "queue": queue_summary(db, capacity=capacity, worker_alive=worker_alive),
        "server_time": _iso(now),
    }


def _project_many(
    db: Session,
    chapters: Sequence[models.Chapter],
    now: datetime,
    running_probe=None,
) -> list[dict[str, Any]]:
    if not chapters:
        return []
    chapter_ids = [int(chapter.id) for chapter in chapters]
    batch_rows = {
        int(row.chapter_id): row
        for row in db.query(models.ChapterBatchRow)
        .filter(models.ChapterBatchRow.chapter_id.in_(chapter_ids))
        .all()
    }
    tasks = latest_tasks(db, [row.id for row in batch_rows.values()])
    positions = queue_positions(db)
    signals = job_signals(
        db, [row.job_id for row in batch_rows.values() if row.job_id],
    )
    projected: list[dict[str, Any]] = []
    for chapter in chapters:
        batch_row = batch_rows.get(int(chapter.id))
        task = tasks.get(int(batch_row.id)) if batch_row else None
        job_id = int(batch_row.job_id) if batch_row and batch_row.job_id else 0
        signal = signals.get(job_id) if job_id else None
        running = bool(running_probe(job_id)) if running_probe and job_id else False
        projected.append(
            project_row(
                chapter, batch_row, signal, task,
                position=positions.get(int(task.id)) if task else None,
                now=now, process_running=running,
            )
        )
    return projected


def project_one(
    db: Session, chapter_id: int, *, running_probe=None,
) -> dict[str, Any] | None:
    """A single row, freshly derived — the shape every mutation returns."""
    chapter = db.get(models.Chapter, int(chapter_id))
    if chapter is None:
        return None
    rows = _project_many(db, [chapter], _now(), running_probe)
    return rows[0] if rows else None
