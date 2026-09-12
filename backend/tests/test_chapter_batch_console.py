"""The batch console: derivation, admission, the lease, and honest outcomes.

These cover the four things that would be expensive to get wrong: a row that
reads as done when it is not, a chapter that runs twice, an attempt budget that
never empties, and a queue that answers a pre-spend pause by itself.
"""
import json
from datetime import datetime, timedelta

import pytest

from app import models
from app.db import SessionLocal
from app.services import build_concepts_release as release_svc
from app.services import chapter_batches, chapter_queue, chapter_queue_worker


@pytest.fixture()
def session():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.rollback()
        db.close()


def _chapter(db, code="10CBMA_T01"):
    chapter = models.Chapter(
        chapter_code=code, board="CBSE", grade="10", subject="Maths",
        unit="Algebra", chapter_title="Polynomials",
        chapter_display_name="Polynomials",
    )
    db.add(chapter)
    db.flush()
    return chapter


def _job(db, *, status="converted", inventory=None, checkpoint=None):
    job = models.UploadJob(
        owner_sub="google:owner", module="build_concepts", learning_kind="post",
        filename="chapter.pdf", status=status,
        question_inventory=inventory or {},
        generation_checkpoint=checkpoint or {},
    )
    db.add(job)
    db.flush()
    return job


def _row(db, chapter, job=None):
    row = models.ChapterBatchRow(
        chapter_id=chapter.id, job_id=job.id if job else None,
        previous_job_ids=[], created_by_sub="google:owner",
        created_by_email="owner@up.school",
    )
    db.add(row)
    db.flush()
    return row


def _marker(status, **extra):
    payload = {
        "version": release_svc.CONCEPT_REVIEW_VERSION,
        "status": status,
        "available_lanes": ["post"],
        "required_lanes": ["post"],
        "reviewed_lanes": [],
        "master_review": {},
        "corrected_inputs": {},
    }
    payload.update(extra)
    return {release_svc.CONCEPT_REVIEW_KEY: payload}


# --------------------------------------------------------------------------- #
# Derivation — nothing may read as done when it is not
# --------------------------------------------------------------------------- #

def test_queued_row_shows_no_stage_and_no_progress(session):
    chapter = _chapter(session)
    job = _job(session, inventory={
        **_marker(release_svc.CONCEPT_REVIEW_PENDING),
        # A previous run left a stage behind. A queued row must not borrow it.
    })
    job.run_state = {"stage": "Building Master files", "progress": 0.8}
    row = _row(session, chapter, job)
    task = models.ChapterBatchTask(
        batch_row_id=row.id, kind="step02", state="queued", lanes=[],
    )
    session.add(task)
    session.flush()

    projected = chapter_batches.project_one(session, chapter.id)
    assert projected["state"] == "step02_queued"
    assert projected["stage"] == ""
    assert projected["progress"] == 0.0


def test_expired_lease_reads_as_recovering_not_running(session):
    chapter = _chapter(session, code="10CBMA_T02")
    job = _job(session, inventory=_marker(release_svc.CONCEPT_REVIEW_REVIEWED))
    row = _row(session, chapter, job)
    session.add(models.ChapterBatchTask(
        batch_row_id=row.id, kind="step02", state="leased",
        lease_owner="a-dead-process",
        lease_expires_at=datetime.utcnow() - timedelta(minutes=5),
    ))
    session.flush()

    projected = chapter_batches.project_one(session, chapter.id)
    assert projected["state"] == "recovering"
    assert projected["queue"]["lease_expired"] is True


def test_queued_cms_append_is_partly_published_never_published(session):
    chapter = _chapter(session, code="10CBMA_T03")
    marker = _marker(
        release_svc.CONCEPT_REVIEW_MASTER_READY,
        master_review={
            "post": {
                "status": "reviewed", "version": 2,
                "published": {
                    "release_uid": "u1", "version": 2,
                    "cms_workbook": {
                        "status": "queued",
                        "queued_reason": "the workbook was locked",
                    },
                },
            },
        },
    )
    job = _job(session, inventory=marker)
    _row(session, chapter, job)
    session.flush()

    projected = chapter_batches.project_one(session, chapter.id)
    assert projected["state"] == "partly_published"
    lane = next(item for item in projected["lanes"] if item["lane"] == "post")
    assert lane["master"] == "queued"
    assert "locked" in lane["master_reason"]


