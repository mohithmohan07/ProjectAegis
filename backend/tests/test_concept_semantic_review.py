"""Semantic judgments belong to API authors and independent advisory critics.

These regressions exercise the real decision, assembly, normalization and
release seams with injected providers. They never infer meaning from words,
overlap scores or statement length, and never call a live model.
"""
from __future__ import annotations

import copy

import pytest

from app.services import concept_refiner, concept_validator, release_refiner
from app.services.phase3 import analyse, assemble, envelope, kernel, place, settle


SOURCE = (
    "A ruler need not begin at zero to measure length. Place one end of the "
    "object at an undamaged scale mark and read the scale at its other end. "
    "Subtract the starting reading from the ending reading. Moving the ruler "
    "does not change the actual length of the object."
)
DESCRIPTION = (
    "A ruler measures the interval between an object's two endpoints. If its "
    "zero mark is damaged, align the first endpoint with another readable mark "
    "and note both readings. Subtract the starting reading from the ending "
    "reading to obtain the length; the starting number alone is not a length."
)
MASTERY = "Measure length."
BELIEF = (
    "When measuring an object with a broken ruler, the learner assumes that "
    "subtracting the starting scale reading from the ending scale reading "
    "changes the object's actual length."
)
ERROR = (
    "When measuring an object with a broken ruler, the learner subtracts "
    "the ending scale reading from the starting scale reading when "
    "recording the object's actual length."
)
METADATA = {
    "subject": "Science", "grade": "6", "board": "MSBSHSE",
    "chapter_title": "Measurement", "chapter_code": "SCI-06-02",
    "pre_post": "Post", "source_book": "Balbharati",
    "inventory": {"items": []}, "mined_types": {"types": []},
    "source_text": SOURCE,
}


def _row() -> dict:
    return {
        "topic": "Measuring length", "parent_concept": "",
        "concept_title": "Reading a shifted ruler",
        "concept_details": f"Description: {DESCRIPTION}\nAchieving Mastery: {MASTERY}",
        "keywords": "length | ruler | scale", "_semantic_topic_id": "T-1",
        "_source_block_ids": ["B-1"],
    }


def _env() -> dict:
    return envelope.build(
        graph={
            "source_contract_hash": "measurement-source",
            "topics": [{"topic_id": "T-1", "title": "Measuring length"}],
            "subtopics": [],
            "blocks": [{"block_id": "B-1", "topic_id": "T-1", "kind": "paragraph"}],
        },
        canonical={"blocks": [{"block_id": "B-1", "display_text": SOURCE}]},
        skeleton_rows=[_row()], inventory={"items": []}, mined_types={"types": []},
        metadata=METADATA,
    )


def _verified(_request) -> dict:
    return {"verdict": "verified", "confidence": 0.999, "issues": []}


def _inventory() -> list[dict]:
    return [
        {"item_id": "LA-0001", "kind": "misconception", "text": BELIEF,
         "evidence": "B-1", "rationale": "A false belief about changing the object's length."},
        {"item_id": "LA-0002", "kind": "error_analysis", "text": ERROR,
         "evidence": "B-1", "rationale": "A reversed subtraction while applying the method."},
    ]


def _analysis_provider(calls):
    def provider(request):
        calls.append(copy.deepcopy(request))
        if request["stage"] == "analyse.inventory":
            assert request["evidence"]["source_blocks"][0]["text"] == SOURCE
            return {"items": _inventory()}
        return {"allotments": [
            {"item_id": item["item_id"],
             "concept_id": request["settled_concepts"][0]["concept_id"],
             "rationale": "The shifted-ruler method is the teaching this item concerns."}
            for item in request["items"]
        ]}
    return provider


def _stamp_and_normalize(result) -> list[dict]:
    rows = [_row()]
    concept_ids = place.mint_concept_ids(rows)
    assemble.stamp_analysis_allotments(rows, result, dict(zip(concept_ids, rows)))
    rows = concept_refiner.refine_chapter(rows)
    return concept_validator.ensure_valid_learner_analysis(rows)


def test_settle_accepts_short_complete_mastery_after_independent_source_review():
    author_calls = []
    critic_calls = []

    def topology(request):
        return {"decisions": [
            {"concept_id": concept["concept_id"], "decision": "keep",
             "confidence": 0.999, "reason": "One coherent measurement method.",
             "segments": [{key: concept[key] for key in (
                 "concept_title", "parent_concept", "concept_details", "keywords",
             )}]}
            for concept in request["concepts"]
        ]}

    def grounding(request):
        return {"concepts": [
            {"concept_id": concept["concept_id"], "source_block_ids": ["B-1"],
             "confidence": 0.999, "reason": "The source explains both endpoint readings."}
            for concept in request["concepts"]
        ]}

    def author(request):
        author_calls.append(copy.deepcopy(request))
        assert request["concepts"][0]["source_blocks"] == [{"block_id": "B-1", "text": SOURCE}]
        return {"rows": [{
            "concept_id": request["concepts"][0]["concept_id"],
            "concept_description": DESCRIPTION, "achieving_mastery": MASTERY,
        }]}

    def critic(request):
        if request["stage"] == "content_authoring":
            critic_calls.append(copy.deepcopy(request))
            assert request["concepts"][0]["source_blocks"][0]["text"] == SOURCE
            assert request["proposed_decision"]["rows"][0]["achieving_mastery"] == MASTERY
        return _verified(request)

    rows = settle.settle(
        _env(), topology_provider=topology, grounding_provider=grounding,
        analysis_provider=author, critic=critic, store=kernel.DecisionStore(),
    )

    assert len(author_calls) == len(critic_calls) == len(rows) == 1
    assert rows[0]["concept_details"].endswith("Achieving Mastery: " + MASTERY)
    report = concept_validator.validate_concept_rows(
        rows, strict_mastery_statement=True, analysis_allotted_keys=set(),
    )
    assert report["errors"] == []


