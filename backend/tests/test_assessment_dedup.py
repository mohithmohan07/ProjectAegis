"""Q15 pinned: duplicate GENERATED questions removed by a recorded verdict.

Owner ruling, 21 Aug 2026 (job-65 Master review: T01_C01 Q02 and Q03 were
the same question re-worded): one survivor ships and the others are
REMOVED from the Master — a model verdict per pre-learning concept group,
recorded on the release under ``duplicates_removed`` with the survivor and
the reason, reviewable, never silent. The verdict runs BEFORE the cell
verdicts so a removed question costs nothing downstream, and the checker
is mechanics only: it refuses impossible citations, never judges sameness.
"""
from __future__ import annotations

import copy

import pytest

from app.services import assessment_dedup as dedup
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
from tests.test_generated_cell_decisions import _cell_verdict_author
from tests.test_pre_release_lane_wiring import _both_lanes_job

ENVELOPE = "e" * 64


def _no_duplicates(payload):
    return {
        "duplicate_sets": [],
        "confidence": 1.0,
        "rationale": "each question asks something distinct",
    }


def _second_duplicates_first(payload):
    ids = [q["pre_question_id"] for q in payload["questions"]]
    return {
        "duplicate_sets": [
            {
                "survivor_pre_question_id": ids[0],
                "removed": [
                    {
                        "pre_question_id": ids[1],
                        "reason": "the same count-aloud ask re-worded",
                    },
                ],
            },
        ],
        "confidence": 0.9,
        "rationale": "the second question re-words the first's exact ask",
    }


def _verified(payload):
    return {"verdict": "verified", "confidence": 1.0, "issues": []}


# --------------------------------------------------------------------------- #
# The verdict unit
# --------------------------------------------------------------------------- #

def test_every_declared_openai_purpose_in_the_app_is_a_known_purpose():
    """A live default naming an unknown purpose crashes at the first real
    call — and only there, because tests inject providers. Pin the whole
    app tree so the mistake (this module shipped ``purpose="assessment"``)
    cannot recur silently."""
    import pathlib
    import re

    from aegis_pipeline.openai_policy import REASONING_EFFORT_BY_PURPOSE

    app_root = pathlib.Path(dedup.__file__).resolve().parents[1]
    declared = {
        (path, match)
        for path in app_root.rglob("*.py")
        for match in re.findall(
            r'purpose="([^"]+)"', path.read_text(encoding="utf-8")
        )
    }
    unknown = {
        (str(path.relative_to(app_root)), purpose)
        for path, purpose in declared
        if purpose not in REASONING_EFFORT_BY_PURPOSE
    }
    assert unknown == set()

def test_no_duplicates_removes_nothing_and_keeps_order():
    questions = _questions(3)
    survivors, removed = dedup.decide_generated_duplicates(
        questions,
        concept_id="PRC-0001",
        concept_evidence={"concept_title": "Counting to ten"},
        meta={"subject": "Math"},
        envelope_sha256=ENVELOPE,
        provider=_no_duplicates,
        critic=_verified,
        store=kernel.DecisionStore(),
    )
    assert survivors == questions
    assert removed == []


def test_a_duplicate_is_removed_with_its_survivor_named():
    questions = _questions(3)
    survivors, removed = dedup.decide_generated_duplicates(
        questions,
        concept_id="PRC-0001",
        concept_evidence={"concept_title": "Counting to ten"},
        meta={"subject": "Math"},
        envelope_sha256=ENVELOPE,
        provider=_second_duplicates_first,
        critic=_verified,
        store=kernel.DecisionStore(),
    )
    # Input order survives the removal — no reordering, no renumbering.
    assert [q["pre_question_id"] for q in survivors] == [
        "PRC-0001-PRQ-0001", "PRC-0001-PRQ-0003",
    ]
    assert len(removed) == 1
    record = removed[0]
    assert record["pre_question_id"] == "PRC-0001-PRQ-0002"
    assert record["survivor_pre_question_id"] == "PRC-0001-PRQ-0001"
    assert record["pre_concept_id"] == "PRC-0001"
    assert record["reason"] == "the same count-aloud ask re-worded"
    # The full removed question rides the record: recorded exclusion,
    # never loss (R4).
    assert record["generated_question"]["question_text"]
    assert any("Q15" in flag for flag in record["flags"])


def test_a_group_of_one_costs_nothing():
    def never_called(payload):
        raise AssertionError("a single question is never judged")

    questions = _questions(1)
    survivors, removed = dedup.decide_generated_duplicates(
        questions,
        concept_id="PRC-0001",
        concept_evidence=None,
        meta={},
        envelope_sha256=ENVELOPE,
        provider=never_called,
        store=kernel.DecisionStore(),
    )
    assert survivors == questions
    assert removed == []


