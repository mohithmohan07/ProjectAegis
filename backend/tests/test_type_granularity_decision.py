from __future__ import annotations

import copy
import json
from types import SimpleNamespace

import httpx
import pytest

from app import schemas
from app.services import build_concepts
from app.services import generation as g
from app.services import semantic_recovery
from app.services import type_granularity_decision as gate


def _inventory(count: int = 12) -> dict:
    return {
        "items": [
            {
                "qid": f"QINV-{index:04d}",
                "topic_hint": "Topic",
                "raw_task": (
                    "Analyze source evidence to explain historical "
                    f"development {index}."
                ),
                "task_kind": "question",
                "requires_visual": index % 2 == 0,
            }
            for index in range(1, count + 1)
        ],
        "stats": {"total_inventory_items": count},
    }


def _parent_expanded_inventory() -> dict:
    """RNE-shaped inventory: 26 parents materialized as 31 leaf Cases."""

    items = copy.deepcopy(_inventory(24)["items"])
    for suffix in ("A", "B"):
        items.append({
            "qid": f"QINV-0025-{suffix}",
            "parent_qid": "QINV-0025",
            "topic_hint": "Italy Unified",
            "raw_task": f"Interpret Italian map Case {suffix}.",
            "task_kind": "map_task",
            "requires_visual": True,
        })
    for suffix in ("A", "B", "C", "D", "E"):
        items.append({
            "qid": f"QINV-0026-{suffix}",
            "parent_qid": "QINV-0026",
            "topic_hint": f"Short-note destination {suffix}",
            "raw_task": f"Write a note on historical target {suffix}.",
            "task_kind": "short_answer",
            "requires_visual": False,
        })
    return {
        "items": items,
        "stats": {"total_inventory_items": len(items)},
    }


def _types(count: int = 10) -> dict:
    return {
        "types": [
            {
                "type_id": f"TYPE-{index:04d}",
                "type_title": f"Distinct assessment method {index}",
                "type_description": f"Apply method {index}.",
                "task_pattern": f"Given input {index}, apply method {index}.",
                "concept_match_hint": "Shared assessed concept",
                "parent_concept_match_hint": "Shared parent concept",
                "topic_match_hint": "Topic",
                "difficulty_hint": "Intermediate",
                "cognitive_skill_hint": "Apply",
                "subject_skill_hint": "Source analysis",
                "is_activity": False,
                "placement_scope": "normal",
                "source_question_ids": [f"QINV-{index:04d}"],
                "case_prompts": [{
                    "case_id": f"CASE-{index:04d}",
                    "case_title": f"Variation {index}",
                    "case_signature": f"constraint-{index}",
                    "concept_match_hint": "Shared assessed concept",
                    "parent_concept_match_hint": "Shared parent concept",
                    "topic_match_hint": "Topic",
                    "difficulty_hint": "Intermediate",
                    "cognitive_skill_hint": "Apply",
                    "subject_skill_hint": "Source analysis",
                    "is_activity": False,
                    "placement_scope": "normal",
                    "examples": [{
                        "source_question_id": f"QINV-{index:04d}",
                        "example_prompt": (
                            "Analyze source evidence to explain historical "
                            f"development {index}."
                        ),
                    }],
                }],
            }
            for index in range(1, count + 1)
        ],
    }


def _merged_candidate(original: dict) -> list[dict]:
    candidate = copy.deepcopy(original["types"][:1])
    candidate[0]["source_question_ids"] = ["QINV-0001", "QINV-0002"]
    candidate[0]["case_prompts"].append(copy.deepcopy(
        original["types"][1]["case_prompts"][0]
    ))
    return candidate


def _review() -> dict:
    return gate.build_review(
        raw_type_count=10,
        consolidated_type_count=10,
        inventory_count=12,
        sufficiency_added_concepts=1,
    )


def _verdict(fragmented: bool = True, **overrides) -> dict:
    return {
        "fragmented": fragmented,
        "rationale": (
            "Each Type restates one source question rather than a reusable "
            "method." if fragmented else
            "The Types are reusable methods that recur across questions."
        ),
        "review_flags": [],
        **overrides,
    }


