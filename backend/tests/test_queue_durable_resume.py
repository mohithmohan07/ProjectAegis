"""Deployment and Batch waits preserve paid work, ownership and notifications."""
from datetime import datetime, timedelta
from uuid import uuid4

import pytest

from app import models
from app.db import SessionLocal
from app.services import chapter_queue, chapter_queue_worker, run_control


@pytest.fixture
def queued(db):
    chapter = models.Chapter(chapter_code="durable-" + uuid4().hex[:12], board="CBSE",
                             grade="10", subject="Maths", chapter_title="Durable")
    db.add(chapter)
    job = models.UploadJob(owner_sub="uploader", module="build_concepts",
                           learning_kind="post", status="converted", filename="source.pdf")
    db.add(job)
    db.flush()
    row = models.ChapterBatchRow(chapter_id=chapter.id, job_id=job.id)
    db.add(row)
    db.flush()
    outcome = chapter_queue.enqueue_one(
        db, chapter.id, step="step01", push_group_id="selected-batch",
        actor_sub="reviewer", actor_email="reviewer@up.school")
    assert outcome["verdict"] == "queued"
    db.commit()
    task_id = outcome["task_id"]
    yield chapter, job, row, db.get(models.ChapterBatchTask, task_id)
    db.rollback()
    db.query(models.RunNotification).filter_by(task_id=task_id).delete()
    db.query(models.ChapterBatchTask).filter_by(id=task_id).delete()
    db.commit()


def test_push_freezes_job_starter_and_batch_lane(db, queued):
    chapter, job, _, task = queued
    assert task.job_id == job.id
    assert task.cohort_id == "selected-batch"
    assert job.owner_sub == "uploader"
    assert job.execution_mode == "batch"
    assert job.requested_chapter_id == chapter.id
    assert (job.started_by_sub, job.started_by_email) == ("reviewer", "reviewer@up.school")


def test_explicit_synchronous_push_records_actual_transport(db, queued):
    chapter, job, _, task = queued
    task.state = "cancelled"
    db.flush()
    outcome = chapter_queue.enqueue_one(
        db, chapter.id, step="step01", push_group_id="explicit-sync", cohort_id="")
    assert outcome["verdict"] == "queued"
    assert not db.get(models.ChapterBatchTask, outcome["task_id"]).cohort_id
    assert job.execution_mode == "synchronous"
    assert job.requested_chapter_id == chapter.id
    # The shared fixture owns the original task; roll back this second push.
    db.rollback()


def test_replaced_source_cannot_retarget_historical_task(db, queued):
    chapter, job, row, task = queued
    new_job = models.UploadJob(owner_sub="uploader", module="build_concepts",
                               learning_kind="post", status="converted", filename="replacement.pdf")
    db.add(new_job)
    db.flush()
    row.job_id = new_job.id
    task.state = "failed"
    db.commit()
    assert chapter_queue.reconcile_before_dispatch(db, task)["failure_code"] == "source_replaced"
    assert chapter_queue.retry_one(db, chapter.id)["reason_code"] == "source_replaced"
    assert task.job_id == job.id


@pytest.mark.parametrize("reason", ["batch_wait", "deployment"])
def test_deferred_run_requeues_with_durable_delay_without_failure_email(db, queued, monkeypatch, reason):
    _, _, _, task = queued
    task.max_attempts = 1
    db.commit()
    chapter_queue.claim(db, task.id)

    def defer(_db, _task):
        raise run_control.RunDeferred("Saved work is waiting", reason=reason, delay=60)

    monkeypatch.setattr(chapter_queue_worker, "_schedule_checkpoint_backup", lambda *a: None)
    worker = chapter_queue_worker.ChapterQueueWorker(SessionLocal, runner=defer)
    before = datetime.utcnow()
    worker._run_one(task.id)
    db.expire_all()
    resumed = db.get(models.ChapterBatchTask, task.id)
    assert resumed.state == "queued"
    assert resumed.attempt == 0
    assert resumed.failure_code == reason
    assert resumed.start_after >= before + timedelta(seconds=60)
    assert chapter_queue.claim(db, task.id) is None
    assert db.query(models.RunNotification).filter_by(task_id=task.id).count() == 0


