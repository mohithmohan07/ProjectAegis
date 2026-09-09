"""Same-run timing/progress invariants for the Concept review handoff."""
from __future__ import annotations

import copy
import hashlib
import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import URL
from sqlalchemy.orm import sessionmaker

from app import models
from app import db as db_module
from app.services import checkpoints, progress, run_journal, uploads
from app.services import openai_usage, run_state


def test_review_pause_resume_rebases_open_intervals_and_freezes_completion():
    state = run_state.new(now=100.0, stage="Concept extraction", progress=0.20)

    # A read is observational; repeated reads at the same instant are stable.
    assert run_state.snapshot(state, now=105.0)["active_elapsed_seconds"] == 5.0
    assert run_state.snapshot(state, now=105.0)["active_elapsed_seconds"] == 5.0

    state = run_state.stage(state, "Concept files", progress=0.70, now=110.0)
    assert run_state.snapshot(state, now=115.0)["active_elapsed_seconds"] == 15.0
    # A lower stage update cannot move a run backward.
    state = run_state.progress(state, 0.40, label="Concept files", now=120.0)
    assert state["progress"] == pytest.approx(0.70)

    state = run_state.pause_for_review(
        state, progress=0.70, stage="Concept files ready for review", now=120.0,
    )
    assert state["stage_history"][-1]["stage"] == "Concept files ready for review"
    waiting = run_state.snapshot(state, now=220.0)
    assert waiting["active_elapsed_seconds"] == pytest.approx(20.0)
    assert waiting["review_wait_seconds"] == pytest.approx(100.0)
    assert waiting["wall_elapsed_seconds"] == pytest.approx(120.0)

    state = run_state.resume(
        state, stage="Building Master files", progress=0.75, now=230.0,
    )
    assert state["stage_history"][-1]["stage"] == "Building Master files"
    running = run_state.snapshot(state, now=235.0)
    assert running["active_elapsed_seconds"] == pytest.approx(25.0)
    assert running["review_wait_seconds"] == pytest.approx(110.0)
    assert running["wall_elapsed_seconds"] == pytest.approx(135.0)

    state = run_state.finish(state, progress=1.0, now=240.0)
    complete = run_state.snapshot(state, now=999.0)
    assert complete["active_elapsed_seconds"] == pytest.approx(30.0)
    assert complete["review_wait_seconds"] == pytest.approx(110.0)
    assert complete["wall_elapsed_seconds"] == pytest.approx(140.0)
    assert run_state.snapshot(state, now=999.0) == complete


def test_failed_review_retry_reopens_wall_clock_without_losing_history():
    state = run_state.new(now=100.0, stage="Concept files", progress=0.70)
    state = run_state.finish(
        state, now=110.0, progress=0.70, stage="failed", status="failed",
    )
    state = run_state.pause_for_review(
        state, now=120.0, progress=0.70, stage="Upload failed; awaiting retry",
    )
    waiting = run_state.snapshot(state, now=220.0)
    assert waiting["active_elapsed_seconds"] == pytest.approx(10.0)
    assert waiting["review_wait_seconds"] == pytest.approx(100.0)
    assert waiting["wall_elapsed_seconds"] == pytest.approx(120.0)
    state = run_state.resume(
        state, now=230.0, stage="Building Master files", progress=0.70,
    )
    running = run_state.snapshot(state, now=235.0)
    assert running["active_elapsed_seconds"] == pytest.approx(15.0)
    assert running["review_wait_seconds"] == pytest.approx(110.0)
    assert running["wall_elapsed_seconds"] == pytest.approx(135.0)


