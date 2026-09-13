"""Q71: a repeated Case title inside one mined Type goes back to the model.

The CASE WORDING rule ("Two Cases in one chapter never share a case_title")
was in the prompt, but nothing read it back: the reviewers' "Explaining the
importance of DNA copying …" titled two Cases. Under generation-quality v4
the miner's coverage follow-up carries ``duplicate_case_titles``, a title
repair can never buy its fix with exact-once coverage, and the Fixer is the
final resort. Pre-v4 runs replay the loop they were sealed with.
"""
from __future__ import annotations

import copy

import pytest

from app.services import generation as g
from app.services import generation_quality_policy as quality
from app.services.phase3 import fixer as p3_fixer
from app.services.phase3 import kernel

TITLE = "Explaining the importance of DNA copying for producing new cells and offspring"


def _type(cases):
    return {
        "type_id": "TYPE-0001", "type_title": "Explaining a process",
        "source_question_ids": [q for _, qids in cases for q in qids],
        "case_prompts": [
            {"case_title": title,
             "examples": [{"source_question_id": q, "example_prompt": f"Question {q[-1]}"} for q in qids]}
            for title, qids in cases
        ],
    }


INVENTORY = {"items": [{"qid": "QINV-0001", "raw_task": "Question 1"},
                       {"qid": "QINV-0002", "raw_task": "Question 2"}], "stats": {}}


def _logs(monkeypatch):
    lines: list[str] = []
    monkeypatch.setattr(g.progress, "log", lambda message, **kw: lines.append(str(message)))
    return lines


def test_the_detector_is_within_type_and_folds_only_whitespace_and_case():
    same = [_type([(TITLE, ["QINV-0001"]), ("explaining  the importance of DNA copying for producing new cells and offspring", ["QINV-0002"])])]
    groups = g._duplicate_case_titles(same)
    assert len(groups) == 1
    assert groups[0]["type_index"] == 0 and groups[0]["case_indexes"] == [0, 1]
    assert groups[0]["case_title"] == TITLE
    assert groups[0]["example_qids"] == [["QINV-0001"], ["QINV-0002"]]
    across = [_type([(TITLE, ["QINV-0001"])]), {**_type([(TITLE, ["QINV-0002"])]), "type_id": "TYPE-0002"}]
    assert g._duplicate_case_titles(across) == []
    symbol = [_type([("AB || CD", ["QINV-0001"]), ("AB ∥ CD", ["QINV-0002"])])]
    assert g._duplicate_case_titles(symbol) == []
    odd = [{"type_id": "T", "case_prompts": ["not a dict", {"case_title": "", "examples": []}]}]
    assert g._duplicate_case_titles(odd) == []


def _fake_miner(replies, calls):
    def fake(system, user, **kw):
        calls.append(user)
        return copy.deepcopy(replies[min(len(calls), len(replies)) - 1])
    return fake


def test_a_v4_run_sends_repeated_case_titles_back_through_the_coverage_follow_up(monkeypatch):
    calls: list[str] = []
    repeated = {"types": [_type([(TITLE, ["QINV-0001"]), (TITLE, ["QINV-0002"])])]}
    fixed = {"types": [_type([(TITLE + " in new cells", ["QINV-0001"]), (TITLE + " in offspring", ["QINV-0002"])])]}
    monkeypatch.setattr(g, "_openai_json", _fake_miner([repeated, fixed], calls))
    monkeypatch.setattr(p3_fixer, "default_provider", lambda: pytest.fail("no Fixer needed"))
    lines = _logs(monkeypatch)
    meta = g._metadata(subject="Science")
    assert quality.duplicate_case_titles_returned(meta)
    mined = g._mine_types_from_inventory_via_api(meta=meta, inventory=INVENTORY, max_coverage_attempts=2)
    assert len(calls) == 2
    assert '"duplicate_case_titles"' in calls[1] and "TYPE-0001" in calls[1]
    assert "COMPLETE corrected" in calls[1]
    assert "Two Cases in one Type never share a case_title" in calls[1]
    assert g._duplicate_case_titles(mined["types"]) == []
    assert not g._uncovered_inventory_items(INVENTORY, mined["types"])
    assert not g._duplicate_inventory_assignments(INVENTORY, mined["types"])
    assert any("1 repeated Case title(s)" in line for line in lines)


@pytest.mark.parametrize("stamp", ["v3", None])
def test_a_pre_v4_run_never_asks_about_case_titles(monkeypatch, stamp):
    calls: list[str] = []
    repeated = {"types": [_type([(TITLE, ["QINV-0001"]), (TITLE, ["QINV-0002"])])]}
    monkeypatch.setattr(g, "_openai_json", _fake_miner([repeated], calls))
    monkeypatch.setattr(p3_fixer, "default_provider", lambda: pytest.fail("no Fixer on a pre-v4 run"))
    meta = dict(g._metadata(subject="Science"))
    if stamp == "v3":
        meta[quality.KEY] = quality.V3
    else:
        meta.pop(quality.KEY, None)
    mined = g._mine_types_from_inventory_via_api(meta=meta, inventory=INVENTORY, max_coverage_attempts=2)
    assert len(calls) == 1
    assert not any("duplicate_case_titles" in c or "repeated Case title" in c for c in calls)
    assert [c["case_title"] for c in mined["types"][0]["case_prompts"]] == [TITLE, TITLE]


