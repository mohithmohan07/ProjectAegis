"""Shared upload-job helpers: stage a file, replace it, convert it to MMD.

Uploading now ONLY saves the file (status ``uploaded``) — it never auto-runs
Mathpix/MMD. The user can replace the file (e.g. wrong PDF) before an explicit
``convert`` step, which is where the (slower) MMD conversion happens with live
progress logs.
"""
from __future__ import annotations

from pathlib import Path

from sqlalchemy.orm import Session

from .. import config, models
from . import mmd, progress


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