def test_usage_summary_can_join_durable_run_timing_without_repricing():
    state = run_state.new(now=100.0, stage="Concepts", progress=0.5)
    state = run_state.pause_for_review(state, now=110.0)
    current = openai_usage.UsageAccumulator()
    before = current.summary()
    joined = openai_usage.apply_run_timing(
        copy.deepcopy(before), state, now=210.0,
    )
    assert joined["estimated_cost_usd"] == 0
    assert joined["active_elapsed_seconds"] == pytest.approx(10.0)
    assert joined["review_wait_seconds"] == pytest.approx(100.0)
    assert joined["wall_elapsed_seconds"] == pytest.approx(110.0)
    assert joined["elapsed_seconds"] == joined["active_elapsed_seconds"]


def test_fixed_progress_allocation_contains_terminal_concept_values():
    try:
        with progress.fixed_allocation(0.0, 0.70):
            progress.set_progress(0.98, label="legacy Concept terminal value")
            assert progress.current_value() == pytest.approx(0.686)
        # The allocation is scoped to the Concept stage; a later explicit
        # review boundary can advance to exactly its 70% handoff value.
        progress.set_progress(0.70, label="Concept files ready for review")
        assert progress.current_value() == pytest.approx(0.70)
    finally:
        progress._progress_floor.set(0.0)


def test_materialized_timing_is_stable_across_repeated_persistence():
    state = run_state.new(now=100.0, stage="Concepts", progress=0.5)
    at_pause = run_state.pause_for_review(state, now=110.0)
    persisted = run_state.materialize(at_pause, now=210.0)
    assert persisted["review_wait_seconds"] == pytest.approx(100.0)
    # The review anchor was rebased when the interval was folded in. A later
    # checkpoint/read adds only the newly elapsed waiting time.
    assert run_state.snapshot(persisted, now=220.0)["review_wait_seconds"] == pytest.approx(110.0)
    persisted_again = run_state.materialize(persisted, now=220.0)
    assert persisted_again["review_wait_seconds"] == pytest.approx(110.0)
    assert run_state.snapshot(persisted_again, now=220.0)["review_wait_seconds"] == pytest.approx(110.0)


def test_init_db_migrates_run_columns_on_an_existing_upload_jobs_table(
    tmp_path, monkeypatch,
):
    """The additive migration must retain a pre-change upload table."""
    database_path = tmp_path / "legacy-aegis.sqlite3"
    legacy_engine = create_engine(URL.create("sqlite", database=str(database_path)))
    legacy_session = sessionmaker(bind=legacy_engine, autoflush=False)
    try:
        with legacy_engine.begin() as connection:
            connection.exec_driver_sql(
                "CREATE TABLE upload_jobs ("
                "id INTEGER PRIMARY KEY, module VARCHAR(32) NOT NULL, "
                "filename VARCHAR(255) DEFAULT ''"
                ")"
            )
            connection.exec_driver_sql(
                "INSERT INTO upload_jobs (id, module, filename) "
                "VALUES (1, 'build_concepts', 'legacy.mmd')"
            )
        monkeypatch.setattr(db_module, "DB_URL", "sqlite:///legacy-aegis.sqlite3")
        monkeypatch.setattr(db_module, "engine", legacy_engine)
        monkeypatch.setattr(db_module, "SessionLocal", legacy_session)
        db_module.init_db()
        with legacy_engine.connect() as connection:
            columns = {
                row[1]
                for row in connection.exec_driver_sql("PRAGMA table_info(upload_jobs)")
            }
            row = connection.exec_driver_sql(
                "SELECT module, filename, run_id, run_state "
                "FROM upload_jobs WHERE id = 1"
            ).one()
        assert {"run_id", "run_state"}.issubset(columns)
        assert row[0:2] == ("build_concepts", "legacy.mmd")
        assert row[2] == ""
        assert row[3] == "{}"
    finally:
        legacy_engine.dispose()


