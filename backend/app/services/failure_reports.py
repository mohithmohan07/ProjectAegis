"""Private, immutable failure evidence and a separately allowlisted public export.

No provider calls, database writes, git operations, source file copies or remote
uploads occur here. Capture is bounded and best effort: telemetry must never
change the outcome of generation. Paid evidence remains on the data volume.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import re
import tempfile
import traceback
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .. import config, models
from . import failure_reports_public as public

log = logging.getLogger(__name__)
MAX_PRIVATE_BYTES = 2 * 1024 * 1024
MAX_PAGE_BYTES = 8 * 1024 * 1024
_REPORT_NAME = re.compile(r"^[0-9]{8}T[0-9]{12}Z_[a-f0-9]{32}\.json$")
_SECRET_KEY = re.compile(r"(?:api[_-]?key|authorization|cookie|password|secret|access[_-]?token|refresh[_-]?token|email|owner_sub|started_by_sub)", re.I)
_SECRET_TEXT = re.compile(r"(?i)(?:bearer\s+[A-Za-z0-9._~+/=-]+|(?:api[_-]?key|authorization|password|secret|access[_-]?token|refresh[_-]?token)\s*[:=]\s*['\"]?[^\s,'\"}]+|\bsk-[A-Za-z0-9_-]{8,}|\bAIza[A-Za-z0-9_-]{20,})")
_EMAIL = re.compile(r"\b[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_URL = re.compile(r"https?://[^\s<>\"']+", re.I)
_RAW_KEYS = {"mmd_text", "raw_text", "source_text", "raw_task", "task_snippet", "question_text", "prompt", "messages", "headers", "body", "pdf", "pdf_bytes", "source_pdf", "upload_storage_key"}
_DIAGNOSTIC_KEYS = {"issues", "phase2_issues", "phase3_issues", "blocking_issues", "rich_text_repair_refusals", "missing_ids", "missing_qids", "missing_block_ids", "missing_concept_ids", "validation_errors", "defects"}
_ID_KEYS = {"qid", "block_id", "concept_id", "pre_concept_id", "type_id", "case_id", "host_id", "topic_id", "group_id", "candidate_id", "missing_ids", "missing_qids", "duplicate_qids", "missing_block_ids", "missing_concept_ids"}
_SOURCE_REPORTS = ("source.source-report.json", "source.semantic-graph-report.json", "source.semantic-graph.json")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def root() -> Path:
    return Path(config.DATA_DIR) / "failure_reports"


def _hash(value) -> str:
    # Stream checkpoint hashing; never build an extra copy of source content.
    digest = hashlib.sha256()
    for chunk in json.JSONEncoder(sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str).iterencode(value):
        digest.update(chunk.encode("utf-8"))
    return digest.hexdigest()


def _text_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _redact(text, limit=16000) -> str:
    value = str(text)
    # Environment credentials have many provider-specific shapes. Known
    # secrets are removed as exact strings before generic token scrubbing.
    for key, secret in os.environ.items():
        if _SECRET_KEY.search(key) and len(secret) >= 8:
            value = value.replace(secret, "[REDACTED]")
    value = _SECRET_TEXT.sub("[REDACTED]", value)
    value = re.sub(r"(?im)(?:set-cookie|cookie)\s*:\s*[^\r\n]+", "[COOKIE REDACTED]", value)
    value = re.sub(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b", "[TOKEN REDACTED]", value)
    value = _EMAIL.sub("[EMAIL REDACTED]", value)
    value = _URL.sub("[URL REDACTED]", value)
    return value[:limit] + (" [TRUNCATED]" if len(value) > limit else "")


def _sanitize(value, *, depth=0, budget=None):
    budget = budget if budget is not None else [6000]
    budget[0] -= 1
    if budget[0] < 0 or depth > 12:
        return "[TRUNCATED]"
    if isinstance(value, Mapping):
        return {_redact(key, 120): (
            "[EXCLUDED]" if _SECRET_KEY.search(str(key)) or str(key).lower() in _RAW_KEYS
            else _sanitize(raw, depth=depth + 1, budget=budget)
        ) for key, raw in list(value.items())[:300]}
    if isinstance(value, (list, tuple)):
        return [_sanitize(raw, depth=depth + 1, budget=budget) for raw in value[:500]]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return _redact(value)


def _walk(value, *, budget=10000):
    pending = [value]
    while pending and budget > 0:
        current = pending.pop()
        budget -= 1
        if isinstance(current, Mapping):
            yield current
            pending.extend(raw for raw in current.values() if isinstance(raw, (Mapping, list, tuple)))
        elif isinstance(current, (list, tuple)):
            pending.extend(raw for raw in current[:2000] if isinstance(raw, (Mapping, list, tuple)))


def _diagnostics(surfaces):
    findings = []
    seen = set()
    for surface in surfaces:
        for node in _walk(surface):
            entries = [node] if node.get("code") or node.get("rich_text_issue_codes") else []
            for key in _DIAGNOSTIC_KEYS:
                raw = node.get(key)
                if isinstance(raw, list):
                    entries.extend(raw[:500])
            for entry in entries:
                if len(findings) >= 500:
                    return findings
                if isinstance(entry, Mapping):
                    ids = []
                    for key in _ID_KEYS:
                        value = entry.get(key)
                        ids.extend(str(item) for item in (value if isinstance(value, list) else [value]) if item is not None)
                    codes = entry.get("rich_text_issue_codes") or entry.get("rich_text_issues") or []
                    code = entry.get("code") or entry.get("gate") or "validation_issue"
                    # Only explicit diagnostic fields; never the whole source row.
                    finding = {"code": str(code), "entity_ids": ids,
                               "details": {key: entry[key] for key in (
                                   "reason", "message", "attempt", "gate", "expected", "actual",
                                   "rich_text_issue_codes", "rich_text_issues", "missing_ids",
                                   "source_identities", "recorded_owner_rows", "rendered_rows", "before_placement_rows", "hub_rows",
                               ) if key in entry}}
                    if isinstance(codes, list):
                        finding["issue_codes"] = codes
                else:
                    finding = {"code": "validation_issue", "entity_ids": [], "details": {"message": str(entry)}}
                key = _hash(finding)
                if key not in seen:
                    seen.add(key)
                    findings.append(_sanitize(finding))
    return findings


def _artifact_diagnostics(job_id):
    evidence = []
    directory = Path(config.UPLOAD_DIR) / str(job_id) / "source-shadow"
    for filename in _SOURCE_REPORTS:
        path = directory / filename
        try:
            if path.is_symlink() or not path.resolve().is_relative_to(Path(config.UPLOAD_DIR).resolve()) or path.stat().st_size > MAX_PRIVATE_BYTES:
                continue
            raw = path.read_bytes()
            parsed = json.loads(raw)
            evidence.append({"artifact": filename, "sha256": hashlib.sha256(raw).hexdigest(),
                             "validation": _diagnostics([parsed])})
        except (OSError, ValueError, TypeError):
            continue
    return evidence


def _policies(surfaces):
    found = {}
    for surface in surfaces:
        for node in _walk(surface):
            for name in public.POLICIES:
                if name in node:
                    digest = _hash(node[name])
                    found[(name, digest)] = {"name": name, "sha256": digest,
                                            "value": _sanitize(node[name])}
                    if len(found) >= 100:
                        return list(found.values())
    return list(found.values())


def _recorded_hashes(surfaces, names):
    values = set()
    for surface in surfaces:
        for node in _walk(surface):
            for name in names:
                raw = node.get(name)
                if isinstance(raw, str) and re.fullmatch(r"[a-f0-9]{64}", raw):
                    values.add(raw)
    return sorted(values)[:100]


def _journal(job_id):
    from . import run_journal
    path = run_journal.journal_path(job_id)
    try:
        return {"path": str(path), "size_bytes_at_capture": path.stat().st_size,
                "format": "append_only_ndjson", "contains_full_stream": True}
    except OSError:
        return {"available": False}


def _usage(job):
    from . import openai_usage
    saved = job.openai_usage if job is not None and isinstance(job.openai_usage, dict) else {}
    live = openai_usage.visible_summary() if openai_usage.is_tracking() else {}
    # Upload persistence already owns merging; do not sum two cumulative ledgers.
    ledger = live if live.get("attempt_count") or live.get("request_count") else (saved or live)
    attempts = list(ledger.get("request_attempts") or [])
    paid = []
    for row in attempts[-500:]:
        if not isinstance(row, dict):
            continue
        receipt_id = row.get("receipt_id")
        if not isinstance(receipt_id, str) or not receipt_id:
            continue
        path = Path(config.DATA_DIR) / "batch" / "receipts" / f"{_text_hash(receipt_id)}.json"
        try:
            if path.is_symlink() or path.stat().st_size > 65536:
                continue
            record = json.loads(path.read_bytes())
            if record.get("receipt_id") == receipt_id:
                paid.append(_sanitize(record))
        except (OSError, ValueError, TypeError):
            continue
    totals = {key: value for key, value in ledger.items() if value is None or isinstance(value, (bool, int, float))}
    return {"ledger_sha256": _hash(ledger), "totals": totals,
            "attempts": _sanitize(attempts[-500:]),
            "omitted_attempts": max(0, len(attempts) - 500),
            "paid_receipts": paid,
            "persisted_ledger_sha256": _hash(saved),
            "live_segment_sha256": _hash(live) if live else None}


def _frames(error):
    result = []
    for frame in traceback.extract_tb(error.__traceback__):
        path = Path(frame.filename)
        try:
            relative = path.resolve().relative_to(Path(config.ROOT).resolve()).as_posix()
        except (OSError, ValueError):
            relative = "external"
        result.append({"file": relative, "line": frame.lineno, "function": frame.name})
    return result[-128:]


def _suspension(error):
    from . import batch_broker, run_control, uploads
    return isinstance(error, (run_control.RunDeferred, batch_broker.BatchPending, uploads.JobAlreadyRunningError))


def _atomic(path: Path, report: dict):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    encoded = json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    if len(encoded) > MAX_PRIVATE_BYTES:
        # Keep identity/trace/fingerprints and declare any bounded evidence loss.
        report["truncated"] = True
        report["full_capture_sha256"] = hashlib.sha256(encoded).hexdigest()
        report["validation"] = report.get("validation", [])[:100]
        report["usage"]["attempts"] = report["usage"].get("attempts", [])[-100:]
        report["usage"]["paid_receipts"] = report["usage"].get("paid_receipts", [])[-100:]
        report["error"]["traceback"] = report["error"].get("traceback", "")[:32000]
        report["error"]["attributes"] = {"sha256": _hash(report["error"].get("attributes"))}
        encoded = json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    if len(encoded) > MAX_PRIVATE_BYTES:
        # Preserve a usable incident even for pathological diagnostic arrays.
        # Every omitted surface keeps its hash; this never claims completeness.
        report["job"]["run_state"] = {"sha256": _hash(report["job"].get("run_state"))}
        report["artifact_evidence"] = [{"artifact": row.get("artifact"), "sha256": row.get("sha256")}
                                       for row in report.get("artifact_evidence", [])]
        report["policies"] = [{"name": row["name"], "sha256": row["sha256"]}
                              for row in report.get("policies", [])]
        report["validation"] = [{"code": _redact(row.get("code", ""), 128),
                                 "entity_ids": [_redact(value, 128) for value in row.get("entity_ids", [])[:100]],
                                 "details": {"sha256": _hash(row)}}
                                for row in report.get("validation", [])[:20]]
        prior = report["usage"].get("attempts", [])
        report["usage"]["omitted_attempts"] += max(0, len(prior) - 20)
        keys = {"attempt_id", "request_id", "response_id", "batch_id", "wave_id", "request_sha256", "receipt_id", "provider", "model", "actual_model", "requested_model", "delivery_mode", "reused", "usage_reported", "estimated_cost_usd"}
        report["usage"]["attempts"] = [{key: (_redact(value, 256) if isinstance(value, str) else value)
                                           for key, value in row.items() if key in keys}
                                          for row in prior[-20:] if isinstance(row, dict)]
        report["usage"]["paid_receipts"] = []
        encoded = json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    if len(encoded) > MAX_PRIVATE_BYTES:
        raise ValueError("failure identity exceeds private capture bound")
    fd, temporary = tempfile.mkstemp(prefix=".capture-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(encoded)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def record_failure(db, job_id: int | None, error: BaseException | None, *,
                   task=None, disposition="failed", failure_code="", lane="",
                   origin="unknown", message="", historical_events=None) -> str | None:
    """Capture one immutable observation; never commit, rollback or raise."""
    try:
        if error is not None and _suspension(error):
            return None
        if disposition not in public.DISPOSITIONS:
            return None
        from . import progress
        job = None
        snapshot_unavailable = False
        try:
            if db is not None and job_id:
                with db.no_autoflush:
                    job = db.get(models.UploadJob, int(job_id))
        except Exception:
            # A database failure is itself valuable evidence. Save the actual
            # exception/receipt context even when its job cannot be loaded.
            snapshot_unavailable = True
        checkpoint = job.generation_checkpoint if job is not None else {}
        inventory = job.question_inventory if job is not None else {}
        attributes = {key: getattr(error, key) for key in (
            "failure_code", "reason_code", "evidence_identity", "request_id", "batch_id",
            "wave_id", "request_sha256", "status_code", "diagnostics", "details", "issues",
            "missing_ids", "missing_qids", "missing_block_ids", "missing_concept_ids",
            "coverage_diagnostics", "validation_diagnostics",
        ) if error is not None and hasattr(error, key)}
        if historical_events is not None:
            attributes["saved_error_events"] = _sanitize(historical_events)
        reason = str(error) if error is not None else str(message)
        trace = "".join(traceback.format_exception(error)) if error is not None else ""
        frames = _frames(error) if error is not None else []
        now = datetime.now(timezone.utc)
        report_id = uuid4().hex
        code = str(attributes.get("failure_code") or failure_code or "")
        fingerprint = getattr(error, "_aegis_failure_fingerprint", None) if error else None
        fingerprint = fingerprint or _hash({"type": type(error).__name__ if error else "QueueOutcome",
                                            "message": reason, "code": code,
                                            "frames": frames[-6:]})
        artifacts = _artifact_diagnostics(job_id) if job_id else []
        validation = _diagnostics([checkpoint, inventory, attributes])
        validation.extend(row for artifact in artifacts for row in artifact["validation"])
        task_fields = {key: getattr(task, key, None) for key in (
            "id", "job_id", "batch_row_id", "kind", "state", "attempt", "max_attempts",
            "push_group_id", "cohort_id", "lease_owner", "lease_expires_at", "failure_code", "blocked_kind",
        )} if task is not None else {}
        if isinstance(task_fields.get("lease_expires_at"), datetime):
            task_fields["lease_expires_at"] = task_fields["lease_expires_at"].isoformat()
        report = {
            "schema_version": 1, "report_id": report_id,
            "occurred_at": now.isoformat().replace("+00:00", "Z"),
            "fingerprint": fingerprint, "origin": origin, "disposition": disposition,
            "related_report_id": getattr(error, "_aegis_failure_report_id", None) if error else None,
            "failure_code": _redact(code), "lane": _redact(lane or progress.current_lane() or ""),
            "stage": _redact(progress.current_stage() or ((job.run_state or {}).get("stage") if job else "") or ""),
            "job": {"id": job_id, "run_id": _redact(job.run_id), "module": job.module,
                    "execution_mode": job.execution_mode, "status": job.status,
                    "chapter_id": job.requested_chapter_id or (checkpoint or {}).get("target_chapter_id"),
                    "checkpoint_stage": _redact((checkpoint or {}).get("stage") or ""),
                    "run_state": _sanitize(job.run_state)} if job is not None else {"id": job_id},
            "source": {"mmd_sha256": _text_hash(job.mmd_text or ""),
                       "filename_sha256": _text_hash(job.filename or ""),
                       "checkpoint_sha256": _hash(checkpoint), "inventory_sha256": _hash(inventory),
                       "recorded_pdf_sha256s": _recorded_hashes([checkpoint, inventory, attributes], {"pdf_sha256", "source_pdf_sha256", "original_file_sha256", "expected_pdf_sha256", "actual_pdf_sha256"}),
                       "page_evidence_sha256s": _recorded_hashes([checkpoint, inventory, attributes], {"page_evidence_sha256"})} if job is not None else {},
            "error": {"type": f"{type(error).__module__}.{type(error).__name__}" if error else ("SavedFailure" if origin == "historical" else "QueueOutcome"),
                      "message": _redact(reason, 32000), "message_sha256": _text_hash(reason),
                      "traceback": _redact(trace, 96000), "traceback_sha256": _text_hash(trace),
                      "frames": frames, "attributes": _sanitize(attributes)},
            "task": _sanitize(task_fields), "policies": _policies([checkpoint, inventory]),
            "validation": validation[:500], "artifact_evidence": artifacts,
            "private_journal": _journal(job_id) if job_id else {},
            "usage": _usage(job), "truncated": len(validation) > 500 or len(trace) > 96000 or len(reason) > 32000,
            "job_snapshot_unavailable": snapshot_unavailable,
        }
        # Stage, errors and private paths are not trusted to cross the boundary.
        public.project_public_report(report)
        stamp = now.strftime("%Y%m%dT%H%M%S%fZ")
        path = root() / now.strftime("%Y-%m-%d") / f"{stamp}_{report_id}.json"
        _atomic(path, report)
        if error is not None:
            error._aegis_failure_fingerprint = fingerprint
            error._aegis_failure_report_id = report_id
        return report_id
    except Exception:
        # No exception text here: it might itself contain sensitive evidence.
        log.warning("Structured failure evidence could not be saved", exc_info=False)
        return None


def record_failure_for_job(job_id, error, **kwargs):
    """Fallback for stream/conversion code without an existing DB session."""
    try:
        from ..db import SessionLocal
        with SessionLocal() as db:
            return record_failure(db, job_id, error, **kwargs)
    except Exception:
        return record_failure(None, job_id, error, **kwargs)


def _cursor(path: str) -> str:
    return base64.urlsafe_b64encode(path.encode()).decode().rstrip("=")


def _after(cursor: str | None) -> str:
    if not cursor:
        return ""
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", cursor):
        raise ValueError("invalid failure report cursor")
    value = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode()
    parts = value.split("/")
    if len(parts) != 2 or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", parts[0]) or not _REPORT_NAME.fullmatch(parts[1]):
        raise ValueError("invalid failure report cursor")
    return value


def export_public_page(*, cursor=None, limit=200, max_bytes=MAX_PAGE_BYTES) -> dict:
    """Read-only deterministic pagination. Never expose raw files or messages."""
    if not 1 <= int(limit) <= 10000 or not 1024 <= int(max_bytes) <= MAX_PAGE_BYTES:
        raise ValueError("invalid failure export bound")
    after = _after(cursor)
    base = root()
    paths = sorted(path for path in base.glob("[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]/*.json")
                   if not path.is_symlink() and not path.parent.is_symlink()
                   and path.resolve().is_relative_to(base.resolve()) and _REPORT_NAME.fullmatch(path.name))
    remaining = [path for path in paths if path.relative_to(base).as_posix() > after]
    reports = []
    size = 0
    consumed = 0
    last = after
    for path in remaining:
        if len(reports) >= int(limit):
            break
        if path.stat().st_size > MAX_PRIVATE_BYTES:
            raise ValueError("private report exceeds capture bound")
        # Invalid evidence fails the collection visibly rather than silently
        # skipping an incident or handing a raw dictionary to a public repo.
        private = json.loads(path.read_bytes())
        report = public.project_public_report(private)
        encoded = json.dumps(report, separators=(",", ":")).encode()
        if size + len(encoded) > max_bytes:
            if not reports:
                raise ValueError("one public report exceeds page bound")
            break
        size += len(encoded)
        reports.append(report)
        consumed += 1
        last = path.relative_to(base).as_posix()
    envelope = {"schema_version": 1, "exported_at": _now(), "reports": reports,
                "next_cursor": _cursor(last) if consumed < len(remaining) else None,
                "total_count": len(paths)}
    public.validate_public_envelope(envelope)
    return envelope


def backfill_existing(db) -> dict:
    """Explicit one-shot capture of saved failed jobs; never alter their state."""
    from . import build_concepts_release
    captured = skipped = unavailable = 0
    for job in db.query(models.UploadJob).order_by(models.UploadJob.id).yield_per(1):
        clock = job.run_state if isinstance(job.run_state, dict) else {}
        review = build_concepts_release.concept_review_state(job)
        if not (job.status == "failed" or clock.get("status") == "failed"
                or review.get("status") == build_concepts_release.CONCEPT_REVIEW_MASTER_FAILED):
            continue
        errors = [row for row in (job.generation_log or []) if isinstance(row, dict)
                  and (row.get("type") == "error" or row.get("level") == "error")]
        identity = _hash({"job_id": job.id, "run_id": job.run_id,
                          "errors": errors, "detail": job.detail, "status": clock.get("status")})
        marker = root() / "backfill_index" / f"{identity}.json"
        if marker.is_file():
            skipped += 1
            continue
        message = str((errors[-1] if errors else {}).get("message") or job.detail or "Saved failed run")
        report_id = record_failure(db, job.id, None, origin="historical",
                                   failure_code="generation_historical_failure",
                                   message=message, historical_events=errors)
        if report_id is None:
            unavailable += 1
            continue
        _atomic(marker, {"job_id": job.id, "saved_failure_sha256": identity, "report_id": report_id})
        captured += 1
    return {"captured": captured, "already_captured": skipped, "unavailable": unavailable}
