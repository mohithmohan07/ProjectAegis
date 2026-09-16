"""The owner's seven decided options, 13 September 2026 (Q65).

"No, go with the best suitable option for all, dont deploy yet."

1. Step 02 admission — the worker refuses to start below the gate it needs,
   naming the arithmetic; the dispatcher skips an inadmissible task instead
   of stopping the scan, and holds that task's cost back so later cheaper
   tasks cannot starve it.
2. Stranded rows — a startup sweep restores the Concept-review marker Step 01
   owed a batch-pushed run before Q64.
3. Karnataka stays the Board label (nothing to test).
4. Option A — Activity/Info Hub notes are separated by a break, not a space.
5. Keyword weights — the marks >= 0.5 x keywords coupling is named in the
   defect text and stated to the authoring and marking models.
6. KaTeX explanation — the exact answer may open the explanation as a
   [Katex] span whatever medium the answer cell declared.
7. 98% — the Master build band ends at 0.98 so 99% means finished-incomplete
   and nothing else.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from app import models
from app.services import assessment_release as rel
from app.services import build_concepts_release as release
from app.services import chapter_queue_worker as worker_mod
from app.services import concept_refiner as cr
from app.services import build_concepts_release_contract as contract
from app.services import reviewed_file_workflow_policy as workflow
from tests.test_independent_reviewed_files import setup_job
from tests.test_owner_column_spec import _profile


def _v2_job(db):
    """A staged run stamped with the V2 workflow, as every new run is."""
    with workflow.bind_run(workflow.V2):
        return setup_job(db)


# --------------------------------------------------------------------------- #
# 1. admission
# --------------------------------------------------------------------------- #

def test_a_low_synchronous_gate_does_not_disable_the_batch_worker(monkeypatch):
    monkeypatch.setattr(worker_mod.config, "OPENAI_MAX_CONCURRENCY", 8, raising=False)
    monkeypatch.setattr(worker_mod.config, "phase3_decision_workers", lambda: 6)
    monkeypatch.delenv("AEGIS_QUEUE_PROVIDER_RESERVE", raising=False)
    monkeypatch.delenv("AEGIS_QUEUE_WORKER", raising=False)
    monkeypatch.setattr(worker_mod, "_worker", None)

    reason = worker_mod.admission_shortfall()
    assert "AEGIS_OPENAI_MAX_CONCURRENCY=8" in reason
    assert "at least 28" in reason           # 16 reserve + 6 workers x cost 2

    monkeypatch.setattr(worker_mod.ChapterQueueWorker, "start", lambda self: None)
    monkeypatch.setattr(worker_mod, "_recover_batch_waves", lambda: 0)
    started = worker_mod.initialize_chapter_queue(lambda: None)
    assert started is not None
    assert started.admits("step01", cohort=True)
    assert not started.admits("step01", cohort=False)
    assert worker_mod.worker_alive() is False
    assert worker_mod.disabled_reason() == ""
    assert worker_mod.capacity_details()["synchronous_admission_reason"] == reason


def test_the_production_gate_has_no_shortfall(monkeypatch):
    monkeypatch.setattr(worker_mod.config, "OPENAI_MAX_CONCURRENCY", 48, raising=False)
    monkeypatch.setattr(worker_mod.config, "phase3_decision_workers", lambda: 16)
    monkeypatch.delenv("AEGIS_QUEUE_PROVIDER_RESERVE", raising=False)
    assert worker_mod.admission_shortfall() == ""


def test_an_older_task_holds_its_cost_back_from_later_ones(monkeypatch):
    """[step02, step01] with one step01 in flight: nothing cuts in front."""
    worker = worker_mod.ChapterQueueWorker(lambda: None)
    monkeypatch.setattr(worker_mod.config, "OPENAI_MAX_CONCURRENCY", 48, raising=False)
    monkeypatch.setattr(worker_mod.config, "phase3_decision_workers", lambda: 16)
    monkeypatch.delenv("AEGIS_QUEUE_PROVIDER_RESERVE", raising=False)
    monkeypatch.setenv("AEGIS_QUEUE_MAX_CONCURRENT_RUNS", "4")
    assert worker._provider_budget() == 2

    worker._in_flight = {1: "step01"}          # spent 1 of 2
    assert worker.admits("step02") is False    # needs 2 more
    # A later step01 fits on its own...
    assert worker.admits("step01") is True
    # ...but not once the older step02's cost is held back for it.
    assert worker.admits("step01", reserved=2) is False


# --------------------------------------------------------------------------- #
# 2. stranded rows
# --------------------------------------------------------------------------- #

def test_a_markerless_batch_run_gets_its_review_marker_back(db):
    job = _v2_job(db)
    # Undo what the fixture's initialize_concept_review wrote, leaving exactly
    # the state the old queue Step 01 produced: a staged V2 release, no marker.
    durable = dict(job.question_inventory or {})
    durable.pop(release.CONCEPT_REVIEW_KEY, None)
    job.question_inventory = durable
    job.status = "released"
    db.add(models.ChapterBatchRow(
        chapter_id=int(release.release_payload(job)["target_chapter_id"]),
        job_id=job.id,
    ))
    db.commit()
    assert release.concept_review_state(job) == {}

    healed = release.sweep_markerless_batch_runs(db)

    assert healed == [job.id]
    db.refresh(job)
    assert release.concept_review_state(job).get("status") == release.CONCEPT_REVIEW_PENDING
    assert job.status == "concept_review"
    # Idempotent: a second boot changes nothing.
    assert release.sweep_markerless_batch_runs(db) == []


def test_the_sweep_leaves_jobs_that_are_not_on_a_batch_row_alone(db):
    job = _v2_job(db)
    durable = dict(job.question_inventory or {})
    durable.pop(release.CONCEPT_REVIEW_KEY, None)
    job.question_inventory = durable
    job.status = "released"
    db.commit()

    assert release.sweep_markerless_batch_runs(db) == []
    assert release.concept_review_state(job) == {}


# --------------------------------------------------------------------------- #
# 4. Option A
# --------------------------------------------------------------------------- #

def test_hub_notes_sit_on_their_own_lines():
    details = "Description: A lens.\nAchieving Mastery: Trace a ray."
    one = cr.append_activity_hub(details, "Figure — Fig. 5 – A portrait.")
    two = cr.append_activity_hub(one, "Figure — Fig. 6 – A press.")

    sections = dict(cr.split_sections(two))
    hub = sections["Activity/Info Hub"]
    assert hub == "Figure — Fig. 5 – A portrait.\nFigure — Fig. 6 – A press."
    # The section join is untouched: still " // ", never a newline.
    assert two.count(" // ") == 1
    # Idempotent on a repeat.
    assert cr.append_activity_hub(two, "Figure — Fig. 6 – A press.") == two


# --------------------------------------------------------------------------- #
# 5. keyword coupling
# --------------------------------------------------------------------------- #

def _descriptive_candidate(sub_marks: str, keyword_weights: list[str]) -> dict:
    return {
        "candidate_id": "CAND-1",
        "sheet_kind": "descriptive",
        "question": "Prove the triangles are similar.",
        "question_text": "Prove the triangles are similar.",
        "marks": sub_marks,
        "answer_restriction": "Open",
        "answers": [],
        "sub_questions": [{
            "text": "State the criterion.",
            "marks": sub_marks,
            "keywords": [
                {"answer_type": "Phrases", "keyword": f"point {i}", "weightage": w}
                for i, w in enumerate(keyword_weights, 1)
            ],
        }],
    }


def test_an_unsatisfiable_keyword_count_is_named_in_arithmetic():
    candidate = _descriptive_candidate("0.5", ["0.25", "0.25"])
    errors = rel.validate_candidate(candidate, _profile("Mathematics"))
    joined = "\n".join(errors)
    assert "has 2 keyword(s) but only 0.5 mark(s)" in joined
    assert "needs at least 1 mark(s)" in joined
    assert "no more than 1 keyword(s)" in joined


def test_a_satisfiable_keyword_count_says_nothing_about_coupling():
    candidate = _descriptive_candidate("1", ["0.5", "0.5"])
    errors = rel.validate_candidate(candidate, _profile("Mathematics"))
    assert not any("keyword(s) but only" in e for e in errors)


# --------------------------------------------------------------------------- #
# 6. KaTeX explanation
# --------------------------------------------------------------------------- #

def test_a_text_typed_latex_answer_may_open_the_explanation_as_a_katex_span():
    answers = [
        {"answer_type": "Text", "answer_content": r"\text{AB} \perp \text{CD}", "correct_answer": "Yes"},
        {"answer_type": "Text", "answer_content": r"\text{AB} \parallel \text{CD}", "correct_answer": "No"},
    ]
    wrapped = r"a) [Katex]\text{AB} \perp \text{CD}[/Katex] because the angle is 90°."
    assert rel.objective_explanation_defects(answers, wrapped, include_option_label=True) == []
    # The raw spelling is still accepted (the rich-text gate is what refuses it).
    raw = r"a) \text{AB} \perp \text{CD} because the angle is 90°."
    assert rel.objective_explanation_defects(answers, raw, include_option_label=True) == []
    # A different answer is still refused.
    wrong = r"a) [Katex]\text{AB} \parallel \text{CD}[/Katex] because..."
    assert rel.objective_explanation_defects(answers, wrong, include_option_label=True)


# --------------------------------------------------------------------------- #
# 7. 98%
# --------------------------------------------------------------------------- #

def test_the_master_band_ends_below_the_finished_incomplete_value():
    import inspect

    signature = inspect.signature(contract._build_master_siblings)
    assert signature.parameters["progress_end"].default == 0.98
    source = inspect.getsource(contract.build_review_masters)
    assert "progress_end=0.98" in source
    assert "1.0 if all_ready else 0.99" in source
