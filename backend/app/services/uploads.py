"""Shared upload-job helpers: stage a file, replace it, convert it to MMD.

Uploading now ONLY saves the file (status ``uploaded``) — it never auto-runs
the conversion. The user can replace the file (e.g. wrong PDF) before an explicit
``convert`` step, which is where the (slower) MMD conversion happens with live
progress logs.
"""
from __future__ import annotations

import re
import json
import threading
import time
import traceback
import uuid
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from .. import config, models
from . import auth, generation_recovery, mmd, openai_usage, progress, run_state


_usage_job_locks: dict[int, threading.Lock] = {}
_usage_job_locks_guard = threading.Lock()
_SECRET_PATTERN = re.compile(
    r"(?i)\b(?:sk-(?:proj-)?[A-Za-z0-9_-]{12,}|bearer\s+[A-Za-z0-9._~-]{12,})"
)


class UploadJobNotFound(ValueError):
    pass


class JobAlreadyRunningError(RuntimeError):
    pass


def _usage_job_lock(job_id: int) -> threading.Lock:
    """Return the current-process lock for one upload job."""
    with _usage_job_locks_guard:
        return _usage_job_locks.setdefault(job_id, threading.Lock())


def is_job_running(job_id: int | None) -> bool:
    if not job_id:
        return False
    with _usage_job_locks_guard:
        lock = _usage_job_locks.get(int(job_id))
        return bool(lock and lock.locked())


@contextmanager
def exclusive_job_operation(job_id: int):
    """Claim the process-local mutation lock or fail without waiting."""
    lock = _usage_job_lock(job_id)
    if not lock.acquire(blocking=False):
        raise JobAlreadyRunningError(
            "generation is already running for this upload; wait for the "
            "active run to finish before changing or resuming it"
        )
    try:
        yield
    finally:
        lock.release()


def normalize_owner_sub(owner_sub: str | None) -> str:
    return str(owner_sub or auth.LOCAL_OWNER_SUB).strip() or auth.LOCAL_OWNER_SUB


def _storage_path(storage_key: str) -> Path:
    root = config.UPLOAD_DIR.resolve()
    candidate = (root / storage_key).resolve()
    if candidate == root or root not in candidate.parents:
        raise ValueError("invalid upload storage key")
    return candidate


def _new_storage_key(job_id: int, filename: str) -> str:
    suffix = Path(filename).suffix.lower()
    if not re.fullmatch(r"\.[a-z0-9]{1,12}", suffix):
        suffix = ".bin"
    return f"{int(job_id)}/{uuid.uuid4().hex}{suffix}"


def save_upload_file(
    filename: str,
    raw_bytes: bytes,
    *,
    storage_key: str = "",
) -> Path:
    """Write an upload to an opaque path (or the legacy basename fallback)."""
    dest = (
        _storage_path(storage_key)
        if storage_key
        else config.UPLOAD_DIR / Path(filename).name
    )
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(raw_bytes)
    return dest


def persist_new_job(
    db: Session,
    job: models.UploadJob,
    raw_bytes: bytes,
) -> models.UploadJob:
    """Assign an ID, store bytes under that job, then commit the row."""
    stored: Path | None = None
    try:
        db.add(job)
        db.flush()
        job.upload_storage_key = _new_storage_key(job.id, job.filename)
        stored = save_upload_file(
            job.filename, raw_bytes, storage_key=job.upload_storage_key)
        db.commit()
        db.refresh(job)
        return job
    except Exception:
        db.rollback()
        if stored is not None:
            stored.unlink(missing_ok=True)
        raise


def get_job(
    db: Session,
    job_id: int,
    *,
    owner_sub: str | None = None,
    module: str = "",
    learning_kind: str = "",
) -> models.UploadJob:
    query = db.query(models.UploadJob).filter(
        models.UploadJob.id == job_id,
        models.UploadJob.owner_sub == normalize_owner_sub(owner_sub),
    )
    if module:
        query = query.filter(models.UploadJob.module == module)
    if learning_kind:
        query = query.filter(
            models.UploadJob.learning_kind == learning_kind.strip().lower())
    job = query.one_or_none()
    if not job:
        raise UploadJobNotFound("upload job not found")
    return job


def upload_file_path(job: models.UploadJob) -> Path:
    if job.upload_storage_key:
        return _storage_path(job.upload_storage_key)
    return config.UPLOAD_DIR / Path(job.filename).name


def _job_run_state(job: models.UploadJob) -> dict:
    state = run_state.for_job(job)
    job_run_id = str(getattr(job, "run_id", "") or "")
    if state and not state.get("run_id"):
        state["run_id"] = job_run_id
    elif not state and job_run_id:
        # Older rows may receive the scalar identity before the JSON state is
        # backfilled. Preserve that identity when their first resumed request
        # opens the richer timing record.
        state = {"run_id": job_run_id}
    return state