def test_post_only_run_can_reach_published(session):
    chapter = _chapter(session, code="10CBMA_T04")
    job = _job(session, inventory=_marker(release_svc.CONCEPT_REVIEW_PUBLISHED))
    _row(session, chapter, job)
    session.flush()

    projected = chapter_batches.project_one(session, chapter.id)
    assert projected["state"] == "published"
    lanes = {item["lane"]: item for item in projected["lanes"]}
    assert lanes["post"]["available"] is True
    assert lanes["pre"]["available"] is False


def test_step01_done_without_a_marker_is_a_visible_inconsistency(session):
    chapter = _chapter(session, code="10CBMA_T05")
    job = _job(session, status="converted", inventory={})
    row = _row(session, chapter, job)
    session.add(models.ChapterBatchTask(
        batch_row_id=row.id, kind="step01", state="done",
    ))
    session.flush()

    projected = chapter_batches.project_one(session, chapter.id)
    assert projected["state"] == "blocked"
    assert projected["blocked_kind"] == "no_review_marker"


def test_non_resumable_run_is_dead_and_offers_no_retry(session):
    chapter = _chapter(session, code="10CBMA_T06")
    job = _job(session, inventory={
        models.GENERATION_RECOVERY_INVENTORY_KEY: {
            "resume_allowed": False,
            "message": "the converted source is unusable",
            "recovery_action": "reconvert the PDF",
        },
    })
    row = _row(session, chapter, job)
    session.add(models.ChapterBatchTask(
        batch_row_id=row.id, kind="step01", state="failed",
        failure_code="non_resumable",
    ))
    session.flush()

    projected = chapter_batches.project_one(session, chapter.id)
    assert projected["state"] == "dead"
    assert projected["can"]["retry"] is False
    assert projected["can"]["step01"] is False


def test_pending_human_decision_blocks_and_names_itself(session):
    chapter = _chapter(session, code="10CBMA_T07")
    job = _job(
        session,
        inventory=_marker(release_svc.CONCEPT_REVIEW_PENDING),
        checkpoint={"human_decisions": {"pending": {
            "decision_id": "d-1", "kind": "source_topic",
            "question": "which topic owns this block?",
            "companion_pending_decisions": [{"decision_id": "d-2"}],
        }}},
    )
    _row(session, chapter, job)
    session.flush()

    projected = chapter_batches.project_one(session, chapter.id)
    assert projected["state"] == "blocked"
    assert projected["blocked_kind"] == "human_decision"
    assert projected["pending_decision"]["decision_id"] == "d-1"
    assert projected["pending_decision"]["companions"] == 1


def test_signals_are_read_without_loading_the_release_payloads(session):
    """The marker is extracted by key, not by loading a megabyte of payload."""
    chapter = _chapter(session, code="10CBMA_T08")
    inventory = _marker(release_svc.CONCEPT_REVIEW_PENDING)
    inventory["items"] = [{"qid": f"q{index}"} for index in range(5000)]
    inventory[release_svc.release_key_for_lane("post")] = {
        "summary": {"database_uploaded": True},
    }
    job = _job(session, inventory=inventory)
    session.commit()

    signals = chapter_batches.job_signals(session, [job.id])[job.id]
    assert signals["marker"]["status"] == release_svc.CONCEPT_REVIEW_PENDING
    assert signals["concept_uploaded"]["post"] is True
    assert "items" not in signals


# --------------------------------------------------------------------------- #
# Admission
# --------------------------------------------------------------------------- #

def test_push_refuses_a_chapter_with_no_source(session):
    chapter = _chapter(session, code="10CBMA_T09")
    _row(session, chapter, None)
    session.flush()

    outcome = chapter_queue.enqueue_one(
        session, chapter.id, step="step01", push_group_id="g1",
    )
    assert outcome["verdict"] == "refused"
    assert outcome["reason_code"] == "no_source"


def test_a_second_push_cannot_start_a_second_run(session):
    chapter = _chapter(session, code="10CBMA_T10")
    job = _job(session, status="converted")
    _row(session, chapter, job)
    session.flush()

    first = chapter_queue.enqueue_one(
        session, chapter.id, step="step01", push_group_id="g1",
    )
    assert first["verdict"] == "queued"
    session.flush()
    second = chapter_queue.enqueue_one(
        session, chapter.id, step="step01", push_group_id="g2",
    )
    assert second["verdict"] == "already_queued"
    live = session.query(models.ChapterBatchTask).filter(
        models.ChapterBatchTask.state.in_(models.CHAPTER_BATCH_LIVE_TASK_STATES)
    ).count()
    assert live == 1


