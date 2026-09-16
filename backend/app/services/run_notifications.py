"""Email stage outcomes to the authenticated person who started the run.

Outbox rows commit with queue outcomes. SMTP runs on a separate worker and
cannot hold a generation lease or turn a finished chapter into a failure.
No mailbox is inferred from a name, uploader or mutable last-actor field.
"""
from __future__ import annotations

import logging
import os
import smtplib
import ssl
import threading
from datetime import datetime, timedelta
from email.message import EmailMessage

from sqlalchemy import update

from .. import config, models

log = logging.getLogger(__name__)
_stop = threading.Event()
_thread: threading.Thread | None = None


def configuration_status() -> dict:
    required = ("AEGIS_SMTP_HOST", "AEGIS_NOTIFICATION_FROM")
    missing = [name for name in required if not os.environ.get(name, "").strip()]
    if os.environ.get("AEGIS_SMTP_USER") and not os.environ.get("AEGIS_SMTP_PASSWORD"):
        missing.append("AEGIS_SMTP_PASSWORD")
    return {
        "configured": not missing,
        "status": "ready" if not missing else "sender_not_configured",
        "missing_settings": missing,
    }


def _recipient(job, task) -> str:
    value = str(getattr(job, "started_by_email", "") or "").strip()
    # Address syntax/transport safety only, not identity inference. Principals
    # entering the queue have already passed Google's email_verified check.
    if any(c in value for c in "\r\n") or value.count("@") != 1:
        return ""
    local, domain = value.rsplit("@", 1)
    return value if local and "." in domain and domain != "localhost" else ""


def queue_task_result(db, task, *, state: str, error: str = "") -> None:
    if state not in {"done", "failed"}:
        return
    row = db.get(models.ChapterBatchRow, int(task.batch_row_id))
    job_id = int(getattr(task, "job_id", 0) or (row.job_id if row else 0) or 0)
    job = db.get(models.UploadJob, job_id) if job_id else None
    if job is None:
        return
    recipient = _recipient(job, task)
    if not recipient:
        return
    key = f"chapter-task:{task.id}:attempt:{task.attempt}:{state}"
    if db.query(models.RunNotification.id).filter_by(event_key=key).first():
        return
    chapter = db.get(models.Chapter, row.chapter_id) if row else None
    title = str(getattr(chapter, "chapter_display_name", "") or
                getattr(chapter, "chapter_title", "") or job.filename)
    stage = {"step01": "Concept files", "step02": "Master files", "publish": "Publication"}.get(task.kind, task.kind)
    outcome = "ready for review" if state == "done" and task.kind != "publish" else (
        "completed" if state == "done" else "failed")
    url = config.PUBLIC_BASE_URL.rstrip("/")
    lines = [f"{title}: {stage} {outcome}.", "", f"Run: {job.run_id or job.id}"]
    if state == "failed":
        # Avoid emailing raw provider exception payloads or document content.
        lines.append("Open Aegis for the recorded reason, saved work and recovery action.")
    elif task.kind == "step01":
        lines.append("Review the Concept files, upload your corrections, then select the chapter for Step 02.")
    elif task.kind == "step02":
        lines.append("Review the Master files before publication.")
    if url:
        lines.extend(["", f"Open Aegis: {url}/chapters"])
    db.add(models.RunNotification(
        event_key=key, job_id=job_id, task_id=int(task.id), recipient=recipient,
        subject=f"Aegis: {title} — {stage} {outcome}", body="\n".join(lines),
        status="pending", attempts=0, available_at=datetime.utcnow(),
    ))


def queue_direct_result(db, job, *, failed: bool = False) -> None:
    """Cover the explicitly selected legacy synchronous workflow as well."""
    if not job.started_by_email or not job.started_by_sub:
        return
    # A durable task settles its own outcome in the same transaction. Do not
    # send a second email from an inner usage wrapper or source-conversion step.
    live = db.query(models.ChapterBatchTask.id).filter(
        models.ChapterBatchTask.job_id == job.id,
        models.ChapterBatchTask.state.in_(models.CHAPTER_BATCH_LIVE_TASK_STATES),
    ).first()
    if live:
        return
    from types import SimpleNamespace
    if not _recipient(job, SimpleNamespace(enqueued_by_email="")):
        return
    from . import build_concepts_release
    review = build_concepts_release.concept_review_state(job)
    status = str(review.get("status") or "")
    if not failed and status not in {
        build_concepts_release.CONCEPT_REVIEW_PENDING,
        build_concepts_release.CONCEPT_REVIEW_MASTER_READY,
        build_concepts_release.CONCEPT_REVIEW_PUBLISHED,
    }:
        return
    stage = "Master files" if status.startswith("master") else "Concept files"
    outcome = "failed" if failed else "ready for review"
    if status == build_concepts_release.CONCEPT_REVIEW_PUBLISHED and not failed:
        stage, outcome = "Publication", "completed"
    key = f"direct:{job.id}:{job.run_id}:{stage}:{outcome}"
    if db.query(models.RunNotification.id).filter_by(event_key=key).first():
        return
    db.add(models.RunNotification(
        event_key=key, job_id=job.id, recipient=job.started_by_email,
        subject=f"Aegis: {job.filename} — {stage} {outcome}",
        body=f"{job.filename}: {stage} {outcome}.\n\nOpen Aegis for saved files and next steps: {config.PUBLIC_BASE_URL.rstrip('/')}/build-concepts?job={job.id}",
    ))


