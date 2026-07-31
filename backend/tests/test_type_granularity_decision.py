from __future__ import annotations

import copy

import pytest

from app import schemas
from app.services import generation as g
from app.services import semantic_recovery
from app.services import type_granularity_decision as gate


def _inventory(count: int = 12) -> dict:
    return {
        "items": [
            {
                "qid": f"QINV-{index:04d}",
                "topic_hint": "Topic",
                "raw_task": f"Complete source task {index}.",
                "task_kind": "question",
            }
            for index in range(1, count + 1)
        ],
        "stats": {"total_inventory_items": count},
    }


def _types(count: int = 10) -> dict:
    return {
        "types": [
            {
                "type_id": f"TYPE-{index:04d}",
                "type_title": f"Distinct assessment method {index}",
                "type_description": f"Apply method {index}.",
                "task_pattern": f"Given input {index}, apply method {index}.",
                "topic_match_hint": "Topic",
                "is_activity": False,
                "placement_scope": "normal",
                "source_question_ids": [f"QINV-{index:04d}"],
                "case_prompts": [{
                    "case_id": f"CASE-{index:04d}",
                    "case_title": f"Variation {index}",
                    "examples": [{
                        "source_question_id": f"QINV-{index:04d}",
                        "example_prompt": f"Complete source task {index}.",
                    }],
                }],
            }
            for index in range(1, count + 1)
        ],
    }


def _review() -> dict:
    return gate.build_review(
        raw_type_count=10,
        consolidated_type_count=10,
        inventory_count=12,
        sufficiency_added_concepts=1,
    )


def test_fragmentation_gate_pauses_without_a_model_diagnosis():
    with pytest.raises(semantic_recovery.HumanDecisionRequired) as caught:
        gate.resolve_or_pause(
            review=_review(),
            inventory=_inventory(),
            mined_types=_types(),
            meta={"subject": "History", "chapter_title": "Nationalism"},
        )

    pending = caught.value.pending_decision
    validated = schemas.PendingSemanticDecision.model_validate(pending)
    assert validated.kind == "type_granularity_review"
    assert validated.item.type_title == "10 Types for 12 QIDs"
    assert [option.choice for option in validated.options] == [
        "consolidate_types",
        "keep_distinct_types",
        "custom_instruction",
    ]
    assert validated.options[0].recommended is True
    assert len(validated.context_hash) == 64


def test_fragmentation_gate_applies_only_the_exact_saved_resolution():
    with pytest.raises(semantic_recovery.HumanDecisionRequired) as caught:
        gate.resolve_or_pause(
            review=_review(),
            inventory=_inventory(),
            mined_types=_types(),
            meta={"subject": "History"},
        )
    pending = caught.value.pending_decision
    resolution = {
        **copy.deepcopy(pending),
        "status": "ready",
        "choice": "custom_instruction",
        "instruction": "Group into 6-8 reusable methods where evidence allows.",
    }
    with gate.human_resolution_context([resolution]):
        directive = gate.resolve_or_pause(
            review=_review(),
            inventory=_inventory(),
            mined_types=_types(),
            meta={"subject": "History"},
        )

    assert directive == {
        "action": "consolidate",
        "choice": "custom_instruction",
        "instruction": "Group into 6-8 reusable methods where evidence allows.",
        "decision_id": pending["decision_id"],
        "context_hash": pending["context_hash"],
    }


def test_fragmentation_gate_does_not_pause_small_or_already_merged_taxonomy():
    small = gate.build_review(
        raw_type_count=8,
        consolidated_type_count=8,
        inventory_count=9,
        sufficiency_added_concepts=0,
    )
    merged = gate.build_review(
        raw_type_count=12,
        consolidated_type_count=10,
        inventory_count=12,
        sufficiency_added_concepts=0,
    )
    assert gate.resolve_or_pause(
        review=small,
        inventory=_inventory(9),
        mined_types=_types(8),
        meta={},
    ) == {"action": "continue"}
    assert gate.resolve_or_pause(
        review=merged,
        inventory=_inventory(),
        mined_types=_types(),
        meta={},
    ) == {"action": "continue"}


