"""The batch console, unattended: what the Q64 audit left open (Q66).

Each test reproduces one verified finding from
`docs/chapter-batch-console-audit-2026-09-13.md` against the real derivation,
worker and queue code, then pins the fix:

* the three pre-spend pauses name themselves on the row (contract section 6)
  and carry the question they recorded, not a generic sentence;
* a Step 01 that returned with a resumable ``run_incomplete`` marker goes back
  in line instead of settling as a clean ``done`` with no Concept files;
* a refunded lock collision on the LAST attempt is requeued, never recorded
  ``failed/attempts_exhausted`` for a step that never ran, and is held back one
  backoff interval instead of being re-claimed on the next poll;
* Step 02 is not admitted into a volume that cannot hold a Master batch;
* the reviewed-Concept upload is offered exactly where the route accepts it.
"""
from __future__ import annotations

import time

import pytest

from app import models
from app.db import SessionLocal
from app.services import build_concepts_release as release_svc
from app.services import chapter_batches, chapter_queue_worker, semantic_recovery
from tests.test_chapter_batch_console import (
    _chapter, _job, _marker, _only_this_task, _row, session,  # noqa: F401
)


# --------------------------------------------------------------------------- #
# The pauses name themselves
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    ("kind", "blocked_kind"),
    [
        ("phase3_source_graph_review", "source_review"),
        ("source_topic_coverage_review", "source_topic_recovery"),
        ("type_granularity_review", "type_granularity"),
        ("concept_critic_conflict", "human_decision"),
    ],
)
def test_each_pre_spend_pause_names_itself_on_the_row(session, kind, blocked_kind):
    chapter = _chapter(session, code=f"10CBMA_P{abs(hash(kind)) % 90 + 10}")
    job = _job(
        session,
        inventory=_marker(release_svc.CONCEPT_REVIEW_PENDING),
        checkpoint={"human_decisions": {"pending": {
            "decision_id": "d-1", "context_hash": "c-1", "kind": kind,
            "decision_question": "Keep every source topic separate?",
        }}},
    )
    _row(session, chapter, job)
    session.flush()

    projected = chapter_batches.project_one(session, chapter.id)
    assert projected["state"] == "blocked"
    assert projected["blocked_kind"] == blocked_kind
    # The question the pause wrote, not the generic sentence the old read
    # fell back to because it looked for keys no pause records.
    assert projected["blocked_reason"] == "Keep every source topic separate?"
    # The drawer's decision card reads the same recorded question.
    assert projected["pending_decision"]["kind"] == kind
    assert projected["pending_decision"]["question"] == (
        "Keep every source topic separate?"
    )


def test_a_raised_pause_is_classified_by_the_kind_it_recorded():
    exc = semantic_recovery.HumanDecisionRequired({
        "decision_id": "d-9", "context_hash": "c-9",
        "kind": "type_granularity_review",
        "decision_question": "Keep these distinct Types?",
    })
    outcome = chapter_queue_worker.classify_exception(exc)
    assert outcome["state"] == "blocked"
    assert outcome["blocked_kind"] == "type_granularity"


# --------------------------------------------------------------------------- #
# A run that did not complete is not done
# --------------------------------------------------------------------------- #

def _incomplete(*, resume_allowed: bool) -> dict:
    return {"run_incomplete": {
        "error": "APITimeoutError: request timed out",
        "message": "Generation did NOT complete: APITimeoutError: request timed out.",
        "resume_allowed": resume_allowed,
        "recovery_action": "resume_checkpoint" if resume_allowed else "reconvert_new_upload",
        "recovery": (
            "Re-run generation: it resumes from the saved checkpoint."
            if resume_allowed
            else "Start a new upload and conversion before generation."
        ),
    }}


def test_a_resumable_incomplete_step01_goes_back_in_line(session):
    job = _job(session, status="released")
    session.flush()

    outcome = chapter_queue_worker._after_generation(
        session, job.id, _incomplete(resume_allowed=True),
    )
    assert outcome["state"] == "retry"
    assert outcome["failure_code"] == "run_incomplete"
    assert "did NOT complete" in outcome["error"]


def test_a_non_resumable_incomplete_step01_is_over(session):
    job = _job(session, status="released")
    session.flush()

    outcome = chapter_queue_worker._after_generation(
        session, job.id, _incomplete(resume_allowed=False),
    )
    assert outcome["state"] == "failed"
    assert outcome["failure_code"] == "non_resumable"
    assert "new upload" in outcome["error"]