def test_checkpoint_resume_keeps_run_identity_usage_and_journal_cursor(db):
    state = run_state.new(now=100.0, stage="Concept files", progress=0.70)
    state = run_state.pause_for_review(state, now=110.0, progress=0.70)
    state = run_state.materialize(state, now=210.0)
    job = models.UploadJob(
        module="build_concepts",
        upload_type="document",
        learning_kind="post",
        filename="reviewed.mmd",
        mmd_text="# Reviewed Concept source",
        status="converted",
        question_inventory={"items": [], "stats": {}},
        generation_checkpoint={},
        generation_log=[{
            "type": "log", "level": "info",
            "message": "Concept files ready", "ts": 110.0,
        }],
        openai_usage={
            "model": "gpt-4o",
            "request_count": 1,
            "input_tokens": 10,
            "cached_input_tokens": 0,
            "output_tokens": 5,
            "reasoning_tokens": 0,
            "total_tokens": 15,
            "estimated_cost_usd": 0.0123,
        },
        run_id=state["run_id"],
        run_state=state,
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    _, raw_bundle = checkpoints.export_bundle(db, job.id)
    bundle = json.loads(raw_bundle)
    assert bundle["payload"]["job"]["run_id"] == state["run_id"]
    assert bundle["payload"]["run_state"]["run_id"] == state["run_id"]
    mismatched = copy.deepcopy(bundle)
    mismatched["payload"]["job"]["run_id"] = "different-run"
    mismatched["payload_sha256"] = hashlib.sha256(
        checkpoints._json_bytes(mismatched["payload"])
    ).hexdigest()
    with pytest.raises(ValueError, match="does not match"):
        checkpoints._read_bundle(
            json.dumps(mismatched, separators=(",", ":")).encode()
        )
    restored = checkpoints.import_bundle(db, raw_bundle)
    assert restored.run_id == state["run_id"]
    assert restored.run_state["status"] == "review"
    assert restored.run_state["review_wait_seconds"] == pytest.approx(100.0)
    assert restored.openai_usage["estimated_cost_usd"] == pytest.approx(0.0123)

    # A resumed stream can append to the restored job's journal while keeping
    # one cursor for the same run. This is what lets reconnects/reuploads
    # replay in order without re-emitting sequence 1 or charging a receipt.
    first = run_journal.RunJournal(restored.id)
    first.publish({"type": "log", "message": "Master started"}, lambda _: None)
    first.close()
    resumed = run_journal.RunJournal(restored.id, continue_existing=True)
    resumed.publish({"type": "result", "data": {"ok": True}}, lambda _: None)
    resumed.close()
    events = run_journal.read_after(restored.id, 0)["events"]
    assert [event["seq"] for event in events] == [1, 2]
    assert [event["type"] for event in events] == ["log", "result"]


def test_upload_run_helpers_resume_review_as_one_run_without_billing_wait(
    db, monkeypatch,
):
    # Normalization samples the clock once and the transition samples it once
    # more; keep both observations at each deterministic boundary.
    clock = [100.0, 100.0, 110.0, 110.0, 210.0]
    monkeypatch.setattr(
        run_state, "_now", lambda: clock.pop(0) if clock else 210.0,
    )
    job = models.UploadJob(
        module="build_concepts",
        upload_type="document",
        learning_kind="post",
        filename="concepts.mmd",
        mmd_text="# Concept source",
        status="converted",
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    started = uploads.start_or_resume_run(
        db, job.id, stage="Concept authoring", progress_value=0.5,
    )
    paused = uploads.pause_run_for_review(
        db, job.id, stage="Concept files ready for review", progress_value=0.7,
    )
    resumed = uploads.start_or_resume_run(
        db, job.id, stage="Building Master files", progress_value=0.75,
    )
    assert resumed["run_id"] == started["run_id"] == paused["run_id"]
    assert resumed["status"] == "master"
    assert resumed["progress"] == pytest.approx(0.75)
    assert resumed["review_wait_seconds"] == pytest.approx(100.0)
    assert resumed["active_elapsed_seconds"] == pytest.approx(10.0)
    db.refresh(job)
    assert job.run_id == started["run_id"]
    assert job.run_state["review_wait_seconds"] == pytest.approx(100.0)
