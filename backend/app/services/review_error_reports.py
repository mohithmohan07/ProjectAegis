"""Explicit reviewer reports: full private evidence, safe maintenance index.

All workbook cells, notes and saved provider responses are untrusted evidence.
This module archives them; it never interprets instructions, calls a model or
changes accepted content. Snapshots are independent copies, never hardlinks.
"""
from __future__ import annotations

import copy
import hashlib
import json
import logging
import os
import re
import shutil
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException, Request
from sqlalchemy import inspect

from .. import config, models
from . import failure_reports, failure_reports_public, storage_capacity, uploads

log = logging.getLogger(__name__)
MAX_NOTES_CHARS = 20000
_REPORT_ID = re.compile(r"^[a-f0-9]{32}$")
_ASSET = re.compile(r"/source-assets/[0-9]+/([a-f0-9]{64}\.jpg)")
_WRITE_CHUNK_BYTES = 1024 * 1024


class ReviewEvidenceUnavailable(RuntimeError):
    pass


async def requested_notes(request: Request) -> str | None:
    # Form(None) collapses an explicitly present empty string to None. An
    # empty note is a valid checked checkbox: preserve field presence here.
    form = await request.form()
    if "review_error_notes" not in form:
        return None
    value = form["review_error_notes"]
    if not isinstance(value, str) or len(value) > MAX_NOTES_CHARS:
        raise HTTPException(422, "Error notes must be text of at most 20,000 characters.")
    return value


def root() -> Path:
    return Path(config.DATA_DIR) / "review_error_reports"


def _record(row):
    return {column.key: getattr(row, column.key) for column in inspect(row).mapper.column_attrs}


def _json_chunks(value):
    for chunk in json.JSONEncoder(ensure_ascii=False, sort_keys=True, default=str).iterencode(value):
        yield chunk.encode("utf-8")


def _sha(path):
    value = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


class _EvidenceFile:
    """A seekable, unbuffered sink guarded at each bounded physical write."""

    def __init__(self, path):
        self.path = path
        with storage_capacity.reserve_review_evidence_write(0, required_inodes=1, path=path.parent):
            self.file = path.open("x+b", buffering=0)
            try:
                os.chmod(path, 0o600)
            except Exception:
                self.file.close()
                path.unlink(missing_ok=True)
                raise

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.file.close()

    def __getattr__(self, name):
        return getattr(self.file, name)

    def write(self, data):
        view = memoryview(data)
        written = 0
        while written < len(view):
            chunk = view[written:written + _WRITE_CHUNK_BYTES]
            with storage_capacity.reserve_review_evidence_write(len(chunk), path=self.path.parent):
                count = self.file.write(chunk)
                if not count:
                    raise OSError("review evidence write made no progress")
                # FileIO is unbuffered: capacity consumption reaches the OS
                # before the shared reservation is released, including short writes.
            written += count
        return written


def _mkdir_private(path):
    missing = 0
    existing = path
    while not existing.exists():
        missing += 1
        existing = existing.parent
    if missing:
        with storage_capacity.reserve_review_evidence_write(0, required_inodes=missing, path=existing):
            path.mkdir(parents=True, exist_ok=True, mode=0o700)


def _write_json(path, value):
    # Unique report directories and exclusive creation make every observation
    # immutable. Directory fsync below covers renaming the completed snapshot.
    created = False
    try:
        with _EvidenceFile(path) as output:
            created = True
            for chunk in _json_chunks(value):
                output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
    except Exception:
        # In particular, do not leave a truncated upload outcome that would
        # make later read-only reconciliation look like corrupt evidence.
        if created:
            path.unlink(missing_ok=True)
        raise
    _sync_directory(path.parent)


def _sync_directory(path):
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _safe_path(path: Path, base: Path):
    if not path.resolve().is_relative_to(base.resolve()):
        raise ValueError("review evidence path escaped its storage root")
    current = path
    while current != base.parent:
        if current.is_symlink():
            raise ValueError("review evidence must not follow symlinks")
        if current == base:
            break
        current = current.parent


