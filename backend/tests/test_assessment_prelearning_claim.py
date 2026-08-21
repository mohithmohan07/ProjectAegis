"""Q18 stage 1 pinned: recap source questions leave the Post Master.

Owner ruling, 21 Aug 2026 (option b): questions belonging to
prerequisite-recap material are claimed OUT of the Post Master by one
recorded per-chapter verdict — before any cell verdict is paid for — and
ride the payload under ``pre_learning_claimed`` with their reasons,
reviewable, never silent. Position is never evidence (the Sorrieu rule),
an empty claim is legitimate, and the 17-Aug steer stands: a claimed
question never enters any Pre artefact.
"""
from __future__ import annotations

from app.services import assessment_prelearning_claim as claim_mod
from app.services import release_core
from app.services.phase3 import kernel

from tests.test_assessment_release_run import (
    OWNER,
    _authorities,
    _chapter_with_concepts,
    _decision_context,
    _make_job,
)

ENVELOPE = "e" * 64


def _atoms(count: int = 2) -> list[dict]:
    return [
        {
            "source_qid": f"QINV-{index:04d}",
            "source_paper_number": f"Exercise 1({index})",
            "source_kind": "exercise",
            "normalized_public_text": f"Task number {index}.",
        }
        for index in range(1, count + 1)
    ]


def _verified(payload):
    return {"verdict": "verified", "confidence": 1.0, "issues": []}


def test_the_checker_refuses_impossible_claims():
    check = claim_mod._claim_checker({"QINV-0001", "QINV-0002"})
    defects = check({
        "claimed": [
            {"source_qid": "QINV-9999", "reason": "recap"},
            {"source_qid": "QINV-0001", "reason": ""},
            {"source_qid": "QINV-0001", "reason": "again"},
        ],
    })
    text = "\n".join(defects)
    assert "'QINV-9999' is not one of this chapter's source questions" in text
    assert "carries no reason" in text
    assert "claimed twice" in text
    assert check({"claimed": []}) == []


def test_an_empty_claim_keeps_every_atom():
    atoms = _atoms(2)
    kept, claimed = claim_mod.decide_pre_learning_claims(
        atoms,
        meta={"subject": "History"},
        envelope_sha256=ENVELOPE,
        provider=lambda payload: {
            "claimed": [], "confidence": 1.0,
            "rationale": "all chapter teaching",
        },
        critic=_verified,
        store=kernel.DecisionStore(),
    )
    assert kept == atoms
    assert claimed == []


def test_a_claimed_question_is_removed_with_its_reason_recorded():
    atoms = _atoms(3)

    def claim_first(payload):
        assert payload["source_questions"][0]["source_qid"] == "QINV-0001"
        return {
            "claimed": [{
                "source_qid": "QINV-0001",
                "reason": "revises earlier-class counting before the "
                          "chapter's own teaching begins",
            }],
            "confidence": 0.9,
            "rationale": "one recap drill, two chapter tasks",
        }

    kept, claimed = claim_mod.decide_pre_learning_claims(
        atoms,
        meta={},
        envelope_sha256=ENVELOPE,
        provider=claim_first,
        critic=_verified,
        store=kernel.DecisionStore(),
    )
    assert [atom["source_qid"] for atom in kept] == [
        "QINV-0002", "QINV-0003"]
    assert len(claimed) == 1
    record = claimed[0]
    assert record["source_qid"] == "QINV-0001"
    assert "earlier-class" in record["reason"]
    assert record["source_atom"]["normalized_public_text"]
    assert any("Q18" in flag for flag in record["flags"])


def test_no_atoms_costs_nothing():
    def never_called(payload):
        raise AssertionError("an empty chapter is never judged")

    kept, claimed = claim_mod.decide_pre_learning_claims(
        [], meta={}, envelope_sha256=ENVELOPE,
        provider=never_called, store=kernel.DecisionStore(),
    )
    assert kept == [] and claimed == []


def test_a_claimed_question_leaves_the_post_master_before_any_cell_spend(db):
    from app.services import assessment_release_run as run

    chapter = _chapter_with_concepts(db)
    job = _make_job(db, chapter)
    calls: dict = {}
    authorities, _ = _authorities(db, chapter, calls=calls)

    def claim_second(payload):
        return {
            "claimed": [{
                "source_qid": "QINV-0002",
                "reason": "an earlier-class revision drill",
            }],
            "confidence": 0.9,
            "rationale": "one recap item",
        }

    authorities["pre_claim"] = (claim_second, authorities["pre_claim"][1])

    released = run.run_release_for_job(
        db, job.id, owner_sub=OWNER, authorities=authorities,
        **_decision_context(),
    )
    payload = released.payload
    assert len(payload["source_atoms"]) == 1
    assert payload["source_atoms"][0]["source_qid"] == "QINV-0001"
    assert len(payload["candidates"]) == 1, "only chapter teaching ships"
    assert len(calls["cells"]) == 1, "the claimed question cost no verdict"

    claimed = payload["pre_learning_claimed"]
    assert len(claimed) == 1
    assert claimed[0]["source_qid"] == "QINV-0002"
    assert claimed[0]["reason"]
    assert claimed[0]["source_atom"]["source_qid"] == "QINV-0002"
    assert any("Q18" in flag for flag in claimed[0]["flags"])
    # Reviewable, never silent: the claim alone names the release.
    assert release_core.release_state(released) == (
        release_core.READY_WITH_FLAGS
    )
