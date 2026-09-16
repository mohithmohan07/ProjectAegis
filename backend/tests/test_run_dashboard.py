"""Accounting, history and access regressions; no paid provider calls."""
from datetime import datetime
import pytest

from app import models
from app.db import SessionLocal
from app.services import run_dashboard


def test_cost_delivery_is_receipt_evidence_and_reuse_is_not_new_spend():
    usage = {
        "estimated_cost_usd": 9, "known_usage_estimated_cost_usd": 9,
        "request_count": 3,
        "request_attempts": [
            {"attempt_id": "batch", "delivery_mode": "batch", "estimated_cost_usd": 2},
            {"attempt_id": "batch", "delivery_mode": "batch", "estimated_cost_usd": 2},
            {"attempt_id": "sync", "delivery_mode": "synchronous", "estimated_cost_usd": 3},
            {"attempt_id": "old", "estimated_cost_usd": 4},
            {"attempt_id": "reuse", "delivery_mode": "batch", "reused": True, "estimated_cost_usd": 0},
        ],
    }
    result = run_dashboard.cost_view(usage, started=True)
    assert result["known_cost_usd"] == 9
    assert result["batch_cost_usd"] == 2
    assert result["synchronous_cost_usd"] == 3
    assert result["unclassified_cost_usd"] == 4
    assert result["reused_responses"] == 1
    assert result["batch_requests"] == 1


def test_pending_and_unpriced_usage_do_not_appear_as_complete_zero_cost():
    result = run_dashboard.cost_view({
        "estimated_cost_usd": None, "known_usage_estimated_cost_usd": 1.25,
        "pending_request_count": 4, "unresolved_usage_request_count": 1,
    }, started=True)
    assert result["known_cost_usd"] == 1.25
    assert result["cost_complete"] is False
    assert result["pending_requests"] == 4
    assert run_dashboard.cost_view({}, started=True)["cost_complete"] is False
    assert run_dashboard.cost_view({}, started=False)["cost_complete"] is True


def test_dashboard_keeps_replaced_runs_counts_jobs_once_and_excludes_private_jobs():
    db = SessionLocal()
    try:
        chapter = models.Chapter(chapter_code="DASH-HISTORY", chapter_title="dash-history", chapter_display_name="dash-history", board="CBSE", grade="09", subject="Science", catalogue_active=False)
        db.add(chapter); db.flush()
        old = models.UploadJob(owner_sub="someone-else", module="build_concepts", filename="dash-history-old.pdf", status="converted", openai_usage={"estimated_cost_usd": 2}, run_id="old-run")
        current = models.UploadJob(owner_sub="someone-else", module="build_concepts", filename="dash-history-current.pdf", status="uploaded", openai_usage={"estimated_cost_usd": None, "known_usage_estimated_cost_usd": 3, "pending_request_count": 1}, run_id="new-run")
        hidden = models.UploadJob(owner_sub="someone-else", module="build_concepts", filename="dash-history-private.pdf", openai_usage={"estimated_cost_usd": 100})
        own = models.UploadJob(owner_sub="dashboard-viewer", module="build_concepts", filename="dash-history-own.pdf", status="uploaded")
        db.add_all([old, current, hidden, own]); db.flush()
        binding = models.ChapterBatchRow(chapter_id=chapter.id, job_id=current.id, previous_job_ids=[old.id, old.id], created_by_email="uploader@example.com")
        db.add(binding); db.flush()
        task = models.ChapterBatchTask(batch_row_id=binding.id, job_id=old.id, kind="step01", state="failed", started_at=datetime.utcnow(), enqueued_by_email="starter@example.com", cohort_id="old-cohort")
        db.add(task); db.commit()
        result = run_dashboard.snapshot(db, owner_sub="dashboard-viewer", q="dash-history")
        assert result["total"] == 3
        assert result["summary"]["known_cost_usd"] == 5
        assert result["summary"]["runs_started"] == 2
        assert result["summary"]["unique_chapters_started"] == 1
        assert result["summary"]["cost_complete"] is False
        by_id = {row["job_id"]: row for row in result["items"]}
        assert hidden.id not in by_id
        assert by_id[old.id]["historical_source"] is True
        assert by_id[old.id]["initiator_email"] == "starter@example.com"
        assert by_id[current.id]["initiator_email"] == ""
        assert by_id[old.id]["unclassified_cost_usd"] == 2  # cohort does not prove billing mode
        assert by_id[current.id]["catalogue_active"] is False
        assert by_id[own.id]["started"] is False
        paged = run_dashboard.snapshot(db, owner_sub="dashboard-viewer", q="dash-history", page_size=1)
        assert len(paged["items"]) == 1
        assert paged["summary"]["known_cost_usd"] == 5
    finally:
        db.close()


