"""Restart recovery adopts paid interactive work without changing its target."""
import copy
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import models
from app.services import build_concepts_release as release
from app.services import run_recovery as recovery, run_state, uploads


@pytest.fixture()
def session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'recovery.db'}")
    models.Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    try:
        yield db
    finally:
        db.close()
        engine.dispose()


def chapter(db, code="saved-code"):
    value = models.Chapter(chapter_code=code, board="CBSE", grade="09",
                           subject="Social Science", chapter_title="Saved title")
    db.add(value)
    db.flush()
    return value


def job(db, target, *, master=False):
    value = models.UploadJob(
        owner_sub="google:original-owner", started_by_sub="google:starter",
        started_by_email="starter@up.school", module="build_concepts",
        learning_kind="post", filename="original-source.pdf", source_book="NCERT",
        status="concept_review" if master else "converted", mmd_text="untouched source",
        run_id="paid-run", run_state=run_state.new(run_id="paid-run", stage="Concepts", progress=0.44),
        generation_checkpoint={"target_chapter_id": target.id, "stage": "saved-stage", "frozen": "paid decision"},
        question_inventory={"paid": {"verdict": "kept"}},
        openai_usage={"total_tokens": 99},
    )
    if master:
        value.question_inventory = {**value.question_inventory, release.CONCEPT_REVIEW_KEY: {
            "version": release.CONCEPT_REVIEW_VERSION, "status": release.CONCEPT_REVIEW_MASTER_BUILDING,
            "target_chapter_id": target.id, "reviewed_lanes": ["post"],
            "required_lanes": ["post"], "master_started_at": "2026-09-15T09:00:00Z",
        }}
    db.add(value)
    db.commit()
    return value


@pytest.mark.parametrize("master", [False, True])
@pytest.mark.parametrize("execution_mode", ["", "synchronous", "batch", "legacy-unknown"])
def test_recovers_same_job_once_with_recorded_transport_and_evidence(session, master, execution_mode):
    target = chapter(session)
    original = job(session, target, master=master)
    original.execution_mode = execution_mode
    session.commit()
    evidence = copy.deepcopy((original.generation_checkpoint, original.openai_usage,
                              original.question_inventory, original.run_id,
                              original.owner_sub, original.source_book, original.mmd_text))
    assert recovery.recover_interrupted_runs(session) == [original.id]
    row = session.query(models.ChapterBatchRow).one()
    task = session.query(models.ChapterBatchTask).one()
    assert (row.chapter_id, row.job_id, task.job_id) == (target.id, original.id, original.id)
    assert task.kind == ("step02" if master else "step01")
    assert task.state == "queued" and task.attempt == 0
    assert task.push_group_id and len(task.push_group_id) <= 32
    assert task.cohort_id == (task.push_group_id if execution_mode == "batch" else "")
    assert original.execution_mode == execution_mode
    assert task.enqueued_by_email == "starter@up.school"
    assert row.source_filename == "original-source.pdf"
    assert original.generation_checkpoint == evidence[0]
    assert original.openai_usage == evidence[1]
    assert original.question_inventory["paid"] == evidence[2]["paid"]
    assert (original.run_id, original.owner_sub, original.source_book, original.mmd_text) == evidence[3:]
    assert original.run_state["progress"] == 0.44
    assert original.run_state["status"] == "waiting"
    if master:
        assert original.question_inventory[release.CONCEPT_REVIEW_KEY] == evidence[2][release.CONCEPT_REVIEW_KEY]
    assert recovery.recover_interrupted_runs(session) == []
    assert session.query(models.ChapterBatchTask).count() == 1


@pytest.mark.parametrize("marker_status", [release.CONCEPT_REVIEW_PENDING,
    release.CONCEPT_REVIEW_REVIEWED, release.CONCEPT_REVIEW_MASTER_READY,
    release.CONCEPT_REVIEW_MASTER_FAILED, release.CONCEPT_REVIEW_PUBLISHED])
def test_never_reopens_review_or_terminal_markers(session, marker_status):
    original = job(session, chapter(session), master=True)
    inventory = copy.deepcopy(original.question_inventory)
    inventory[release.CONCEPT_REVIEW_KEY]["status"] = marker_status
    original.question_inventory = inventory
    session.commit()
    assert recovery.recover_interrupted_runs(session) == []
    assert original.question_inventory == inventory
    assert session.query(models.ChapterBatchTask).count() == 0