def start_or_resume_run(
    db: Session,
    job_id: int,
    *,
    owner_sub: str | None = None,
    stage: str = "",
    progress_value: float | None = None,
) -> dict:
    """Open processing for this run, retaining its id/history across requests."""
    job = get_job(db, job_id, owner_sub=owner_sub)
    current = _job_run_state(job)
    state = run_state.start(
        current,
        stage=stage,
        progress=progress_value,
    )
    job.run_id = state["run_id"]
    run_state.set_for_job(job, state)
    db.commit()
    db.refresh(job)
    if progress_value is not None:
        progress.seed_progress(progress_value, label=stage)
    elif state.get("progress"):
        progress.seed_progress(state["progress"], label=state.get("stage", ""))
    return state


def update_run_stage(
    db: Session,
    job_id: int,
    stage: str,
    *,
    progress_value: float | None = None,
    owner_sub: str | None = None,
) -> dict:
    job = get_job(db, job_id, owner_sub=owner_sub)
    current = _job_run_state(job)
    if not current:
        current = run_state.new(stage=stage, progress=progress_value or 0.0)
    state = run_state.stage(current, stage, progress=progress_value)
    job.run_id = state["run_id"]
    run_state.set_for_job(job, state)
    db.commit()
    db.refresh(job)
    progress.seed_progress(state["progress"], label=stage)
    return state


def pause_run_for_review(
    db: Session,
    job_id: int,
    *,
    progress_value: float | None = None,
    stage: str = "Concept files ready for review",
    owner_sub: str | None = None,
) -> dict:
    """Persist the review handoff and stop the active processing clock."""
    job = get_job(db, job_id, owner_sub=owner_sub)
    current = _job_run_state(job)
    if not current:
        current = run_state.new(stage=stage, progress=progress_value or 0.0)
    state = run_state.pause_for_review(
        current, progress=progress_value, stage=stage,
    )
    job.run_id = state["run_id"]
    run_state.set_for_job(job, state)
    db.commit()
    db.refresh(job)
    progress.seed_progress(state["progress"], label=stage)
    progress.log(
        "Concept files are ready for review. Processing time is paused; "
        "the same run will resume after the reviewed file is received."
    )
    return state


def resume_run_after_review(
    db: Session,
    job_id: int,
    *,
    stage: str = "Building Master files from reviewed Concept files",
    progress_value: float | None = None,
    owner_sub: str | None = None,
) -> dict:
    """Resume processing after review without reopening a new billing run."""
    job = get_job(db, job_id, owner_sub=owner_sub)
    current = _job_run_state(job)
    if not current:
        current = run_state.new(stage=stage, progress=progress_value or 0.0)
    state = run_state.resume(
        current, stage=stage, progress=progress_value,
    )
    job.run_id = state["run_id"]
    run_state.set_for_job(job, state)
    db.commit()
    db.refresh(job)
    progress.seed_progress(state["progress"], label=stage)
    progress.log("Resuming the same run after Concept review.")
    return state


def finish_run(
    db: Session,
    job_id: int,
    *,
    progress_value: float | None = None,
    stage: str = "",
    status: str = "completed",
    owner_sub: str | None = None,
) -> dict:
    """Close the run's active/review clocks at its real terminal boundary."""
    job = get_job(db, job_id, owner_sub=owner_sub)
    current = _job_run_state(job)
    if not current:
        current = run_state.new(stage=stage, progress=progress_value or 0.0)
    state = run_state.finish(
        current, progress=progress_value, stage=stage, status=status,
    )
    job.run_id = state["run_id"]
    run_state.set_for_job(job, state)
    db.commit()
    db.refresh(job)
    progress.seed_progress(state["progress"], label=stage)
    return state


def replace_file(
    db: Session,
    job_id: int,
    *,
    filename: str,
    raw_bytes: bytes,
    owner_sub: str | None = None,
    module: str = "",
) -> models.UploadJob:
    """Swap the staged file before conversion (status must still be 'uploaded')."""
    job = get_job(db, job_id, owner_sub=owner_sub, module=module)
    with exclusive_job_operation(job.id):
        db.refresh(job)
        generation_recovery.require_mutation_allowed(
            job, operation="replace this upload's file"
        )
        if job.status not in {"uploaded", "converted"}:
            raise ValueError(
                "cannot replace the file after generation has started; "
                "start a new upload"
            )
        previous_key = job.upload_storage_key
        new_key = _new_storage_key(job.id, filename)
        stored = save_upload_file(filename, raw_bytes, storage_key=new_key)
        job.filename = Path(filename).name
        job.upload_storage_key = new_key
        job.mmd_text = ""
        job.question_inventory = {}
        job.generation_checkpoint = {}
        job.generation_log = []
        job.openai_usage = {}
        job.status = "uploaded"
        try:
            db.commit()
            db.refresh(job)
        except Exception:
            db.rollback()
            stored.unlink(missing_ok=True)
            raise
        if previous_key:
            try:
                _storage_path(previous_key).unlink(missing_ok=True)
            except (OSError, ValueError):
                pass
    return job