def test_shared_reader_keeps_exact_previous_job_membership_and_private_boundary():
    import pytest
    from app.services import uploads
    db = SessionLocal()
    try:
        chapter = models.Chapter(chapter_code="DASH-ACCESS", chapter_title="History access")
        old = models.UploadJob(owner_sub="history-owner", module="build_concepts", filename="prior.pdf")
        unrelated = models.UploadJob(owner_sub="history-owner", module="build_concepts", filename="prior.pdf")
        db.add_all([chapter, old, unrelated]); db.flush()
        db.add(models.ChapterBatchRow(chapter_id=chapter.id, previous_job_ids=[old.id])); db.commit()
        assert uploads.get_job_for_reader(db, old.id, owner_sub="teammate", module="build_concepts").id == old.id
        with pytest.raises(uploads.UploadJobNotFound):
            uploads.get_job_for_reader(db, unrelated.id, owner_sub="teammate", module="build_concepts")
        with pytest.raises(uploads.UploadJobNotFound):
            uploads.get_job_for_reader(db, old.id, owner_sub="teammate", module="build_assessments")
    finally:
        db.close()


def test_durable_paid_receipts_survive_missing_job_accounting_without_duplicate_cost(monkeypatch):
    monkeypatch.setattr(run_dashboard.openai_usage, "estimate_batch_receipt", lambda row: {
        "estimated_cost_usd": row["amount"], "estimated_cost_inr": row["amount"] * 80,
        "usage_complete": True,
    })
    receipts = [
        {"receipt_id": "already-journaled", "amount": 2},
        {"receipt_id": "harvested-before-crash", "amount": 3},
        {"receipt_id": "harvested-before-crash", "amount": 3},
    ]
    result = run_dashboard.reconcile_paid_receipts({
        "estimated_cost_usd": 2, "request_count": 1,
        "billed_receipt_ids": ["already-journaled"],
        "request_attempts": [{"attempt_id": "a", "receipt_id": "already-journaled", "delivery_mode": "batch", "estimated_cost_usd": 2}],
    }, receipts)
    assert result["known_usage_estimated_cost_usd"] == 5
    assert result["recovered_batch_receipts"] == 1
    assert run_dashboard.cost_view(result, started=True)["batch_cost_usd"] == 5
    # Compact summaries retain receipt identities, so they also deduplicate.
    compact = run_dashboard.reconcile_paid_receipts({"estimated_cost_usd": 2, "billed_receipt_ids": ["already-journaled"]}, receipts[:1])
    assert compact["known_usage_estimated_cost_usd"] == 2
    empty = run_dashboard.reconcile_paid_receipts({}, receipts[:1])
    assert empty["estimated_cost_usd"] == 2
    assert empty["known_usage_estimated_cost_inr"] == 160


def test_ambiguous_legacy_receipts_are_visible_but_not_summed_twice(monkeypatch):
    monkeypatch.setattr(run_dashboard.openai_usage, "estimate_batch_receipt", lambda row: {"estimated_cost_usd": 2})
    result = run_dashboard.reconcile_paid_receipts({"estimated_cost_usd": 4, "request_count": 2}, [{"receipt_id": "legacy", "amount": 2}])
    cost = run_dashboard.cost_view(result, started=True)
    assert cost["known_cost_usd"] == 4
    assert cost["unreconciled_batch_receipts"] == 1
    assert cost["unreconciled_batch_cost_usd"] == 2
    assert cost["cost_complete"] is False


def _price(receipt):
    return {"estimated_cost_usd": receipt["amount"], "estimated_cost_inr": receipt["amount"] * 80,
            "usage_complete": True, "pricing_complete": True}


def test_new_identified_charge_does_not_hide_old_unidentified_charge(monkeypatch):
    monkeypatch.setattr(run_dashboard.openai_usage, "estimate_batch_receipt", _price)
    result = run_dashboard.reconcile_paid_receipts({
        "estimated_cost_usd": 5, "request_count": 2, "billed_receipt_ids": ["new"],
        "request_attempts": [
            {"attempt_id": "old", "delivery_mode": "batch", "estimated_cost_usd": 2},
            {"attempt_id": "new", "delivery_mode": "batch", "receipt_id": "new", "estimated_cost_usd": 3},
        ],
    }, [{"receipt_id": "old", "amount": 2}, {"receipt_id": "new", "amount": 3}])
    assert result["known_usage_estimated_cost_usd"] == 5
    assert result["unreconciled_batch_receipts"] == 1
    assert result["recovered_batch_receipts"] == 0


