"""Read-only, job-based run accounting for the shared chapter worklist.

The cumulative job ledger is counted once, including superseded source jobs.
Queue attempts are not invoices. Historical receipts without a delivery-mode
stamp remain unclassified; a selected cohort alone is no proof of a discount.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .. import models
from . import chapter_batches, chapter_queue_worker, directory, uploads, run_notifications, batch_broker, openai_usage


def _amount(value: Any) -> Decimal | None:
    try:
        result = Decimal(str(value))
        return result if result.is_finite() and result >= 0 else None
    except (InvalidOperation, TypeError, ValueError):
        return None


def _count(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError, OverflowError):
        return 0


def cost_view(usage: dict | None, *, started: bool = False) -> dict:
    """Keep known spend, unpriced/pending usage and delivery evidence distinct."""
    usage = usage if isinstance(usage, dict) else {}
    known = _amount(usage.get("known_usage_estimated_cost_usd"))
    if known is None:
        known = _amount(usage.get("estimated_cost_usd")) or Decimal(0)
    pending = _count(usage.get("pending_request_count"))
    unresolved = _count(usage.get("unresolved_usage_request_count"))
    complete = not (pending or unresolved or usage.get("usage_complete") is False
                    or usage.get("pricing_complete") is False
                    or (started and _amount(usage.get("estimated_cost_usd")) is None))
    split = {"batch": Decimal(0), "synchronous": Decimal(0)}
    counts: Counter = Counter()
    reused = 0
    # Attempt ids are stable through cumulative merges and checkpoint restore.
    seen: set[str] = set()
    for row in usage.get("request_attempts") or []:
        if not isinstance(row, dict):
            continue
        identity = str(row.get("attempt_id") or "")
        if identity and identity in seen:
            continue
        if identity:
            seen.add(identity)
        mode = str(row.get("delivery_mode") or "unknown")
        if row.get("reused"):
            reused += 1
            continue
        amount = _amount(row.get("estimated_cost_usd"))
        if amount is not None:
            counts[mode if mode in split else "unknown"] += 1
            if mode in split:
                split[mode] += amount
    classified = sum(split.values(), Decimal(0))
    # A malformed legacy breakdown cannot inflate or reduce the job's ledger.
    if classified > known:
        split = {key: Decimal(0) for key in split}
        classified = Decimal(0)
    inr = _amount(usage.get("known_usage_estimated_cost_inr"))
    if inr is None:
        inr = _amount(usage.get("estimated_cost_inr"))
    return {
        "known_cost_usd": float(known),
        "known_cost_inr": float(inr) if inr is not None else None,
        "cost_complete": complete,
        "batch_cost_usd": float(split["batch"]),
        "synchronous_cost_usd": float(split["synchronous"]),
        "unclassified_cost_usd": float(known - classified),
        "batch_requests": counts["batch"],
        "synchronous_requests": counts["synchronous"],
        "reused_responses": reused,
        "pending_requests": pending,
        "unresolved_requests": unresolved,
        "request_count": _count(usage.get("request_count")),
        "recovered_batch_receipts": _count(usage.get("recovered_batch_receipts")),
        "unreconciled_batch_receipts": _count(usage.get("unreconciled_batch_receipts")),
        "unreconciled_batch_cost_usd": float(_amount(usage.get("unreconciled_batch_cost_usd")) or 0),
    }


def billed_receipt_ids(usage: dict | None) -> set[str]:
    """Transport identity alone is not evidence that the job booked a charge."""
    usage = usage if isinstance(usage, dict) else {}
    result = {str(value) for value in usage.get("billed_receipt_ids") or [] if value}
    result.update(str(row["receipt_id"]) for row in usage.get("request_attempts") or []
                  if isinstance(row, dict) and row.get("receipt_id") and not row.get("reused")
                  and (row.get("usage_status") in {"reported", "missing", "incomplete"}
                       or row.get("usage_reported") or _amount(row.get("estimated_cost_usd")) is not None))
    return result


def reconcile_paid_receipts(usage: dict | None, receipts: list[dict], *,
                            globally_billed_ids: set[str] | None = None) -> dict:
    """Union durable paid responses with the job journal by immutable identity.

    Provider receipts survive a crash between harvest and job accounting. A
    historical ledger without receipt identities cannot safely be added twice;
    its overlap is named as unreconciled instead of guessed away.
    """
    paid_ids = {str(row.get("receipt_id")) for row in receipts if row.get("receipt_id")}
    result = dict(openai_usage.settle_batch_pending(usage or {}, paid_ids | (globally_billed_ids or set())))
    attempts = [dict(row) for row in result.get("request_attempts") or [] if isinstance(row, dict)]
    local_ids = billed_receipt_ids(result)
    known_ids = local_ids | (globally_billed_ids or set())
    current = _amount(result.get("known_usage_estimated_cost_usd"))
    if current is None:
        current = _amount(result.get("estimated_cost_usd")) or Decimal(0)
    # Prove how much of the frozen total has a synchronous receipt or an
    # identified batch receipt. A newer identified charge must not erase an
    # older unidentified one in the same cumulative job. Compact snapshots can
    # use their billed ids plus the corresponding frozen provider prices.
    identified = Decimal(0)
    counted_attempts: set[str] = set()
    priced_batch_ids: set[str] = set()
    for row in attempts:
        identity = str(row.get("attempt_id") or "")
        if row.get("reused") or identity and identity in counted_attempts:
            continue
        if identity:
            counted_attempts.add(identity)
        amount = _amount(row.get("estimated_cost_usd"))
        receipt_id = str(row.get("receipt_id") or "")
        mode = row.get("delivery_mode")
        if amount is not None and (mode == "synchronous" or mode == "batch" and receipt_id in local_ids):
            if mode != "batch" or receipt_id not in priced_batch_ids:
                identified += amount
            if mode == "batch":
                priced_batch_ids.add(receipt_id)
    for receipt in receipts:
        receipt_id = str(receipt.get("receipt_id") or "")
        if receipt_id in local_ids and receipt_id not in priced_batch_ids:
            identified += _amount(openai_usage.estimate_batch_receipt(receipt).get("estimated_cost_usd")) or Decimal(0)
            priced_batch_ids.add(receipt_id)
    ambiguous = current > identified + Decimal("0.000000001")
    recovered = 0
    unresolved = 0
    uncertain_cost = Decimal(0)
    for receipt in receipts:
        receipt_id = str(receipt.get("receipt_id") or "")
        if not receipt_id or receipt_id in known_ids:
            continue
        known_ids.add(receipt_id)
        priced = openai_usage.estimate_batch_receipt(receipt)
        amount = _amount(priced.get("estimated_cost_usd"))
        if ambiguous:
            unresolved += 1
            uncertain_cost += amount or Decimal(0)
            continue
        recovered += 1
        empty_ledger = not (current or result.get("request_count") or result.get("unresolved_usage_request_count"))
        current += amount or Decimal(0)
        result["request_count"] = _count(result.get("request_count")) + 1
        old_total = _amount(result.get("estimated_cost_usd"))
        if old_total is None and empty_ledger:
            old_total = Decimal(0)
        result["estimated_cost_usd"] = float(old_total + amount) if old_total is not None and amount is not None else None
        old_inr = _amount(result.get("known_usage_estimated_cost_inr"))
        if old_inr is None:
            old_inr = _amount(result.get("estimated_cost_inr"))
        if old_inr is None and empty_ledger:
            old_inr = Decimal(0)
        new_inr = _amount(priced.get("estimated_cost_inr"))
        result["known_usage_estimated_cost_inr"] = float(old_inr + new_inr) if old_inr is not None and new_inr is not None else None
        result["estimated_cost_inr"] = None
        if amount is None:
            result["pricing_complete"] = False
        if priced.get("usage_complete") is False:
            result["usage_complete"] = False
            result["unresolved_usage_request_count"] = _count(result.get("unresolved_usage_request_count")) + 1
        attempts.append({"attempt_id": f"durable:{receipt_id}", "receipt_id": receipt_id,
                         "delivery_mode": "batch", "reused": False,
                         "usage_status": "reported" if priced.get("usage_complete") is not False else "incomplete",
                         "estimated_cost_usd": float(amount) if amount is not None else None})
    if unresolved:
        result["usage_complete"] = False
    result["known_usage_estimated_cost_usd"] = float(current)
    result["request_attempts"] = attempts
    result["recovered_batch_receipts"] = recovered
    result["unreconciled_batch_receipts"] = unresolved
    result["unreconciled_batch_cost_usd"] = float(uncertain_cost)
    return result


def aggregate_cost(rows: list[dict]) -> dict:
    fields = ("known_cost_usd", "batch_cost_usd", "synchronous_cost_usd", "unclassified_cost_usd", "unreconciled_batch_cost_usd")
    result = {field: float(sum((Decimal(str(row[field])) for row in rows), Decimal(0))) for field in fields}
    for field in ("request_count", "batch_requests", "synchronous_requests", "reused_responses", "pending_requests", "unresolved_requests", "recovered_batch_receipts", "unreconciled_batch_receipts"):
        result[field] = sum(row[field] for row in rows)
    inr = [row["known_cost_inr"] for row in rows]
    result["known_cost_inr"] = float(sum((Decimal(str(value)) for value in inr), Decimal(0))) if all(value is not None for value in inr) else None
    result["cost_complete"] = all(row["cost_complete"] for row in rows)
    result["incomplete_runs"] = sum(not row["cost_complete"] for row in rows)
    return result


def _task_job_id(task, board_row) -> int | None:
    frozen = getattr(task, "job_id", None)
    if frozen:
        return int(frozen)
    # Old tasks have no job stamp. A row with replacement history is ambiguous.
    if board_row and not board_row.previous_job_ids and board_row.job_id:
        return int(board_row.job_id)
    return None


def snapshot(db: Session, *, owner_sub: str, board: str = "", grade: str = "",
             subject: str = "", q: str = "", state: str = "", initiator: str = "",
             page: int = 1, page_size: int = 25) -> dict:
    bindings = db.query(models.ChapterBatchRow).all()
    by_binding = {row.id: row for row in bindings}
    chapter_ids = {row.chapter_id for row in bindings}
    chapters = {row.id: row for row in db.query(models.Chapter).filter(models.Chapter.id.in_(chapter_ids)).all()}
    job_chapters: dict[int, tuple[Any, Any]] = {}
    for row in bindings:
        chapter = chapters.get(row.chapter_id)
        if chapter:
            for job_id in [*(row.previous_job_ids or []), row.job_id]:
                if job_id:
                    job_chapters[int(job_id)] = (chapter, row)
    # The same team boundary as Chapters. Private standalone jobs belong only
    # to the signed-in owner; being able to view the dashboard never widens it.
    query = select(models.UploadJob.id, models.UploadJob.filename, models.UploadJob.owner_sub,
                   models.UploadJob.created_at, models.UploadJob.openai_usage, models.UploadJob.started_by_email, models.UploadJob.execution_mode, models.UploadJob.deposit_scope_type, models.UploadJob.deposit_scope_ids).where(
        models.UploadJob.module == "build_concepts",
        or_(models.UploadJob.id.in_(job_chapters), models.UploadJob.owner_sub == owner_sub))
    jobs = db.execute(query).mappings().all()
    # A shared batch waiter can consume a receipt under another visible job.
    # Reconcile against the whole visible journal before adding its original
    # owner's provider receipt; filtered pages must not count it a second time.
    globally_billed_ids: set[str] = set()
    for job in jobs:
        globally_billed_ids.update(billed_receipt_ids(job["openai_usage"]))
    standalone_chapters = {int(job["deposit_scope_ids"][0]) for job in jobs
                           if job["deposit_scope_type"] == "chapter" and len(job["deposit_scope_ids"] or []) == 1}
    chapters.update({row.id: row for row in db.query(models.Chapter).filter(models.Chapter.id.in_(standalone_chapters)).all()})
    paid_by_job = defaultdict(list)
    visible_job_ids = {job["id"] for job in jobs}
    for receipt in batch_broker.BatchStore().paid_receipts():
        if receipt.get("owner_job_id") in visible_job_ids:
            paid_by_job[receipt["owner_job_id"]].append(receipt)
    signals = chapter_batches.job_signals(db, [job["id"] for job in jobs])
    tasks_by_job: dict[int, list] = defaultdict(list)
    for task in db.query(models.ChapterBatchTask).order_by(models.ChapterBatchTask.id).all():
        job_id = _task_job_id(task, by_binding.get(task.batch_row_id))
        if job_id in signals:
            tasks_by_job[job_id].append(task)
    notices = {}
    for notice in db.query(models.RunNotification).filter(models.RunNotification.job_id.in_(signals)).order_by(models.RunNotification.id).all():
        notices[notice.job_id] = {"state": notice.status, "recipient": notice.recipient, "error": notice.last_error}
    records = []
    for job in jobs:
        job_id = int(job["id"])
        signal = signals[job_id]
        chapter, binding = job_chapters.get(job_id, (None, None))
        if chapter is None and job["deposit_scope_type"] == "chapter" and len(job["deposit_scope_ids"] or []) == 1:
            chapter = chapters.get(int(job["deposit_scope_ids"][0]))
        tasks = tasks_by_job.get(job_id, [])
        task = next((row for row in reversed(tasks) if row.state in models.CHAPTER_BATCH_LIVE_TASK_STATES), tasks[-1] if tasks else None)
        verdict = chapter_batches.derive_state(signal, task, process_running=uploads.is_job_running(job_id))
        run_state = signal.get("run_state") or {}
        recovery = signal.get("recovery_request") or {}
        # Non-console runs have no queue lease; their durable state is the
        # authority for historical failures, while the process lock is live.
        if task is None and verdict["state"] == "source_staged":
            if uploads.is_job_running(job_id):
                verdict["state"] = "step01_running"
            elif run_state.get("status") in {"failed", "interrupted", "recovering"}:
                verdict["state"] = "recovering" if run_state["status"] in {"interrupted", "recovering"} else "failed"
            elif recovery.get("status") == "blocked":
                verdict["state"] = "blocked"
            elif recovery.get("status") == "queued":
                verdict["state"] = "recovering"
        usage = job["openai_usage"] if isinstance(job["openai_usage"], dict) else {}
        usage = reconcile_paid_receipts(usage, paid_by_job[job_id], globally_billed_ids=globally_billed_ids)
        globally_billed_ids.update(billed_receipt_ids(usage))
        started = bool(signal.get("run_id") or usage.get("request_count") or usage.get("attempt_count")
                       or any(row.started_at for row in tasks) or signal.get("marker")
                       or signal.get("status") in {"converted", "generated", "released", "concept_review"})
        # An uploader is not necessarily the person who pressed Start.
        starter = str(job["started_by_email"] or run_state.get("started_by_email") or "")
        if not starter:
            starter = next((row.enqueued_by_email for row in tasks if row.kind in {"step01", "step02"} and row.enqueued_by_email), "")
        saved_stage = str(run_state.get("stage") or signal.get("saved_checkpoint_stage") or "")
        saved_progress = run_state.get("progress") or signal.get("saved_checkpoint_progress")
        item = {
            "job_id": job_id, "chapter_id": chapter.id if chapter else None,
            "chapter_title": (chapter.chapter_display_name or chapter.chapter_title) if chapter else job["filename"],
            "chapter_code": chapter.chapter_code if chapter else "",
            "board": chapter.board if chapter else "", "grade": chapter.grade if chapter else "",
            "subject": directory.effective_subject_for_tags(chapter.board, chapter.subject) if chapter else "",
            "catalogue_active": bool(getattr(chapter, "catalogue_active", True)) if chapter else None,
            "historical_source": bool(binding and binding.job_id != job_id),
            "filename": job["filename"], "started": started,
            "created_at": chapter_batches._iso(job["created_at"]),
            "state": verdict["state"], "state_label": chapter_batches._STATE_LABELS.get(verdict["state"], verdict["state"]),
            "stage": str(run_state.get("stage") or saved_stage),
            "saved_stage": saved_stage, "saved_progress": saved_progress,
            "recovery": recovery,
            "initiator_email": starter,
            "notification": notices.get(job_id),
            "requested_mode": job["execution_mode"] if job["execution_mode"] in {"batch", "synchronous"} else "batch" if task and task.cohort_id else "synchronous" if task else "unknown",
            "cohort_id": task.cohort_id if task else "",
            "error": verdict["error_message"] or verdict["blocked_reason"] or str(recovery.get("reason") or ""),
            **cost_view(usage, started=started),
        }
        lanes = chapter_batches._lane_views(signal)
        available = [lane for lane in lanes if lane["available"]]
        item["concepts_complete"] = bool(signal.get("marker") and signal["marker"].get("status") not in {"", "failed"})
        item["masters_complete"] = bool(available and all(lane["master"] in {"ready", "published", "queued"} for lane in available))
        item["published"] = item["state"] == "published"
        if board and item["board"] != board or grade and item["grade"] != grade or subject and item["subject"] != subject:
            continue
        if state and item["state"] != state or initiator and (starter or "unknown") != initiator:
            continue
        if q and q.casefold() not in " ".join(str(item[field]) for field in ("chapter_title", "chapter_code", "filename", "initiator_email")).casefold():
            continue
        records.append(item)
    records.sort(key=lambda item: item["job_id"], reverse=True)
    by_state = Counter(row["state"] for row in records)
    users: dict[str, list[dict]] = defaultdict(list)
    for row in records:
        users[row["initiator_email"] or "unknown"].append(row)
    active_states = {"step01_running", "step02_running", "publish_running"}
    summary = {
        "uploaded_runs": len(records), "runs_started": sum(row["started"] for row in records),
        "unique_chapters_started": len({row["chapter_id"] for row in records if row["started"] and row["chapter_id"]}),
        "unassigned_runs": sum(row["started"] and row["chapter_id"] is None for row in records),
        "running": sum(by_state[value] for value in active_states),
        "queued": sum(by_state[value] for value in {"step01_queued", "step02_queued", "publish_queued"}),
        "recovering": by_state["recovering"],
        "failed": sum(by_state[value] for value in {"failed", "master_failed", "dead"}),
        "concepts_complete": sum(row["concepts_complete"] for row in records),
        "masters_complete": sum(row["masters_complete"] for row in records),
        "published": sum(row["published"] for row in records),
        **aggregate_cost(records),
    }
    page = max(1, page); page_size = max(1, min(100, page_size))
    return {
        "summary": summary,
        "states": [{"value": value, "label": chapter_batches._STATE_LABELS.get(value, value), "count": count} for value, count in sorted(by_state.items())],
        "users": [{"email": email, "runs": len(rows), **aggregate_cost(rows)} for email, rows in sorted(users.items())],
        "items": records[(page - 1) * page_size:page * page_size],
        "page": page, "page_size": page_size, "total": len(records), "total_pages": max(1, (len(records) + page_size - 1) // page_size),
        "queue": chapter_batches.queue_summary(db, capacity=chapter_queue_worker.capacity(), worker_alive=chapter_queue_worker.worker_alive()),
        "notifications": run_notifications.summary(db, job_ids=[row["job_id"] for row in records]),
        "server_time": datetime.now(timezone.utc).isoformat(),
        "cost_scope": "Cumulative recorded model API estimates, counted once per source job. Pending and unpriced requests are excluded from known spend. Conversion services and infrastructure are excluded unless recorded in the usage ledger.",
        "visibility": "Shared chapter runs, including prior source files and previous catalogue entries, plus your private standalone runs.",
    }
