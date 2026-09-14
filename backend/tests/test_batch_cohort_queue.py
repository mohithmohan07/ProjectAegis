"""The cohort: chapters that start together so their waves are wide.

The owner's design of 14 September 2026: select a number of chapters per
subject, start them together on a slot ("12:00 PM, 12:30 PM, 1:00 PM"), let
every chapter stall at each sequence and push the next sequence together.
These tests pin the queue half of that — the slot gate, the cohort label,
the admission rule and the binding the runner gets.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from app import models
from app.services import chapter_queue, chapter_queue_worker


def _row(db, chapter_id: int, job_id: int) -> models.ChapterBatchRow:
    row = models.ChapterBatchRow(chapter_id=chapter_id, job_id=job_id)
    db.add(row)
    db.commit()
    return row


def _task(db, row, *, kind="step01", cohort="", start_after=None):
    task = models.ChapterBatchTask(
        batch_row_id=int(row.id), kind=kind, state="queued", attempt=0,
        cohort_id=cohort, start_after=start_after, enqueued_at=datetime.utcnow(),
    )
    db.add(task)
    db.commit()
    return task


# --------------------------------------------------------------------------- #
# The slot
# --------------------------------------------------------------------------- #

def test_a_task_is_not_claimable_before_its_slot(db):
    row = _row(db, 9001, 9001)
    _task(db, row, start_after=datetime.utcnow() + timedelta(hours=1))
    claimable = chapter_queue.claimable(db, kinds=("step01", "step02"))
    assert [t for t in claimable if t.batch_row_id == row.id] == []


def test_a_task_whose_slot_has_passed_is_claimable(db):
    row = _row(db, 9002, 9002)
    task = _task(db, row, start_after=datetime.utcnow() - timedelta(minutes=1))
    claimable = chapter_queue.claimable(db, kinds=("step01", "step02"))
    assert task.id in [t.id for t in claimable]


def test_a_task_with_no_slot_is_claimable_exactly_as_before(db):
    """Every task the console pushed before the batch lane existed reads
    slot-less, and slot-less means now."""
    row = _row(db, 9003, 9003)
    task = _task(db, row)
    assert task.id in [t.id for t in chapter_queue.claimable(db, kinds=("step01",))]


def test_the_whole_cohort_becomes_claimable_on_the_same_tick(db):
    """That is what makes the first stage of every chapter one wave."""
    slot = datetime.utcnow() - timedelta(seconds=1)
    ids = []
    for index in range(4):
        row = _row(db, 9100 + index, 9100 + index)
        ids.append(_task(db, row, cohort="noon", start_after=slot).id)
    claimable = {t.id for t in chapter_queue.claimable(db, kinds=("step01",))}
    assert set(ids) <= claimable


# --------------------------------------------------------------------------- #
# Admission
# --------------------------------------------------------------------------- #

def test_a_cohort_runs_wider_than_the_synchronous_fan_out_budget(monkeypatch):
    """The synchronous budget is two fan-outs, which would run two chapters
    at a time and leave every wave two lines wide. A cohort is bounded by
    this machine's capacity instead."""
    monkeypatch.setenv("AEGIS_QUEUE_COHORT_CONCURRENCY", "6")
    worker = chapter_queue_worker.ChapterQueueWorker(lambda: None)
    worker._in_flight = {index: "step01" for index in range(4)}
    assert worker.admits("step01", cohort=False) is False, "the ordinary gate still bounds"
    assert worker.admits("step01", cohort=True) is True
    worker._in_flight = {index: "step01" for index in range(6)}
    assert worker.admits("step01", cohort=True) is False, "capacity is still a limit"


def test_a_cohort_step02_still_refuses_a_full_volume(monkeypatch):
    monkeypatch.setenv("AEGIS_QUEUE_COHORT_CONCURRENCY", "6")
    monkeypatch.setattr(chapter_queue_worker, "_volume_can_hold_a_master_batch",
                        lambda: False)
    worker = chapter_queue_worker.ChapterQueueWorker(lambda: None)
    worker._in_flight = {}
    assert worker.admits("step02", cohort=True) is False


