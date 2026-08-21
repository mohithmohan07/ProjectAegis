"""The Master-file unblockers, pinned.

Live failure 2026-08-20 (CH01 Three Dimensional Shapes, MSBSHSE): Output 02
died with "blueprint provides 0 cells for 78 generated question(s)" because
the supported API route passes no blueprint — only tests ever supplied
cells — and Output 04 died with "exactly one correct option required
(got 0)" because ONE candidate exhausted the materialize contract and took
the whole Master with it. Three fixes, each pinned here:

1. ``decide_generated_cells`` — one recorded model verdict per generated
   question; no external blueprint required (the concept-mapping lane
   depends on no blueprint, per the owner's steer).
2. The PRC → release concept_key translation is mechanical, through the
   snapshot's ``pre_concept_id`` projection.
3. A materialize obligation that exhausts bounded corrections AND The
   Fixer blocks ITSELF, recorded loudly; the Master ships the rest.
"""
from __future__ import annotations

import copy

import pytest

from app.services import assessment_cells as cells_mod
from app.services import assessment_materialization as materialization
from app.services import release_core
from app.services.phase3 import kernel

from tests.test_assessment_release_run import (
    OWNER,
    _chapter_with_concepts,
    _decision_context,
)
from tests.test_assessment_pre_release_lane import (
    _generated_authorities,
    _questions,
)
from tests.test_pre_release_lane_wiring import _both_lanes_job

ENVELOPE = "e" * 64

_PROFILE = {
    "name": "reference-1",
    "appears_in": "Practice",
    "sheet_kinds": ("objective", "descriptive"),
}

_CONCEPTS_BY_KEY = {
    "release:aaaaaaaaaaaaaaaaaaaa:0001": {
        "concept_key": "release:aaaaaaaaaaaaaaaaaaaa:0001",
        "pre_concept_id": "PRC-0001",
        "concept_title": "Counting to ten",
        "concept_details": "Description: names each number in order.",
    },
}


def _cell_verdict_author(payload):
    generated = payload["generated_question"]
    return {
        "pre_question_id": generated["pre_question_id"],
        "sheet_kind": "descriptive",
        # A member of the CMS's descriptive vocabulary — the cell checker
        # now gates question_category per sheet kind (owner review: "MCQ"
        # / "Multiple Choice" / "Multiple Choice Question" on siblings).
        "question_category": "Long Answer",
        "cognitive_skill": "Remember",
        "difficulty": "Less",
        "marks": 2,
        "rationale": "a recall prerequisite answered in a short phrase",
    }


def test_each_generated_question_gets_a_recorded_cell_verdict():
    dissent = {
        "verdict": "dissent",
        "confidence": 0.5,
        "issues": ["marks may be low for a two-part answer"],
    }
    cells = cells_mod.decide_generated_cells(
        _questions(2),
        meta={"subject": "Math", "grade": "6"},
        profile=_PROFILE,
        envelope_sha256=ENVELOPE,
        concept_records_by_key=copy.deepcopy(_CONCEPTS_BY_KEY),
        provider=_cell_verdict_author,
        critic=lambda payload: dict(dissent),
        store=kernel.DecisionStore(),
    )
    assert len(cells) == 2
    for cell in cells:
        # The judgment fields are the model's verdict...
        assert cell["sheet_kind"] == "descriptive"
        assert cell["cognitive_skill"] == "Remember"
        assert cell["marks"] == 2.0
        # ...the routing bind is the mechanical PRC -> concept_key join...
        assert cell["concept_key"] == "release:aaaaaaaaaaaaaaaaaaaa:0001"
        assert cell["accepted_source_qids"] == []
        # ...and the decision audit rides the cell: key + critic dissent.
        assert cell["authority"]["decision_key"]
        assert any("dissent" in flag for flag in cell["flags"])


