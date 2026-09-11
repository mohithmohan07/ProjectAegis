"""Recorded context decisions survive display without restoring raw extracts.

Scripted APIs exercise transport, stimulus conservation and historical replay;
they do not claim to measure a live model's semantic editing quality.
"""
from __future__ import annotations

import copy
import json

import pytest

from app import config
from app.services import assessment_materialization as materialization
from app.services import assessment_source_inventory as inventory
from app.services import generation
from app.services import generation_quality_policy as quality
from app.services import question_polishing as polishing
from app.services import source_task_polishing_policy as source_format
from app.services.phase3 import kernel
from tests.test_mes_materialization import _cell, _descriptive_response


@pytest.fixture(autouse=True)
def isolated_polish_state(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "use_live_generation", lambda: True)
    with polishing._memory_lock:
        polishing._memory_cache.clear()
    yield
    with polishing._memory_lock:
        polishing._memory_cache.clear()


def _source(raw, context, **extra):
    return {
        "qid": "QINV-0001", "source_kind": "in_text",
        "raw_task": raw, "normalized_task": raw,
        "requires_context": True, "shared_context": context,
        "source_context": {"text": context}, "block_ids": ["BLK-001"],
        **extra,
    }


def _polish(source, wording, context, *, calls=None, current=True):
    def api(system, user, **kwargs):
        request = json.loads(user)
        if calls is not None:
            calls.append((system, request, kwargs["purpose"]))
        if kwargs["purpose"] == "advisory_critic":
            return {"items": [{"qid": source["qid"], "verdict": "verified", "issues": []}]}
        return {"items": [{
            "qid": source["qid"], "polished_task": wording,
            "learner_context": context,
            "note": "Resolve the named referent; retain the supplied task dependencies.",
        }]}

    return polishing.polish_inventory(
        {"items": [source]},
        meta={quality.KEY: quality.VERSION} if current else {}, api_call=api,
    )["items"][0]


def test_explicit_no_separate_context_prevents_old_extract_reinsertion():
    raw = "Why does this happen?"
    context = (
        "A pupil's shadow changes length during the day. The chapter next "
        "describes morning routines and includes an extended classroom discussion."
    )
    wording = "Why does a pupil's shadow change length during the day?"
    source = _source(raw, context)
    original = copy.deepcopy(source)
    calls = []
    item = _polish(source, wording, "", calls=calls)
    assert source == original
    assert item["raw_task"] == item["normalized_task"] == raw
    assert item["shared_context"] == context
    assert item["source_context"] == {"text": context}
    assert item["learner_context"] == ""
    assert item["frozen_task_text"] == wording
    assert generation._inventory_task_text(item) == wording
    assert item["polish_audit"]["source_evidence"]["shared_context"] == context
    assert len(calls) == 2
    for system, request, purpose in calls:
        assert request[quality.KEY] == quality.VERSION
        assert request["questions"][0]["shared_context"] == context
        if purpose == "advisory_critic":
            assert request["questions"][0]["learner_context"] == ""
            assert request["questions"][0]["proposed_task"] == wording
            assert source_format.CONTEXT_REVIEW_RULES in system
        else:
            assert source_format.CONTEXT_POLISH_RULES in system

    # The unmarked recorded contract still uses its old context projection.
    historical = _polish(source, wording, "", current=False)
    assert context in generation._inventory_task_text(historical)
    assert quality.KEY not in historical
    assert "learner_context" not in historical