def test_shared_vocabulary_keeps_belief_and_process_error_under_api_chosen_kinds():
    calls = []
    reviews = []

    def critic(request):
        reviews.append(copy.deepcopy(request))
        if request["stage"] == "analyse.inventory":
            assert request["evidence"]["source_blocks"][0]["text"] == SOURCE
            assert request["proposed_decision"]["items"] == _inventory()
        return _verified(request)

    result = analyse.analyse(
        _env(), [_row()], provider=_analysis_provider(calls),
        critic=critic, store=kernel.DecisionStore(),
    )
    rows = _stamp_and_normalize(result)

    assert len(calls) == len(reviews) == 2  # inventory and allotment, no heuristic re-asks
    assert result["inventory"] == _inventory()
    assert concept_refiner.analysis_components(rows[0]["concept_details"]) == (BELIEF, ERROR)
    assert rows[0][assemble.ANALYSIS_ALLOTMENTS_FIELD] == ["LA-0001", "LA-0002"]
    report = concept_validator.validate_concept_rows(
        rows, strict_mastery_statement=True, strict_analysis_section=True,
        analysis_allotted_keys={0},
    )
    assert report["errors"] == []


@pytest.mark.parametrize("review_state", ["dissent", "unavailable"])
def test_semantic_review_flags_and_content_survive_disk_replay(tmp_path, review_state):
    calls = []
    reviews = []

    def critic(request):
        reviews.append(copy.deepcopy(request))
        if request["stage"] == "analyse.inventory":
            assert request["proposed_decision"]["items"] == _inventory()
            if review_state == "unavailable":
                raise RuntimeError("independent reviewer unavailable")
            return {"verdict": "dissent", "confidence": 0.999, "issues": [
                "Check the source support for the learner-analysis distinction.",
            ]}
        return _verified(request)

    first = analyse.analyse(
        _env(), [_row()], provider=_analysis_provider(calls), critic=critic,
        store=kernel.DecisionStore(tmp_path),
    )

    def forbidden(_request):
        raise AssertionError("A saved semantic decision must not be re-authored or re-reviewed.")

    restored_store = kernel.DecisionStore(tmp_path)
    replayed = analyse.analyse(
        _env(), [_row()], provider=forbidden, critic=forbidden, store=restored_store,
    )

    assert len(calls) == len(reviews) == 2
    assert replayed == first
    assert first["inventory"] == _inventory()
    assert set(first["review_flags"]) == {"LA-0001", "LA-0002"}
    rows = _stamp_and_normalize(replayed)
    assert concept_refiner.analysis_components(rows[0]["concept_details"]) == (BELIEF, ERROR)
    assert rows[0]["review_flags"]
    saved = next(
        restored_store.get(key) for key in restored_store.keys()
        if restored_store.get(key)["kind"] == "analyse.inventory"
    )
    assert saved["response"]["items"] == _inventory()
    assert saved["review_flags"]
    assert any(
        ("dissent" if review_state == "dissent" else "critic failed to run") in flag
        for flag in saved["review_flags"]
    )


def test_release_refiner_short_mastery_and_authored_analysis_survive_deposit(monkeypatch):
    monkeypatch.setenv(release_refiner.SCOPE_ENV, "all")
    before = _row()
    old_mastery = "Measure accurately with a ruler by comparing both endpoint readings."
    before["concept_details"] = before["concept_details"].replace(MASTERY, old_mastery)
    before["concept_details"] += (
        f" // Misconception/ Error Analysis: Misconceptions: {BELIEF}; Error Analysis: {ERROR}"
    )
    before[assemble.ANALYSIS_ALLOTMENTS_FIELD] = ["LA-0001", "LA-0002"]
    calls = []
    reviews = []

    def provider(request):
        calls.append(copy.deepcopy(request))
        row = request["rows"][0]
        return {"rows": [{
            "row_ref": row["row_ref"], "keywords": row["keywords"],
            "concept_details": row["concept_details"].replace(old_mastery, MASTERY),
            "rationale": "The short statement names the complete observable capability.",
        }]}

    def critic(request):
        reviews.append(copy.deepcopy(request))
        proposed = request["proposed_decision"]["rows"][0]["concept_details"]
        assert f"Achieving Mastery: {MASTERY}" in proposed
        assert concept_refiner.analysis_components(proposed) == (BELIEF, ERROR)
        return _verified(request)

    rows, diff, flags = release_refiner.refine_release(
        [before], metadata=METADATA, provider=provider, critic=critic,
        store=kernel.DecisionStore(),
    )

    assert len(calls) == len(reviews) == len(rows) == 1
    assert f"Achieving Mastery: {MASTERY}" in rows[0]["concept_details"]
    assert old_mastery not in rows[0]["concept_details"]
    assert concept_refiner.analysis_components(rows[0]["concept_details"]) == (BELIEF, ERROR)
    assert rows[0][assemble.ANALYSIS_ALLOTMENTS_FIELD] == ["LA-0001", "LA-0002"]
    assert diff["changes"]
    assert not any("rolled back" in flag or "discarded" in flag for flag in flags)
