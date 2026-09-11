"""Accepted Pre scope and grouped Post evidence reach effective model prompts."""
from __future__ import annotations

import copy

import pytest

from app.services import assessment_item_review as review
from app.services import assessment_master_refiner as master
from app.services import assessment_materialization as materialize
from app.services import generation, generation_repair_policy as repair
from app.services import prelearning_capture_policy as capture
from app.services import release_refiner
from app.services.phase3 import kernel
from tests import test_release_refiner as release_fixtures


QUESTION = "Which statement puts two familiar events in chronological order?"
CONTRACT = {
    "answer_restriction": "Specific", "answer_space_contract": "Select the correct order.",
    "required_elements": ["earlier event first"], "accepted_variations": [],
}
GROUP = {
    "source_qid": "QINV-1", "raw_text": "What is a record, and how is it used?",
    "source_context": {
        "reviewed_source_qids": ["QINV-1", "QINV-2"],
        "reviewed_source_dependencies": [
            {"source_qid": "QINV-1", "source_answer": "A stored account."},
            {"source_qid": "QINV-2", "source_answer": "For reference."},
        ],
    },
}


def _payload(stage, metadata, atom=None):
    cell = {"cell_id": "CELL-1", "sheet_kind": "objective", "source_policy": "generated"}
    candidate = {
        "candidate_id": "CAND-1", "question": QUESTION,
        "question_text": QUESTION, "sheet_kind": "objective",
        "_aegis_assessment_answer_restriction": copy.deepcopy(CONTRACT),
    }
    if stage == "materialize":
        return materialize._decision_payload(
            atom, cell, candidate_id="CAND-1", meta=metadata,
            context={"accepted_concepts": [{"description": "Put earlier events before later events."}]},
            descriptive_answer_capacity=10,
        )
    if stage == "review":
        return review._payload(candidate, cell, atom, meta=metadata, format_policy={})
    if stage == "master":
        return master._unit_payload(
            unit_kind="candidate", unit_id="CAND-1", record=candidate,
            rendered_rows=[], metadata=metadata, instruction_set=None,
            context={"source_atoms": [copy.deepcopy(atom)]} if atom else {},
        )
    return {
        "stage": "refine_release", "metadata": metadata,
        "output_kind": "pre_concepts_release" if metadata.get("learning_kind") == "pre" else "concepts_release",
        "rules": "Preserve all immutable fields.", "rows": [],
        "source_atom": copy.deepcopy(atom),
    }


ADAPTERS = [
    ("materialize", materialize._live_materialize),
    ("materialize", materialize._live_critic),
    ("review", review._live_review),
    ("master", master._live_author),
    ("master", master._live_critic),
    ("release", release_refiner._live_refine),
    ("release", release_refiner._live_critic),
]


@pytest.mark.parametrize("stage,adapter", ADAPTERS)
def test_pre_scope_repair_reaches_effective_author_and_critic_systems(monkeypatch, stage, adapter):
    calls = []

    def fake(system, user, **kwargs):
        calls.append((system, user, kwargs.get("prompt_cache_prefix", "")))
        return {}

    monkeypatch.setattr(generation, "_openai_json", fake)
    pre_meta = {repair.KEY: repair.VERSION, "learning_kind": "pre", "subject": "English", "grade": "10"}
    current = _payload(stage, pre_meta)
    adapter(current)
    adapter(_payload(stage, {**pre_meta, "learning_kind": "post"}))
    adapter(_payload(stage, {"learning_kind": "pre", "subject": "English", "grade": "10"}))
    assert repair.PRE_ASSESSMENT_INSTRUCTION in calls[0][0]
    assert capture.REPAIR_INSTRUCTION in calls[0][0]
    assert "Preserve frozen question wording" in calls[0][0]
    assert repair.PRE_ASSESSMENT_INSTRUCTION not in calls[1][0]
    assert repair.PRE_ASSESSMENT_INSTRUCTION not in calls[2][0]
    if stage in {"review", "master"}:
        assert current["adopted_answer_contract"] == CONTRACT
        item = current["item"] if stage == "review" else current["candidate"]
        assert item["question"] == QUESTION
        assert item["question_text"] == QUESTION


@pytest.mark.parametrize("stage,adapter", ADAPTERS)
def test_grouped_post_members_remain_evidence_for_one_frozen_question(monkeypatch, stage, adapter):
    systems = []
    monkeypatch.setattr(generation, "_openai_json", lambda system, user, **kw: systems.append(system) or {})
    meta = {repair.KEY: repair.VERSION, "learning_kind": "post", "subject": "English"}
    payload = _payload(stage, meta, GROUP)
    before = copy.deepcopy(payload)
    adapter(payload)
    assert repair.POST_GROUP_INSTRUCTION in systems[0]
    assert repair.PRE_ASSESSMENT_INSTRUCTION not in systems[0]
    assert payload == before
    if stage == "master":
        assert payload["context"]["source_atoms"][0]["source_context"] == GROUP["source_context"]
    else:
        assert payload["source_atom"]["source_context"] == GROUP["source_context"]
    single = copy.deepcopy(GROUP)
    single["source_context"]["reviewed_source_qids"] = ["QINV-1"]
    adapter(_payload(stage, meta, single))
    adapter(_payload(stage, {"learning_kind": "post"}, GROUP))
    assert repair.POST_GROUP_INSTRUCTION not in systems[1]
    assert repair.POST_GROUP_INSTRUCTION not in systems[2]