def prepare(db, job, *, review_kind, lane, corrected_bytes, filename, notes, actor_sub):
    """Durably capture BEFORE applying an upload; raise before content changes.

    A notes field absent from the request is the entire opt-out. Missing
    historical artifacts are declared, never represented as captured files.
    Existing artifacts that cannot be copied make the request fail visibly.
    """
    if not isinstance(notes, str):
        return None
    if review_kind not in {"concept", "master"} or lane not in {"pre", "post"}:
        raise ValueError("invalid review report classification")
    if len(notes) > MAX_NOTES_CHARS:
        raise ValueError("Error notes exceed 20,000 characters.")
    now = datetime.now(timezone.utc)
    report_id = uuid4().hex
    receipt = {"report_id": report_id, "status": "queued", "review_kind": review_kind,
               "lane": lane, "occurred_at": now.isoformat().replace("+00:00", "Z"),
               "corrected_sha256": hashlib.sha256(corrected_bytes).hexdigest()}
    staging = None
    destination = None
    index_path = None
    try:
        base = root()
        _mkdir_private(base)
        _safe_path(base, Path(config.DATA_DIR))
        with storage_capacity.reserve_review_evidence_write(0, required_inodes=1, path=base):
            staging = Path(tempfile.mkdtemp(prefix=".capture-", dir=base))
        entries, missing, asset_names = [], [], set()
        archive = staging / "snapshot.zip"
        with _EvidenceFile(archive) as sink, zipfile.ZipFile(
            sink, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=1,
        ) as bundle:
            def add_chunks(name, chunks):
                digest = hashlib.sha256()
                size = 0
                with bundle.open(name, "w", force_zip64=True) as target:
                    for chunk in chunks:
                        target.write(chunk)
                        digest.update(chunk)
                        size += len(chunk)
                entries.append({"path": name, "sha256": digest.hexdigest(), "size_bytes": size})

            def add_json(name, value):
                # Extract only this server's content-addressed asset addresses;
                # no network fetching and no filenames supplied by the reviewer.
                def chunks():
                    for chunk in _json_chunks(value):
                        asset_names.update(_ASSET.findall(chunk.decode("utf-8")))
                        yield chunk
                add_chunks(name, chunks())

            def add_file(path, storage_base, name, *, optional=False):
                _safe_path(path, storage_base)
                if optional and not path.exists():
                    missing.append(name)
                    return
                before = path.stat()
                with path.open("rb") as source:
                    add_chunks(name, iter(lambda: source.read(1024 * 1024), b""))
                after = path.stat()
                if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                    raise ValueError("review evidence changed during capture")

            def add_tree(path, storage_base, prefix):
                _safe_path(path, storage_base)
                if not path.exists():
                    missing.append(prefix)
                    return
                for item in sorted(path.rglob("*")):
                    _safe_path(item, storage_base)
                    if item.is_file():
                        add_file(item, storage_base, prefix + "/" + item.relative_to(path).as_posix())

            add_json("context/job.json", _record(job))
            add_json("review/submission.json", {
                **receipt, "status": "captured_before_upload", "job_id": job.id,
                "run_id": job.run_id, "filename": filename, "reviewer_notes": notes,
                "actor_sub": actor_sub, "evidence_is_untrusted": True,
            })
            add_chunks("review/corrected-upload.bin", [corrected_bytes])
            for cls in (models.ConceptReleaseVersion, models.ConceptRevision,
                        models.AssessmentRelease, models.ChapterBatchRow,
                        models.ChapterBatchTask, models.RunNotification):
                for row in db.query(cls).filter(cls.job_id == job.id).order_by(cls.id).yield_per(1):
                    add_json(f"database/{cls.__tablename__}/{row.id}.json", _record(row))
                    if cls is models.AssessmentRelease:
                        from .assessment_release_service import _version_dir, _releases_root
                        add_tree(_version_dir(row), _releases_root(), f"outputs/master/{row.id}/v{row.version}")
            if job.requested_chapter_id:
                chapter = db.get(models.Chapter, job.requested_chapter_id)
                if chapter is not None:
                    add_json("context/chapter.json", _record(chapter))
            add_tree(Path(config.UPLOAD_DIR) / str(job.id), Path(config.UPLOAD_DIR), "job-files")
            add_file(uploads.upload_file_path(job), Path(config.UPLOAD_DIR), "source/original-upload.bin", optional=True)
            # Concept files are rendered on download rather than stored on disk.
            # Reuse their existing, provider-free projection and retain full
            # immutable historical payloads above as well as both current files.
            from . import build_concepts_release as releases, build_concepts_release_files as files
            for output_lane in ("pre", "post"):
                if releases.release_payload(job, lane=output_lane) is not None:
                    add_chunks(f"outputs/concept/{output_lane}.xlsx", [
                        files.build_release_bulk_import_workbook(db, job, lane=output_lane)])
            from . import source_asset_store
            for name in sorted(asset_names):
                for path in (source_asset_store.stored_asset_path(name), source_asset_store.manifest_path(name)):
                    add_file(path, source_asset_store.store_root(), "assets/" + path.name, optional=True)
            # These are existing paid responses and receipts, not new requests.
            for attempt in (job.openai_usage or {}).get("request_attempts", []):
                if not isinstance(attempt, dict):
                    continue
                for directory, identifier in (
                    ("responses", attempt.get("request_sha256")),
                    ("receipts", hashlib.sha256(str(attempt["receipt_id"]).encode()).hexdigest()
                     if attempt.get("receipt_id") else None),
                ):
                    if isinstance(identifier, str) and re.fullmatch(r"[a-f0-9]{64}", identifier):
                        name = f"batch/{directory}/{identifier}.json"
                        if not any(row["path"] == name for row in entries):
                            add_file(Path(config.DATA_DIR) / name, Path(config.DATA_DIR) / "batch", name, optional=True)
            manifest = {"schema_version": 1, "report_id": report_id, "job_id": job.id,
                        "run_id": job.run_id, "review_kind": review_kind, "lane": lane,
                        "captured_at": receipt["occurred_at"], "entries": entries,
                        "unavailable_at_capture": missing, "evidence_is_untrusted": True}
            # Manifest does not contain itself in its entry/hash list.
            bundle.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, default=str))
        os.chmod(archive, 0o600)
        with archive.open("rb") as saved:
            os.fsync(saved.fileno())
        descriptor = {**receipt, "status": "captured_before_upload", "job_id": job.id, "run_id": job.run_id,
                      "archive_sha256": _sha(archive), "archive_size_bytes": archive.stat().st_size,
                      "evidence_file_count": len(entries), "unavailable_count": len(missing)}
        _write_json(staging / "receipt.json", descriptor)
        private = {
            "schema_version": 1, "report_id": report_id, "occurred_at": receipt["occurred_at"],
            "fingerprint": failure_reports._hash({"kind": review_kind, "lane": lane,
                "corrected": receipt["corrected_sha256"], "notes": notes}),
            "origin": "review_" + review_kind, "disposition": "reported",
            "failure_code": "generation_review_correction", "lane": lane,
            "job": {"id": job.id, "run_id": job.run_id, "chapter_id": job.requested_chapter_id,
                    "module": job.module, "execution_mode": job.execution_mode},
            "review": {"kind": review_kind, "corrected_sha256": receipt["corrected_sha256"],
                       "notes_sha256": hashlib.sha256(notes.encode()).hexdigest(),
                       "evidence_sha256": descriptor["archive_sha256"],
                       "evidence_file_count": len(entries), "evidence_bytes": descriptor["archive_size_bytes"],
                       "unavailable_count": len(missing)},
            "source": {"checkpoint_sha256": failure_reports._hash(job.generation_checkpoint),
                       "inventory_sha256": failure_reports._hash(job.question_inventory)},
        }
        failure_reports_public.project_public_report(private)
        destination = base / report_id
        with storage_capacity.reserve_review_evidence_write(1, path=base):
            staging.rename(destination)
        staging = None
        # Collector sees an explicit review submission, not a generation
        # failure. An outcome observation below distinguishes upload success.
        stamp = now.strftime("%Y%m%dT%H%M%S%fZ")
        index_path = failure_reports.root() / now.strftime("%Y-%m-%d") / f"{stamp}_{report_id}.json"
        _mkdir_private(index_path.parent)
        # Existing atomic publication flushes before returning. Reserve its
        # complete, measured metadata bytes and one temporary file inode.
        index_bytes = sum(len(chunk) for chunk in _json_chunks(private))
        with storage_capacity.reserve_review_evidence_write(index_bytes, required_inodes=1, path=index_path.parent):
            failure_reports._atomic(index_path, private)
        _sync_directory(base)
        return receipt
    except Exception as exc:
        if staging is not None:
            shutil.rmtree(staging, ignore_errors=True)
        if destination is not None:
            shutil.rmtree(destination, ignore_errors=True)
        if index_path is not None:
            try:
                index_path.unlink(missing_ok=True)
            except OSError:
                log.warning("Incomplete review report index cleanup needs reconciliation", exc_info=False)
        raise ReviewEvidenceUnavailable(
            "The error report evidence could not be saved. The corrected file has not been applied. "
            "Retry the upload with error logging, or uncheck error logging to upload the file alone."
        ) from exc


