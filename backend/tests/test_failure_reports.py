"""Private failure evidence is durable; public exports contain no free text."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import config, models
from app.services import failure_reports as reports
from app.services import failure_reports_public as public
from app.services import batch_broker, run_control, uploads


@pytest.fixture()
def fixture(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "UPLOAD_DIR", tmp_path / "data" / "uploads")
    engine = create_engine(f"sqlite:///{tmp_path / 'failure.db'}")
    models.Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    db = factory()
    chapter = models.Chapter(chapter_code="fixture", board="CBSE", grade="09", subject="Science")
    db.add(chapter)
    db.flush()
    job = models.UploadJob(
        module="build_concepts", filename="Student Jane Smith private source.pdf",
        mmd_text="Private textbook paragraphs and student work", requested_chapter_id=chapter.id,
        owner_sub="teacher-private", started_by_email="teacher@example.edu",
        execution_mode="batch", status="converted", run_id="private-run-id",
        run_state={"status": "processing", "stage": "private teacher stage"},
        generation_checkpoint={"stage": "phase3", "target_chapter_id": chapter.id,
                               "pdf_sha256": "a" * 64, "page_evidence_sha256": "b" * 64,
                               "raw_text": "private source bytes"},
        question_inventory={"generation_quality_policy": "owner-generation-quality-2026-09-16-v5", "items": [{"raw_task": "private task"}]},
        openai_usage={"request_count": 1, "estimated_cost_usd": 0.125,
                      "pricing_complete": True, "usage_complete": True,
                      "request_attempts": [{"attempt_id": "a" * 32, "provider": "openai",
                         "actual_model": "gpt-5.6-luna", "request_id": "req_123", "batch_id": "batch_123",
                         "request_sha256": "c" * 64, "receipt_id": "batch_123:" + "c" * 64,
                         "delivery_mode": "batch", "usage_reported": True}]},
    )
    db.add(job)
    db.flush()
    row = models.ChapterBatchRow(chapter_id=chapter.id, job_id=job.id)
    db.add(row)
    db.flush()
    task = models.ChapterBatchTask(batch_row_id=row.id, job_id=job.id, kind="step01", state="leased",
                                   lease_owner="private-machine:lease-token", cohort_id="cohort123", attempt=1)
    db.add(task)
    db.commit()
    receipt_id = "batch_123:" + "c" * 64
    receipt_path = config.DATA_DIR / "batch" / "receipts" / f"{reports._text_hash(receipt_id)}.json"
    receipt_path.parent.mkdir(parents=True)
    receipt_path.write_text(json.dumps({"receipt_id": receipt_id, "owner_job_id": job.id,
        "cost_estimate": {"estimated_cost_usd": 0.125, "input_tokens": 100, "output_tokens": 20}}))
    try:
        yield db, job, task, factory
    finally:
        db.close()
        engine.dispose()


def failure():
    try:
        raise ValueError("Student Jane Smith: teacher@example.edu API_KEY=super-private-key; missing QINV-0007")
    except ValueError as error:
        error.validation_diagnostics = {"code": "source_canonical_gate", "issues": [{
            "code": "phase2_task_rich_text_invalid", "qid": "QINV-0007",
            "rich_text_issues": ["raw_latex"], "task_snippet": "Private textbook question",
        }]}
        error.coverage_diagnostics = {"code": "rendered_inventory_coverage", "missing_qids": ["QINV-0007"],
                                      "duplicate_qids": ["QINV-0008"], "source_identities": [
                                          {"qid": "QINV-0007", "expected_question_sha256": "e" * 64,
                                           "source_label": "Private teacher label"}]}
        error.failure_code = "source_evidence_mismatch"
        error.reason_code = "original_pdf_changed"
        error.evidence_identity = {"expected_pdf_sha256": "a" * 64, "actual_pdf_sha256": "d" * 64}
        return error


def private_reports():
    return [json.loads(path.read_text()) for path in sorted(reports.root().glob("*/*.json"))
            if path.parent.name != "backfill_index"]


def test_private_capture_keeps_exact_hashes_receipts_ids_without_mutating_work(fixture, monkeypatch):
    db, job, task, _ = fixture
    monkeypatch.setenv("TEST_API_KEY", "super-private-key")
    before = copy.deepcopy((job.generation_checkpoint, job.question_inventory, job.openai_usage,
                            job.run_state, job.status, task.state, task.lease_owner))
    error = failure()
    report_id = reports.record_failure(db, job.id, error, task=task, origin="queue")
    assert report_id and len(report_id) == 32
    private = private_reports()[0]
    serialized = json.dumps(private)
    assert "super-private-key" not in serialized and "teacher@example.edu" not in serialized
    assert "Private textbook question" not in serialized
    assert "Student Jane Smith" in private["error"]["message"]  # private diagnostics only
    assert private["error"]["traceback"] and private["error"]["message_sha256"] == reports._text_hash(str(error))
    assert private["source"]["checkpoint_sha256"] == reports._hash(job.generation_checkpoint)
    assert private["task"]["lease_owner"] == task.lease_owner
    assert private["usage"]["attempts"][0]["receipt_id"] == "batch_123:" + "c" * 64
    assert (job.generation_checkpoint, job.question_inventory, job.openai_usage,
            job.run_state, job.status, task.state, task.lease_owner) == before
    assert not db.dirty
    assert not list(reports.root().glob("**/*.tmp"))


def test_public_projection_is_independent_allowlist_with_actionable_machine_evidence(fixture):
    db, job, task, _ = fixture
    reports.record_failure(db, job.id, failure(), task=task, origin="queue")
    page = reports.export_public_page()
    public.validate_public_envelope(page)
    encoded = json.dumps(page)
    for forbidden in ("Student Jane Smith", "teacher@example", "private source", "private-machine", "raw_text",
                      "traceback\":", "task_snippet", "super-private-key", "private teacher stage"):
        assert forbidden not in encoded
    report = page["reports"][0]
    assert report["source_evidence"]["expected_pdf_sha256"] == "a" * 64
    assert report["source_evidence"]["actual_pdf_sha256"] == "d" * 64
    assert report["source_evidence"]["page_evidence_sha256s"] == ["b" * 64]
    assert report["usage"]["attempts"][0]["batch_id"] == "batch_123"
    assert report["usage"]["totals"]["estimated_cost_usd"] == 0.125
    assert report["usage"]["attempts"][0]["estimated_cost_usd"] == 0.125
    assert report["usage"]["attempts"][0]["input_tokens"] == 100
    assert report["policies"][0]["version"] == "owner-generation-quality-2026-09-16-v5"
    assert any(row["source_identity_hashes"] == [{"qid": "QINV-0007", "expected_question_sha256": "e" * 64}]
               for row in report["validation"])
    assert "Private teacher label" not in encoded
    assert any(row["code"] == "rendered_inventory_coverage" and row["entity_ids"] == ["QINV-0007", "QINV-0008"]
               or row["code"] == "rendered_inventory_coverage" and set(row["entity_ids"]) == {"QINV-0007", "QINV-0008"}
               for row in report["validation"])
    assert any(row["code"] == "phase2_task_rich_text_invalid" and row["issue_codes"] == ["raw_latex"] for row in report["validation"])
    poisoned = copy.deepcopy(page)
    poisoned["reports"][0]["exception"]["message"] = "Sensitive source text"
    with pytest.raises(ValueError):
        public.validate_public_envelope(poisoned)


@pytest.mark.parametrize("error", [run_control.RunDeferred("waiting"), batch_broker.BatchPending("waiting"), uploads.JobAlreadyRunningError("busy")])
def test_waits_and_lock_collisions_are_not_failures(fixture, error):
    db, job, task, _ = fixture
    assert reports.record_failure(db, job.id, error, task=task) is None
    assert reports.export_public_page()["reports"] == []


def test_each_observation_is_immutable_related_and_paginates_without_loss(fixture):
    db, job, task, _ = fixture
    error = failure()
    first = reports.record_failure(db, job.id, error, origin="upload")
    first_path = next(reports.root().glob("*/*.json"))
    first_bytes = first_path.read_bytes()
    second = reports.record_failure(db, job.id, error, origin="queue", task=task)
    assert first_path.read_bytes() == first_bytes
    page1 = reports.export_public_page(limit=1)
    page2 = reports.export_public_page(cursor=page1["next_cursor"], limit=1)
    assert [row["report_id"] for row in page1["reports"] + page2["reports"]] == [first, second]
    assert page2["reports"][0]["related_report_id"] == first
    assert page1["reports"][0]["fingerprint"] == page2["reports"][0]["fingerprint"]
    assert page2["next_cursor"] is None and page2["total_count"] == 2


def test_storage_failure_never_changes_original_error_or_leaves_torn_report(fixture, monkeypatch):
    db, job, task, _ = fixture
    monkeypatch.setattr(reports.os, "replace", lambda *_args: (_ for _ in ()).throw(OSError("full disk")))
    error = failure()
    assert reports.record_failure(db, job.id, error, task=task) is None
    assert "QINV-0007" in str(error)
    assert task.state == "leased" and not list(reports.root().glob("**/*.json"))
    assert not list(reports.root().glob("**/*.tmp"))


def test_existing_failures_backfill_once_without_fake_traceback_or_run_changes(fixture):
    db, job, _task, _ = fixture
    job.run_state = {**job.run_state, "status": "failed"}
    job.generation_log = [{"type": "log", "level": "error", "message": "old exact saved issue QINV-0007"}]
    db.commit()
    snapshot = copy.deepcopy((job.run_state, job.generation_checkpoint, job.question_inventory))
    assert reports.backfill_existing(db) == {"captured": 1, "already_captured": 0, "unavailable": 0}
    assert reports.backfill_existing(db) == {"captured": 0, "already_captured": 1, "unavailable": 0}
    private = private_reports()[0]
    assert private["origin"] == "historical" and private["error"]["type"] == "SavedFailure"
    assert private["error"]["traceback"] == "" and private["error"]["frames"] == []
    assert (job.run_state, job.generation_checkpoint, job.question_inventory) == snapshot


def test_upload_and_queue_hooks_capture_before_lease_settlement(fixture):
    from app.services import chapter_queue_worker
    db, job, task, _ = fixture
    error = failure()
    uploads.persist_current_generation_log(db, job.id, error=error, owner_sub=job.owner_sub)
    chapter_queue_worker._record_failure_outcome(db, task, {"state": "failed", "failure_code": "source_evidence_mismatch"}, error=error)
    saved = private_reports()
    assert [row["origin"] for row in saved] == ["upload", "queue"]
    assert saved[-1]["task"]["state"] == "leased"
    count = len(saved)
    chapter_queue_worker._record_failure_outcome(db, task, {"state": "retry", "refund_attempt": True}, error=run_control.RunDeferred("wait"))
    assert len(private_reports()) == count


def test_cli_empty_export_and_stdlib_validator_do_not_need_db(tmp_path):
    backend = Path(__file__).resolve().parents[1]
    environment = {**os.environ, "AEGIS_DATA_DIR": str(tmp_path / "empty"),
                   "AEGIS_DB_URL": "sqlite:////does/not/exist/aegis.db"}
    result = subprocess.run([sys.executable, "scripts/export_failure_reports.py"], cwd=backend,
                            env=environment, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    page = json.loads(result.stdout)
    public.validate_public_envelope(page)
    assert page["reports"] == [] and page["next_cursor"] is None and page["total_count"] == 0
