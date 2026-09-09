"""Durable state for one Build Concepts run.

The Concept files and the Master files are one user-visible run even though
the review handoff may put them in different HTTP requests.  This module is
the small mechanical state machine shared by those requests.  It deliberately
does not inspect generated content: callers supply the named stage and the
progress value allocated to that stage.

The state is JSON so it can travel in a checkpoint bundle and survive a
process restart.  A run has one stable ``run_id``; a re-upload of a reviewed
Concept file keeps that id and all of the prior counters.
"""
from __future__ import annotations

import copy
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Mapping


SCHEMA_VERSION = 1
MAX_STAGE_HISTORY = 256
MAX_PROGRESS_EVENTS = 512
MAX_STAGE_LABEL = 512
MAX_RUN_ID = 128
_MAX_SECONDS = 366 * 24 * 60 * 60


def _now() -> float:
    return time.time()


def _iso(value: float) -> str:
    return datetime.fromtimestamp(value, timezone.utc).isoformat()


def _float(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if result >= 0 and result == result and result != float("inf") else default


def _bounded_seconds(value: Any) -> float:
    return min(_MAX_SECONDS, _float(value))


def _run_id(value: Any = "") -> str:
    text = str(value or "").strip()
    if not text or len(text) > MAX_RUN_ID:
        return uuid.uuid4().hex
    return text


def _is_master_stage(value: object) -> bool:
    return "master" in str(value or "").strip().lower()


def _state(value: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return a normalized copy without changing the caller's JSON object."""
    raw = dict(value or {})
    now = _now()
    started = _float(raw.get("started_at_epoch"), now)
    progress = max(0.0, min(1.0, _float(raw.get("progress"))))
    status = str(raw.get("status") or "processing")
    if status not in {"processing", "review", "master", "completed", "failed"}:
        status = "processing"
    state = {
        "schema_version": SCHEMA_VERSION,
        "run_id": _run_id(raw.get("run_id")),
        "status": status,
        "stage": str(raw.get("stage") or "")[:MAX_STAGE_LABEL],
        "progress": progress,
        "started_at": str(raw.get("started_at") or _iso(started)),
        "started_at_epoch": started,
        "active_started_at_epoch": (
            _float(raw.get("active_started_at_epoch"))
            if raw.get("active_started_at_epoch") is not None else None
        ),
        "active_elapsed_seconds": _bounded_seconds(raw.get("active_elapsed_seconds")),
        "review_started_at_epoch": (
            _float(raw.get("review_started_at_epoch"))
            if raw.get("review_started_at_epoch") is not None else None
        ),
        "review_wait_seconds": _bounded_seconds(raw.get("review_wait_seconds")),
        "wall_elapsed_seconds": _bounded_seconds(raw.get("wall_elapsed_seconds")),
        "last_updated_at": str(raw.get("last_updated_at") or _iso(now)),
        "finished_at": str(raw.get("finished_at") or ""),
        "finished_at_epoch": (
            _float(raw.get("finished_at_epoch"))
            if raw.get("finished_at_epoch") is not None else None
        ),
        "stage_history": [],
        "progress_events": [],
    }
    for row in raw.get("stage_history") or []:
        if not isinstance(row, Mapping):
            continue
        state["stage_history"].append({
            "stage": str(row.get("stage") or "")[:MAX_STAGE_LABEL],
            "progress": max(0.0, min(1.0, _float(row.get("progress")))),
            "started_at": str(row.get("started_at") or ""),
            "ended_at": str(row.get("ended_at") or ""),
            "active_elapsed_seconds": _bounded_seconds(
                row.get("active_elapsed_seconds")
            ),
        })
    state["stage_history"] = state["stage_history"][-MAX_STAGE_HISTORY:]
    for row in raw.get("progress_events") or []:
        if not isinstance(row, Mapping):
            continue
        state["progress_events"].append({
            "at": str(row.get("at") or ""),
            "value": max(0.0, min(1.0, _float(row.get("value")))),
            "label": str(row.get("label") or "")[:MAX_STAGE_LABEL],
            "stage": str(row.get("stage") or "")[:MAX_STAGE_LABEL],
        })
    state["progress_events"] = state["progress_events"][-MAX_PROGRESS_EVENTS:]
    return state


def new(*, run_id: str = "", now: float | None = None,
        stage: str = "", progress: float = 0.0) -> dict[str, Any]:
    now = _now() if now is None else float(now)
    state = _state({
        "run_id": _run_id(run_id),
        "status": "processing",
        "stage": stage,
        "progress": progress,
        "started_at": _iso(now),
        "started_at_epoch": now,
        "active_started_at_epoch": now,
        "last_updated_at": _iso(now),
    })
    if stage:
        state["stage_history"].append({
            "stage": str(stage)[:MAX_STAGE_LABEL],
            "progress": state["progress"],
            "started_at": _iso(now), "ended_at": "",
            "active_elapsed_seconds": 0.0,
        })
    _record_progress(state, progress, stage=stage, now=now)
    return state


def _snapshot(state: Mapping[str, Any], *, now: float | None = None,
              rebase: bool = False) -> dict[str, Any]:
    now = _now() if now is None else float(now)
    out = _state(state)
    active = out["active_elapsed_seconds"]
    active_started = out.get("active_started_at_epoch")
    if active_started is not None and out["status"] in {"processing", "master"}:
        active += max(0.0, now - _float(active_started))
    waiting = out["review_wait_seconds"]
    review_started = out.get("review_started_at_epoch")
    if review_started is not None and out["status"] == "review":
        waiting += max(0.0, now - _float(review_started))
    finished_at = out.get("finished_at_epoch")
    wall_end = _float(finished_at, now) if finished_at is not None else now
    wall = max(0.0, wall_end - _float(out.get("started_at_epoch"), now))
    out["active_elapsed_seconds"] = _bounded_seconds(active)
    out["review_wait_seconds"] = _bounded_seconds(waiting)
    out["wall_elapsed_seconds"] = _bounded_seconds(wall)
    out["last_updated_at"] = _iso(now)
    # The calculated interval is now part of the durable accumulator. Rebase
    # an open interval before a mutator returns so a later read cannot count
    # the same seconds a second time.
    if rebase:
        if out.get("active_started_at_epoch") is not None and out["status"] in {"processing", "master"}:
            out["active_started_at_epoch"] = now
        if out.get("review_started_at_epoch") is not None and out["status"] == "review":
            out["review_started_at_epoch"] = now
    return out


def snapshot(value: Mapping[str, Any] | None, *, now: float | None = None) -> dict[str, Any]:
    """Return current timing/progress facts, without mutating persisted state."""
    return _snapshot(value or {}, now=now)


def materialize(value: Mapping[str, Any] | None, *, now: float | None = None) -> dict[str, Any]:
    """Persist a current timing observation without reopening its interval.

    ``snapshot`` is intentionally observational for API reads.  A checkpoint
    or usage persistence boundary needs the other form: fold the open active
    or review interval into its accumulator and move the anchor to that same
    instant.  Otherwise a later restore would either lose the interval or
    count it again from the old anchor.
    """
    return _snapshot(value or {}, now=now, rebase=True)


def _record_progress(state: dict[str, Any], value: float, *, stage: str, now: float) -> None:
    value = max(state["progress"], min(1.0, _float(value)))
    state["progress"] = value
    label = str(stage or state.get("stage") or "")[:MAX_STAGE_LABEL]
    if label:
        state["stage"] = label
    state["progress_events"].append({
        "at": _iso(now), "value": value, "label": label, "stage": state["stage"],
    })
    state["progress_events"] = state["progress_events"][-MAX_PROGRESS_EVENTS:]


def _ensure_stage(state: dict[str, Any], label: str, *, now: float) -> str:
    """Close the previous stage and open ``label`` when it changes."""
    label = str(label or "")[:MAX_STAGE_LABEL]
    if not label:
        return str(state.get("stage") or "")
    if label != state.get("stage") or not state["stage_history"]:
        if state["stage_history"]:
            state["stage_history"][-1]["ended_at"] = _iso(now)
            state["stage_history"][-1]["active_elapsed_seconds"] = state[
                "active_elapsed_seconds"
            ]
        state["stage_history"].append({
            "stage": label, "progress": state["progress"],
            "started_at": _iso(now), "ended_at": "",
            "active_elapsed_seconds": 0.0,
        })
        state["stage_history"] = state["stage_history"][-MAX_STAGE_HISTORY:]
    return label


def start(value: Mapping[str, Any] | None = None, *, now: float | None = None,
          stage: str = "", progress: float | None = None) -> dict[str, Any]:
    """Start or resume processing while retaining the existing run history."""
    now = _now() if now is None else float(now)
    if not value or not value.get("run_id"):
        return new(now=now, stage=stage, progress=progress or 0.0)
    state = _snapshot(value, now=now, rebase=True)
    if state.get("status") == "review":
        return resume(state, now=now, stage=stage, progress=progress)
    if state.get("status") == "failed":
        # A failed processing attempt can be explicitly reopened by a review
        # upload or a retry.  Its terminal timestamp froze wall time for the
        # failed attempt; clear that freeze before opening the new interval so
        # the same run's subsequent wall time includes the retry/wait period.
        state["finished_at"] = ""
        state["finished_at_epoch"] = None
    state["status"] = "master" if _is_master_stage(stage) else "processing"
    if state.get("active_started_at_epoch") is None:
        state["active_started_at_epoch"] = now
    if stage:
        _ensure_stage(state, stage, now=now)
    if progress is not None:
        _record_progress(state, progress, stage=stage, now=now)
    elif stage:
        _record_progress(state, state["progress"], stage=stage, now=now)
    return state


def stage(value: Mapping[str, Any], label: str, *, progress: float | None = None,
          now: float | None = None) -> dict[str, Any]:
    """Record an explicit stage and monotonic progress value."""
    now = _now() if now is None else float(now)
    state = _snapshot(value, now=now, rebase=True)
    label = _ensure_stage(state, label, now=now)
    _record_progress(state, state["progress"] if progress is None else progress,
                     stage=label, now=now)
    return state


def progress(value: Mapping[str, Any], fraction: float, *, label: str = "",
             now: float | None = None) -> dict[str, Any]:
    return stage(value, label or str(value.get("stage") or ""), progress=fraction, now=now)


def pause_for_review(value: Mapping[str, Any], *, now: float | None = None,
                     progress: float | None = None, stage: str = "Concept files ready for review") -> dict[str, Any]:
    """Close processing time and open a review waiting interval."""
    now = _now() if now is None else float(now)
    state = _snapshot(value, now=now, rebase=True)
    if state.get("status") == "failed":
        # ``run_with_openai_usage`` records a failed attempt before the review
        # endpoint reopens its waiting interval.  Reopening is an explicit
        # retryable transition, so the terminal wall-time freeze must not
        # survive it.
        state["finished_at"] = ""
        state["finished_at_epoch"] = None
    _ensure_stage(state, stage, now=now)
    if progress is not None:
        _record_progress(state, progress, stage=stage, now=now)
    state["active_started_at_epoch"] = None
    state["review_started_at_epoch"] = state.get("review_started_at_epoch") or now
    state["status"] = "review"
    state["last_updated_at"] = _iso(now)
    return state


def resume(value: Mapping[str, Any], *, now: float | None = None,
           progress: float | None = None, stage: str = "") -> dict[str, Any]:
    """Close review waiting time and reopen processing for the same run."""
    now = _now() if now is None else float(now)
    state = _snapshot(value, now=now, rebase=True)
    state["review_started_at_epoch"] = None
    state["active_started_at_epoch"] = now
    state["status"] = "master" if _is_master_stage(stage) else "processing"
    if stage:
        _ensure_stage(state, stage, now=now)
    if progress is not None:
        _record_progress(state, progress, stage=stage, now=now)
    elif stage:
        _record_progress(state, state["progress"], stage=stage, now=now)
    return state


def finish(value: Mapping[str, Any], *, now: float | None = None,
           progress: float | None = None, status: str = "completed",
           stage: str = "") -> dict[str, Any]:
    """Close all timing windows. A caller chooses the final progress explicitly."""
    now = _now() if now is None else float(now)
    state = _snapshot(value, now=now)
    if progress is not None:
        _ensure_stage(state, stage, now=now)
        _record_progress(state, progress, stage=stage, now=now)
    state["active_started_at_epoch"] = None
    state["review_started_at_epoch"] = None
    state["status"] = status if status in {"completed", "failed"} else "completed"
    state["finished_at"] = _iso(now)
    state["finished_at_epoch"] = now
    state["last_updated_at"] = _iso(now)
    return state


def validate(value: Any, *, path: str = "run_state") -> None:
    """Validate imported run state without coercing it into a new record."""
    if not isinstance(value, dict):
        raise ValueError(f"{path} must be an object")
    if value.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"{path}.schema_version is not supported")
    if not isinstance(value.get("run_id"), str) or not value["run_id"].strip():
        raise ValueError(f"{path}.run_id must be non-empty")
    if value.get("status") not in {"processing", "review", "master", "completed", "failed"}:
        raise ValueError(f"{path}.status is not supported")
    for field in ("progress", "active_elapsed_seconds", "review_wait_seconds", "wall_elapsed_seconds"):
        number = value.get(field)
        if isinstance(number, bool) or not isinstance(number, (int, float)):
            raise ValueError(f"{path}.{field} must be numeric")
        if number < 0 or number > (_MAX_SECONDS if field != "progress" else 1.0):
            raise ValueError(f"{path}.{field} is out of bounds")
    for field in ("stage_history", "progress_events"):
        rows = value.get(field)
        if not isinstance(rows, list):
            raise ValueError(f"{path}.{field} must be an array")
        maximum = MAX_STAGE_HISTORY if field == "stage_history" else MAX_PROGRESS_EVENTS
        if len(rows) > maximum:
            raise ValueError(f"{path}.{field} exceeds its bounded size")


def for_job(job: Any) -> dict[str, Any]:
    return copy.deepcopy(getattr(job, "run_state", None) or {})


def set_for_job(job: Any, state: Mapping[str, Any]) -> dict[str, Any]:
    normalized = _state(state)
    job.run_state = normalized
    return normalized