def convert_job(
    db: Session,
    job_id: int,
    *,
    owner_sub: str | None = None,
    module: str = "",
) -> dict:
    """Convert the staged file to MMD (the explicit, slower processing step)."""
    job = get_job(db, job_id, owner_sub=owner_sub, module=module)
    with exclusive_job_operation(job.id):
        db.refresh(job)
        generation_recovery.require_mutation_allowed(
            job, operation="convert this upload"
        )
        if job.status == "generated":
            raise ValueError(
                "cannot reconvert a completed upload; start a new upload")
        if not job.filename:
            raise ValueError("no file staged for this job")
        path = upload_file_path(job)
        if not path.exists():
            raise ValueError(f"staged file is missing: {job.filename}")

        progress.log(f"Reading {job.filename} ({path.stat().st_size:,} bytes).")
        progress.set_progress(0.1, label="Reading file")
        progress.log("Normalizing document to MMD…")
        progress.set_progress(0.3, label="Converting to MMD")
        from . import model_routing_run

        with model_routing_run.bind_job(job):
            mmd_text = mmd.to_mmd(path)
        job.mmd_text = mmd_text
        job.question_inventory = {}
        job.generation_checkpoint = {}
        job.status = "converted"
        db.commit()
        db.refresh(job)
        progress.set_progress(1.0, label="Converted to MMD")
        progress.log(
            f"Converted to MMD: {len(mmd_text):,} characters.",
            level="success",
        )
        return {
            "job_id": job.id,
            "status": job.status,
            "filename": job.filename,
            "mmd_chars": len(mmd_text),
            "mmd_text": mmd_text,
        }


def persist_current_openai_usage(
    db: Session,
    job_id: int,
    *,
    owner_sub: str | None = None,
) -> dict:
    """Persist durable history plus the active run exactly once.

    This function is safe to call after every automatic checkpoint and again
    during terminal success/failure handling. The active run's immutable
    baseline prevents its cumulative tracker from being added repeatedly.
    """
    job = get_job(db, job_id, owner_sub=owner_sub)
    existing = job.openai_usage if isinstance(job.openai_usage, dict) else {}
    merged = openai_usage.cumulative_summary(
        existing,
        persistence_key=f"upload-job:{job.id}",
    )
    # The billing ledger is cumulative by receipt; the run clock is
    # cumulative by explicit processing/review transitions. Join them at the
    # persistence boundary so a live event, checkpoint, and job GET agree.
    state = _job_run_state(job)
    if state:
        # Fold the open interval into durable run_state and rebase its anchor
        # before writing the usage snapshot. A checkpoint exported after this
        # call must carry the elapsed time already paid for; retaining the old
        # anchor would count that interval again when the restored run starts.
        state = run_state.materialize(state)
        job.run_id = state["run_id"]
        run_state.set_for_job(job, state)
        merged = openai_usage.apply_run_timing(merged, state)
    job.openai_usage = merged
    db.commit()
    db.refresh(job)
    progress.usage(merged)
    return merged


def persist_current_generation_log(
    db: Session,
    job_id: int,
    *,
    error: Exception | None = None,
    owner_sub: str | None = None,
) -> list[dict]:
    """Persist the latest browser-visible run log for diagnostics and export."""
    job = get_job(db, job_id, owner_sub=owner_sub)
    current_events = [
        event
        for event in progress.current_events(limit=1200)
        if event.get("type") in {"log", "step", "progress"}
    ]
    # Checkpoint writes can happen several times during one stream. Append to
    # the durable same-run log, but dedupe the exact event objects so a
    # repeated persistence call never doubles its lines. New events with the
    # same message but a different timestamp remain valid separate events.
    events: list[dict] = []
    seen: set[str] = set()
    for event in [*(job.generation_log or []), *current_events]:
        try:
            key = json.dumps(event, ensure_ascii=False, sort_keys=True,
                             separators=(",", ":"))
        except (TypeError, ValueError):
            key = repr(event)
        if key in seen:
            continue
        seen.add(key)
        events.append(event)
    if error is not None:
        frames: list[dict] = []
        for frame in traceback.extract_tb(error.__traceback__)[-8:]:
            path = Path(frame.filename)
            try:
                display_path = path.resolve().relative_to(config.ROOT.resolve())
            except (OSError, ValueError):
                display_path = Path(path.name)
            frames.append({
                "file": display_path.as_posix(),
                "line": max(1, int(frame.lineno)),
                "function": str(frame.name or "")[:160],
            })
        reason = _SECRET_PATTERN.sub("[REDACTED]", (
            str(error) or error.__class__.__name__
        ))[:4000]
        location = frames[-1] if frames else {}
        where = ""
        if location:
            where = (
                f" at {location['file']}:{location['line']}"
                f" in {location['function']}"
            )
        diagnostic = {
            "type": "log",
            "level": "error",
            "message": (
                f"{error.__class__.__name__}: {reason}{where}"
            ),
            "ts": time.time(),
            "error": {
                "exception_type": error.__class__.__name__,
                "reason": reason,
                "frames": frames,
            },
        }
        # Terminal idempotency: one failure produces exactly one persisted
        # terminal event. The recovery runner (or a prior persistence call)
        # may already have emitted this same terminal reason as an error log;
        # do not append an identical duplicate.
        already_terminal = any(
            event.get("type") == "log"
            and event.get("level") == "error"
            and reason
            and reason in str(event.get("message") or "")
            for event in events[-5:]
        )
        if not already_terminal:
            events.append(diagnostic)
        job.detail = f"Generation failed: {reason}{where}"
    job.generation_log = events[-1200:]
    db.commit()
    db.refresh(job)
    return list(job.generation_log or [])