def test_publish_refuses_without_named_lanes(session):
    chapter = _chapter(session, code="10CBMA_T11")
    job = _job(session, inventory=_marker(
        release_svc.CONCEPT_REVIEW_MASTER_READY))
    _row(session, chapter, job)
    session.flush()

    outcome = chapter_queue.enqueue_one(
        session, chapter.id, step="publish", push_group_id="g1", lanes=[],
    )
    assert outcome["verdict"] == "refused"
    assert outcome["reason_code"] == "no_lanes"


def test_publish_refuses_a_lane_the_run_does_not_have(session):
    chapter = _chapter(session, code="10CBMA_T12")
    job = _job(session, inventory=_marker(
        release_svc.CONCEPT_REVIEW_MASTER_READY))
    _row(session, chapter, job)
    session.flush()

    outcome = chapter_queue.enqueue_one(
        session, chapter.id, step="publish", push_group_id="g1", lanes=["pre"],
    )
    assert outcome["verdict"] == "refused"
    assert outcome["reason_code"] == "no_lanes"


def test_a_blocked_chapter_is_refused_until_a_person_returns_it(session):
    chapter = _chapter(session, code="10CBMA_T13")
    job = _job(session, status="converted")
    row = _row(session, chapter, job)
    session.add(models.ChapterBatchTask(
        batch_row_id=row.id, kind="step01", state="blocked",
        blocked_kind="storage_capacity", last_error="the volume is full",
    ))
    session.flush()

    refused = chapter_queue.enqueue_one(
        session, chapter.id, step="step01", push_group_id="g1",
    )
    assert refused["verdict"] == "refused"
    assert refused["reason_code"] == "blocked"

    returned = chapter_queue.retry_one(session, chapter.id)
    assert returned["verdict"] == "queued"
    task = session.query(models.ChapterBatchTask).one()
    assert task.state == "queued"
    assert task.blocked_kind == ""


def test_a_do_not_resume_verdict_is_never_retried(session):
    chapter = _chapter(session, code="10CBMA_T14")
    job = _job(session, status="converted")
    row = _row(session, chapter, job)
    session.add(models.ChapterBatchTask(
        batch_row_id=row.id, kind="step01", state="failed",
        failure_code="non_resumable", last_error="reconvert the PDF",
    ))
    session.flush()

    outcome = chapter_queue.retry_one(session, chapter.id)
    assert outcome["verdict"] == "refused"
    assert outcome["reason_code"] == "dead"


# --------------------------------------------------------------------------- #
# The lease
# --------------------------------------------------------------------------- #

def _queued_task(session, code):
    chapter = _chapter(session, code=code)
    job = _job(session, status="converted")
    row = _row(session, chapter, job)
    task = models.ChapterBatchTask(
        batch_row_id=row.id, kind="step01", state="queued", lanes=[],
    )
    session.add(task)
    session.commit()
    return task


def test_only_one_claim_wins_and_the_attempt_is_charged_once(session):
    task = _queued_task(session, "10CBMA_T15")
    first = chapter_queue.claim(session, task.id)
    assert first is not None
    assert first.attempt == 1
    assert first.lease_owner == chapter_queue.WORKER_TOKEN

    again = chapter_queue.claim(session, task.id)
    assert again is None
    session.expire_all()
    assert session.get(models.ChapterBatchTask, task.id).attempt == 1


def test_a_dead_process_lease_is_reclaimed_and_a_live_one_is_left_alone(session):
    mine = _queued_task(session, "10CBMA_T16")
    theirs = _queued_task(session, "10CBMA_T17")
    chapter_queue.claim(session, mine.id)
    session.query(models.ChapterBatchTask).filter(
        models.ChapterBatchTask.id == theirs.id
    ).update({
        "state": "leased", "lease_owner": "another-machine:1:deadbeef",
        "lease_expires_at": datetime.utcnow() - timedelta(minutes=1),
        "attempt": 1,
    })
    session.commit()

    recovered = chapter_queue.reclaim_orphans(session)
    assert recovered["requeued"] == 1
    session.expire_all()
    assert session.get(models.ChapterBatchTask, mine.id).state == "leased"
    assert session.get(models.ChapterBatchTask, theirs.id).state == "queued"


