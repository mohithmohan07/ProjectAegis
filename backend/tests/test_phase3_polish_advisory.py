"""Polish preserves the run evidence and records an independent review."""
from __future__ import annotations

import copy

import pytest

from app.services.phase3 import envelope as envelope_mod
from app.services.phase3 import kernel, polish, prompts
from tests.test_phase3_polish import _REPAIRED_DETAILS, _SOURCE, _env, _verbatim_row


def _provider(request):
    return {"rows": [{
        "row_ref": row["row_ref"],
        "concept_title": row["concept_title"],
        "concept_details": _REPAIRED_DETAILS,
    } for row in request["rows"]]}


def test_critic_dissent_is_recorded_once_without_reauthoring(tmp_path):
    calls = {"author": 0, "critic": 0}
    row = _verbatim_row()
    row["question_labels"] = "QINV-0001"
    row["_aegis_analysis_allotments"] = ["LA-0001"]
    original = copy.deepcopy(row)

    def author(request):
        calls["author"] += 1
        return _provider(request)

    def critic(request):
        calls["critic"] += 1
        assert request["proposed_decision"]["rows"][0]["concept_details"] == _REPAIRED_DETAILS
        assert request["rows"][0]["concept_details"] == original["concept_details"]
        return {
            "verdict": "rejected", "confidence": 0.9,
            "issues": ["row_ref 0 concept_details: the cited B-1 needs a fuller explanation."],
        }

    store = kernel.DecisionStore(tmp_path)
    first = polish.polish(_env(), [copy.deepcopy(row)], provider=author, critic=critic, store=store)
    second = polish.polish(_env(), [copy.deepcopy(row)], provider=author, critic=critic,
                           store=kernel.DecisionStore(tmp_path))
    assert calls == {"author": 1, "critic": 1}
    assert first == second
    assert first[0]["concept_details"] == _REPAIRED_DETAILS
    assert first[0]["question_labels"] == original["question_labels"]
    assert first[0]["_aegis_analysis_allotments"] == original["_aegis_analysis_allotments"]
    assert any("needs a fuller explanation" in flag for flag in first[0]["review_flags"])
    decision = store.get(store.keys()[0])
    assert decision["kind"] == "polish.rows"
    assert any("needs a fuller explanation" in flag for flag in decision["review_flags"])


def test_polish_supplies_full_evidence_reference_and_run_context():
    long_source = _SOURCE + " Evidence continuation." * 60 + " FINAL_EVIDENCE"
    env = envelope_mod.build(
        graph={"source_contract_hash": "S-1", "topics": [{"topic_id": "T-1", "title": "Topic"}],
               "subtopics": [], "blocks": [{"block_id": "B-1", "topic_id": "T-1"}]},
        canonical={"blocks": [{"block_id": "B-1", "display_text": long_source},
                               {"block_id": "B-2", "display_text": "Supporting context."}]},
        skeleton_rows=[{"concept_title": "A concept"}], inventory={"items": []},
        mined_types={"types": []}, metadata={"subject": "Social Science", "grade": "10"},
    )
    row = _verbatim_row()
    row["_reference_block_ids"] = ["B-2"]
    seen = []

    def author(request):
        seen.append(request)
        return _provider(request)

    polish.polish(env, [row], provider=author)
    assert seen[0]["rows"][0]["source_blocks"][0]["text"] == long_source
    assert seen[0]["rows"][0]["reference_blocks"] == [
        {"block_id": "B-2", "text": "Supporting context."}
    ]
    assert seen[0]["metadata"]["subject"] == "Social Science"


@pytest.mark.parametrize("damage, expected", [
    ("image", "retain every existing image"),
    ("math", "canonical rich text"),
    ("identity", "preserve concept_title"),
])
def test_repair_retries_asset_math_and_identity_damage(damage, expected):
    image = '[img src="https://projectaegis.fly.dev/assets/figure.png" alt="A diagram"]'
    source = _verbatim_row()
    source["concept_details"] += " " + image
    fixed = _REPAIRED_DETAILS + " " + image
    calls = []

    def author(request):
        calls.append(request)
        result = _provider(request)
        result["rows"][0]["concept_details"] = fixed
        if len(calls) == 1:
            if damage == "image":
                result["rows"][0]["concept_details"] = _REPAIRED_DETAILS
            elif damage == "math":
                result["rows"][0]["concept_details"] += r" $x^2$"
            else:
                result["rows"][0]["concept_title"] = "Invented replacement"
        return result

    out = polish.polish(_env(), [source], provider=author)
    assert len(calls) == 2
    assert any(expected in defect for defect in calls[1]["response_contract_feedback"])
    assert out[0]["concept_details"] == fixed
    assert out[0]["concept_title"] == source["concept_title"]


def test_unavailable_critic_does_not_lose_repaired_content():
    def broken_critic(_request):
        raise TimeoutError("fixture")

    result = polish.polish(_env(), [_verbatim_row()], provider=_provider, critic=broken_critic)
    assert result[0]["concept_details"] == _REPAIRED_DETAILS
    assert any("critic failed to run (TimeoutError)" in flag for flag in result[0]["review_flags"])


def test_a_changed_critic_prompt_cannot_replay_an_old_decision(monkeypatch, tmp_path):
    calls = []

    def author(request):
        calls.append(request)
        return _provider(request)

    store = kernel.DecisionStore(tmp_path)
    polish.polish(_env(), [_verbatim_row()], provider=author, store=store)
    monkeypatch.setattr(prompts, "POLISH_CRITIC_SYSTEM", prompts.POLISH_CRITIC_SYSTEM + " Revised criterion.")
    polish.polish(_env(), [_verbatim_row()], provider=author, store=store)
    assert len(calls) == 2
    assert len(store.keys()) == 2


def test_default_live_adapter_invokes_separate_advisory_request(monkeypatch):
    import json

    from app.services import generation

    calls = []

    def scripted_api(system, user, *, purpose, image_urls=None):
        calls.append((system, purpose))
        payload = json.loads(user)
        if purpose == "concept_validation":
            return _provider(payload)
        assert payload["proposed_decision"]["rows"][0]["concept_details"] == _REPAIRED_DETAILS
        return {"verdict": "verified", "confidence": 0.99, "issues": []}

    monkeypatch.setattr(envelope_mod, "require_live_api", lambda: None)
    monkeypatch.setattr(generation, "_openai_json", scripted_api)
    result = polish.polish(_env(), [_verbatim_row()])
    assert result[0]["concept_details"] == _REPAIRED_DETAILS
    assert calls == [
        (prompts.POLISH_SYSTEM, "concept_validation"),
        (prompts.POLISH_CRITIC_SYSTEM, "advisory_critic"),
    ]