def test_compact_identified_history_can_recover_a_new_paid_line(monkeypatch):
    monkeypatch.setattr(run_dashboard.openai_usage, "estimate_batch_receipt", _price)
    result = run_dashboard.reconcile_paid_receipts({
        "estimated_cost_usd": 3, "request_count": 1, "billed_receipt_ids": ["saved"],
        "request_attempts": [], "attempt_details_included": False,
    }, [{"receipt_id": "saved", "amount": 3}, {"receipt_id": "new", "amount": 2}])
    assert result["estimated_cost_usd"] == 5
    assert result["recovered_batch_receipts"] == 1
    assert result["unreconciled_batch_receipts"] == 0


@pytest.mark.parametrize("compact", [False, True])
def test_pending_transport_id_does_not_hide_paid_receipt_or_remain_pending(monkeypatch, compact):
    usage = run_dashboard.openai_usage
    monkeypatch.setattr(usage, "estimate_batch_receipt", _price)
    with usage.track() as tracked:
        with usage.request_attempt(requested_model="gpt-5.6-luna", provider="openai"):
            usage.record_service_started()
            usage.record_batch_pending(batch_id="paid", request_sha256="sha", wave_id="wave")
        saved = tracked.summary(include_attempts=not compact)
    assert run_dashboard.billed_receipt_ids(saved) == set()
    receipts = [{"receipt_id": "paid:sha", "amount": 2}]
    result = run_dashboard.reconcile_paid_receipts(saved, receipts)
    assert result["pending_request_count"] == 0
    assert result["request_count"] == result["recovered_batch_receipts"] == 1
    assert result["estimated_cost_usd"] == 2
    assert result["known_usage_estimated_cost_inr"] == 160
    assert run_dashboard.cost_view(result, started=True)["cost_complete"]
    replay = run_dashboard.reconcile_paid_receipts(result, receipts)
    assert replay["request_count"] == 1
    assert replay["estimated_cost_usd"] == 2


def test_receipt_union_is_global_to_visible_jobs_without_exposing_hidden_owners(monkeypatch):
    monkeypatch.setattr(run_dashboard.openai_usage, "estimate_batch_receipt", _price)
    db = SessionLocal()
    try:
        owner = models.UploadJob(owner_sub="union-viewer", module="build_concepts", filename="dash-union-owner.pdf", started_by_email="original@example.com")
        consumer = models.UploadJob(owner_sub="union-viewer", module="build_concepts", filename="dash-union-consumer.pdf", openai_usage={"estimated_cost_usd": 2, "request_count": 1, "billed_receipt_ids": ["shared"]})
        orphan = models.UploadJob(owner_sub="union-viewer", module="build_concepts", filename="dash-union-orphan.pdf")
        hidden = models.UploadJob(owner_sub="private-owner", module="build_concepts", filename="dash-union-private.pdf", openai_usage={"estimated_cost_usd": 100})
        db.add_all([owner, consumer, orphan, hidden]); db.flush()
        receipts = [
            {"receipt_id": "shared", "owner_job_id": owner.id, "amount": 2},
            {"receipt_id": "orphan", "owner_job_id": orphan.id, "amount": 3},
            {"receipt_id": "private", "owner_job_id": hidden.id, "amount": 100},
            {"receipt_id": "unknown", "owner_job_id": None, "amount": 200},
        ]
        monkeypatch.setattr(run_dashboard.batch_broker.BatchStore, "paid_receipts", lambda self: receipts)
        db.commit()
        result = run_dashboard.snapshot(db, owner_sub="union-viewer", q="dash-union")
        assert result["total"] == 3
        assert result["summary"]["known_cost_usd"] == 5
        assert result["summary"]["recovered_batch_receipts"] == 1
        by_id = {row["job_id"]: row for row in result["items"]}
        assert by_id[owner.id]["known_cost_usd"] == 0
        assert by_id[orphan.id]["known_cost_usd"] == 3
        assert hidden.id not in by_id
    finally:
        db.close()


def test_starter_and_saved_recovery_metadata_survive_a_later_queue_operator():
    db = SessionLocal()
    try:
        chapter = models.Chapter(chapter_code="DASH-RECOVERY", chapter_title="dash-recovery")
        job = models.UploadJob(owner_sub="recovery-viewer", module="build_concepts", filename="dash-recovery.pdf", started_by_email="starter@example.com", run_state={"status": "waiting", "stage": "saved work", "progress": 0.4})
        db.add_all([chapter, job]); db.flush()
        binding = models.ChapterBatchRow(chapter_id=chapter.id, job_id=job.id)
        db.add(binding); db.flush()
        db.add(models.ChapterBatchTask(batch_row_id=binding.id, job_id=job.id, kind="step02", state="queued", enqueued_by_email="later-operator@example.com"))
        db.commit()
        result = run_dashboard.snapshot(db, owner_sub="recovery-viewer", q="dash-recovery")
        assert result["total"] == 1
        row = result["items"][0]
        assert row["initiator_email"] == "starter@example.com"
        assert row["saved_stage"] == "saved work"
        assert row["saved_progress"] == 0.4
    finally:
        db.close()