def test_another_live_process_keeps_its_lease(session):
    """A foreign token alone never means dead — two processes can share a volume."""
    theirs = _queued_task(session, "10CBMA_T16b")
    session.query(models.ChapterBatchTask).filter(
        models.ChapterBatchTask.id == theirs.id
    ).update({
        "state": "leased", "lease_owner": "another-worker:9:cafebabe",
        "lease_expires_at": datetime.utcnow() + timedelta(minutes=4),
        "attempt": 1,
    })
    session.commit()

    assert chapter_queue.reclaim_orphans(session) == {
        "requeued": 0, "failed": 0,
    }
    session.expire_all()
    assert session.get(models.ChapterBatchTask, theirs.id).state == "leased"


def test_an_expired_lease_of_a_running_task_is_never_reclaimed(session):
    """A late heartbeat must not cost a second two-hour run."""
    task = _queued_task(session, "10CBMA_T18")
    chapter_queue.claim(session, task.id)
    session.query(models.ChapterBatchTask).filter(
        models.ChapterBatchTask.id == task.id
    ).update({"lease_expires_at": datetime.utcnow() - timedelta(minutes=1)})
    session.commit()

    recovered = chapter_queue.reclaim_orphans(session, in_flight=[task.id])
    assert recovered == {"requeued": 0, "failed": 0}
    session.expire_all()
    assert session.get(models.ChapterBatchTask, task.id).state == "leased"

    # Not in flight: the same row is recovered.
    recovered = chapter_queue.reclaim_orphans(session, in_flight=[])
    assert recovered["requeued"] == 1


def test_a_crash_loop_exhausts_its_budget_instead_of_spinning(session):
    task = _queued_task(session, "10CBMA_T19")
    for _ in range(2):
        claimed = chapter_queue.claim(session, task.id)
        assert claimed is not None
        session.query(models.ChapterBatchTask).filter(
            models.ChapterBatchTask.id == task.id
        ).update({"lease_expires_at": datetime.utcnow() - timedelta(minutes=1)})
        session.commit()
        chapter_queue.reclaim_orphans(session, in_flight=[])
    session.expire_all()
    settled = session.get(models.ChapterBatchTask, task.id)
    assert settled.attempt == 2
    assert settled.state == "failed"
    assert settled.failure_code == "worker_restart"


def test_heartbeat_fails_once_the_lease_is_lost(session):
    task = _queued_task(session, "10CBMA_T20")
    chapter_queue.claim(session, task.id)
    assert chapter_queue.heartbeat(session, task.id) is True
    session.query(models.ChapterBatchTask).filter(
        models.ChapterBatchTask.id == task.id
    ).update({"lease_owner": "someone-else"})
    session.commit()
    assert chapter_queue.heartbeat(session, task.id) is False


# --------------------------------------------------------------------------- #
# Reconcile before spend
# --------------------------------------------------------------------------- #

def test_a_completed_step_is_settled_without_spending_again(session):
    chapter = _chapter(session, code="10CBMA_T21")
    job = _job(session, inventory=_marker(release_svc.CONCEPT_REVIEW_PENDING))
    row = _row(session, chapter, job)
    task = models.ChapterBatchTask(
        batch_row_id=row.id, kind="step01", state="leased", attempt=1,
    )
    session.add(task)
    session.commit()

    verdict = chapter_queue.reconcile_before_dispatch(session, task)
    assert verdict is not None
    assert verdict["state"] == "done"
    assert verdict["already_complete"] is True


def test_a_non_resumable_job_is_settled_terminal_before_any_call(session):
    chapter = _chapter(session, code="10CBMA_T22")
    job = _job(session, inventory={
        models.GENERATION_RECOVERY_INVENTORY_KEY: {
            "resume_allowed": False, "message": "unusable source",
            "recovery_action": "reconvert the PDF",
        },
    })
    row = _row(session, chapter, job)
    task = models.ChapterBatchTask(
        batch_row_id=row.id, kind="step01", state="leased", attempt=1,
    )
    session.add(task)
    session.commit()

    verdict = chapter_queue.reconcile_before_dispatch(session, task)
    assert verdict["state"] == "failed"
    assert verdict["failure_code"] == "non_resumable"


# --------------------------------------------------------------------------- #
# Admission arithmetic
# --------------------------------------------------------------------------- #