def finish(db, job, receipt, *, result=None, error=None):
    """Append an immutable outcome; never turn accepted content into failure."""
    if receipt is None:
        return None
    try:
        outcome = {"occurred_at": datetime.now(timezone.utc).isoformat(),
                   "upload_accepted": error is None, "result": result,
                   "error_type": type(error).__name__ if error is not None else None,
                   "error": str(error) if error is not None else None,
                   "review_workflow": copy.deepcopy(job.concept_review)}
        _write_json(root() / receipt["report_id"] / "upload-outcome.json", outcome)
        if error is None:
            from . import build_concepts_release as releases
            durable = copy.deepcopy(job.question_inventory or {})
            state = durable.get(releases.CONCEPT_REVIEW_KEY)
            if isinstance(state, dict):
                history = state.setdefault("review_error_reports", [])
                if not any(row.get("report_id") == receipt["report_id"] for row in history):
                    history.append(receipt)
                job.question_inventory = durable
                db.commit()
                db.refresh(job)
        return receipt
    except Exception:
        db.rollback()
        log.warning("Review report outcome needs reconciliation", exc_info=False)
        return {**receipt, "status": "attention_required",
                "message": "The corrected file was accepted and its error evidence was saved, "
                           "but the report receipt needs reconciliation. Refresh this job; do not reupload only to recreate the report."}