def test_finish_commits_one_notification_for_original_authenticated_starter(db, queued):
    _, job, _, task = queued
    chapter_queue.claim(db, task.id)
    chapter_queue.finish(db, task.id, state="done")
    chapter_queue.finish(db, task.id, state="done")
    notifications = db.query(models.RunNotification).filter_by(task_id=task.id).all()
    assert len(notifications) == 1
    assert notifications[0].job_id == job.id
    assert notifications[0].recipient == "reviewer@up.school"
    assert notifications[0].status == "pending"


def test_a_worker_that_lost_its_lease_cannot_requeue_or_finish_new_owner(db, queued):
    _, _, _, task = queued
    chapter_queue.claim(db, task.id)
    task.lease_owner = "different-live-worker"
    db.commit()
    chapter_queue.requeue(db, task.id, refund_attempt=True)
    chapter_queue.finish(db, task.id, state="failed", error="late exception")
    db.refresh(task)
    assert task.state == "leased"
    assert task.lease_owner == "different-live-worker"
    assert db.query(models.RunNotification).filter_by(task_id=task.id).count() == 0


def test_batch_admission_survives_insufficient_synchronous_capacity(monkeypatch):
    monkeypatch.setattr(chapter_queue_worker.config, "OPENAI_MAX_CONCURRENCY", 8)
    monkeypatch.setattr(chapter_queue_worker.config, "phase3_decision_workers", lambda: 16)
    worker = chapter_queue_worker.ChapterQueueWorker(lambda: None)
    assert chapter_queue_worker.admission_shortfall()
    assert worker.admits("step01", cohort=True)
    assert not worker.admits("step01", cohort=False)
    details = chapter_queue_worker.capacity_details()
    assert details["capacity"] == details["batch_capacity"] >= 6
    assert details["synchronous_capacity"] == 2


def test_wait_refund_never_produces_negative_attempts(db, queued):
    _, _, _, task = queued
    chapter_queue.requeue(db, task.id, refund_attempt=True, delay_seconds=10)
    db.refresh(task)
    assert task.attempt == 0


def test_early_deploy_pause_stops_admission_without_stopping_heartbeat_owner():
    worker = chapter_queue_worker.ChapterQueueWorker(lambda: None)
    run_control.request_pause()
    try:
        assert not worker.admits("step01", cohort=True)
        assert not worker.admits("publish")
        assert worker._dispatch_once() == 0
        assert worker._stopping is False
    finally:
        run_control.reset()


def test_terminal_batch_failure_does_not_automatically_purchase_another_attempt():
    from app.services import batch_broker

    assert chapter_queue_worker.classify_exception(
        batch_broker.BatchUnavailable("request rejected")) == {
            "state": "failed", "failure_code": "batch_failed", "error": "request rejected"}
    waiting = chapter_queue_worker.classify_exception(batch_broker.BatchPending("still running"))
    assert waiting["state"] == "retry"
    assert waiting["refund_attempt"]


def test_requeued_progress_is_saved_history_not_a_running_bar(db, queued):
    from app.services import chapter_batches
    chapter, job, row, task = queued
    task.started_at = datetime.utcnow()
    task.failure_code = 'batch_wait'
    task.state = 'queued'
    job.run_state = {'run_id': 'same-run', 'status': 'waiting', 'stage': 'Analysing source', 'progress': .42}
    db.flush()
    view = chapter_batches.project_one(db, row.chapter_id)
    assert view['state'] == 'step01_queued'
    assert view['progress'] == 0
    assert view['saved_progress'] == .42 and view['saved_stage'] == 'Analysing source'
    assert view['queue']['cohort_id'] == task.cohort_id