def test_admission_holds_a_reserve_back_from_the_provider_gate(monkeypatch):
    worker = chapter_queue_worker.ChapterQueueWorker(SessionLocal)
    monkeypatch.setenv("AEGIS_QUEUE_MAX_CONCURRENT_RUNS", "4")
    monkeypatch.setenv("AEGIS_QUEUE_MAX_CONCURRENT_MASTERS", "2")
    monkeypatch.setattr(
        chapter_queue_worker.config, "OPENAI_MAX_CONCURRENCY", 48,
        raising=False,
    )
    monkeypatch.setattr(
        chapter_queue_worker.config, "phase3_decision_workers", lambda: 16,
    )
    # 48 slots minus a 16-slot reserve is two fan-outs' worth of room.
    assert worker._provider_budget() == 2
    assert worker.admits("step01") is True
    worker._in_flight = {1: "step02"}          # costs two fan-outs
    assert worker.admits("step01") is False
    worker._in_flight = {1: "step01"}
    assert worker.admits("step01") is True
    assert worker.admits("step02") is False


def test_one_publish_at_a_time(monkeypatch):
    worker = chapter_queue_worker.ChapterQueueWorker(SessionLocal)
    assert worker.admits("publish") is True
    worker._in_flight = {9: "publish"}
    assert worker.admits("publish") is False
    # A publish never blocks a generation step, and vice versa.
    worker._in_flight = {9: "step01"}
    assert worker.admits("publish") is True


# --------------------------------------------------------------------------- #
# Outcomes
# --------------------------------------------------------------------------- #

def test_a_busy_job_is_retried_without_burning_an_attempt():
    from app.services import uploads

    outcome = chapter_queue_worker.classify_exception(
        uploads.JobAlreadyRunningError("busy"),
    )
    assert outcome["state"] == "retry"
    assert outcome["refund_attempt"] is True


def test_a_full_volume_blocks_rather_than_retrying():
    from app.services import storage_capacity

    outcome = chapter_queue_worker.classify_exception(
        storage_capacity.StorageCapacityError("no room", phase="master_batch"),
    )
    assert outcome["state"] == "blocked"
    assert outcome["blocked_kind"] == "storage_capacity"


def test_a_publication_order_conflict_blocks_with_its_reason():
    from app.services import master_review

    outcome = chapter_queue_worker.classify_exception(
        master_review.MasterReviewConflict("Publish the post Concept file first"),
    )
    assert outcome["state"] == "blocked"
    assert outcome["blocked_kind"] == "publication_order"
    assert "Concept file first" in outcome["error"]


def _only_this_task(db):
    """Drop committed leftovers so a worker pass claims only this test's row.

    ``_dispatch_once`` claims every claimable task, which is correct: the queue
    is global. Tests that commit a queued task therefore have to clear the line
    first.
    """
    db.query(models.ChapterBatchTask).delete()
    db.commit()



# --------------------------------------------------------------------------- #
# The worker loop, with the engine stubbed out
# --------------------------------------------------------------------------- #

def test_the_worker_claims_runs_and_settles_a_row(session, monkeypatch):
    """One pass end to end: claim, run, settle, and the row reads done."""
    _only_this_task(session)
    chapter = _chapter(session, code="10CBMA_T30")
    job = _job(session, status="converted")
    row = _row(session, chapter, job)
    session.add(models.ChapterBatchTask(
        batch_row_id=row.id, kind="step01", state="queued", lanes=[],
    ))
    session.commit()

    ran: list[int] = []

    def fake_runner(db, task):
        ran.append(int(task.id))
        # The engine would have written the marker; stand in for it so the
        # row's own derivation is what the assertion reads.
        bound = db.get(models.ChapterBatchRow, int(task.batch_row_id))
        target = db.get(models.UploadJob, int(bound.job_id))
        target.question_inventory = _marker(release_svc.CONCEPT_REVIEW_PENDING)
        db.commit()
        return {"state": "done"}

    worker = chapter_queue_worker.ChapterQueueWorker(
        SessionLocal, runner=fake_runner,
    )
    started = worker._dispatch_once()
    assert started == 1
    for _ in range(200):
        if not worker._in_flight_ids():
            break
        import time as _time

        _time.sleep(0.01)
    assert ran

    session.expire_all()
    settled = session.query(models.ChapterBatchTask).filter(
        models.ChapterBatchTask.batch_row_id == row.id).one()
    assert settled.state == "done"
    projected = chapter_batches.project_one(session, chapter.id)
    # The TASK is done. The CHAPTER is waiting for a person — which is what
    # Step 01 finishing actually means.
    assert projected["state"] == "concept_review"
    assert projected["can"]["step02"] is True