def test_critic_dissent_rides_the_removal_flags():
    def dissenting(payload):
        return {
            "verdict": "dissent",
            "confidence": 0.4,
            "issues": ["the removed question may test a different skill"],
        }

    _, removed = dedup.decide_generated_duplicates(
        _questions(2),
        concept_id="PRC-0001",
        concept_evidence=None,
        meta={},
        envelope_sha256=ENVELOPE,
        provider=_second_duplicates_first,
        critic=dissenting,
        store=kernel.DecisionStore(),
    )
    assert len(removed) == 1
    assert any("dissent" in flag for flag in removed[0]["flags"])


def test_the_checker_refuses_impossible_citations():
    """Mechanics only: membership, survivor-not-removed, reasons,
    at-most-one-set — never a judgment about sameness."""

    check = dedup._dedup_checker({"A", "B", "C"})
    defects = check({
        "duplicate_sets": [
            {
                "survivor_pre_question_id": "Z",
                "removed": [{"pre_question_id": "A", "reason": ""}],
            },
            {
                "survivor_pre_question_id": "B",
                "removed": [
                    {"pre_question_id": "B", "reason": "same"},
                    {"pre_question_id": "A", "reason": "same"},
                ],
            },
        ],
    })
    text = "\n".join(defects)
    assert "'Z' is not one of the supplied questions" in text
    assert "carries no reason" in text
    assert "cannot also be removed" in text
    assert "appear twice" in text
    # And a clean verdict passes with no defects at all.
    assert check({"duplicate_sets": []}) == []


def test_an_impossible_verdict_raises_without_a_fixer():
    def survivor_also_removed(payload):
        first = payload["questions"][0]["pre_question_id"]
        return {
            "duplicate_sets": [
                {
                    "survivor_pre_question_id": first,
                    "removed": [
                        {"pre_question_id": first, "reason": "itself"},
                    ],
                },
            ],
        }

    with pytest.raises(kernel.ContractError):
        dedup.decide_generated_duplicates(
            _questions(2),
            concept_id="PRC-0001",
            concept_evidence=None,
            meta={},
            envelope_sha256=ENVELOPE,
            provider=survivor_also_removed,
            store=kernel.DecisionStore(),
        )


# --------------------------------------------------------------------------- #
# The release wiring
# --------------------------------------------------------------------------- #

def test_a_duplicate_is_removed_before_any_cell_spend(db):
    """The run-level pin: the removed question never reaches a cell
    verdict, the survivor ships, the removal rides the payload under
    ``duplicates_removed``, and the release reads Ready-with-flags."""

    from app.services import assessment_release_run as run

    chapter = _chapter_with_concepts(db)
    job = _both_lanes_job(db, chapter, questions=2)
    authorities, _ = _generated_authorities()
    cell_calls: list[dict] = []

    def counting_cell_author(payload):
        cell_calls.append(copy.deepcopy(payload))
        return _cell_verdict_author(payload)

    authorities["cells"] = (counting_cell_author, _verified)
    authorities["dedup"] = (_second_duplicates_first, _verified)

    released = run.run_pre_release_for_job(
        db,
        job.id,
        owner_sub=OWNER,
        authorities=authorities,
        **_decision_context(),
    )
    payload = released.payload
    assert len(payload["candidates"]) == 1, "the survivor ships"
    survivor = payload["candidates"][0]["generated_question"]
    assert survivor["pre_question_id"] == "PRC-0001-PRQ-0001"
    assert len(cell_calls) == 1, "the removed question cost no cell verdict"

    removed = payload["duplicates_removed"]
    assert len(removed) == 1
    record = removed[0]
    assert record["pre_question_id"] == "PRC-0001-PRQ-0002"
    assert record["survivor_pre_question_id"] == "PRC-0001-PRQ-0001"
    assert record["reason"]
    assert record["generated_question"]["question_text"]
    assert any("Q15" in flag for flag in record["flags"])
    # A removal is not a blocked question: the ledgers stay distinct.
    assert payload["materialization_blocked"] == []
    # Reviewable, never silent: the removal alone names the release.
    assert release_core.release_state(released) == (
        release_core.READY_WITH_FLAGS
    )


def test_no_duplicates_leaves_the_release_clean(db):
    """The scripted default authority rules nothing a duplicate: both
    questions ship and ``duplicates_removed`` is empty (no quota,
    removal is never a goal)."""

    from app.services import assessment_release_run as run

    chapter = _chapter_with_concepts(db)
    job = _both_lanes_job(db, chapter, questions=2)
    authorities, calls = _generated_authorities()
    authorities["cells"] = (_cell_verdict_author, _verified)

    released = run.run_pre_release_for_job(
        db,
        job.id,
        owner_sub=OWNER,
        authorities=authorities,
        **_decision_context(),
    )
    payload = released.payload
    assert len(payload["candidates"]) == 2
    assert payload["duplicates_removed"] == []
    assert calls["dedup"], "the verdict was consulted, once per group"
    assert len(calls["dedup"]) == 1