def evidence_for_job(job_id: int, report_id: str):
    if not isinstance(report_id, str) or not _REPORT_ID.fullmatch(report_id):
        raise FileNotFoundError("review error report not found")
    directory = root() / report_id
    _safe_path(directory, root())
    receipt_path = directory / "receipt.json"
    _safe_path(receipt_path, root())
    receipt = json.loads(receipt_path.read_text())
    if receipt.get("job_id") != int(job_id):
        raise FileNotFoundError("review error report not found")
    archive = directory / "snapshot.zip"
    _safe_path(archive, root())
    if not archive.is_file():
        raise FileNotFoundError("review error report evidence is unavailable")
    return receipt, archive


def list_for_job(job_id: int):
    reports = []
    for directory in root().glob("*"):
        if not _REPORT_ID.fullmatch(directory.name):
            continue
        try:
            receipt, _ = evidence_for_job(job_id, directory.name)
            outcome_path = directory / "upload-outcome.json"
            _safe_path(outcome_path, root())
            outcome = json.loads(outcome_path.read_text()) if outcome_path.is_file() else {}
            accepted = outcome.get("upload_accepted")
            reports.append({**receipt, "upload_accepted": accepted,
                            "status": "queued" if accepted is True else (
                                "upload_rejected" if accepted is False else "upload_unconfirmed")})
        except FileNotFoundError:
            continue
    return sorted(reports, key=lambda row: row["occurred_at"])