def test_a_pending_decision_outranks_the_incomplete_marker(session):
    # The wrapper turns HumanDecisionRequired into run_incomplete too; the
    # decision the run recorded before raising is what the row must show.
    job = _job(
        session, status="released",
        checkpoint={"human_decisions": {"pending": {
            "decision_id": "d-2", "context_hash": "c-2",
            "kind": "phase3_source_graph_review",
            "decision_question": "Choose one verified PDF evidence block.",
        }}},
    )
    session.flush()

    outcome = chapter_queue_worker._after_generation(
        session, job.id, _incomplete(resume_allowed=True),
    )
    assert outcome["state"] == "blocked"
    assert outcome["blocked_kind"] == "source_review"
    assert outcome["error"] == "Choose one verified PDF evidence block."


def test_a_clean_return_is_still_done(session):
    job = _job(
        session, status="released",
        inventory=_marker(release_svc.CONCEPT_REVIEW_PENDING),
    )
    session.flush()
    assert chapter_queue_worker._after_generation(session, job.id, {}) == {
        "state": "done",
    }


# --------------------------------------------------------------------------- #
# A refunded collision on the last attempt
# --------------------------------------------------------------------------- #

def _drain(worker):
    for _ in range(300):
        if not worker._in_flight_ids():
            return
        time.sleep(0.01)


def test_a_refunded_collision_on_the_last_attempt_is_requeued_not_failed(
    session, monkeypatch,
):
    _only_this_task(session)
    chapter = _chapter(session, code="10CBMA_U41")
    job = _job(session, status="converted")
    row = _row(session, chapter, job)
    task = models.ChapterBatchTask(
        batch_row_id=row.id, kind="step01", state="leased", lanes=[],
        attempt=2, max_attempts=2, lease_owner="worker:test",
    )
    session.add(task)
    session.commit()
    monkeypatch.setenv("AEGIS_QUEUE_COLLISION_BACKOFF_SECONDS", "30")

    ran: list[int] = []

    def runner(db, task):
        ran.append(int(task.id))
        return {"state": "done"}

    worker = chapter_queue_worker.ChapterQueueWorker(SessionLocal, runner=runner)
    worker._settle(session, task.id, {
        "state": "retry", "refund_attempt": True,
        "error": "another operation held this upload; it will be retried",
    })

    session.expire_all()
    settled = session.get(models.ChapterBatchTask, task.id)
    # Back in line with the attempt given back: a collision is not a try.
    assert settled.state == "queued"
    assert settled.attempt == 1
    assert settled.failure_code == ""
    assert "held this upload" in settled.last_error

    # Held back for one backoff interval, so the dispatcher does not claim
    # it again on the very next poll and spin against the same lock.
    assert worker._backoff_until[task.id] > time.time()
    assert worker._dispatch_once() == 0
    assert ran == []

    worker._backoff_until[task.id] = 0.0
    assert worker._dispatch_once() == 1
    _drain(worker)
    assert ran == [task.id]
    session.expire_all()
    assert session.get(models.ChapterBatchTask, task.id).state == "done"


# --------------------------------------------------------------------------- #
# Storage pre-check
# --------------------------------------------------------------------------- #

def test_step02_is_not_admitted_into_a_volume_that_cannot_hold_a_master_batch(
    monkeypatch,
):
    worker = chapter_queue_worker.ChapterQueueWorker(SessionLocal)
    monkeypatch.setenv("AEGIS_QUEUE_MAX_CONCURRENT_RUNS", "4")
    monkeypatch.setenv("AEGIS_QUEUE_MAX_CONCURRENT_MASTERS", "2")
    monkeypatch.setattr(
        chapter_queue_worker.config, "OPENAI_MAX_CONCURRENCY", 64, raising=False,
    )
    monkeypatch.setattr(
        chapter_queue_worker.config, "phase3_decision_workers", lambda: 16,
    )
    monkeypatch.setattr(
        chapter_queue_worker, "_volume_can_hold_a_master_batch", lambda: False,
    )
    # Step 01 writes no Master and is unaffected; Step 02 waits in the queue
    # rather than claiming, charging an attempt and failing inside the
    # batch reservation.
    assert worker.admits("step01") is True
    assert worker.admits("step02") is False

    monkeypatch.setattr(
        chapter_queue_worker, "_volume_can_hold_a_master_batch", lambda: True,
    )
    assert worker.admits("step02") is True


# --------------------------------------------------------------------------- #
# The Concept upload is offered exactly where the route accepts it
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    ("status", "concept", "master"),
    [
        (release_svc.CONCEPT_REVIEW_PENDING, True, False),
        (release_svc.CONCEPT_REVIEW_REVIEWED, True, False),
        (release_svc.CONCEPT_REVIEW_MASTER_READY, False, True),
        (release_svc.CONCEPT_REVIEW_PUBLISHED, False, True),
    ],
)
def test_the_concept_upload_is_offered_only_where_the_route_accepts_it(
    status, concept, master,
):
    signals = {"status": "released", "marker": {"status": status}}
    can = chapter_batches._can("master_review", signals, None, [])
    assert can["upload_concept"] is concept
    assert can["upload_master"] is master