def test_a_title_repair_may_never_buy_its_fix_with_coverage(monkeypatch):
    calls: list[str] = []
    repeated = {"types": [_type([(TITLE, ["QINV-0001"]), (TITLE, ["QINV-0002"])])]}
    dropped = {"types": [_type([(TITLE + " A", ["QINV-0001"])])]}          # distinct titles, QINV-0002 lost
    fixed = {"types": [_type([(TITLE + " A", ["QINV-0001"]), (TITLE + " B", ["QINV-0002"])])]}
    monkeypatch.setattr(g, "_openai_json", _fake_miner([repeated, dropped, fixed], calls))
    monkeypatch.setattr(p3_fixer, "default_provider", lambda: None)
    lines = _logs(monkeypatch)
    mined = g._mine_types_from_inventory_via_api(
        meta=g._metadata(subject="Science"), inventory=INVENTORY, max_coverage_attempts=2,
    )
    assert any("Rejected Type Mining coverage repair" in line for line in lines)
    titles = [c["case_title"] for c in mined["types"][0]["case_prompts"]]
    assert titles == [TITLE + " A", TITLE + " B"]
    assert not g._uncovered_inventory_items(INVENTORY, mined["types"])


def test_the_fixer_is_the_final_resort_and_never_a_local_rename(monkeypatch):
    calls: list[str] = []
    repeated = {"types": [_type([(TITLE, ["QINV-0001"]), (TITLE, ["QINV-0002"])])]}
    monkeypatch.setattr(g, "_openai_json", _fake_miner([repeated, repeated, repeated], calls))
    seen: list[dict] = []

    def fake_fixer(payload):
        seen.append(copy.deepcopy(payload))
        return {
            "cases": [
                {"case_index": 0, "case_title": TITLE + " for producing new cells"},
                {"case_index": 1, "case_title": TITLE + " for offspring resemblance"},
            ],
            "rationale": "The two Examples ask about different outcomes of DNA copying.",
        }

    monkeypatch.setattr(p3_fixer, "default_provider", lambda: fake_fixer)
    lines = _logs(monkeypatch)
    mined = g._mine_types_from_inventory_via_api(
        meta=g._metadata(subject="Science"), inventory=INVENTORY, max_coverage_attempts=2,
    )
    assert len(seen) == 1
    payload = seen[0]
    assert payload["blocked_check"][0]["code"] == "duplicate_case_titles"
    assert payload["contract"]["kind"] == "fixer.type_mining_case_titles"
    assert len(payload["type"]["case_prompts"]) == 2
    cases = mined["types"][0]["case_prompts"]
    assert [c["case_title"] for c in cases] == [
        TITLE + " for producing new cells", TITLE + " for offspring resemblance",
    ]
    restored = copy.deepcopy(mined["types"])
    for case, original in zip(restored[0]["case_prompts"], repeated["types"][0]["case_prompts"]):
        case["case_title"] = original["case_title"]
    assert restored == g._normalize_mined_type_candidate(copy.deepcopy(repeated["types"]), INVENTORY)
    assert any("different outcomes of DNA copying" in line for line in lines)

    # Decide-once on the same store: no second Fixer call, equal result.
    store = kernel.DecisionStore()
    first = g._resolve_duplicate_case_titles_via_fixer(
        copy.deepcopy(repeated["types"]), inventory=INVENTORY, meta=g._metadata(subject="Science"),
        stage="type_mining", attempts_note="test", fixer=fake_fixer, store=store,
    )
    count = len(seen)
    second = g._resolve_duplicate_case_titles_via_fixer(
        copy.deepcopy(repeated["types"]), inventory=INVENTORY, meta=g._metadata(subject="Science"),
        stage="type_mining", attempts_note="test", fixer=fake_fixer, store=store,
    )
    assert len(seen) == count and second == first


def test_without_a_fixer_the_duplicate_ships_as_mined_and_is_named(monkeypatch):
    calls: list[str] = []
    repeated = {"types": [_type([(TITLE, ["QINV-0001"]), (TITLE, ["QINV-0002"])])]}
    monkeypatch.setattr(g, "_openai_json", _fake_miner([repeated, repeated, repeated], calls))
    monkeypatch.setattr(p3_fixer, "default_provider", lambda: None)
    lines = _logs(monkeypatch)
    mined = g._mine_types_from_inventory_via_api(
        meta=g._metadata(subject="Science"), inventory=INVENTORY, max_coverage_attempts=2,
    )
    assert [c["case_title"] for c in mined["types"][0]["case_prompts"]] == [TITLE, TITLE]
    assert any("no Fixer provider is available" in line for line in lines)


def test_a_fixer_answer_that_still_repeats_or_renames_the_wrong_case_is_refused(monkeypatch):
    repeated = [_type([(TITLE, ["QINV-0001"]), (TITLE, ["QINV-0002"]), ("Another case", ["QINV-0003"])])]
    attempts: list[dict] = []

    def bad_fixer(payload):
        attempts.append(payload)
        return {"cases": [{"case_index": 0, "case_title": "Another case"},
                          {"case_index": 1, "case_title": "Another case"}],
                "rationale": "same"}

    lines = _logs(monkeypatch)
    resolved = g._resolve_duplicate_case_titles_via_fixer(
        copy.deepcopy(repeated), inventory=INVENTORY, meta=g._metadata(subject="Science"),
        stage="type_mining", attempts_note="test", fixer=bad_fixer, store=kernel.DecisionStore(),
    )
    # The checker refused every attempt; the Cases ship as mined, named.
    assert [c["case_title"] for c in resolved[0]["case_prompts"]] == [TITLE, TITLE, "Another case"]
    assert any("the Fixer could not resolve it" in line for line in lines)
    assert attempts
