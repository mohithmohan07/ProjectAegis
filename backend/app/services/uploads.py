"""Shared upload-job helpers: stage a file, replace it, convert it to MMD.

Uploading now ONLY saves the file (status ``uploaded``) — it never auto-runs
Mathpix/MMD. The user can replace the file (e.g. wrong PDF) before an explicit
``convert`` step, which is where the (slower) MMD conversion happens with live
progress logs.
"""
from __future__ import annotations

import re
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
from . import auth, mmd, openai_usage, progress


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
        live = config.use_live_mmd()
        progress.log(
            "Converting to MMD via Mathpix…" if live and path.suffix.lower() in
            {".pdf", ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}
            else "Normalizing document to MMD…")
        progress.set_progress(0.3, label="Converting to MMD")
        mmd_text = mmd.to_mmd(path)
        job.mmd_text = mmd_text
        job.question_inventory = {}
        job.generation_checkpoint = {}
        job.generation_log = []
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
    events = [
        event
        for event in progress.current_events(limit=1200)
        if event.get("type") in {"log", "step", "progress"}
    ]
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
        cumulative = openai_usage.bind_persisted_summary(
            f"upload-job:{job.id}",
            job.openai_usage if isinstance(job.openai_usage, dict) else {},
        )
        if cumulative.get("request_count"):
            # A resumed/imported job exposes its durable history immediately,
            # before the first new provider response arrives.
            progress.usage(cumulative)
        try:
            result = fn()
        except Exception as exc:
            # A failed generation transaction must not erase usage from provider
            # responses already received (and therefore potentially billed).
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