def test_fragmented_verdict_pauses_with_rationale_in_the_packet():
    with pytest.raises(semantic_recovery.HumanDecisionRequired) as caught:
        gate.resolve_or_pause(
            review=_review(),
            inventory=_inventory(),
            mined_types=_types(),
            meta={"subject": "History", "chapter_title": "Nationalism"},
            verdict=_verdict(),
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
    # The model's rationale — not a ratio — is the shown diagnosis/evidence.
    assert _verdict()["rationale"] in pending["diagnosis"]
    assert any(
        row["label"] == "Model fragmentation rationale"
        and _verdict()["rationale"] in row["text"]
        for row in pending["evidence"]
    )
    assert not any(
        row["label"] in {"Type-to-QID ratio", "Type-to-parent-task ratio"}
        for row in pending["evidence"]
    )


def test_fragmentation_gate_applies_only_the_exact_saved_resolution():
    with pytest.raises(semantic_recovery.HumanDecisionRequired) as caught:
        gate.resolve_or_pause(
            review=_review(),
            inventory=_inventory(),
            mined_types=_types(),
            meta={"subject": "History"},
            verdict=_verdict(),
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
            verdict=_verdict(),
        )

    assert directive == {
        "action": "consolidate",
        "choice": "custom_instruction",
        "instruction": "Group into 6-8 reusable methods where evidence allows.",
        "decision_id": pending["decision_id"],
        "context_hash": pending["context_hash"],
    }


def test_carry_forward_keeps_the_types_it_declined_to_judge():
    """A decision settled by changing nothing must not merge the Types.

    ``carry_forward`` says Aegis could not judge this decision and applied
    nothing. Every other choice here means "consolidate", so falling through
    the branch would merge the mined Types on the strength of a decision that
    explicitly declined to decide -- and a reviewer reading the delivered
    workbook cannot unmerge them.
    """

    with pytest.raises(semantic_recovery.HumanDecisionRequired) as caught:
        gate.resolve_or_pause(
            review=_review(),
            inventory=_inventory(),
            mined_types=_types(),
            meta={"subject": "History"},
            verdict=_verdict(),
        )
    pending = caught.value.pending_decision
    resolution = {
        **copy.deepcopy(pending),
        "status": "ready",
        "choice": "carry_forward",
        "instruction": "",
    }
    with gate.human_resolution_context([resolution]):
        directive = gate.resolve_or_pause(
            review=_review(),
            inventory=_inventory(),
            mined_types=_types(),
            meta={"subject": "History"},
            verdict=_verdict(),
        )

    assert directive["choice"] == "carry_forward"
    assert directive["action"] == "keep"


def test_consumed_type_resolution_requires_fresh_decision_through_orchestrator():
    with pytest.raises(semantic_recovery.HumanDecisionRequired) as caught:
        gate.resolve_or_pause(
            review=_review(),
            inventory=_inventory(),
            mined_types=_types(),
            meta={"subject": "History"},
            verdict=_verdict(),
        )
    pending = caught.value.pending_decision
    consumed = {
        "decision_id": pending["decision_id"],
        "context_hash": pending["context_hash"],
        "choice": "consolidate_types",
        "instruction": "Merge only genuinely reusable task patterns.",
        "target_id": "",
        "target_concept_id": "",
        "resolved_at": "2026-08-01T00:00:00+00:00",
        "consumed_at": "2026-08-01T00:01:00+00:00",
        "status": "consumed",
        "pending_decision": copy.deepcopy(pending),
    }
    checkpoint = {
        build_concepts._HUMAN_DECISIONS_KEY: {
            "resolutions": [consumed],
        },
    }

    with build_concepts._human_decision_resolution_context(checkpoint):
        with pytest.raises(semantic_recovery.HumanDecisionRequired) as replay:
            gate.resolve_or_pause(
                review=_review(),
                inventory=_inventory(),
                mined_types=_types(),
                meta={"subject": "History"},
                verdict=_verdict(),
            )

    fresh = replay.value.pending_decision
    assert fresh["decision_id"] != pending["decision_id"]
    assert "already used" in fresh["diagnosis"]


def test_healthy_verdict_continues_regardless_of_counts():
    """No type/QID arithmetic decides: a healthy model verdict continues even
    at exactly one Type per question."""
    review = gate.build_review(
        raw_type_count=24,
        consolidated_type_count=24,
        inventory_count=24,
        sufficiency_added_concepts=0,
    )
    assert gate.resolve_or_pause(
        review=review,
        inventory=_inventory(24),
        mined_types=_types(24),
        meta={"subject": "History"},
        verdict=_verdict(fragmented=False),
    ) == {"action": "continue"}


def test_missing_verdict_fails_closed_into_the_pause():
    """A non-decision never continues silently: the run pauses and asks."""
    with pytest.raises(semantic_recovery.HumanDecisionRequired) as caught:
        gate.resolve_or_pause(
            review=_review(),
            inventory=_inventory(),
            mined_types=_types(),
            meta={"subject": "History"},
        )
    assert "failing closed" in caught.value.pending_decision["diagnosis"]


def test_fragmentation_verdict_author_decides_and_critic_confirms():
    calls: list[tuple[str, str]] = []

    def api(system, user, **kwargs):
        calls.append((system, user))
        if len(calls) == 1:
            return {
                "verdict": "healthy",
                "rationale": "Methods recur across questions.",
            }
        return {"verdict": "confirmed", "rationale": ""}

    out = gate.fragmentation_verdict(
        _review(),
        mined_types=_types(),
        inventory=_inventory(),
        api_call=api,
    )

    assert out == {
        "fragmented": False,
        "rationale": "Methods recur across questions.",
        "review_flags": [],
    }
    assert len(calls) == 2
    # Both calls carry the mined taxonomy and the raw counts as evidence.
    for _system, user in calls:
        assert "Distinct assessment method 1" in user
        assert "raw_type_count" in user
    assert "healthy" in calls[1][1]


def test_fragmentation_verdict_author_non_decision_fails_closed():
    def api(system, user, **kwargs):
        return {"verdict": "maybe"}

    out = gate.fragmentation_verdict(
        _review(),
        mined_types=_types(),
        inventory=_inventory(),
        api_call=api,
    )

    assert out["fragmented"] is True
    assert "failing closed" in out["rationale"]
    assert "fragmentation_author_non_decision" in out["review_flags"]

    def broken(system, user, **kwargs):
        raise RuntimeError("provider unavailable")

    out = gate.fragmentation_verdict(
        _review(),
        mined_types=_types(),
        inventory=_inventory(),
        api_call=broken,
    )
    assert out["fragmented"] is True
    assert "fragmentation_author_unavailable" in out["review_flags"]


def test_fragmentation_critic_dissent_is_advisory_only():
    calls = {"count": 0}

    def api(system, user, **kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            return {"verdict": "healthy", "rationale": "Reusable methods."}
        return {
            "verdict": "not_confirmed",
            "rationale": "Several Types look near-identical.",
        }

    out = gate.fragmentation_verdict(
        _review(),
        mined_types=_types(),
        inventory=_inventory(),
        api_call=api,
    )

    # The author's verdict stands; the dissent rides as a review flag.
    assert out["fragmented"] is False
    assert any(
        flag.startswith("fragmentation_critic_dissent")
        and "near-identical" in flag
        for flag in out["review_flags"]
    )


def test_fragmentation_critic_failure_only_flags():
    calls = {"count": 0}

    def api(system, user, **kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            return {"verdict": "fragmented", "rationale": "One per question."}
        raise RuntimeError("critic transport error")

    out = gate.fragmentation_verdict(
        _review(),
        mined_types=_types(),
        inventory=_inventory(),
        api_call=api,
    )

    assert out["fragmented"] is True
    assert any(
        flag.startswith("fragmentation_critic_unavailable")
        for flag in out["review_flags"]
    )


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


@pytest.mark.parametrize(
    ("location", "field"),
    [
        ("type", "concept_match_hint"),
        ("type", "parent_concept_match_hint"),
        ("type", "difficulty_hint"),
        ("type", "cognitive_skill_hint"),
        ("type", "subject_skill_hint"),
        ("case", "case_id"),
        ("case", "case_title"),
        ("case", "case_signature"),
        ("case", "concept_match_hint"),
        ("case", "parent_concept_match_hint"),
        ("case", "topic_match_hint"),
        ("case", "difficulty_hint"),
        ("case", "cognitive_skill_hint"),
        ("case", "subject_skill_hint"),
        ("case_bool", "is_activity"),
        ("case", "placement_scope"),
        ("inventory", "requires_visual"),
    ],
)
def test_applied_result_identities_bind_every_immutable_semantic_field(
    location,
    field,
):
    review = _review()
    inventory = _inventory()
    mined_types = _types()
    baseline_context = gate.applied_result_context_hash(
        review=review,
        inventory=inventory,
        mined_types=mined_types,
        meta={"subject": "History"},
    )
    baseline_semantics = gate.applied_result_semantic_hash(
        inventory=inventory,
        mined_types=mined_types,
    )
    changed_inventory = copy.deepcopy(inventory)
    changed_types = copy.deepcopy(mined_types)
    if location == "type":
        changed_types["types"][0][field] += " changed"
    elif location == "case":
        changed_types["types"][0]["case_prompts"][0][field] += " changed"
    elif location == "case_bool":
        changed_types["types"][0]["case_prompts"][0][field] = not (
            changed_types["types"][0]["case_prompts"][0][field]
        )
    else:
        changed_inventory["items"][0][field] = not changed_inventory[
            "items"
        ][0][field]

    assert gate.applied_result_context_hash(
        review=review,
        inventory=changed_inventory,
        mined_types=changed_types,
        meta={"subject": "History"},
    ) != baseline_context
    assert gate.applied_result_semantic_hash(
        inventory=changed_inventory,
        mined_types=changed_types,
    ) != baseline_semantics


@pytest.mark.parametrize(
    "stage",
    ["pre_type_assignment", "post_type_assignment", "final_content_ready"],
)
def test_every_type_checkpoint_stage_rejects_tampered_human_result(
    monkeypatch,
    stage,
):
    inventory = _inventory(2)
    mined_types = _types(2)
    review = _review()
    review["human_resolution"] = {
        "decision_id": "type-granularity-example",
        "choice": "consolidate_types",
    }
    mined_types["_granularity_review"] = review
    review["human_resolution"]["semantic_contract_hash"] = (
        gate.applied_result_semantic_hash(
            inventory=inventory,
            mined_types=mined_types,
        )
    )
    mined_types["_granularity_review"] = copy.deepcopy(review)
    current_terminal_contract = (
        {
            g.PHASE3_PRE_RELEASE_FIELD: g.phase3_pre_release_bundle({}, {}),
        }
        if stage in {"post_type_assignment", "final_content_ready"}
        else {}
    )
    checkpoint = g._make_concept_checkpoint(
        stage,
        records=[{"concept_title": "Example"}],
        question_task_inventory=inventory,
        mined_types=mined_types,
        method_row_snapshot=[],
        **current_terminal_contract,
    )
    monkeypatch.setattr(
        g,
        "_placement_certification_contract_complete",
        lambda *_args, **_kwargs: True,
    )
    # This test isolates the human-resolution semantic seal.  Late checkpoint
    # ownership-ledger migration is covered independently by the Type/Case
    # checkpoint regressions.
    monkeypatch.setattr(
        g,
        "_type_case_checkpoint_placement_ledger_valid",
        lambda *_args, **_kwargs: True,
    )

    assert g._compatible_concept_checkpoint_entry(checkpoint)

    tampered = copy.deepcopy(checkpoint)
    tampered["mined_types"]["types"][0]["case_prompts"][0][
        "case_signature"
    ] += " changed"
    assert not g._compatible_concept_checkpoint_entry(tampered)

    unsealed = copy.deepcopy(checkpoint)
    del unsealed["mined_types"]["_granularity_review"][
        "human_resolution"
    ]["semantic_contract_hash"]
    assert not g._compatible_concept_checkpoint_entry(unsealed)


def test_human_directed_consolidation_requires_independent_confident_acceptance(
    monkeypatch,
):
    inventory = _inventory(2)
    original = _types(2)
    candidate = _merged_candidate(original)
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
    assert [
        case["case_signature"]
        for case in result["types"][0]["case_prompts"]
    ] == ["constraint-1", "constraint-2"]
    for field in (
        "concept_match_hint",
        "parent_concept_match_hint",
        "difficulty_hint",
        "cognitive_skill_hint",
        "subject_skill_hint",
        "case_signature",
        "requires_visual",
    ):
        assert f'"{field}"' in calls[1]
    assert '"requires_visual": false' in calls[0]
    assert '"requires_visual": true' in calls[0]
    assert '"requires_visual": false' in calls[1]
    assert '"requires_visual": true' in calls[1]


def test_human_directed_consolidation_applies_with_flag_on_critic_dissent(
    monkeypatch,
):
    """The critic is an auditor (Q10): its review-band or reject dissent is
    recorded on the applied result, never a gate — the deterministic safety
    gates already certified the consolidation's source contracts."""
    inventory = _inventory(2)
    original = _types(2)
    candidate = _merged_candidate(original)
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
    monkeypatch.setenv("AEGIS_SEMANTIC_ACCEPTANCE_MIN_CONFIDENCE", "0.90")

    result, failure, audit = g._human_directed_type_consolidation_via_api(
        original,
        inventory=inventory,
        meta={"subject": "History"},
        instruction="Merge only identical assessed methods.",
    )

    assert failure == ""
    assert result is not None
    assert len(result["types"]) == 1
    assert audit["critic_confidence"] == 0.91
    assert any(
        "dissent" in flag and "The two methods may still differ." in flag
        for flag in audit["review_flags"]
    )


def test_human_directed_consolidation_applies_with_flag_on_critic_reject(
    monkeypatch,
):
    inventory = _inventory(2)
    original = _types(2)
    candidate = _merged_candidate(original)
    responses = [
        {"types": candidate},
        {
            "verdict": "reject",
            "confidence": 0.99,
            "reason": "The merged Cases assess different methods.",
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

    assert failure == ""
    assert result is not None
    assert len(result["types"]) == 1
    assert audit["critic_verdict"] == "reject"
    assert any(
        "verdict=reject" in flag
        and "The merged Cases assess different methods." in flag
        for flag in audit["review_flags"]
    )


@pytest.mark.parametrize(
    "field",
    [
        "concept_match_hint",
        "parent_concept_match_hint",
        "difficulty_hint",
        "cognitive_skill_hint",
        "subject_skill_hint",
    ],
)
def test_human_directed_consolidation_rejects_case_semantic_drift_before_critic(
    monkeypatch,
    field,
):
    original = _types(2)
    original["types"][1]["case_prompts"][0][field] += " alternate"
    candidate = _merged_candidate(original)
    candidate[0]["case_prompts"][1][field] = (
        original["types"][0]["case_prompts"][0][field]
    )
    calls = 0

    def fake_openai(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return {"types": candidate}

    monkeypatch.setattr(g, "_openai_json", fake_openai)
    result, failure, audit = g._human_directed_type_consolidation_via_api(
        original,
        inventory=_inventory(2),
        meta={"subject": "History"},
        instruction="Merge only identical assessed methods.",
    )

    assert result is None
    assert "immutable semantic drift" in failure
    assert audit["semantic_drift"] == 1
    assert calls == 1


@pytest.mark.parametrize("field", ["case_id", "case_title"])
def test_human_directed_consolidation_rejects_case_identity_drift_before_critic(
    monkeypatch,
    field,
):
    original = _types(2)
    candidate = _merged_candidate(original)
    candidate[0]["case_prompts"][1][field] += " rewritten"
    calls = 0

    def fake_openai(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return {"types": candidate}

    monkeypatch.setattr(g, "_openai_json", fake_openai)
    result, failure, audit = g._human_directed_type_consolidation_via_api(
        original,
        inventory=_inventory(2),
        meta={"subject": "History"},
        instruction="Merge only identical assessed methods.",
    )

    assert result is None
    assert "immutable semantic drift" in failure
    assert audit["semantic_drift"] == 1
    assert calls == 1


def test_human_directed_consolidation_rejects_collapsed_case_signature(
    monkeypatch,
):
    original = _types(2)
    candidate = _merged_candidate(original)
    candidate[0]["case_prompts"][1]["case_signature"] = "constraint-1"
    calls = 0

    def fake_openai(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return {"types": candidate}

    monkeypatch.setattr(g, "_openai_json", fake_openai)
    result, failure, audit = g._human_directed_type_consolidation_via_api(
        original,
        inventory=_inventory(2),
        meta={"subject": "History"},
        instruction="Merge only identical assessed methods.",
    )

    assert result is None
    assert "immutable semantic drift" in failure
    assert audit["semantic_drift"] == 1
    assert calls == 1


@pytest.mark.parametrize(
    ("field", "changed_value"),
    [
        ("normalized_task", "Normalized task changed."),
        ("shared_context", "Changed source passage context."),
        ("source_kind", "diagram_task"),
        ("parent_source_label", "Changed parent label"),
        ("page_hint", "42"),
        ("block_ids", ["BLK-CHANGED"]),
        ("options", ["A", "B", "C"]),
        ("image_urls", ["https://example.invalid/changed.png"]),
        ("content_objects", {"figures": ["Figure changed"]}),
        ("requires_context", True),
        ("order_index", 99),
    ],
)
def test_type_replay_identity_binds_complete_source_inventory_item(
    field,
    changed_value,
):
    review = _review()
    inventory = _inventory()
    mined_types = _types()
    baseline_context = gate.applied_result_context_hash(
        review=review,
        inventory=inventory,
        mined_types=mined_types,
        meta={"subject": "History"},
    )
    baseline_semantics = gate.applied_result_semantic_hash(
        inventory=inventory,
        mined_types=mined_types,
    )
    changed = copy.deepcopy(inventory)
    changed["items"][0][field] = changed_value

    assert gate.applied_result_context_hash(
        review=review,
        inventory=changed,
        mined_types=mined_types,
        meta={"subject": "History"},
    ) != baseline_context
    assert gate.applied_result_semantic_hash(
        inventory=changed,
        mined_types=mined_types,
    ) != baseline_semantics


def test_human_directed_consolidation_rejects_emitted_visual_semantic_drift(
    monkeypatch,
):
    original = _types(2)
    candidate = _merged_candidate(original)
    candidate[0]["case_prompts"][1]["examples"][0][
        "requires_visual"
    ] = False
    calls = 0

    def fake_openai(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return {"types": candidate}

    monkeypatch.setattr(g, "_openai_json", fake_openai)
    result, failure, audit = g._human_directed_type_consolidation_via_api(
        original,
        inventory=_inventory(2),
        meta={"subject": "History"},
        instruction="Merge only identical assessed methods.",
    )

    assert result is None
    assert "immutable semantic drift" in failure
    assert audit["semantic_drift"] == 1
    assert calls == 1


def test_deterministic_type_merge_keeps_case_skill_profiles_distinct():
    original = _types(2)["types"]
    for field in ("type_title", "type_description", "task_pattern"):
        original[1][field] = original[0][field]
    original[1]["case_prompts"][0]["difficulty_hint"] = "Advanced"

    merged = g._merge_equivalent_mined_types(original)

    assert len(merged) == 1
    assert [
        case["difficulty_hint"] for case in merged[0]["case_prompts"]
    ] == ["Intermediate", "Advanced"]


def test_ordinary_consolidation_also_rejects_immutable_semantic_drift(
    monkeypatch,
):
    original = _types(2)
    original["types"][1]["case_prompts"][0][
        "cognitive_skill_hint"
    ] = "Evaluate"
    candidate = _merged_candidate(original)
    candidate[0]["case_prompts"][1]["cognitive_skill_hint"] = "Apply"
    monkeypatch.setattr(
        g,
        "_openai_json",
        lambda *_args, **_kwargs: {"types": candidate},
    )

    result = g._consolidate_semantic_types_via_api(
        original,
        inventory=_inventory(2),
        meta={"subject": "History"},
    )

    assert result == {"types": original["types"]}


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
    candidate = _merged_candidate(original)
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


def _chat_response(
    content: str,
    *,
    finish_reason: str = "stop",
) -> SimpleNamespace:
    return SimpleNamespace(
        choices=[SimpleNamespace(
            message=SimpleNamespace(content=content),
            finish_reason=finish_reason,
        )]
    )


def _physical_failure(kind: str):
    if kind == "malformed":
        return _chat_response("not-json")
    if kind == "truncated":
        return _chat_response("{}", finish_reason="length")
    if kind == "transient":
        from openai import APITimeoutError

        return APITimeoutError(request=httpx.Request(
            "POST", "https://api.openai.com/v1/chat/completions"
        ))
    raise AssertionError(f"unknown failure kind: {kind}")


def _install_scripted_openai(monkeypatch, plan: list):
    import openai

    script = list(plan)

    class ScriptedOpenAI:
        calls: list[dict] = []
        init_kwargs: list[dict] = []

        def __init__(self, *_args, **kwargs):
            type(self).init_kwargs.append(dict(kwargs))
            self.chat = SimpleNamespace(
                completions=SimpleNamespace(create=self._create)
            )

        def _create(self, **kwargs):
            type(self).calls.append(dict(kwargs))
            step = script.pop(0)
            if isinstance(step, BaseException):
                raise step
            return step

    monkeypatch.setattr(openai, "OpenAI", ScriptedOpenAI)
    monkeypatch.setattr(g, "_openai_gate", None)
    return ScriptedOpenAI, script


def _physical_pair_payloads() -> tuple[dict, list]:
    original = _types(2)
    candidate = _merged_candidate(original)
    return original, [
        _chat_response(json.dumps({"types": candidate})),
        _chat_response(json.dumps({
            "verdict": "accept",
            "confidence": 0.97,
            "reason": "The immutable Cases share one reusable method.",
        })),
    ]


def test_human_directed_pair_uses_exactly_two_physical_client_requests(
    monkeypatch,
):
    original, successful = _physical_pair_payloads()
    client, script = _install_scripted_openai(monkeypatch, successful)

    result, failure, _audit = g._human_directed_type_consolidation_via_api(
        original,
        inventory=_inventory(2),
        meta={"subject": "History"},
        instruction="Merge only identical assessed methods.",
    )

    assert result is not None
    assert failure == ""
    assert len(client.calls) == 2
    assert not script
    assert len(client.init_kwargs) == 2
    assert all(row["max_retries"] == 0 for row in client.init_kwargs)


@pytest.mark.parametrize("failure_kind", ["malformed", "truncated", "transient"])
def test_human_directed_provider_failure_has_one_physical_request(
    monkeypatch,
    failure_kind,
):
    original, successful = _physical_pair_payloads()
    client, script = _install_scripted_openai(
        monkeypatch,
        [_physical_failure(failure_kind), *successful],
    )
    monkeypatch.setattr(g.config, "OPENAI_TRANSIENT_RETRIES", 10)

    with pytest.raises(RuntimeError):
        g._human_directed_type_consolidation_via_api(
            original,
            inventory=_inventory(2),
            meta={"subject": "History"},
            instruction="Merge only identical assessed methods.",
        )

    assert len(client.calls) == 1
    assert len(script) == 2
    assert client.init_kwargs == [{
        "timeout": g.config.OPENAI_REQUEST_TIMEOUT_SECONDS,
        "max_retries": 0,
    }]


@pytest.mark.parametrize("failure_kind", ["malformed", "truncated", "transient"])
def test_human_directed_critic_failure_has_one_critic_physical_request(
    monkeypatch,
    failure_kind,
):
    original, successful = _physical_pair_payloads()
    provider, critic = successful
    client, script = _install_scripted_openai(
        monkeypatch,
        [provider, _physical_failure(failure_kind), critic],
    )
    monkeypatch.setattr(g.config, "OPENAI_TRANSIENT_RETRIES", 10)

    with pytest.raises(RuntimeError):
        g._human_directed_type_consolidation_via_api(
            original,
            inventory=_inventory(2),
            meta={"subject": "History"},
            instruction="Merge only identical assessed methods.",
        )

    assert len(client.calls) == 2
    assert len(script) == 1
    assert len(client.init_kwargs) == 2
    assert all(row["max_retries"] == 0 for row in client.init_kwargs)