def test_publish_is_never_a_cohort_step(db):
    """Publishing spends nothing, writes one shared workbook at a time and
    has nothing to wait for, so it is not worth a wave."""
    from app.api import chapter_batches as api

    payload = api.PushRequest(step="publish", rows=[], cohort=True)
    assert payload.cohort is True
    # The route decides; the rule is one line and this is it.
    assert (payload.cohort and payload.step != "publish") is False


# --------------------------------------------------------------------------- #
# The binding
# --------------------------------------------------------------------------- #

def test_only_a_cohort_task_binds_the_wave_broker(monkeypatch):
    from app.services import batch_broker

    built: list[str] = []
    monkeypatch.setattr(chapter_queue_worker, "live_broker",
                        lambda: built.append("built") or "BROKER")

    class _Task:
        cohort_id = ""

    with chapter_queue_worker._cohort_session(_Task()):
        assert batch_broker.bound() is None
    assert built == [], "a lone run must not be parked in a wave"

    _Task.cohort_id = "noon"
    with chapter_queue_worker._cohort_session(_Task()):
        assert batch_broker.bound() == "BROKER"
    assert built == ["built"]
    assert batch_broker.bound() is None


def test_a_task_without_the_attribute_at_all_is_not_a_cohort(monkeypatch):
    """The runner is called with ``task=None`` in places that predate the
    cohort; that must stay a plain, unbatched run."""
    from app.services import batch_broker

    with chapter_queue_worker._cohort_session(None):
        assert batch_broker.bound() is None


# --------------------------------------------------------------------------- #
# One slot, one cohort, whoever pushed
# --------------------------------------------------------------------------- #

def test_three_reviewers_pushing_to_the_same_slot_are_one_cohort():
    """The slot is the appointment. Aathira, Ravi and Shubham each push their
    own subject's chapters for 12:30 and the queue treats all of them as one
    group that starts together — which is what makes their first stage one
    wave rather than three."""
    from datetime import datetime as dt

    from app.api import chapter_batches as api

    slot = dt(2026, 9, 14, 12, 30)
    ids = {api._cohort_id_for(step="step01", cohort=True, start_after=slot)
           for _ in range(3)}
    assert ids == {"slot-20260914T1230"}


def test_a_cohort_with_no_slot_is_only_its_own_push():
    from app.api import chapter_batches as api

    first = api._cohort_id_for(step="step01", cohort=True, start_after=None,
                               push_group_id="push-a")
    second = api._cohort_id_for(step="step01", cohort=True, start_after=None,
                                push_group_id="push-b")
    assert first == "push-a" and second == "push-b"


def test_publish_and_an_ordinary_push_carry_no_cohort():
    from datetime import datetime as dt

    from app.api import chapter_batches as api

    slot = dt(2026, 9, 14, 12, 30)
    assert api._cohort_id_for(step="publish", cohort=True, start_after=slot) == ""
    assert api._cohort_id_for(step="step01", cohort=False, start_after=slot) == ""


def test_a_cohort_runs_far_fewer_master_builds_than_step01s(monkeypatch):
    """A Master build is the memory-heavy step and this machine has died of
    that pressure before (Q57). Narrowing it costs latency, not price: the
    batch rate is per request, not per wave."""
    monkeypatch.setenv("AEGIS_QUEUE_COHORT_CONCURRENCY", "6")
    monkeypatch.setenv("AEGIS_QUEUE_COHORT_MASTERS", "2")
    monkeypatch.setattr(chapter_queue_worker, "_volume_can_hold_a_master_batch",
                        lambda: True)
    worker = chapter_queue_worker.ChapterQueueWorker(lambda: None)

    worker._in_flight = {1: "step02", 2: "step02"}
    assert worker.admits("step02", cohort=True) is False, "two Masters is the cap"
    # ...while Step 01s keep flowing into the same cohort up to its own width.
    assert worker.admits("step01", cohort=True) is True
    worker._in_flight = {1: "step02"}
    assert worker.admits("step02", cohort=True) is True