def test_applied_result_identity_rejects_source_or_taxonomy_drift():
    review = _review()
    inventory = _inventory()
    mined_types = _types()
    baseline = gate.applied_result_context_hash(
        review=review,
        inventory=inventory,
        mined_types=mined_types,
        meta={"subject": "History"},
    )
    with_audit = copy.deepcopy(review)
    with_audit["human_resolution"] = {
        "decision_id": "type-granularity-example",
        "audit": {"critic_confidence": 0.95},
    }
    assert gate.applied_result_context_hash(
        review=with_audit,
        inventory=inventory,
        mined_types=mined_types,
        meta={"subject": "History"},
    ) == baseline

    changed_inventory = copy.deepcopy(inventory)
    changed_inventory["items"][0]["raw_task"] += " Added source condition."
    assert gate.applied_result_context_hash(
        review=review,
        inventory=changed_inventory,
        mined_types=mined_types,
        meta={"subject": "History"},
    ) != baseline

    changed_types = copy.deepcopy(mined_types)
    changed_types["types"][0]["task_pattern"] += " Different output."
    assert gate.applied_result_context_hash(
        review=review,
        inventory=inventory,
        mined_types=changed_types,
        meta={"subject": "History"},
    ) != baseline


def test_human_directed_consolidation_requires_independent_confident_acceptance(
    monkeypatch,
):
    inventory = _inventory(2)
    original = _types(2)
    candidate = copy.deepcopy(original["types"][:1])
    candidate[0]["source_question_ids"] = ["QINV-0001", "QINV-0002"]
    candidate[0]["case_prompts"].append(copy.deepcopy(
        original["types"][1]["case_prompts"][0]))
    responses = [
        {"types": candidate},
        {
            "verdict": "accept",
            "confidence": 0.95,
            "reason": "Both Cases use the same reusable method.",
        },
    ]
    calls: list[str] = []

    def fake_openai(_system, user, **_kwargs):
        calls.append(user)
        return responses.pop(0)

    monkeypatch.setattr(g, "_openai_json", fake_openai)
    result, failure, audit = g._human_directed_type_consolidation_via_api(
        original,
        inventory=inventory,
        meta={"subject": "History"},
        instruction="Merge only identical assessed methods.",
    )

    assert failure == ""
    assert result is not None
    assert len(result["types"]) == 1
    assert len(calls) == 2
    assert audit["critic_verdict"] == "accept"
    assert audit["critic_confidence"] == 0.95


def test_human_directed_consolidation_repauses_on_critic_review_band(
    monkeypatch,
):
    inventory = _inventory(2)
    original = _types(2)
    candidate = copy.deepcopy(original["types"][:1])
    candidate[0]["source_question_ids"] = ["QINV-0001", "QINV-0002"]
    candidate[0]["case_prompts"].append(copy.deepcopy(
        original["types"][1]["case_prompts"][0]))
    responses = [
        {"types": candidate},
        {
            "verdict": "accept",
            "confidence": 0.91,
            "reason": "The two methods may still differ.",
        },
    ]
    monkeypatch.setattr(
        g, "_openai_json", lambda *_args, **_kwargs: responses.pop(0))

    result, failure, audit = g._human_directed_type_consolidation_via_api(
        original,
        inventory=inventory,
        meta={"subject": "History"},
        instruction="Merge only identical assessed methods.",
    )

    assert result is None
    assert "threshold 0.920" in failure
    assert audit["critic_confidence"] == 0.91


def test_human_directed_consolidation_keeps_quota_failure_as_a_hard_stop(
    monkeypatch,
):
    monkeypatch.setattr(
        g,
        "_openai_json",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("OpenAI quota exhausted (insufficient_quota)")),
    )

    with pytest.raises(RuntimeError, match="quota exhausted"):
        g._human_directed_type_consolidation_via_api(
            _types(2),
            inventory=_inventory(2),
            meta={"subject": "History"},
            instruction="Merge only identical assessed methods.",
        )


def test_human_directed_consolidation_treats_bad_confidence_as_mechanical(
    monkeypatch,
):
    original = _types(2)
    candidate = copy.deepcopy(original["types"][:1])
    candidate[0]["source_question_ids"] = ["QINV-0001", "QINV-0002"]
    candidate[0]["case_prompts"].append(copy.deepcopy(
        original["types"][1]["case_prompts"][0]))
    responses = [
        {"types": candidate},
        {"verdict": "accept", "confidence": "very sure", "reason": ""},
    ]
    monkeypatch.setattr(
        g, "_openai_json", lambda *_args, **_kwargs: responses.pop(0))

    with pytest.raises(
        semantic_recovery.ProviderResponseContractError,
        match="invalid verdict/confidence",
    ):
        g._human_directed_type_consolidation_via_api(
            original,
            inventory=_inventory(2),
            meta={"subject": "History"},
            instruction="Merge only identical assessed methods.",
        )