def test_full_statistics_table_reaches_frozen_master_and_replays_once():
    table = (
        r"[Katex] \begin{array}{|c|c|c|}\hline "
        r"\text{Interval (cm)} & \text{Frequency} & \text{Tally} \\\hline "
        r"0\leq x<10 & 4 & \phantom{IIII} \\\hline "
        r"10\leq x<20 & 7 & \phantom{IIIIIII} \\\hline "
        r"20\leq x\leq30 & 2 & \phantom{II} \\\hline "
        r"\text{Total} & 13 & \phantom{IIII} \\\hline\end{array} [/Katex]"
    )
    raw = "Complete the tally column for the data above and explain the interval boundaries."
    exposition = "The surrounding chapter contains a long discussion of survey design."
    source = _source(raw, table + "<br>" + exposition, tables=[{
        "columns": ["Interval (cm)", "Frequency", "Tally"],
        "rows": [["0 ≤ x < 10", 4, ""], ["10 ≤ x < 20", 7, ""],
                 ["20 ≤ x ≤ 30", 2, ""], ["Total", 13, ""]],
    }])
    wording = "Complete the tally column and explain the interval boundaries.<br>" + table
    item = _polish(source, wording, table)
    public = generation._inventory_task_text(item)
    assert public.count(table) == 1
    assert exposition not in public
    atom = inventory.source_atom_from_item(item, source_document_hash="chapter-sha")
    assert atom["tables"] == source["tables"]
    assert atom["learner_context"] == table
    assert atom["shared_context"] == source["shared_context"]
    assert inventory.source_task_evidence(atom)["learner_context"] == table
    calls = []

    def author(payload):
        calls.append(copy.deepcopy(payload))
        return _descriptive_response(payload, question=wording)

    cell = _cell(sheet_kind="descriptive", question_category="Short Answer Type (2 Marks)", marks=2)
    store = kernel.DecisionStore()
    kwargs = dict(meta={}, envelope_sha256="c" * 64, provider=author, store=store)
    candidate = materialization.materialize_candidate(atom, cell, **kwargs)
    assert materialization.materialize_candidate(atom, cell, **kwargs) == candidate
    assert len(calls) == 1
    payload = calls[0]
    assert payload["source_wording_authority"]["learner_context"] == table
    assert payload["source_wording_authority"]["frozen_task_text"] == wording
    assert source_format.CONTEXT_REVIEW_RULES in payload["rules"]
    assert source_format.CONTEXT_REVIEW_RULES in payload["critic_rules"]
    assert candidate["question"] == candidate["question_text"] == wording
    assert candidate["learner_context"] == table
    assert candidate["source_evidence"] == raw
    assert candidate["source_context"] == source["source_context"]


def test_complete_assessed_passage_is_not_shortened_by_transport():
    passage = "<br>".join(
        f"Scene {index}: the traveller observes a different part of the same landscape."
        for index in range(1, 61)
    )
    ask = "Explain how the traveller's observations change across the complete passage."
    source = _source(ask, passage)
    wording = ask + "<br>" + passage
    item = _polish(source, wording, passage)
    assert item["learner_context"] == passage
    assert item["frozen_task_text"] == wording
    assert generation._inventory_task_text(item).count(passage) == 1


@pytest.mark.parametrize("value", [None, 4, "A context block absent from the proposal."])
def test_missing_or_unrendered_context_decision_keeps_source_with_visible_flag(value):
    source = _source("Why does this happen?", "Supplied context.")
    calls = []
    item = _polish(source, "Explain the named source event.", value, calls=calls)
    assert item["frozen_task_text"] == source["raw_task"]
    assert item["polish_flag"] == polishing.FLAG_KEPT
    assert item["polish_review_required"] is True
    assert "learner_context" not in item
    assert not source_format.context_applies(item)
    assert source["shared_context"] in generation._inventory_task_text(item)
    atom = inventory.source_atom_from_item(item, source_document_hash="failed-context")
    assert "learner_context" not in materialization._source_wording_authority(atom)
    assert source_format.context_review_rules(atom) == ""
    assert item["polish_audit"]["author"]["learner_context"] == value
    assert calls[-1][1]["questions"][0]["proposed_task"] == source["raw_task"]


def test_new_context_policy_has_separate_cache_and_cannot_upgrade_recorded_polish():
    source = _source("Explain this.", "A complete source context.")
    metadata = {quality.KEY: quality.VERSION}
    assert polishing._cache_key([source], {}) != polishing._cache_key([source], metadata)
    recorded = _polish(source, "Explain the recorded source event.", "", current=False)
    original = copy.deepcopy(recorded)

    def forbidden(*args, **kwargs):
        raise AssertionError("A new policy cannot reauthor a sealed historical item")

    replay = polishing.polish_inventory({"items": [recorded]}, meta=metadata, api_call=forbidden)
    assert replay["items"] == [original]
    atom = inventory.source_atom_from_item(replay["items"][0], source_document_hash="old")
    assert quality.KEY not in atom
    payload = materialization._decision_payload(
        atom, _cell(), candidate_id="C1", meta={}, context={}, descriptive_answer_capacity=10,
    )
    assert payload["rules"] == materialization.SOURCE_FORMAT_MATERIALIZE_SYSTEM
    assert "critic_rules" not in payload


