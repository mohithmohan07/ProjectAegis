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

import copy

from app.services import assessment_prelearning_claim as claim_mod
from app.services import build_concepts_release as release
from app.services import release_core
from app.services.phase3 import kernel

from tests.test_assessment_release_run import (
    OWNER,
    _authorities,
    _chapter_with_concepts,
    _decision_context,
    _make_job,
)
from tests.test_generated_cell_decisions import (
    PUBLICATION,
    _item_review_verified,
)

ENVELOPE = "e" * 64


def _contract_materialize_author(payload):
    """A contract-v2.0 proposal for one source question of the fixture.

    Objective: the explanation OPENS with the exact correct-option text and
    names no option letter (§22.5). Descriptive: ``display_answer`` and
    ``answer_explanation`` are one model answer (§24), the criteria carry
    no bracket tag outside an English run (§28) and are worth exactly
    1 mark each (§27.5), summing to the cell's 3 marks.
    """

    cell = payload["blueprint_cell"]
    atom = payload["source_atom"]
    if cell["sheet_kind"] == "objective":
        return {
            "candidate_id": payload["candidate_id"],
            "question": atom["normalized_public_text"],
            "display_answer": "",
            "answers": [
                {"answer_type": "Phrases", "answer_content": "Cube",
                 "correct_answer": "Yes", "answer_weightage": "1"},
                {"answer_type": "Phrases", "answer_content": "Circle",
                 "correct_answer": "No", "answer_weightage": "0"},
            ],
            "sub_questions": [],
            "answer_explanation": (
                "Cube is the solid: it occupies space in three dimensions, "
                "while a circle is a flat figure."
            ),
            "requires_visual": False,
            "rationale": "preserves the source question and answer",
        }
    model_answer = (
        "A cube occupies space in three dimensions: it has length, breadth "
        "and height, so it is a solid."
    )
    return {
        "candidate_id": payload["candidate_id"],
        "question": atom["normalized_public_text"],
        "display_answer": model_answer,
        "answers": [
            {"answer_type": "Phrases", "answer_weightage": "1",
             "answer_content": "states that a cube occupies space"},
            {"answer_type": "Phrases", "answer_weightage": "1",
             "answer_content": "names all three dimensions"},
            {"answer_type": "Phrases", "answer_weightage": "1",
             "answer_content": "concludes that a cube is therefore a solid"},
        ],
        "sub_questions": [],
        "answer_explanation": model_answer,
        "requires_visual": False,
        "rationale": "preserves the constructed-response obligation",
    }


def _contract_marking_author(payload):
    """Marking under §27.5: the correct option carries the cell's marks and
    every Descriptive criterion keeps a 0.5-or-1 weight."""

    from app.services import assessment_release as rel

    candidate = payload["candidate"]
    cell = payload["blueprint_evidence"]["explicit_blueprint_cell"]
    answers = copy.deepcopy(candidate["answers"])
    if cell["sheet_kind"] == "objective":
        for answer in answers:
            answer["answer_weightage"] = (
                cell["marks"]
                if rel.is_correct_option(answer.get("correct_answer"))
                else 0
            )
        duration, keyboard = 2, ""
    else:
        for answer in answers:
            answer["answer_weightage"] = 1
        duration, keyboard = 5, "No"
    return {
        "candidate_id": candidate["candidate_id"],
        "question": candidate["question"],
        "question_text": candidate["question_text"],
        "answers": answers,
        "sub_questions": copy.deepcopy(candidate["sub_questions"]),
        "question_duration": duration,
        "duration_basis_count": None,
        "math_keyboard": keyboard,
        "rationale": "The explicit cell owns the complete decomposition.",
    }


def _contract_authorities(db, chapter, *, calls=None):
    """``_authorities`` with the v2.0 item authorities in place (Q26).

    Only the stages whose scripted output the contract retired are
    replaced — materialize and marking — plus the joint item review the
    contract added (§27 step 6); the ``calls`` ledger is unchanged.
    """

    authorities, first_concept_name = _authorities(db, chapter, calls=calls)
    verified = authorities["materialize"][1]
    authorities["materialize"] = (_contract_materialize_author, verified)
    authorities["marking"] = (_contract_marking_author, verified)
    authorities["item_review"] = (_item_review_verified, verified)
    return authorities, first_concept_name


def _published_job(db, chapter):
    """``_make_job`` whose staged Post slot names the publication (§18)."""

    job = _make_job(db, chapter)
    inventory = copy.deepcopy(job.question_inventory)
    slot = inventory.get(release.RELEASE_KEY)
    if isinstance(slot, dict) and not str(
        slot.get("source_book") or ""
    ).strip():
        slot["source_book"] = PUBLICATION
    if not str(job.source_book or "").strip():
        job.source_book = PUBLICATION
    job.question_inventory = inventory
    db.commit()
    db.refresh(job)
    return job


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
    """Fixtures follow contract v2.0 (Q26): the staged release names its
    publication (§18) and the item authorities are contract-conformant."""

    from app.services import assessment_release_run as run

    chapter = _chapter_with_concepts(db)
    job = _published_job(db, chapter)
    calls: dict = {}
    authorities, _ = _contract_authorities(db, chapter, calls=calls)

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