@pytest.mark.parametrize("condition", ["completed", "failed", "dead", "pending", "local_lock"])
def test_terminal_decisions_and_active_local_worker_are_not_restarted(session, monkeypatch, condition):
    original = job(session, chapter(session))
    if condition in {"completed", "failed"}:
        original.run_state = {**original.run_state, "status": condition}
    elif condition == "dead":
        original.question_inventory = {models.GENERATION_RECOVERY_INVENTORY_KEY: {"resume_allowed": False}}
    elif condition == "pending":
        original.generation_checkpoint = {**original.generation_checkpoint,
                                         "human_decisions": {"pending": {"kind": "source_review"}}}
    else:
        monkeypatch.setattr(uploads, "is_job_running", lambda _id: True)
    session.commit()
    assert recovery.recover_interrupted_runs(session) == []
    assert session.query(models.ChapterBatchTask).count() == 0


@pytest.mark.parametrize("state", ["queued", "leased", "blocked"])
def test_existing_tasks_and_foreign_leases_are_left_to_queue_owner(session, state):
    target = chapter(session)
    original = job(session, target, master=True)
    row = models.ChapterBatchRow(chapter_id=target.id, job_id=original.id)
    session.add(row)
    session.flush()
    task = models.ChapterBatchTask(batch_row_id=row.id, job_id=original.id,
        kind="step02", state=state, attempt=2, lease_owner="other-machine:worker",
        lease_expires_at=datetime.utcnow() + timedelta(minutes=4))
    session.add(task)
    session.commit()
    snapshot = (task.id, task.state, task.attempt, task.lease_owner, task.lease_expires_at)
    assert recovery.recover_interrupted_runs(session) == []
    assert (task.id, task.state, task.attempt, task.lease_owner, task.lease_expires_at) == snapshot
    assert release.concept_review_state(original)["status"] == release.CONCEPT_REVIEW_MASTER_BUILDING


def test_rebound_chapter_is_not_hijacked_by_old_interactive_job(session):
    target = chapter(session)
    original = job(session, target)
    replacement = models.UploadJob(module="build_concepts", filename="replacement.pdf", status="uploaded")
    session.add(replacement)
    session.flush()
    row = models.ChapterBatchRow(chapter_id=target.id, job_id=replacement.id,
                                source_filename="replacement.pdf", previous_job_ids=[original.id])
    session.add(row)
    session.commit()
    assert recovery.recover_interrupted_runs(session) == []
    assert row.job_id == replacement.id and row.previous_job_ids == [original.id]
    assert original.question_inventory[recovery.RECOVERY_REQUEST_KEY]["reason_code"] == "chapter_source_replaced"
    assert session.query(models.ChapterBatchTask).count() == 0


@pytest.mark.parametrize("kind", ["missing", "conflicting", "deleted"])
def test_target_must_be_one_existing_explicit_id(session, kind):
    target = chapter(session)
    original = job(session, target)
    if kind == "missing":
        original.generation_checkpoint = {}
    elif kind == "conflicting":
        original.deposit_scope_type = "chapter"
        original.deposit_scope_ids = [chapter(session, "another").id]
    else:
        session.delete(target)
    session.commit()
    assert recovery.recover_interrupted_runs(session) == []
    assert original.question_inventory[recovery.RECOVERY_REQUEST_KEY]["status"] == "blocked"
    assert session.query(models.ChapterBatchTask).count() == 0


def test_requested_chapter_recovers_before_first_source_checkpoint(session):
    target = chapter(session)
    original = job(session, target)
    original.generation_checkpoint = {}
    original.requested_chapter_id = target.id
    original.status = "uploaded"
    session.commit()
    assert recovery.recover_interrupted_runs(session) == [original.id]
    assert session.query(models.ChapterBatchTask).one().kind == "step01"


def test_unknown_starter_does_not_become_a_guessed_email(session):
    original = job(session, chapter(session))
    original.started_by_sub = ""
    original.started_by_email = ""
    session.commit()
    assert recovery.recover_interrupted_runs(session) == [original.id]
    assert session.query(models.ChapterBatchTask).one().enqueued_by_email == ""
    assert original.owner_sub == "google:original-owner"


def test_missing_master_boundary_never_turns_into_source_generation_on_next_boot(session):
    original = job(session, chapter(session))
    original.run_state = {**original.run_state, "status": "master"}
    session.commit()
    assert recovery.recover_interrupted_runs(session) == []
    assert original.question_inventory[recovery.RECOVERY_REQUEST_KEY]["reason_code"] == "review_boundary_missing"
    assert original.run_state["status"] == "waiting"
    assert recovery.recover_interrupted_runs(session) == []
    assert session.query(models.ChapterBatchTask).count() == 0
    assert original.question_inventory[recovery.RECOVERY_REQUEST_KEY]["reason_code"] == "review_boundary_missing"