def test_a_question_whose_concept_the_snapshot_dropped_is_refused_loudly():
    orphan = _questions(1, concept="PRC-9999")
    with pytest.raises(cells_mod.CellDecisionError) as raised:
        cells_mod.decide_generated_cells(
            orphan,
            meta={},
            profile=_PROFILE,
            envelope_sha256=ENVELOPE,
            concept_records_by_key=copy.deepcopy(_CONCEPTS_BY_KEY),
            provider=_cell_verdict_author,
            store=kernel.DecisionStore(),
        )
    assert "PRC-9999" in str(raised.value)
    assert orphan[0]["pre_question_id"] in str(raised.value)


def test_output_02_builds_with_no_blueprint_anywhere(db):
    """The live blocker itself: the supported route supplies no cells.

    ``run_pre_release_for_job`` with NO ``blueprint_cells`` must decide
    one cell per staged generated question and publish — the
    concept-mapping lane depends on no blueprint (owner steer,
    2026-08-20)."""

    from app.services import assessment_release_run as run

    chapter = _chapter_with_concepts(db)
    job = _both_lanes_job(db, chapter, questions=2)
    authorities, calls = _generated_authorities()
    cell_calls: list[dict] = []

    def counting_cell_author(payload):
        cell_calls.append(copy.deepcopy(payload))
        return _cell_verdict_author(payload)

    authorities["cells"] = (
        counting_cell_author,
        lambda payload: {"verdict": "verified", "confidence": 1.0,
                         "issues": []},
    )
    released = run.run_pre_release_for_job(
        db,
        job.id,
        owner_sub=OWNER,
        authorities=authorities,
        **_decision_context(),
    )
    assert released.payload["pre_post_learning"] == "Pre"
    assert len(cell_calls) == 2, "one recorded verdict per question"
    assert len(released.payload["candidates"]) == 2
    for cell in released.payload["blueprint_cells"]:
        assert cell["source_policy"] == "generate"
        # The recorded verdict's audit survives the binder (Q10: the
        # critic's dissent must never be overwritten by mechanics).
        assert cell["authority"]["decision_key"]


def test_one_impossible_question_blocks_itself_not_the_master(db):
    """Live failure two: one candidate that can never satisfy the
    materialize contract must not take the finished Master down."""

    from app.services import assessment_release_run as run

    chapter = _chapter_with_concepts(db)
    job = _both_lanes_job(db, chapter, questions=2)
    authorities, _ = _generated_authorities()
    healthy_author = authorities["materialize"][0]

    def failing_second(payload):
        generated = payload["blueprint_cell"]["generated_question"]
        if generated["question_id"] == "PRQ-0002":
            return {"nonsense": True}  # never satisfies the checker
        return healthy_author(payload)

    authorities["materialize"] = (
        failing_second, authorities["materialize"][1],
    )
    authorities["cells"] = (
        _cell_verdict_author,
        lambda payload: {"verdict": "verified", "confidence": 1.0,
                         "issues": []},
    )
    released = run.run_pre_release_for_job(
        db,
        job.id,
        owner_sub=OWNER,
        authorities=authorities,
        **_decision_context(),
    )
    payload = released.payload
    assert len(payload["candidates"]) == 1, "the sound question ships"
    blocked = payload["materialization_blocked"]
    assert len(blocked) == 1
    assert blocked[0]["assessment_eligibility"] == (
        materialization.BLOCKED_ELIGIBILITY
    )
    assert blocked[0]["flags"], "every defect is named"
    assert any(
        "Fixer" in flag for flag in blocked[0]["flags"]
    ), "the record says the Fixer was exhausted too"
    # The gap is user-visible: the release reads Ready-with-flags, never
    # a silent clean Ready.
    assert release_core.release_state(released) == (
        release_core.READY_WITH_FLAGS
    )