def test_real_pre_concept_refiner_payload_retains_repair_and_protected_rows(tmp_path, monkeypatch):
    seen = []
    base = release_fixtures._Provider()

    def provider(payload):
        seen.append(copy.deepcopy(payload))
        return base(payload)

    metadata = {
        **release_fixtures._METADATA, "pre_post": "Pre", repair.KEY: repair.VERSION,
        "prerequisite_evidence": {"accepted_scope": "General sequence and evidence skills"},
    }
    rows = release_fixtures._rows()
    before = copy.deepcopy(rows)
    result, diff, flags = release_refiner.refine_release(
        rows, metadata=metadata, output_kind="pre_concepts_release", provider=provider,
        critic=lambda _: {"verdict": "verified", "confidence": 0.99, "issues": []},
        store=kernel.DecisionStore(tmp_path / "refiner"),
    )
    assert seen
    assert all(row["metadata"][repair.KEY] == repair.VERSION for row in seen)
    assert all(repair.PRE_ASSESSMENT_INSTRUCTION in row["rules"] for row in seen)
    assert all(row["chapter_evidence"] == {"prerequisite_evidence": metadata["prerequisite_evidence"]} for row in seen)
    assert [row["concept_details"] for row in result] == [row["concept_details"] for row in before]
    assert [row["concept_title"] for row in result] == [row["concept_title"] for row in before]
    assert rows == before


@pytest.mark.parametrize("container", ["row", "chapter"])
def test_concept_refiner_group_guidance_reads_real_evidence_containers(container):
    payload = {"metadata": {repair.KEY: repair.VERSION}, "rows": []}
    if container == "row":
        payload["rows"] = [{"source_evidence": {"question_task_inventory": [GROUP]}}]
    else:
        payload["chapter_evidence"] = {"question_task_inventory": {"items": [GROUP]}}
    original = copy.deepcopy(payload)
    assert repair.POST_GROUP_INSTRUCTION in repair.grouped_source_instruction(payload)
    assert payload == original


@pytest.mark.parametrize("stage,adapter", ADAPTERS[:5])
def test_corrected_atom_activates_group_review_with_historical_chapter_metadata(monkeypatch, stage, adapter):
    systems = []
    monkeypatch.setattr(generation, "_openai_json", lambda system, user, **kw: systems.append(system) or {})
    metadata = {"subject": "English", "learning_kind": "post"}
    atom = {**copy.deepcopy(GROUP), repair.KEY: repair.VERSION}
    payload = _payload(stage, metadata, atom)
    assert payload[repair.KEY] == repair.VERSION
    assert repair.KEY not in payload["metadata"]
    adapter(payload)
    adapter(_payload(stage, metadata, GROUP))
    assert repair.POST_GROUP_INSTRUCTION in systems[0]
    assert repair.POST_GROUP_INSTRUCTION not in systems[1]
    assert repair.PRE_ASSESSMENT_INSTRUCTION not in systems[0]
    assert repair.KEY not in metadata


def test_materialized_group_retains_policy_and_dependencies_for_candidate_only_review(monkeypatch):
    systems = []
    monkeypatch.setattr(generation, "_openai_json", lambda system, user, **kw: systems.append(system) or {})
    atom = {**copy.deepcopy(GROUP), repair.KEY: repair.VERSION}
    cell = {"cell_id": "CELL-1", "sheet_kind": "descriptive"}
    candidate = materialize._assemble(
        {"question": GROUP["raw_text"], "answers": [], "sub_questions": []},
        atom, cell, candidate_id="CAND-1",
        decision={"key": "decision-1", "review_flags": []},
    )
    assert candidate[repair.KEY] == repair.VERSION
    assert candidate["source_context"] == GROUP["source_context"]
    candidate["_aegis_assessment_answer_restriction"] = copy.deepcopy(CONTRACT)
    metadata = {"subject": "English"}
    review_payload = review._payload(candidate, cell, None, meta=metadata, format_policy={})
    master_payload = master._unit_payload(
        unit_kind="candidate", unit_id="CAND-1", record=candidate,
        rendered_rows=[], metadata=metadata, instruction_set=None, context={},
    )
    for payload, adapter in ((review_payload, review._live_review),
                             (master_payload, master._live_author),
                             (master_payload, master._live_critic)):
        assert payload[repair.KEY] == repair.VERSION
        adapter(payload)
    assert all(repair.POST_GROUP_INSTRUCTION in system for system in systems)
    assert review_payload["adopted_answer_contract"] == CONTRACT
    assert master_payload["adopted_answer_contract"] == CONTRACT