def summary(db, *, job_ids: list[int] | None = None) -> dict:
    query = db.query(models.RunNotification)
    if job_ids is not None:
        query = query.filter(models.RunNotification.job_id.in_(job_ids))
    counts: dict[str, int] = {}
    for (state,) in query.with_entities(models.RunNotification.status).all():
        counts[state] = counts.get(state, 0) + 1
    return {**configuration_status(), "counts": counts}


class DeliveryNotStarted(RuntimeError):
    """Connection/authentication failed before the message could be accepted."""


def _send(item) -> None:
    host = os.environ["AEGIS_SMTP_HOST"].strip()
    mode = os.environ.get("AEGIS_SMTP_SECURITY", "starttls").strip().lower()
    if mode not in {"starttls", "ssl"}:
        raise ValueError("AEGIS_SMTP_SECURITY must be starttls or ssl")
    port = int(os.environ.get("AEGIS_SMTP_PORT", "465" if mode == "ssl" else "587"))
    sender = os.environ["AEGIS_NOTIFICATION_FROM"].strip()
    message = EmailMessage()
    message["From"] = sender
    message["To"] = item.recipient
    message["Subject"] = item.subject
    message["Message-ID"] = f"<aegis-notification-{item.id}@{sender.rsplit('@', 1)[-1]}>"
    message.set_content(item.body)
    factory = smtplib.SMTP_SSL if mode == "ssl" else smtplib.SMTP
    kwargs = {"timeout": 20}
    if mode == "ssl":
        kwargs["context"] = ssl.create_default_context()
    client = None
    try:
        client = factory(host, port, **kwargs)
        if mode == "starttls":
            client.starttls(context=ssl.create_default_context())
        username = os.environ.get("AEGIS_SMTP_USER", "")
        if username:
            client.login(username, os.environ["AEGIS_SMTP_PASSWORD"])
    except smtplib.SMTPAuthenticationError:
        if client is not None:
            client.close()
        raise
    except Exception as exc:
        if client is not None:
            client.close()
        raise DeliveryNotStarted(type(exc).__name__) from exc
    try:
        client.send_message(message)
    finally:
        # QUIT failure after successful DATA must not undo a sent receipt.
        client.close()


def deliver_pending(session_factory, *, send=None, limit: int = 20) -> int:
    if not configuration_status()["configured"]:
        return 0
    deliver = send or _send
    delivered = 0
    db = session_factory()
    try:
        ids = [value for (value,) in db.query(models.RunNotification.id).filter(
            models.RunNotification.status == "pending",
            models.RunNotification.available_at <= datetime.utcnow(),
        ).order_by(models.RunNotification.id).limit(limit).all()]
        for item_id in ids:
            changed = db.execute(update(models.RunNotification).where(
                models.RunNotification.id == item_id,
                models.RunNotification.status == "pending",
            ).values(status="sending", attempts=models.RunNotification.attempts + 1))
            db.commit()
            if changed.rowcount != 1:
                continue
            item = db.get(models.RunNotification, item_id)
            try:
                deliver(item)
            except DeliveryNotStarted as exc:
                item.status = "pending"
                item.last_error = str(exc)
                item.available_at = datetime.utcnow() + timedelta(
                    seconds=min(3600, 30 * 2 ** min(item.attempts, 7)))
            except (smtplib.SMTPAuthenticationError, smtplib.SMTPRecipientsRefused,
                    smtplib.SMTPSenderRefused) as exc:
                item.status = "failed"
                item.last_error = type(exc).__name__
            except Exception as exc:
                # SMTP cannot prove whether a disconnected DATA request was
                # accepted. Do not automatically resend and spam the starter.
                item.status = "delivery_unknown"
                item.last_error = type(exc).__name__
            else:
                item.status = "sent"
                item.sent_at = datetime.utcnow()
                item.last_error = ""
                delivered += 1
            db.commit()
    finally:
        db.close()
    return delivered


def start(session_factory) -> None:
    global _thread
    if os.environ.get("AEGIS_NOTIFICATION_WORKER", "1").lower() in {"0", "false", "off", "no"}:
        return
    if _thread is not None and _thread.is_alive():
        return
    # A process can stop after SMTP acceptance but before the sent commit.
    db = session_factory()
    try:
        db.query(models.RunNotification).filter_by(status="sending").update(
            {"status": "delivery_unknown", "last_error": "delivery interrupted by restart"})
        db.commit()
    finally:
        db.close()
    _stop.clear()
    def work():
        while not _stop.is_set():
            try:
                deliver_pending(session_factory)
            except Exception:
                log.warning("notification outbox pass failed", exc_info=True)
            _stop.wait(10)
    _thread = threading.Thread(target=work, name="run-notifications", daemon=True)
    _thread.start()


def stop() -> None:
    _stop.set()
    if _thread is not None:
        _thread.join(timeout=2)