def test_materialize_candidates_returns_a_blocked_marker_in_place():
    """Unit pin for the seam itself: exact-once accounting holds with a
    blocked obligation answering for its slot."""

    cell = {
        "cell_id": "CELL-x",
        "sheet_kind": "descriptive",
        "question_category": "Short Answer",
        "cognitive_skill": "Remember",
        "difficulty": "Less",
        "marks": 2,
        "count": 1,
        "appears_in": ["Practice"],
        "accepted_source_qids": [],
        "source_policy": "generate",
        "generated_question": {
            "pre_question_id": "PRC-0001-PRQ-0001",
            "pre_concept_id": "PRC-0001",
            "question_id": "PRQ-0001",
            "question_text": "Count aloud from one to five.",
            "answer": "one two three four five",
            "rationale": "checks counting order",
        },
    }
    result = materialization.materialize_candidates(
        [(None, cell)],
        meta={"subject": "Math"},
        envelope_sha256=ENVELOPE,
        provider=lambda payload: {"nonsense": True},
        store=kernel.DecisionStore(),
    )
    assert result["accepted"] == 0
    assert result["flagged"] == 1
    assert result["zero_loss"]["holds"]
    marker = result["candidates"][0]
    assert marker["assessment_eligibility"] == (
        materialization.BLOCKED_ELIGIBILITY
    )
    assert marker["blueprint_cell_id"] == "CELL-x"
    assert marker["flags"]


def test_an_orphaned_generated_question_blocks_itself_not_the_master(db):
    """The run-level follow-up to the loud refusal above: a question whose
    home row the snapshot dropped is partitioned onto the blocked ledger
    BEFORE any cell verdict is paid for, and the Master ships the rest.
    (The direct ``decide_generated_cells`` contract is unchanged — a
    caller that skips the runner's partition still gets the named
    refusal.)"""

    from app.services import assessment_release_run as run
    from app.services import build_concepts_release as release

    chapter = _chapter_with_concepts(db)
    job = _both_lanes_job(db, chapter, questions=2)
    inventory = copy.deepcopy(job.question_inventory)
    questions = inventory[release.PRE_RELEASE_KEY]["generated_questions"]
    orphan_qid = str(questions[1]["pre_question_id"])
    questions[1]["pre_concept_id"] = "PRC-9999"
    job.question_inventory = inventory
    db.commit()

    authorities, _ = _generated_authorities()
    authorities["cells"] = (
        _cell_verdict_author,
        lambda payload: {"verdict": "verified", "confidence": 1.0,
                         "issues": []},
    )
    released = run.run_pre_release_for_job(
        db,
        job.id,
        owner_sub=OWNER,
        authorities=authorities,
        **_decision_context(),
    )
    payload = released.payload
    assert len(payload["candidates"]) == 1, "the routable question ships"
    blocked = payload["materialization_blocked"]
    assert len(blocked) == 1
    marker = blocked[0]
    assert marker["assessment_eligibility"] == (
        materialization.BLOCKED_ELIGIBILITY
    )
    assert marker["generated_question"]["pre_question_id"] == orphan_qid
    assert any("PRC-9999" in flag for flag in marker["flags"]), (
        "the defect names the missing snapshot row"
    )
    assert release_core.release_state(released) == (
        release_core.READY_WITH_FLAGS
    )


def test_an_ambiguous_pre_concept_id_never_routes_first_wins():
    """Two snapshot rows claiming one ``pre_concept_id`` is an ambiguity,
    refused by name — never resolved by iteration order."""

    concepts = {
        "release:aaaaaaaaaaaaaaaaaaaa:0001": {"pre_concept_id": "PRC-0001"},
        "release:aaaaaaaaaaaaaaaaaaaa:0002": {"pre_concept_id": "PRC-0001"},
        "release:aaaaaaaaaaaaaaaaaaaa:0003": {"pre_concept_id": "PRC-0002"},
    }
    mapping, ambiguous = cells_mod.concept_keys_by_pre_id(concepts)
    assert mapping == {"PRC-0002": "release:aaaaaaaaaaaaaaaaaaaa:0003"}
    assert set(ambiguous) == {"PRC-0001"}
    assert len(ambiguous["PRC-0001"]) == 2

    with pytest.raises(cells_mod.CellDecisionError) as raised:
        cells_mod.decide_generated_cells(
            _questions(1),
            meta={},
            profile=_PROFILE,
            envelope_sha256=ENVELOPE,
            concept_records_by_key=concepts,
            provider=_cell_verdict_author,
            store=kernel.DecisionStore(),
        )
    assert "ambiguous" in str(raised.value)
    assert "PRC-0001" in str(raised.value)
