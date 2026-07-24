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
from collections.abc import Callable
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from .. import config, models
from . import mmd, openai_usage, progress


_usage_job_locks: dict[int, threading.Lock] = {}
_usage_job_locks_guard = threading.Lock()
_SECRET_PATTERN = re.compile(
    r"(?i)\b(?:sk-(?:proj-)?[A-Za-z0-9_-]{12,}|bearer\s+[A-Za-z0-9._~-]{12,})"
)


def _usage_job_lock(job_id: int) -> threading.Lock:
    """Serialize generation/accounting for the same upload within a worker."""
    with _usage_job_locks_guard:
        return _usage_job_locks.setdefault(job_id, threading.Lock())


def save_upload_file(filename: str, raw_bytes: bytes) -> Path:
    dest = config.UPLOAD_DIR / Path(filename).name
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(raw_bytes)
    return dest


def get_job(db: Session, job_id: int) -> models.UploadJob:
    job = db.get(models.UploadJob, job_id)
    if not job:
        raise ValueError("upload job not found")
    return job


def replace_file(db: Session, job_id: int, *, filename: str, raw_bytes: bytes) -> models.UploadJob:
    """Swap the staged file before conversion (status must still be 'uploaded')."""
    job = get_job(db, job_id)
    if job.status not in {"uploaded", "converted"}:
        raise ValueError(
            "cannot replace the file after generation has started; start a new upload")
    save_upload_file(filename, raw_bytes)
    job.filename = Path(filename).name
    job.mmd_text = ""
    job.question_inventory = {}
    job.generation_checkpoint = {}
    job.generation_log = []
    job.openai_usage = {}
    job.status = "uploaded"
    db.commit()
    db.refresh(job)
    return job


def convert_job(db: Session, job_id: int) -> dict:
    """Convert the staged file to MMD (the explicit, slower processing step)."""
    job = get_job(db, job_id)
    if not job.filename:
        raise ValueError("no file staged for this job")
    path = config.UPLOAD_DIR / job.filename
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
    progress.log(f"Converted to MMD: {len(mmd_text):,} characters.", level="success")
    return {
        "job_id": job.id,
        "status": job.status,
        "filename": job.filename,
        "mmd_chars": len(mmd_text),
        "mmd_text": mmd_text,
    }


def persist_current_openai_usage(db: Session, job_id: int) -> dict:
    """Merge this run's usage into the durable total for the staged file."""
    job = get_job(db, job_id)
    current = openai_usage.current_summary()
    existing = job.openai_usage if isinstance(job.openai_usage, dict) else {}
    merged = openai_usage.merge_summaries(existing, current)
    job.openai_usage = merged
    db.commit()
    db.refresh(job)
    return merged


def persist_current_generation_log(
    db: Session, job_id: int, *, error: Exception | None = None,
) -> list[dict]:
    """Persist the latest browser-visible run log for diagnostics and export."""
    job = get_job(db, job_id)
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
    db: Session, job_id: int, fn: Callable[[], Any]
) -> dict:
    """Run uploaded-file generation and persist usage on success or failure."""
    with _usage_job_lock(job_id):
        try:
            result = fn()
        except Exception as exc:
            # A failed generation transaction must not erase usage from provider
            # responses already received (and therefore potentially billed).
            db.rollback()
            try:
                persist_current_openai_usage(db, job_id)
            except Exception:  # pragma: no cover - preserve the generation error
                db.rollback()
            try:
                persist_current_generation_log(db, job_id, error=exc)
            except Exception:  # pragma: no cover - preserve the generation error
                db.rollback()
            raise

        summary = persist_current_openai_usage(db, job_id)
        persist_current_generation_log(db, job_id)
        if isinstance(result, dict):
            result = {**result, "openai_usage": summary}
        return result