@pytest.mark.parametrize("action,prior_context,expected", [
    ("inherit", "Previously accepted short setup.", "Previously accepted short setup."),
    ("inherit", "", ""),
    ("replace", "Previously accepted short setup.", "New source-grounded setup."),
    ("remove", "Previously accepted short setup.", ""),
])
def test_corrected_concept_context_decision_reaches_master_without_raw_fallback(
    monkeypatch, action, prior_context, expected,
):
    from app.services import concept_question_review as review
    from app.services import release_workbook_edits as edits
    from tests.test_concept_question_review import payload as source_payload, question, verdict

    current = source_payload()
    current[quality.KEY] = quality.VERSION
    original = current["question_task_inventory"]["items"][1]
    raw_exposition = "An entire raw chapter extract that must not be restored."
    original.update({
        quality.KEY: quality.VERSION, source_format.FIELD: source_format.VERSION,
        "frozen_task_text": "Retained question", "learner_context": prior_context,
        "shared_context": raw_exposition, "requires_context": True,
        "source_context": {"shared_context": raw_exposition},
        "polish_audit": {"source_evidence": {"shared_context": raw_exposition}},
    })
    before = copy.deepcopy(current)
    text = "Explain the corrected source event."
    edited = [{"topic": "Old", "concept_title": "First", "concept_details": (
        "Description: New source-grounded setup. // Types: Example: " + text
    )}]
    proposed = question("Q2", 0, text)
    proposed["context_review"] = {
        "action": action,
        "sources": [{"kind": "edited_concept", "concept_row": 0, "source_qid": "",
                     "reason": "The edited row supplies the revised setup."}]
                   if action == "replace" else [],
        "rationale": "Use only the context still needed by the corrected task.",
    }
    proposed["shared_context"] = expected if action == "replace" else ""
    calls = []

    def call(system, evidence, *, critic=False):
        calls.append((system, copy.deepcopy(evidence), critic))
        if critic:
            assert evidence["proposed_verdict"]["questions"][0]["shared_context"] == expected
            return {"verdict": "verified", "issues": []}
        return verdict([proposed], rows=1)

    monkeypatch.setattr(review, "_call", call)
    rows = review.review_canonical_questions(current, edited)
    assert current == before
    assert rows[0]["question_text"] == text
    assert rows[0]["learner_context"] == expected
    assert rows.receipt[quality.KEY] == quality.VERSION
    assert rows.receipt["policy"].endswith(quality.VERSION)
    assert all(review.CONTEXT_QUALITY in system for system, _, _ in calls)
    edits._apply_question_review(current, rows, lane="post")
    accepted = current["question_task_inventory"]["items"][0]
    assert accepted["frozen_task_text"] == text
    assert accepted[quality.KEY] == quality.VERSION
    assert accepted["learner_context"] == expected
    assert accepted["shared_context"] == expected
    assert accepted["source_context"]["learner_context"] == expected
    assert accepted["polish_audit"]["source_evidence"]["shared_context"] == raw_exposition
    audit = current["review_question_audit"]["edited"][0]
    assert audit["before"]["learner_context"] == prior_context
    assert audit["after"]["learner_context"] == expected
    public = generation._inventory_task_text(accepted)
    assert text in public and raw_exposition not in public
    if expected:
        assert public.count(expected) == 1
    else:
        assert public == text
    atom = inventory.source_atom_from_item(accepted, source_document_hash="reviewed")
    authority = materialization._source_wording_authority(atom)
    assert authority["learner_context"] == expected
    assert authority["reviewed_context"]["action"] == action
    assert authority["frozen_task_text"] == text


def test_direct_complete_example_edit_clears_stale_context_metadata_and_keeps_audit():
    from app.services.reviewed_question_set import reconcile_post_review

    original = _polish(_source("Original task.", "Raw chapter exposition."),
                       "Accepted setup.<br>Original task.", "Accepted setup.")
    current = {"question_task_inventory": {"items": [original]}, "type_case_rows": []}
    result = reconcile_post_review(current, [{
        "row_kind": "example", "example_qid": original["qid"],
        "example_prompt": "Corrected standalone task.", "type_id": "T1", "case_id": "C1",
    }])
    accepted = result["question_task_inventory"]["items"][0]
    assert accepted[quality.KEY] == quality.VERSION
    assert accepted["learner_context"] == ""
    assert generation._inventory_task_text(accepted) == "Corrected standalone task."
    assert result["original_source_questions"][0] == original
    assert result["review_question_audit"]["edited"][0]["before"]["learner_context"] == "Accepted setup."