def test_a_retryable_failure_goes_back_in_line_until_the_budget_is_spent(
    session,
):
    _only_this_task(session)
    chapter = _chapter(session, code="10CBMA_T31")
    job = _job(session, status="converted")
    row = _row(session, chapter, job)
    session.add(models.ChapterBatchTask(
        batch_row_id=row.id, kind="step01", state="queued", lanes=[],
        max_attempts=2,
    ))
    session.commit()

    def always_fails(db, task):
        raise RuntimeError("the provider timed out")

    worker = chapter_queue_worker.ChapterQueueWorker(
        SessionLocal, runner=always_fails,
    )
    import time as _time

    for _ in range(3):
        worker._dispatch_once()
        for _ in range(200):
            if not worker._in_flight_ids():
                break
            _time.sleep(0.01)

    session.expire_all()
    settled = session.query(models.ChapterBatchTask).filter(
        models.ChapterBatchTask.batch_row_id == row.id).one()
    assert settled.attempt == 2
    assert settled.state == "failed"
    assert "timed out" in settled.last_error
    projected = chapter_batches.project_one(session, chapter.id)
    assert projected["state"] == "failed"
    assert projected["can"]["retry"] is True


def test_a_blocked_step_never_burns_another_attempt(session):
    from app.services import storage_capacity

    _only_this_task(session)
    chapter = _chapter(session, code="10CBMA_T32")
    job = _job(session, status="converted")
    row = _row(session, chapter, job)
    session.add(models.ChapterBatchTask(
        batch_row_id=row.id, kind="step01", state="queued", lanes=[],
        max_attempts=2,
    ))
    session.commit()

    def full_volume(db, task):
        raise storage_capacity.StorageCapacityError(
            "the volume is full", phase="master_batch",
        )

    worker = chapter_queue_worker.ChapterQueueWorker(
        SessionLocal, runner=full_volume,
    )
    import time as _time

    for _ in range(3):
        worker._dispatch_once()
        for _ in range(200):
            if not worker._in_flight_ids():
                break
            _time.sleep(0.01)

    session.expire_all()
    settled = session.query(models.ChapterBatchTask).filter(
        models.ChapterBatchTask.batch_row_id == row.id).one()
    assert settled.state == "blocked"
    assert settled.blocked_kind == "storage_capacity"
    # One claim, one attempt: a blocked row waits for a person, it does not
    # spend its way to failure.
    assert settled.attempt == 1


def test_a_filtered_page_reports_a_true_total(session):
    """The filter counts every match, not just the first page's worth."""
    _only_this_task(session)
    for index in range(7):
        chapter = _chapter(session, code=f"10CBMA_F{index:02d}")
        job = _job(session, inventory=_marker(release_svc.CONCEPT_REVIEW_PENDING))
        _row(session, chapter, job)
    session.commit()

    page = chapter_batches.list_page(
        session, state="concept_review", page=1, page_size=2,
    )
    assert page["total"] >= 7
    assert len(page["items"]) == 2
    assert page["total_pages"] == (page["total"] + 1) // 2
    assert all(row["state"] == "concept_review" for row in page["items"])


def test_the_worker_starts_and_stops_and_reports_itself(monkeypatch):
    """It is off in tests by default; this pins that it still comes up."""
    monkeypatch.setenv("AEGIS_QUEUE_WORKER", "1")
    chapter_queue_worker.shutdown_chapter_queue()
    worker = chapter_queue_worker.initialize_chapter_queue(SessionLocal)
    try:
        assert worker is not None
        assert chapter_queue_worker.worker_alive() is True
        status = worker.status()
        assert status["alive"] is True
        assert status["capacity"] >= 1
    finally:
        chapter_queue_worker.shutdown_chapter_queue()
    assert chapter_queue_worker.worker_alive() is False


def test_the_worker_stays_off_when_the_deployment_disables_it(monkeypatch):
    monkeypatch.setenv("AEGIS_QUEUE_WORKER", "0")
    chapter_queue_worker.shutdown_chapter_queue()
    assert chapter_queue_worker.initialize_chapter_queue(SessionLocal) is None
    assert chapter_queue_worker.worker_alive() is False