def run_with_openai_usage(
    db: Session,
    job_id: int,
    fn: Callable[[], Any],
    *,
    owner_sub: str | None = None,
) -> dict:
    """Run uploaded-file generation and persist usage on success or failure."""
    # Verify ownership before acquiring or exposing another job's run state.
    job = get_job(db, job_id, owner_sub=owner_sub)
    with exclusive_job_operation(job_id):
        # A prior run may have committed after this Session populated its
        # identity map but before we acquired the lock.
        db.refresh(job)
        if job.status == "generated":
            raise ValueError(
                "this upload has already been generated; start a new upload")
        # Bind the stable run before the first provider call. If the previous
        # request paused at Concept review, this reopens processing while
        # preserving every receipt, stage and progress sample.
        # Keep an explicit stage selected by the workflow owner (for example
        # the Master handoff). The generic usage wrapper must open the clock
        # without replacing that stage with a transport-level label.
        run_stage = str(_job_run_state(job).get("stage") or "Processing this run")
        start_or_resume_run(
            db,
            job_id,
            owner_sub=owner_sub,
            stage=run_stage,
        )
        db.refresh(job)
        cumulative = openai_usage.bind_persisted_summary(
            f"upload-job:{job.id}",
            job.openai_usage if isinstance(job.openai_usage, dict) else {},
        )
        if cumulative.get("request_count"):
            # A resumed/imported job exposes its durable history immediately,
            # before the first new provider response arrives.
            progress.usage(cumulative)
        try:
            from . import model_routing_run

            require_pre = job.module == "build_concepts"
            if require_pre:
                from . import build_concepts_release

                review = build_concepts_release.concept_review_state(job)
                release_uids = review.get("concept_release_uids")
                # A recorded Concept-review boundary has already completed
                # source generation. Its remaining historical stages use
                # OpenAI; a newly requested Pre revision binds its own mini
                # profile inside that workflow. Do not require the retired
                # v1 Pre author's credential before the workflow can enter.
                # A status label or an empty marker alone proves no boundary.
                review_boundary = (
                    review.get("version") == build_concepts_release.CONCEPT_REVIEW_VERSION
                    and review.get("status") in build_concepts_release.CONCEPT_REVIEW_STATUSES
                    and isinstance(release_uids, dict)
                    and any(str(value or "").strip() for value in release_uids.values())
                )
                require_pre = not review_boundary
            with model_routing_run.bind_job(
                job, require_pre=require_pre
            ):
                result = fn()
        except Exception as exc:
            # A failed generation transaction must not erase usage from provider
            # responses already received (and therefore potentially billed).
            db.rollback()
            try:
                finish_run(
                    db, job_id, owner_sub=owner_sub,
                    progress_value=run_state.for_job(job).get("progress"),
                    status="failed",
                )
            except Exception:  # pragma: no cover - preserve provider error
                db.rollback()
            try:
                persist_current_openai_usage(
                    db, job_id, owner_sub=owner_sub)
            except Exception:  # pragma: no cover - preserve the generation error
                db.rollback()
            try:
                persist_current_generation_log(
                    db, job_id, error=exc, owner_sub=owner_sub)
            except Exception:  # pragma: no cover - preserve the generation error
                db.rollback()
            raise

        summary = persist_current_openai_usage(
            db, job_id, owner_sub=owner_sub)
        persist_current_generation_log(
            db, job_id, owner_sub=owner_sub)
        if isinstance(result, dict):
            result = {**result, "openai_usage": summary}
        return result
