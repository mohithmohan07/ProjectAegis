from __future__ import annotations

import copy
import json
import re

import pytest

from app.services import generation as g
from app.services import type_granularity_decision


def test_type_mining_system_requires_semantic_reusable_patterns():
    system = g.prompts.get_text("concepts.type_mining.system")
    assert "reusable assessment pattern" in system.lower()
    assert "source_question_ids" in system
    assert "case_prompts" in system


def test_mine_types_retains_hard_gate_for_unrecoverable_empty_task(monkeypatch):
    calls = {"n": 0}

    def fake_openai(system, user, **kwargs):
        calls["n"] += 1
        return {"types": []}

    monkeypatch.setattr(g, "_openai_json", fake_openai)
    with pytest.raises(
        RuntimeError, match=r"1 unclassified.*0 duplicate",
    ):
        g._mine_types_from_inventory_via_api(
            meta=g._metadata(subject="Mathematics"),
            inventory={"items": [{
                "qid": "QINV-0001",
                "source_kind": "exercise",
                "topic_hint": "Topic A",
                "raw_task": "",
                "normalized_task": "",
            }], "stats": {}},
            max_focused_attempts=1,
        )

    assert calls["n"] == 2


def test_residual_type_fallback_never_authors_semantics_without_live_fixer(
    monkeypatch,
):
    """Rule 1: source images/topic survive as evidence, not code-authored Types."""
    from app.services.phase3 import fixer as fixer_mod

    image_url = "https://cdn.mathpix.com/cropped/diagram-42.png"
    source_task = (
        "Study the construction in Figure 4.2 and determine the requested "
        "length, using every labelled value."
    )
    item = {
        "qid": "QINV-0042",
        "source_kind": "diagram_task",
        "topic_hint": "Geometric Constructions",
        "raw_task": source_task + "\nSolution: The length is 8 cm.",
        "normalized_task": "shortened task",
        "raw_solution_or_answer": "The length is 8 cm.",
        "image_urls": [image_url],
    }
    inventory = {"items": [item], "stats": {}}
    monkeypatch.setattr(fixer_mod, "default_provider", lambda: None)

    normalized, added = g._append_deterministic_type_fallbacks(
        [], missed_items=[item], inventory=inventory)

    assert normalized == []
    assert added == 0
    # The source evidence itself is untouched for a future model/Fixer verdict.
    assert item["topic_hint"] == "Geometric Constructions"
    assert item["image_urls"] == [image_url]
    assert source_task in item["raw_task"]


@pytest.mark.parametrize(
    "marker",
    [
        "Solution. The length is 8 cm.",
        "Sol. The length is 8 cm.",
        "Ans. The length is 8 cm.",
    ],
)
def test_inventory_task_without_solution_handles_common_answer_markers(marker):
    text = f"Find the length.\n{marker}"
    assert g._inventory_task_without_solution(text, aggressive=True) == (
        "Find the length."
    )


def test_semantic_fallback_wording_does_not_leak_solution_text():
    item = {
        "qid": "QINV-0008",
        "source_kind": "short_answer",
        "topic_hint": "Algebra",
        "raw_task": "Explain the identity.\nSolution: x + 0 = x.",
        "normalized_task": "Explain the identity.",
    }
    fallback = g._deterministic_fallback_type(item)
    assert fallback is not None
    rendered = json.dumps(fallback)
    assert "x + 0 = x" not in rendered


def test_fallback_action_wording_is_not_source_label_based():
    item = {
        "qid": "QINV-0010",
        "source_kind": "exercise",
        "topic_hint": "Fractions",
        "raw_task": "Compare the two fractions and justify your answer.",
        "normalized_task": "Compare the two fractions and justify your answer.",
    }
    title, case = g._semantic_fallback_wording(item, item["raw_task"])
    assert title.startswith("Comparing")
    assert "Exercise" not in title
    assert "exercise" not in case.lower()


def test_type_granularity_review_uses_model_verdict_not_ratio():
    review = type_granularity_decision.build_review(
        raw_type_count=9,
        consolidated_type_count=7,
        inventory_count=12,
        sufficiency_added_concepts=0,
        sufficiency_audit_complete=True,
    )
    assert "fragmentation_ratio" not in review
    assert "fragmentation_threshold" not in review


def test_type_qid_contracts_are_stable_under_case_order():
    base = [{
        "type_id": "TYPE-0001",
        "type_title": "Compare quantities",
        "source_question_ids": ["QINV-0001", "QINV-0002"],
        "case_prompts": [
            {
                "case_id": "CASE-1",
                "case_title": "A",
                "topic_match_hint": "Topic A",
                "examples": [{
                    "source_question_id": "QINV-0001",
                    "example_prompt": "Compare A.",
                }],
            },
            {
                "case_id": "CASE-2",
                "case_title": "B",
                "topic_match_hint": "Topic B",
                "examples": [{
                    "source_question_id": "QINV-0002",
                    "example_prompt": "Compare B.",
                }],
            },
        ],
    }]
    reversed_cases = copy.deepcopy(base)
    reversed_cases[0]["case_prompts"].reverse()
    assert g._type_qid_contracts(base) == g._type_qid_contracts(reversed_cases)
